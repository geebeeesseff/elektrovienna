# Implementation plan

Milestones are incremental and human-reviewed. Technology choices are made when evidence supports them. Completion requires inspecting changes and running the relevant checks, with limitations reported. No milestone authorizes work in later phases automatically.

## Phase 0 — Repository and architecture foundation (current scope)

Deliver `AGENTS.md`, the four architecture documents, a minimal installable Python package scaffold, a tests location, and `.gitignore`. Inspect consistency and the final repository contents. No business logic, runtime knowledge folders, fake data, provider integrations, OAuth, PDF extraction, AI classification, or customer communications.

## Phase 1 — READ-ONLY HISTORICAL GMAIL INGESTION

This is the first implementation milestone after the foundation.

- Authenticate to Gmail for `office@elektrovienna.at` using local OAuth for a desktop application and the least-privilege Gmail read-only scope `https://www.googleapis.com/auth/gmail.readonly`. Store credential and token files outside Git and the SharePoint Knowledgebase, in the default directory `%LOCALAPPDATA%\ElektroViennaKnowledge\credentials\`.
- Read historical messages from `2026-01-01 00:00:00 Europe/Vienna`, inclusive. Include received, sent, and archived mail, replies, and forwarded messages; initially exclude spam, trash, and drafts. A coarse Gmail API query may improve efficiency, but the application must enforce the exact cutoff using provider timestamps.
- Collect message metadata, detect threads, and enumerate attachments. Do not restrict ingestion by sender or technician.
- Use SQLite for durable technical processing state in the initial single-machine implementation, outside Git and SharePoint at `%LOCALAPPDATA%\ElektroViennaKnowledge\state\pipeline.sqlite3`. Preserve provider message IDs, attachment identities, pagination/checkpoints, processing status, retries, failures, hashes, and idempotency state. This is not the canonical business-knowledge store; allow later migration for multi-machine/server execution.
- Maintain idempotency: repeat imports and interrupted/retried imports must not duplicate message or attachment occurrences.
- Make **NO mailbox modifications**, perform **NO AI extraction**, **send NOTHING**, and **delete NOTHING**. No Gmail label or read-status mutations.

Acceptance: tests establish stable identity, the exact inclusive cutoff using provider timestamps, included/excluded mail coverage, pagination, interrupted resume, repeat-import deduplication, and safe failure behavior. Check SQLite and credential/token locations remain outside Git and SharePoint, and authorization uses only the specified read-only scope. Validate that the adapter has no mailbox mutation/send path. Report discovered counts and incomplete items without presenting metadata discovery as completed original archiving. A real read-only validation requires separately configured credentials and authorized access; tests must not pretend that a live integration was exercised.

## Later milestones

| Phase | Deliverable | Completion evidence / boundary |
| --- | --- | --- |
| 2 | Document and original email archiving | Immutable message/attachment originals under the authorized Knowledgebase root, integrity checks, provenance, bounded writes, safe retry behavior; categorized document references preserve raw originals. |
| 3 | Email/document classification | Versioned labels including unknown; evaluate real representative cases with human review, including inbound/outbound, replies, forwarded content, and mixed content. |
| 4 | Structured extraction | Traceable fields and source locators; versioned processors; historical reprocessing preserves prior results; evaluate uncertainty and extraction errors. Select PDF/AI tooling only here as needed. |
| 5 | Airtable case matching | Inspect actual Customers/Tickets/Craftsmen schema; reuse external identities; preserve ambiguous candidates; test false matches and no-match behavior. Initial integration is read-only. |
| 6 | Offer/Evidence creation | Contextual offers and claims with source lineage, amount semantics, and conflicts; technician quote cannot become global pricing knowledge. |
| 7 | Manual review workflow | Attributable decisions, corrections, unresolved queues, and review history; human feedback is captured as evidence. Preserve CaseEvents for operational chronology, including calls, WhatsApp, notes, technician feedback, site visits, and outcomes outside email, with links to offers and appointments where applicable. Earlier phases still expose uncertainty for human inspection. |
| 8 | Knowledge derivation | Reviewed, scoped, versioned KnowledgeItems linked to supporting/conflicting evidence; demonstrate that new observations cannot silently overwrite accepted guidance. |
| 9 | Knowledge retrieval | Retrieve applicable reviewed guidance with evidence and uncertainty; avoid representing disputed proposals as established facts. No vector database unless separately justified and approved as an architectural change. |
| 10 | Intake/shadow communication agent | Prepare questions, scope, and recommendations in shadow mode; human evaluation against actual cases; no autonomous prices or customer communications. |
| 11 | Outlook Calendar appointment preparation | First-class structured Appointment drafts from confirmed Case facts, including provenance, missing fields, time zone, assignee, price context, materials and access details. Validate preparation without publishing events or sending invitations. |
| 12 | Customer/employee appointment confirmation drafts | Separate audience-specific CommunicationDraft records linked to Case/Appointment; detect stale drafts and prevent leakage of internal notes; no sending. |
| 13 | Invoice ingestion | Extend archiving/classification/extraction to structured invoices and case/outcome links; preserve source lineage, uncertainties, and idempotency. Earlier phases may already discover/archive invoice documents. |
| 14 | Feedback/evaluation system | Compare estimates, execution and outcomes; track review corrections and regressions; turn feedback into new evidence and reviewed knowledge proposals. Evaluation starts in earlier milestones and becomes systematic here. |
| 15 | Eventual controlled automation | Only after explicit enablement: define permitted actions, approval roles, audit/retry safeguards, reversibility, monitoring, and stop controls. Calendar publication, invitations, communication delivery, and pricing authority require explicit decisions before activation. |

## Decisions to resolve as work advances

Use `ARCHITECTURE.md` as the decision record. Initial OAuth authorization and credential location, exact Gmail cutoff/coverage, and SQLite technical-state storage are resolved there. Phase 1 must still design transaction/recovery details, local backups, and partial-import reconciliation. Before later phases, resolve archive formats, Airtable mappings, review and knowledge promotion policies, external processing/privacy choices, and calendar/communication authorization. Update the architecture and conceptual model deliberately as those choices become concrete. These documentation amendments do not implement Phase 1 or CaseEvent functionality.
