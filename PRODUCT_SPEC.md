# ActionInbox — Two-Day MVP Specification

## 1. Exact user journey

1. User opens ActionInbox.
2. User selects:

   * **Try Demo** — immediate access to five synthetic emails.
   * **Connect Gmail** — optional, approved test accounts only.
3. Application imports emails without modifying Gmail.
4. Each email is classified and analyzed.
5. Dashboard shows actionable tasks ordered by deadline.
6. User opens a task.
7. Detail page displays:

   * Original email.
   * Extracted facts.
   * Exact highlighted evidence for every fact.
   * Guidance from the uploaded business procedure.
   * Clearly labeled AI suggestions.
8. User views a proposed reply and may copy it. Nothing is sent.
9. User returns to the dashboard.

The polished demo workflow is:

**Demo inbox → actionable task → evidence → business guidance → proposed reply.**

---

## 2. Minimum required screens

| Screen          | Required content                                                                                          |
| --------------- | --------------------------------------------------------------------------------------------------------- |
| Landing         | Product explanation, **Try Demo**, optional **Connect Gmail**                                             |
| Daily Dashboard | Tasks grouped by overdue, today, upcoming, and no deadline; classification badges                         |
| Task Detail     | Original email, extracted facts, evidence highlighting, business guidance, AI suggestions, reply proposal |
| Settings        | Gmail connection status and upload/replace one procedures document                                        |

No separate inbox, calendar, chat, or administration screens.

---

## 3. Database entities

| Entity             | Minimum fields                                                                                    |
| ------------------ | ------------------------------------------------------------------------------------------------- |
| `User`             | `id`, `email`, `created_at`                                                                       |
| `GmailConnection`  | `user_id`, `account_email`, encrypted token, scopes, `last_synced_at`                             |
| `Email`            | `id`, `external_id`, sender, subject, received time, normalized body, source, attachment metadata |
| `Analysis`         | `email_id`, classification, action flag, summary, model, structured result, analyzed time         |
| `Task`             | `email_id`, title, deadline, deadline text, uncertainty                                           |
| `Fact`             | `analysis_id`, type, value, normalized value, confidence, uncertainty                             |
| `Evidence`         | `fact_id`, source type, source ID, exact quote, start offset, end offset                          |
| `BusinessResource` | filename, extracted text, upload time                                                             |
| `Guidance`         | `email_id`, type, text, source type, supporting evidence references                               |

One business resource per user. Accept one text-based PDF or TXT file.

---

## 4. API endpoints

| Method | Endpoint                         | Purpose                               |
| ------ | -------------------------------- | ------------------------------------- |
| `GET`  | `/`                              | Landing page                          |
| `GET`  | `/demo`                          | Start/reset public demo               |
| `GET`  | `/dashboard`                     | Daily task dashboard                  |
| `GET`  | `/tasks/{task_id}`               | Task and evidence detail              |
| `POST` | `/api/emails/{email_id}/analyze` | Produce validated analysis            |
| `POST` | `/api/resources`                 | Upload or replace procedures document |
| `GET`  | `/auth/google`                   | Begin read-only OAuth                 |
| `GET`  | `/auth/google/callback`          | Complete OAuth                        |
| `POST` | `/api/gmail/sync`                | Read latest bounded email set         |
| `POST` | `/auth/google/disconnect`        | Remove local credentials              |

No endpoint may send or modify email.

---

## 5. AI structured-output schema

Use Responses API Structured Outputs with a strict JSON Schema through `text.format`. All properties are required; optional values use `null`; every object sets `additionalProperties: false`. [OpenAI Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs)

```text
EmailAnalysis {
  primary_classification:
    "action_required" | "informational" |
    "newsletter_noise" | "invoice" | "meeting"

  action_required: boolean
  summary: string

  tasks: [{
    id: string
    title: string
    due_at: ISO-8601 string | null
    due_text: string | null
    uncertainty: string | null
    evidence_ids: string[]
  }]

  email_facts: [{
    id: string
    type:
      "deadline" | "amount" | "required_document" |
      "important_link" | "meeting_time" | "other"
    value: string
    normalized_value: string | null
    confidence: "high" | "medium" | "low"
    uncertainty: string | null
    evidence: {
      id: string
      exact_quote: string
      start_offset: integer
      end_offset: integer
    }
  }]

  resource_guidance: [{
    id: string
    instruction: string
    related_fact_ids: string[]
    resource_evidence: {
      exact_quote: string
      section: string | null
      start_offset: integer
      end_offset: integer
    }
  }]

  ai_suggestions: [{
    type: "next_step" | "reply_draft"
    text: string
    supporting_fact_ids: string[]
    supporting_guidance_ids: string[]
    uncertainty: string | null
  }]

  missing_information: string[]
}
```

