# ActionInbox Project Handoff

## Git state

- Branch: `main`
- Baseline commit: `efdd1c368521e6f2399166721a481cd4a9f16988`
- Feature scope: automatic-ingestion triage and safe execution-guidance preparation, verified for Git preservation

## Architecture

- `POST /demo` is the explicit synthetic ingestion trigger. It seeds demo resources, ingests missing demo email, calls the shared triage service, and redirects to the prepared Dashboard.
- `triage_unanalyzed_emails` scopes work by `user_id` and reuses `analyze_email`; `POST /api/inbox/analyze-all` remains a manual retry.
- Execution guidance extends the strict `EmailAnalysisResult` and remains stored in the existing `Analysis.structured_result`; no schema migration is required.
- Execution guidance is evidence-validated by source layer. Unsupported email-fact or business-guidance instructions are removed without dropping the supported task.
- Work/Codex preview and download routes resolve an owned task, omit the full email body, redact secret-shaped strings, and perform no database or external side effect.

## Safety boundaries

- Email and resource text remain untrusted data.
- Links are never opened or fetched.
- Packages include only the current user's selected task facts, resource guidance, and execution instructions.
- The MVP prepares previews and downloadable packages only. It does not send, run, deploy, create calendar events, invoke Work/Codex, or call connectors.
- External execution remains: preview → explicit approval → supported integration → verified result → audit record.

## Database

- No model or migration change was needed for this feature.
- SQLite local/test behavior and the MySQL/Alembic direction remain unchanged.
- Migration verification must use a fresh isolated database only.

## Verification

- Complete suite: `34 passed, 1 skipped`; the skipped test is the opt-in MySQL integration test.
- Import/startup passed, and Alembic upgraded and checked a fresh temporary SQLite database at revision `20260719_0001`.
- Fresh local runtime: `/health`, landing, Inbox, Dashboard, all three actionable Task pages, Work/Codex previews, and package downloads returned HTTP 200.
- Fresh demo POST checked five email and created exactly three actionable tasks; a second POST checked zero and preserved the same task IDs.
- Task detail and both package routes returned 404 for another tenant's task.
- Docker project `actioninbox-execution-verify-20260721` built from the working tree with a new isolated volume, became healthy, and passed the same automatic demo and idempotency checks. It remains running at `http://localhost:8000`.
- The older verification container was stopped to free port 8000; its volume was not reused, migrated, modified, or deleted.
- `.tmp-pytest-*` is excluded from Git and Docker build contexts after a Windows ACL-protected pytest artifact blocked the first build attempt.

## Next task

Select the next product milestone explicitly; do not infer a connector or autonomous-execution scope.
