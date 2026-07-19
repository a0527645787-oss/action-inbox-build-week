# Milestone 2 Implementation Plan

1. Add strict Pydantic models for the complete email-analysis schema and a server-only GPT-5.6 Responses API client with bounded input, timeout, `store: false`, no tools, and prompt-injection-resistant instructions.
2. Validate every model evidence quote and offset against the original email, discard unsupported facts and tasks, reject invented links, and record validation failures as missing information.
3. Persist the full validated structured result plus analysis source/model while continuing to project actionable analysis into the existing dashboard task flow; retain deterministic demo fallback for missing keys and API/model failures.
4. Update the UI with Live GPT-5.6 versus Demo fallback provenance and a re-analyze action without adding Gmail, uploads, sending, calendar, link fetching, or web search.
5. Add fully mocked OpenAI and safety tests, run the complete suite, validate the local service and health endpoint, attempt one live analysis only if a server-side key is available, and verify Docker and Compose before committing stable milestones.
