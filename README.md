# ActionInbox

ActionInbox turns incoming emails into clear, evidence-backed tasks. Milestone 1 is a local public demo with five synthetic emails, fixed structured analysis, task extraction, and exact evidence highlighting.

No Gmail account, OpenAI API key, email sending, or external service is used.

## Run locally

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
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

All analysis is deterministic sample data for Milestone 1.
