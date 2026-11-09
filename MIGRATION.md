# Ordinary-archive migration gates

This release adds application admission limits, not a hard filesystem quota.
Existing archive content is not purged. A rejected edit/delete rolls back its
message mutation and all newly queued alert parts; the cached original remains.
Rejected events are not automatically backfilled. Sender-budget consumption is
retained even for rejected events, preventing repeated rejected work.

## Custom receipt-based installations

Do not replace their watcher wholesale. A reviewed operational variant has:
- a create_watch factory reading an existing private bot configuration;
- validated positive integer acknowledgements and delivery_receipts;
- delivery_quarantine for ambiguous acknowledgements (never auto-replayed);
- a separate legacy disappearing-media spool and external spool root.

The public ordinary-only watcher does not implement those integration contracts.
Its additive migration preserves existing receipt/quarantine tables and records,
but preservation of rows is NOT preservation of runtime receipt semantics.
Synthetic migration fixtures verify that the old records are untouched and not
replayed. They do not certify an operational merged watcher.

## Minimal merge plan

1. Keep the operational factory, acknowledgement validator, receipt transaction,
   and quarantine branch unchanged. Merge only limits initialization, additive
   outbox retry columns, sender budgets, quota checks, atomic admission, and
   ordinary download limits. Never import or enable a disappearing-media producer.
2. Apply fair next_delivery selection only to ordinary work. Ambiguous successful
   acknowledgements remain quarantined immediately, not counted as transient
   retries. Count retained quarantine entries in the total queue budget too.
3. Preserve legacy spool rows/files without cleaning or sending them in the
   ordinary worker. An operator must choose whether to pause that separate legacy
   delivery route; silently disabling it is a behavioral change. Continuing or
   enhancing that route is outside this ordinary-only release.
4. Add merged-worker fixtures for valid receipts, wrong-recipient/media ACKs,
   ambiguous-success quarantine, 429 pause, retry exhaustion, no automatic replay,
   and unchanged preexisting spool files. Run both ordinary and operational test
   suites against the staged merged module with synthetic temporary state only.
5. Build a separate Python 3.12 environment from requirements.lock and pip-check
   it; retain the old environment. Do not mutate the running environment.

## Backup / activation / rollback

After the legacy-route decision and passing merged-worker gates:
- Inspect the current app service definition. Preserve it and all config/session
  paths. Record source hashes and a manifest of archive file sizes/hashes locally.
- Stop only the app collector and confirm its session lock is released. Never
  stop or modify OpenClaw/Gateway. Identify other writers before snapshotting.
- Make a mode-0700 private backup of source, launcher and configuration/session
  files; take a consistent SQLite backup, integrity-check it, and record row counts
  without displaying message bodies. Copy all archive/spool data or prove an
  existing immutable backup covers it. Sufficient backup disk space is a gate.
- Atomically install the staged merged app module and switch only the app's
  interpreter to the new environment. Keep prior source/env beside the backup.
- Restart once, verify a fresh connected health timestamp and matching new PID,
  package versions, no restart loop, and unchanged archive/session manifests.
  Synthetic ACK tests are not evidence of a successful real Telegram delivery.
- On failure, stop the app, restore prior source/interpreter and restart. Keep
  additive database columns/tables: do NOT restore an old database over newly
  captured records. Preserve new dead-letter/quarantine rows and do not replay.
  If the old binary cannot safely coexist with new queue semantics, stay stopped
  and reconcile on a copy instead of discarding records or risking duplicates.

No operational activation is claimed by this document.
