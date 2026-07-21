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

Open [http://localhost:8000](http://localhost:8000). The local demo requires `LOCAL_DEMO_AUTH_ENABLED=true`. Production fails closed without a real authentication dependency.

## Demo flow

1. Select **Start the public demo**. This explicit POST ingests five synthetic emails and immediately analyzes all new email for the demo user.
2. The prepared Dashboard reports `5 emails checked · 3 actionable tasks created.`
3. Open any task to see verified facts and evidence, execution guidance, business guidance, AI recommendations, and execution options.
4. Select **Prepare for Work** or **Prepare for Codex** to preview a safe action package before copying or downloading it.
5. **Check for new emails** in the Inbox remains a manual retry for newly ingested email.

Reopening the demo and repeated triage are idempotent and do not duplicate analyses, tasks, or guidance.

## Environment

Configuration is read only from server-side environment variables:

```text
DATABASE_URL=sqlite:///./actioninbox.db
LOCAL_DEMO_AUTH_ENABLED=true
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
