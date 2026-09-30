# Engineering constitution

This repository is the source code and technical documentation for the Elektro Vienna Knowledge System. Its purpose is operational learning, not document management.

## Required context

- Read `docs/NORTH_STAR.md` before substantial work.
- Read `docs/ARCHITECTURE.md` before architectural changes.
- Read `docs/DATA_MODEL.md` before schema changes.
- Use `docs/IMPLEMENTATION_PLAN.md` to select milestones and assess completion.
- Inspect existing code before adding implementations. Prefer extending existing modules over creating duplicate systems.

## Durable rules

- Preserve SOURCE → EXTRACTION → EVIDENCE → KNOWLEDGE. Neither sources nor AI output are automatically established company knowledge.
- Preserve immutable original messages and documents, provenance, extraction versions, and reprocessing history.
- New evidence, technician offers, and human feedback must never silently overwrite established knowledge or global pricing rules.
- Keep uncertain matches uncertain. No match is preferable to a false match.
- Gmail is the email source for `office@elektrovienna.at`, with historical ingestion starting `2026-01-01`. Outlook/Graph mail must not become the ingestion architecture.
- Microsoft Outlook Calendar is a separate appointment integration. Keep Case, Appointment, and customer communication separate but linked.
- Initial behavior is human-in-the-loop. Prepare drafts only; do not autonomously send invitations, emails, confirmations, or customer prices. Later automation requires explicit enablement and documented controls.
- External integrations belong behind clear interfaces. Do not create fake integrations that appear functional.
- Do not add a vector database, n8n, or unvalidated infrastructure.
- Runtime data, imported customer documents, raw emails, PDFs, databases, credentials, and secrets do not belong in Git.
- The only authorized SharePoint-synchronized storage root is `C:\Users\giova\Balun Energy\Balun Energy - Dokumente\06 Elektrovienna\07 Playground\Knowledgebase`. Never write elsewhere in SharePoint-synchronized folders without explicit instruction. Git source and knowledge storage remain separate. OS/sandbox permissions still apply.

## Change discipline

- Never invent business requirements or silently change architecture. Explain proposed departures and update the relevant documents as part of an intentional change.
- Report architectural uncertainty; do not silently settle unresolved questions.
- Preserve backwards compatibility unless intentionally changing it, with a documented migration and validation approach where relevant.
- Add or update meaningful tests with implementation changes, including provenance, idempotency, and prohibited side effects where applicable.
- Avoid shortcuts that violate long-term goals. Prefer maintainability and correctness.
- Do not mark work complete without inspecting every changed file, the resulting diff, and relevant test/check results. Report checks not run and remaining questions honestly.
- The foundation milestone contains documentation and scaffolding only. Do not implement integrations or business functionality until that work is requested.