Validation rule: a fact without valid matching evidence is discarded or returned as missing/uncertain.

---

## 6. Safety boundaries

* Gmail OAuth uses read-only access exclusively.
* Gmail mutation and send methods are absent from the application.
* Email text is untrusted data and cannot override system instructions.
* The backend never opens, fetches, validates, or executes email links.
* URLs are displayed as copied text only.
* No invented facts. Missing information remains `null` or explicitly unknown.
* Dates, amounts, documents, links, and tasks require exact email evidence.
* Business guidance requires an exact resource quotation.
* AI suggestions are visually labeled **AI suggestion — not stated in the email**.
* Reply drafts are local proposals with no send button.
* No calendar integration or automatic event creation.
* Gmail attachments are metadata only; their contents are not executed or analyzed.
* OpenAI requests use `store: false`. The Responses API supports disabling default response storage this way. [Responses API migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)

---

## 7. Definition of done

The MVP is complete when:

* Public demo works without login.
* All five synthetic emails load and classify.
* Actionable emails produce dashboard tasks.
* Every displayed important fact has clickable exact evidence.
* Clicking evidence highlights the matching original-email text.
* One procedures document influences guidance with its own citation.
* Email facts, business guidance, and AI suggestions have visibly different labels/colors.
* At least one useful reply proposal is generated and copyable.
* Gmail integration, if enabled, uses only approved accounts and read-only scope.
* Application runs with one Docker command.
* GitHub Actions runs unit tests and Docker build.
* Tests cover schema validation, evidence offsets, missing evidence rejection, and read-only Gmail boundaries.
* The complete demo can be presented in under three minutes.

Gmail connectivity is optional for core acceptance; the synthetic demo must be flawless first.

---

## 8. Features that must not be built

* Sending or replying to emails.
* Deleting, archiving, labeling, starring, or marking emails read.
* Calendar integration or event creation.
* Opening or crawling links.
* Attachment content analysis or OCR.
* Multiple business documents or a full RAG system.
* Notifications, reminders, background workers, or scheduled sync.
* Team accounts, permissions, billing, or administration.
* Chatbot interface.
* Mobile application.
* Custom model training.
* Email search across an entire mailbox.

---

## 9. Two-day development order

### Day 1

1. FastAPI, Jinja2, SQLite, Docker skeleton.
2. Seed the five demo emails.
3. Build dashboard and task-detail screen using fixed sample analysis.
4. Implement original-email highlighting from evidence offsets.
5. Add Responses API structured analysis.
6. Validate and persist results.

### Day 2

7. Upload and extract one procedures document.
8. Generate resource-backed guidance and reply proposals.
9. Polish source labels, uncertainty states, and demo presentation.
10. Add safety and schema tests.
11. Add Docker build and GitHub Actions.
12. Add Gmail read-only OAuth only if steps 1–11 are complete.
13. Final three-minute demo rehearsal.

---

## 10. Five synthetic demo emails

### 1. Invoice

**From:** [billing@northstar-office.example](mailto:billing@northstar-office.example)
**Subject:** Invoice INV-2048 requires approval by July 21

> Please approve invoice INV-2048 for USD 1,280 by July 21, 2026. The invoice PDF is attached. Before approval, confirm that purchase order PO-774 appears in your records.

Expected: `invoice`, actionable, amount, deadline, invoice number, required check.

### 2. Meeting

**From:** [maya@acme.example](mailto:maya@acme.example)
**Subject:** Choose a time for the supplier review

> Please confirm whether you can attend on July 22 at 10:00 AM or July 23 at 2:30 PM. Bring the June delivery metrics. Reply with your preferred time by July 20.

Expected: `meeting`, actionable, two proposed times, deadline, required material.

### 3. Required documents

**From:** [compliance@harbor.example](mailto:compliance@harbor.example)
**Subject:** Updated documents needed for vendor renewal

> To complete the vendor renewal, send your current W-9 form and proof of insurance. We need both documents by July 24, 2026. If either document is unavailable, tell us before the deadline.

Expected: `action_required`, two documents, deadline, fallback instruction.

### 4. Informational

**From:** [operations@acme.example](mailto:operations@acme.example)
**Subject:** Office access maintenance on Sunday

> The employee entrance will be unavailable on Sunday, July 26, from 8:00 AM until noon. Use the visitor entrance during that period. No response is required.

Expected: `informational`, no task.

### 5. Newsletter/noise

**From:** [updates@productweekly.example](mailto:updates@productweekly.example)
**Subject:** This week’s product and design stories

> This week: five interface trends, an interview with a design leader, and our recommended reading list. Visit [https://productweekly.example/july](https://productweekly.example/july) to read the issue. You are receiving this monthly newsletter because you subscribed.

Expected: `newsletter_noise`, no task; URL extracted only as inert evidence-backed text.
