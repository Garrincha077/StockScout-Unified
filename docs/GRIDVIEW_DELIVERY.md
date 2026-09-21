# Verified GridView delivery

Unified is the sole owner of GridView Telegram messages. Trend Birth publishes
data and hosts the snapshot-aware UI. Its separate scheduler PR must remove the
old optional Telegram sender before this delivery workflow is enabled.

Before delivery the notifier checks the committed publication pointer against
the public deployment, archive SHA-256, run/session identity, counts, complete
chart coverage and the snapshot-aware frontend. It also checks the active
Unified manifest. Failed, stale or partially deployed publications cannot reserve
a delivery. Telegram uses `?snapshot=<runId>--<sha256>`, never a moving latest URL.

## Delivery and recovery

`.state/trend-birth-gridview-deliveries.json` is an operational outbox keyed by
market session. The existing date marker remains a migration floor, so already
notified sessions are not resent. Revisions on the same day do not resend.

1. Verify publication and credentials; prepare `reserved` record.
2. Commit/push reservation. If this fails, do not send.
3. Make exactly one Telegram request, honoring the global no-notify guard.
4. Commit a `sent` receipt with message ID, or an `uncertain` outcome.

If the job dies after reservation or Telegram times out, reruns do not resend.
Review the run and Telegram chat. If the message arrived, reconcile the record
to `sent` with its message ID. Only after confirming it did not arrive may an
operator remove that session's reservation and explicitly retry. Preserve the
audit trail in the recovery commit. Never delete an ambiguous reservation just
to make a red workflow green. Later sessions can proceed independently.

This favors no automatic duplicates over guaranteed delivery: an unresolved
crash can leave a message unsent until reconciled. It does not claim exactly-once
delivery across GitHub and Telegram. Do not directly rerun `--send-reserved` from
an old checkout. Rerun the workflow from its prepare stage with a new attempt ID.

## Testing and activation

The CLI without switches and manual workflow without `deliver` are read-only.
`--prepare` writes a reservation and `--send-reserved` sends it; these are for the
ordered production workflow, not smoke tests. No sending on push or PR events.

Pause the old GridView notifier and Trend Birth refresh while merging the three
PRs. Deploy the lab publisher/loader, replace its scheduler (which removes its
sender), produce and verify publication v1, then merge this change and run the
manual dry-run before re-enabling the schedule. The normal Unified EOD workflow
is unaffected. The previous date marker must remain during migration.

Rollback must retain immutable archives and the snapshot-aware UI to keep old
Telegram links working. Turn off scheduled delivery before rolling back sender
code. No production scan, ranking or standard Telegram series is changed here.
