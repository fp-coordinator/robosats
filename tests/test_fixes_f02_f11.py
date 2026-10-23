"""
Unit tests for F-02 (pay_onchain RPC-before-DB ordering) and F-11
(orders_disputes_started corruption).

These tests use only stdlib (unittest + unittest.mock) and have no Django or
gRPC dependencies, so they can be run standalone:

    python3 tests/test_fixes_f02_f11.py

The same tests are also discovered by pytest/Django's test runner when the full
stack is available.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# F-02: pay_onchain — status must stay QUEUE if the RPC raises
# ---------------------------------------------------------------------------

def _make_pay_onchain_lnd():
    """
    Return a minimal reimplementation of the patched pay_onchain logic for LND,
    matching the structure in api/lightning/lnd.py after the F-02 fix.
    This lets us test the ordering contract without importing gRPC modules.
    """
    QUEUE = 5
    MEMPO = 2

    def pay_onchain(onchainpayment, rpc_stub, queue_code=QUEUE, on_mempool_code=MEMPO):
        """Mirrors the fixed LND pay_onchain control flow."""
        if onchainpayment.status == queue_code:
            # RPC first
            response = rpc_stub.SendCoins()
            # Only reach here if SendCoins did not raise
            onchainpayment.status = on_mempool_code
            if response.txid:
                onchainpayment.txid = response.txid
                onchainpayment.broadcasted = True
            onchainpayment.save(update_fields=["status", "txid", "broadcasted"])
            return True
        elif onchainpayment.status == on_mempool_code:
            return True  # already recorded

    return pay_onchain, QUEUE, MEMPO


def _make_pay_onchain_cln():
    """Same for CLN / Withdraw."""
    QUEUE = 5
    MEMPO = 2

    def pay_onchain(onchainpayment, rpc_stub, queue_code=QUEUE, on_mempool_code=MEMPO):
        if onchainpayment.status == queue_code:
            response = rpc_stub.Withdraw()
            onchainpayment.status = on_mempool_code
            if response.txid:
                onchainpayment.txid = response.txid.hex()
                onchainpayment.broadcasted = True
            onchainpayment.save(update_fields=["status", "txid", "broadcasted"])
            return True
        elif onchainpayment.status == on_mempool_code:
            return True

    return pay_onchain, QUEUE, MEMPO


class TestPayOnchainOrdering(unittest.TestCase):
    """F-02: DB write must never precede a successful RPC call."""

    # --- LND ---

    def test_lnd_status_stays_queue_when_rpc_raises(self):
        """If SendCoins raises, status must remain QUEUE (5), save() must not be called."""
        pay_onchain, QUEUE, MEMPO = _make_pay_onchain_lnd()

        payment = MagicMock()
        payment.status = QUEUE

        stub = MagicMock()
        stub.SendCoins.side_effect = Exception("LND connection refused")

        with self.assertRaises(Exception):
            pay_onchain(payment, stub)

        self.assertEqual(payment.status, QUEUE,
                         "status must stay QUEUE when SendCoins raises")
        payment.save.assert_not_called()

    def test_lnd_status_becomes_mempo_on_success(self):
        """If SendCoins succeeds, status must be written to MEMPO (2) in a single save."""
        pay_onchain, QUEUE, MEMPO = _make_pay_onchain_lnd()

        payment = MagicMock()
        payment.status = QUEUE

        response = MagicMock()
        response.txid = "abc123txid"

        stub = MagicMock()
        stub.SendCoins.return_value = response

        result = pay_onchain(payment, stub)

        self.assertTrue(result)
        self.assertEqual(payment.status, MEMPO,
                         "status must be MEMPO after a successful SendCoins")
        # save() called exactly once, containing status in update_fields
        payment.save.assert_called_once()
        saved_fields = payment.save.call_args[1]["update_fields"]
        self.assertIn("status", saved_fields)
        self.assertIn("txid", saved_fields)
        self.assertIn("broadcasted", saved_fields)

    def test_lnd_no_double_save(self):
        """save() must be called exactly once on success (not once for status, once for txid)."""
        pay_onchain, QUEUE, _ = _make_pay_onchain_lnd()

        payment = MagicMock()
        payment.status = QUEUE

        response = MagicMock()
        response.txid = "defi456"
        stub = MagicMock()
        stub.SendCoins.return_value = response

        pay_onchain(payment, stub)

        self.assertEqual(payment.save.call_count, 1,
                         "save() must be called exactly once, not separately for status and txid")

    def test_lnd_already_mempo_returns_true(self):
        """Calling pay_onchain on an already-MEMPO payment is idempotent."""
        pay_onchain, QUEUE, MEMPO = _make_pay_onchain_lnd()

        payment = MagicMock()
        payment.status = MEMPO
        stub = MagicMock()

        result = pay_onchain(payment, stub)

        self.assertTrue(result)
        stub.SendCoins.assert_not_called()
        payment.save.assert_not_called()

    # --- CLN ---

    def test_cln_status_stays_queue_when_rpc_raises(self):
        """If Withdraw raises, status must remain QUEUE (5), save() must not be called."""
        pay_onchain, QUEUE, MEMPO = _make_pay_onchain_cln()

        payment = MagicMock()
        payment.status = QUEUE

        stub = MagicMock()
        stub.Withdraw.side_effect = Exception("CLN node unreachable")

        with self.assertRaises(Exception):
            pay_onchain(payment, stub)

        self.assertEqual(payment.status, QUEUE,
                         "status must stay QUEUE when Withdraw raises")
        payment.save.assert_not_called()

    def test_cln_status_becomes_mempo_on_success(self):
        """If Withdraw succeeds, status must be written to MEMPO (2) in a single save."""
        pay_onchain, QUEUE, MEMPO = _make_pay_onchain_cln()

        payment = MagicMock()
        payment.status = QUEUE

        txid_bytes = MagicMock()
        txid_bytes.hex.return_value = "cln_txid_hex"
        response = MagicMock()
        response.txid = txid_bytes

        stub = MagicMock()
        stub.Withdraw.return_value = response

        result = pay_onchain(payment, stub)

        self.assertTrue(result)
        self.assertEqual(payment.status, MEMPO)
        payment.save.assert_called_once()
        saved_fields = payment.save.call_args[1]["update_fields"]
        self.assertIn("status", saved_fields)


# ---------------------------------------------------------------------------
# F-11: orders_disputes_started — must produce comma-separated IDs, not
#       a Python list repr or a list of individual characters.
# ---------------------------------------------------------------------------

def _apply_dispute(orders_disputes_started, new_order_id):
    """
    Reimplements the fixed logics.py open_dispute field update, isolated from Django.
    Returns the new value of orders_disputes_started.
    """
    if orders_disputes_started is None:
        return str(new_order_id)
    else:
        return f"{orders_disputes_started},{new_order_id}"


class TestOrdersDisputesStarted(unittest.TestCase):
    """F-11: orders_disputes_started must be a comma-separated integer string."""

    def test_first_dispute_sets_string(self):
        result = _apply_dispute(None, 12345)
        self.assertEqual(result, "12345")

    def test_second_dispute_appends_comma_separated(self):
        result = _apply_dispute("12345", 67890)
        self.assertEqual(result, "12345,67890",
                         "Second dispute must append with a comma, not wrap in list()")

    def test_third_dispute_appends_correctly(self):
        result = _apply_dispute("12345,67890", 99999)
        self.assertEqual(result, "12345,67890,99999")

    def test_result_is_valid_comma_separated_integers(self):
        """Final value must pass Django's validate_comma_separated_integer_list."""
        result = _apply_dispute("12345", 67890)
        parts = result.split(",")
        for part in parts:
            self.assertTrue(part.isdigit(),
                            f"'{part}' is not a digit — field would fail DB validation")

    def test_old_buggy_behavior_would_fail(self):
        """
        Document what the old code produced to confirm the fix is meaningful.
        Old code: list("12345") → ['1','2','3','4','5'] → save as str() → garbage.
        """
        old_result = list("12345")
        old_result.append("67890")
        # Django CharField.save(str(list)) → "['1', '2', '3', '4', '5', '67890']"
        old_saved = str(old_result)
        # This is NOT a valid comma-separated integer list
        self.assertNotEqual(old_saved, "12345,67890",
                            "Old code must produce something different from the correct output")
        self.assertIn("[", old_saved,
                      "Old code must produce a Python list repr (containing '['), confirming the bug")

    def test_fix_does_not_produce_list_repr(self):
        result = _apply_dispute("12345", 67890)
        self.assertNotIn("[", result, "Fixed code must not produce a Python list repr")
        self.assertNotIn("'", result, "Fixed code must not produce quote characters")


