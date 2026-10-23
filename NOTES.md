# FreePort Coordinator — Branch Notes

## Branch: `freeport-patches`

**Base version:** v0.8.7-alpha (`017de9a2`)

---

## What Is Patched and Why

This branch applies order-locking fixes on top of the unmodified v0.8.7-alpha release.
The patch covers 6 files:

| File | Change |
|------|--------|
| `api/logics.py` | `select_for_update` + `transaction.atomic` on `withdraw_rewards`, `confirm_fiat`, `settle_escrow` idempotency guard, F() expressions in `add_slashed_rewards` |
| `api/views.py` | Forwarding of `select_for_update` context into reward withdrawal path |
| `api/admin.py` | `transaction.atomic` + F() expressions on admin dispute-resolution actions |
| `api/lightning/lnd.py` | `pay_onchain` RPC-before-DB-write ordering fix |
| `api/models/order.py` | Row-lock helpers used by patched logics |
| `api/management/commands/follow_invoices.py` | Guard against concurrent `finalize_contract` / `trade_escrow_received` calls |

**Root problem addressed:** Concurrent HTTP requests (or Celery tasks) operating on the
same order/robot without database-level locks could settle the same hold invoice multiple
times, credit bond rewards multiple times, or drain `earned_rewards` via a double-withdrawal
race. The previous patch (included in v0.8.7-alpha) added `select_for_update` to
`cancel_order` and `open_dispute` but left `withdraw_rewards`, `confirm_fiat`, and
`settle_escrow` unguarded.

**Reference:** GHSA / upstream PR #2864 (`db-lock-in-trade-state-machine`)

---

## Upstream Status (as of 2026-10-23)

fp-coordinator `main` is behind upstream `main` by ~6 days (896f07d9 vs 2dda6f3f).
Outstanding upstream commits include dependency bumps and PR #2850
("Fix race conditions in orders") plus a reverted GHSA fix (PR #2846).
Review those before rebasing this branch onto a newer base.

---

## Rebase Policy

- **Do not merge `main` into this branch.** Rebase only.
- Before rebasing onto a new upstream tag: verify the 6 file hashes above still match
  what is deployed on the coordinator. If the coordinator has drifted, reconcile first.
- After any rebase: re-run `md5` on all 6 patched files and confirm they match the
  expected production hashes documented in the deploy runbook.
- Keep `main` on fp-coordinator pointing at a clean upstream tag; all FreePort-specific
  changes live only on `freeport-patches`.
