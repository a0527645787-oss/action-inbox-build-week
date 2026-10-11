# ActionInbox

ActionInbox turns incoming email into evidence-backed tasks, explains how to complete them, and prepares a safe execution handoff for user review.

## Implemented

- Explicit five-email demo ingestion with automatic inbox triage.
- Tasks only for actionable email, with exact email evidence and highlighted source text.
- Live GPT-5.6 structured analysis when `OPENAI_API_KEY` is configured server-side.
- Deterministic fallback with complete guidance when no API key is available or live analysis fails.
- Separate email facts, evidence-backed business-resource guidance, and visibly labeled AI recommendations.
- Per-task outcome, ordered steps, required inputs, missing information, safety checks, proposed deliverable, executor recommendation, and readiness.
- Preview, clipboard copy, and JSON download of a tenant-scoped execution package for ChatGPT Work or Codex.
- Multi-user ownership, SQLite/MySQL SQLAlchemy configuration, and Alembic migrations.

The execution package is preparation only. ActionInbox does not claim that Work, Codex, Gmail, Calendar, or another service executed anything.

## Not implemented

- Real Gmail synchronization or continuous background scanning.
- Direct ChatGPT Work or Codex invocation.
- External connectors, email sending, or calendar-event creation.
- AWS/RDS deployment or autonomous external execution.
- Link fetching, file uploads, or public registration.

## Local setup

Python 3.12 is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000). Configure a strong
`SESSION_SECRET` before using signed-in routes. The landing page and synthetic
demo are public; personal data routes fail closed without a valid server-side
session.

## Demo flow

1. Select **View safe demo**. This explicit POST ingests and analyzes only the
   five synthetic emails.
2. The prepared Dashboard reports `5 emails checked · 3 actionable tasks created.`
3. Open any task to see verified facts and evidence, execution guidance, business guidance, AI recommendations, and execution options.
4. Select **Prepare for Work** or **Prepare for Codex** to preview a safe action package before copying or downloading it.
5. In a personal session, **Check for new emails** performs the bounded Gmail
   sync and analyzes only new or previously incomplete Gmail messages.

Reopening the demo and repeated triage are idempotent and do not duplicate analyses, tasks, or guidance.

## Environment

Configuration is read only from server-side environment variables:

```text
DATABASE_URL=sqlite:///./actioninbox.db
SESSION_SECRET=replace-with-at-least-32-random-characters
OPENAI_API_KEY=
OPENAI_CA_BUNDLE=
```

Supported database examples (templates only; do not commit credentials):

```text
sqlite:///./actioninbox.db
mysql+pymysql://actioninbox:change-me@localhost:3306/actioninbox?charset=utf8mb4
```

`OPENAI_CA_BUNDLE` is optional and must point to a readable server-side CA bundle. Never place API keys, certificates, local databases, or real credentials in Git.

## Docker

```powershell
docker compose up --build
```

