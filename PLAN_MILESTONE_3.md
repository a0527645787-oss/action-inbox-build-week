# Milestone 3 Implementation Plan

1. Add a `BusinessResource` model, SQLite-safe migration, three idempotent demo seeds, and pasted-text CRUD routes for viewing, creating, editing, enabling/disabling, and deleting resources.
2. Select a bounded set of relevant enabled resources for each email using deterministic local matching, and pass them to GPT-5.6 as separately labeled untrusted data with no tools or external retrieval.
3. Extend the strict analysis schema and validation so every resource guidance item identifies its resource, carries an exact quote and deterministic offsets, and is discarded when it cannot be matched to the selected stored resource.
4. Preserve deterministic fallback analysis by generating only evidence-backed fallback guidance from selected resources; keep email facts, business guidance, and AI suggestions structurally and visually separate.
5. Add resource-evidence highlighting and no-relevant-resource messaging to task detail, comprehensive CRUD/safety/re-analysis tests, then validate the full suite, live GPT-5.6, Docker, and Compose in small descriptive commits.