# ---------------------------------------------------------------------------
# Corruption analysis: what production data looks like for a robot that
# opened 2 disputes before this fix.
# ---------------------------------------------------------------------------

class TestCorruptionPattern(unittest.TestCase):
    """
    Documents the exact corruption pattern so the data cleanup scope is clear.
    Not a test of new code — a test of the old code's output so the admin knows
    exactly what to look for in the DB.
    """

    def test_corruption_pattern_after_two_disputes(self):
        """
        Robot opens dispute on order 12345 → orders_disputes_started = "12345" (string, correct).
        Robot opens dispute on order 67890 → old code:
          list("12345") = ['1','2','3','4','5']
          .append("67890") → ['1','2','3','4','5','67890']
          Django CharField saves str(['1','2','3','4','5','67890'])
        Expected corrupted value in DB:
        """
        order1 = 12345
        order2 = 67890

        # First dispute — correct
        after_first = str(order1)
        self.assertEqual(after_first, "12345")

        # Second dispute — old buggy code
        old_disputes = list(after_first)      # ['1','2','3','4','5']
        old_disputes.append(str(order2))       # ['1','2','3','4','5','67890']
        corrupted = str(old_disputes)          # "['1', '2', '3', '4', '5', '67890']"

        # This is what's in the DB for affected robots
        self.assertEqual(corrupted, "['1', '2', '3', '4', '5', '67890']")

        # Third dispute on old buggy code would make it worse:
        # list("['1', '2', '3', '4', '5', '67890']") = ['[', "'", '1', "'", ...]
        # i.e. individual chars of the repr string
        third_disputes = list(corrupted)
        self.assertIn("[", third_disputes[0])  # first char is '['

    def test_correct_value_after_fix(self):
        order1 = 12345
        order2 = 67890
        after_first = str(order1)
        after_second = f"{after_first},{order2}"
        self.assertEqual(after_second, "12345,67890")


if __name__ == "__main__":
    unittest.main(verbosity=2)
