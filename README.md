# ActionInbox

ActionInbox turns incoming emails into clear, evidence-backed tasks. It supports live GPT-5.6 analysis with strict structured output and exact source validation, while retaining the deterministic five-email demo fallback.

No Gmail account is required. Gmail, email sending, business-resource uploads, calendar actions, and link fetching are not implemented.

## Run locally

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # optional on Windows; add OPENAI_API_KEY for live analysis
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

The fallback analysis is deterministic sample data. With `OPENAI_API_KEY` configured in the server environment, Analyze and Re-analyze use the Responses API with `gpt-5.6`. Without a key—or if the API or structured output fails—the safe deterministic demo analysis is used. The browser never receives the API key.

## Environment

- `OPENAI_API_KEY` — optional; enables live GPT-5.6 analysis. Read only by the backend.
- `DATABASE_URL` — optional; defaults to `sqlite:///./actioninbox.db`.

Live analysis uses a 25-second request timeout, disables response storage, does not enable tools, and rejects email bodies longer than 12,000 characters. Links are treated as inert text and are never opened or fetched.