The default service uses SQLite in a named volume and listens on [http://localhost:8000](http://localhost:8000). The MySQL profile is intended for isolated integration validation:

```powershell
docker compose --profile mysql up --build
```

Never run migration validation against an existing database or volume; create a fresh isolated Compose project instead.

## Emergency AWS deployment

The emergency hosted architecture uses one small Ubuntu EC2 instance, an Elastic IP, Nginx, the ActionInbox container, and a separate MySQL 8.4 container with a persistent named Docker volume. Only Nginx publishes a web port; MySQL is reachable only through an internal Docker network. Deployment secrets live only in a mode-`600` environment file on the instance and are not managed by Terraform.

Terraform lives in `infra/terraform`, and the production Compose/Nginx configuration lives in `deploy`. The emergency deployment intentionally does not create RDS. Managed RDS MySQL remains the planned production architecture after the Build Week emergency deployment is stabilized.

The AWS deployment uses MySQL exclusively. It must not silently fall back to SQLite.

## Tests

```powershell
pytest
```

The MySQL integration test is opt-in through its documented test environment variables. All OpenAI API calls are mocked in automated tests.

## Bounded Gmail ingestion

Gmail is optional and read-only. A user starts every sync manually. The server
queries `INBOX` with `newer_than:7d`, requests at most 25 message IDs, excludes
Spam and Trash, and creates at most 20 new tasks. Stored Gmail message IDs make
repeat syncs idempotent. The integration never sends, labels, archives, deletes,
or marks messages as read.

OAuth configuration is server-side only: `GMAIL_CLIENT_ID`,
`GMAIL_CLIENT_SECRET`, `GMAIL_REDIRECT_URI`, and `TOKEN_ENCRYPTION_KEY`. Never
place the downloaded Google credentials JSON in the repository.

## Work and Codex

The public HTTPS `/mcp` endpoint exposes three read-only tools over synthetic
demo data only:
`list_actioninbox_tasks`, `get_actioninbox_task`, and
`prepare_task_execution`. It never exposes Gmail or personal database records.
Task pages can copy a review-first prompt for Work or open the supported
`codex://new` deep link with a prefilled prompt and repository origin. Neither
path executes an external action automatically.
# Production HTTPS

The emergency AWS deployment is served at
`https://actioninboxapp.com`. TLS is terminated by the existing
Nginx container with a Let's Encrypt certificate; MySQL remains private on the
internal Docker network. Deployment and renewal details are in
`deploy/README.md`.

## Configured table actions

Settings → Tables accepts a name, an existing Google Sheets link, and its exact tab name. Share the spreadsheet manually with the existing service account first; this workflow neither creates credentials nor changes Google permissions. Inspect the live headers, map each column, and save. One column must map to the ActionInbox tracking key. A dedicated tab with unique headers in row 1 is required.

On a task, select **Add to a table**, choose a destination (a unique compatible destination is recommended), review the meaningful values, then explicitly approve once. Preparation validates the live schema. The worker checks the configured destination and schema again, appends only the frozen ordered values using RAW input, and verifies every cell by read-back. Google IDs, credential paths, and source identifiers are omitted from public plan/status/receipt projections. Technical audit details are collapsed.

Migration `20260910_0007` adds table destinations, generic append receipts, and an append-attempt timestamp. It adapts the existing environment-configured Expenses target into per-user destination metadata. Its headers must pass live validation before a proposal can be prepared. Historical executions and Sheets receipts are left unchanged. Old invoice tools are no longer eligible for approval or worker execution. New destinations use no environment-specific target configuration.

A task can add one row per physical table. Repeated preparation returns the existing action, including terminal history, without retrying or changing it. A provider timeout after sending an append is treated as uncertain: bounded retries search for the tracking key and read back the original row, never blindly repeat the write. If the row cannot be confirmed, the action stops for human review. This deliberately favors duplicate prevention over automatic recovery from an uncertain unsent request.

The web process mounts the same existing credential file read-only for header inspection and obtains a read-only Google scope. Only the approved execution worker requests write scope. No credential file contents or sharing permissions are changed. GitHub Actions is the authoritative test/build/deployment gate; the production job deploys only after tests and image build succeed.

## Inbox progress and sync timings

Browser sync submissions return to the Inbox; JavaScript requests explicitly ask for JSON and poll a shared progress component. The enqueue request does no Gmail fetch or AI analysis. A single active job per credential remains enforced. Without JavaScript, the Inbox shows durable progress and a refresh link.

The worker imports unseen messages, then analyzes only emails assigned to that job. At most two independent analysis calls run together, with detached inputs, unchanged exact-evidence validation, and ordered database writes on the worker session. Attempt markers prevent automatic re-analysis on recovery. Existing processed emails and all table proposals are excluded. Failed new-email analysis remains available for explicit review from the Inbox. Gmail detail fetch concurrency remains four. The worker now uses the existing OpenAI key; Google credentials and scopes are unchanged.

`gmail_sync_jobs` records aggregate `fetch_ms`, `storage_ms`, `triage_ms`, and initial `queue_wait_ms`. Logs contain timings and counts only. Historical jobs have no stage timings; zero defaults must not be interpreted as measured durations. The deployment pipeline reads aggregate historical timings before and after deployment without initiating sync. `scripts/benchmark_sync.py` compares one versus two analysis calls on six synthetic emails with a fixed mocked provider delay. This is a controlled concurrency benchmark, not a claim about live Gmail/OpenAI speed.
