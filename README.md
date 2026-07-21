# ActionInbox

ActionInbox turns incoming emails into clear, evidence-backed tasks. It supports live GPT-5.6 analysis with strict structured output and exact source validation, while retaining the deterministic five-email demo fallback. Enabled pasted-text business resources add personalized guidance without being mixed into email facts.

No Gmail account is required. Gmail, email sending, file uploads, calendar actions, and link fetching are not implemented.

## Run locally

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # optional on Windows; add OPENAI_API_KEY for live analysis
alembic upgrade head
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000). Health: [http://localhost:8000/health](http://localhost:8000/health).

## Run tests

```bash
pytest -q
```

## Run with Docker Compose

```bash
docker compose up --build
```

Stop with `docker compose down`. The demo database persists in a named volume.

## Demo flow

1. Select **Try the public demo**.
2. Open a synthetic email and select **Analyze email**.
3. Actionable emails become dashboard tasks; informational and newsletter emails do not.
4. Open a task to compare extracted details, exact evidence, highlighted original text, and the clearly separated AI suggestion.
5. Open **Business resources** to create, view, edit, enable/disable, or delete pasted procedures, policies, role directories, templates, and instructions.

The public demo seeds an expense reimbursement procedure, employee responsibility directory, and invoice approval policy. Re-analysis selects the latest relevant enabled resources. Resource guidance is displayed separately with its resource title, exact highlighted quote, and deterministic character offsets. If nothing relevant is enabled, the task page says so explicitly.

The fallback analysis is deterministic sample data. With `OPENAI_API_KEY` configured in the server environment, Analyze and Re-analyze use the Responses API with `gpt-5.6`. Without a key—or if the API or structured output fails—the safe deterministic demo analysis is used. The browser never receives the API key.

## Environment

- `OPENAI_API_KEY` — optional; enables live GPT-5.6 analysis. Read only by the backend.
- `DATABASE_URL` — optional; defaults to `sqlite:///./actioninbox.db`.
- `LOCAL_DEMO_AUTH_ENABLED` — set explicitly to `true` for local/demo use. If it is absent or false, application routes fail closed until a real authentication dependency is configured. Public registration is not implemented.

Supported database URL shapes:

```text
sqlite:///./actioninbox.db
mysql+pymysql://app-user:replace-me@db-host:3306/actioninbox?charset=utf8mb4
```

Use environment-managed credentials in hosted environments; never commit them. Apply schema changes before starting a release:

```bash
alembic upgrade head
```

The Docker image performs this migration before starting Uvicorn. The optional Compose MySQL validation stack is available with `docker compose --profile mysql up --build mysql actioninbox-mysql`; it serves the MySQL-backed app on port 8001.

Live analysis uses a 60-second request timeout, disables response storage, does not enable tools, and rejects email bodies longer than 12,000 characters. Pasted resources are limited to 12,000 characters each and selected resource context is capped at 18,000 characters. Email and resource text are treated as untrusted data. Links are inert text and are never opened or fetched.
