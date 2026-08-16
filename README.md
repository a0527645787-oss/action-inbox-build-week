# ActionInbox

ActionInbox turns incoming email into evidence-backed tasks, explains how to complete them, and prepares a safe execution handoff for user review.

The current verified workflow is **invoice email → evidence-backed expense record → explicit approval → Google Sheets write → exact read-back verification**. It records payment tracking only; it does not pay invoices.

## Implemented

- Explicit five-email demo ingestion with automatic inbox triage.
- Tasks only for actionable email, with exact email evidence and highlighted source text.
- Live GPT-5.6 structured analysis when `OPENAI_API_KEY` is configured server-side.
- Deterministic fallback with complete guidance when no API key is available or live analysis fails.
- Separate email facts, evidence-backed business-resource guidance, and visibly labeled AI recommendations.
- Per-task outcome, ordered steps, required inputs, missing information, safety checks, proposed deliverable, executor recommendation, and readiness.
- Preview, clipboard copy, and JSON download of a tenant-scoped execution package for ChatGPT Work or Codex.
- Multi-user ownership, SQLite/MySQL SQLAlchemy configuration, and Alembic migrations.
- Evidence-only invoice extraction and an approval-gated Google Sheets expense register connector.

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

## Google Sheets expense tracking

Create a Google Cloud service account with access only to the target spreadsheet,
keep its JSON key outside the repository, and share the sheet with the service
account email. Configure `GOOGLE_APPLICATION_CREDENTIALS`,
`ACTIONINBOX_SHEET_ID`, and `ACTIONINBOX_SHEET_TAB`. Production Compose mounts
the key read-only; it is never copied into the image. For local Docker, add the
same read-only bind mount in the ignored `docker-compose.override.yml`.

The configured tab must have this header row:

```text
Created At | Supplier | Invoice Number | Amount | Currency | Due Date | Status | Source Email ID | ActionInbox Task ID | Verification Status
```

The public MCP endpoint remains read-only. `MCP_SHEETS_WRITE_ENABLED=false`
documents that no unauthenticated MCP write tool is active in this MVP.
# Production HTTPS

The emergency AWS deployment is served at
`https://actioninboxapp.com`. TLS is terminated by the existing
Nginx container with a Let's Encrypt certificate; MySQL remains private on the
internal Docker network. Deployment and renewal details are in
`deploy/README.md`.
