# Architecture foundation

Status: Phase 1 read-only Gmail metadata inventory is implemented. Later integrations and business processing remain conceptual.

## Boundaries and flow

Use a small Python application with a standard `src/` package layout. Add modules as milestones need them, rather than creating empty service layers now. SQLite is selected for initial single-machine technical processing state only. No business-knowledge database, web framework, queue, vector database, or n8n dependency is selected.

The conceptual flow is:

1. Provider adapters read sources and preserve stable provider identities.
2. Archiving preserves immutable originals and their integrity metadata.
3. Versioned classification and extraction produce derived records with precise source references.
4. Case association reuses Airtable references when a match is justified; ambiguous candidates enter review.
5. Offers and evidence express contextual claims, uncertainty, and provenance.
6. Review and knowledge derivation create versioned, scoped knowledge supported by evidence.
7. Retrieval supports inquiry handling, estimation, execution, and evaluation.

SOURCE → EXTRACTION → EVIDENCE → KNOWLEDGE is the logical trust hierarchy. Later extraction from a source also records its classification and processing history. Manual observations or corrections must be recorded as attributable source material and a review/interpretation record, not as an untraceable knowledge edit.

## Integration interfaces

Define interfaces when implementing their milestones. Domain records should not depend on provider SDK objects.

| Boundary | Responsibility | Initial side-effect limit |
| --- | --- | --- |
| Gmail reader | Read `office@elektrovienna.at`, message/thread metadata and attachment descriptors from `2026-01-01 00:00:00 Europe/Vienna` onward, using the coverage rules below | Read-only mailbox access; no labels, moves, deletes, or sends |
| Source archive | Store original message and attachment bytes with checksums and source references | Write only within the configured authorized knowledge root |
| Extraction/classification | Produce versioned derived records from sources | No source mutation or automatic knowledge promotion |
| Airtable case resolver | Reuse Customers, Tickets, and Craftsmen references | Initial matching is read-only; writes require a later explicit scope |
| Knowledge repository | Preserve evidence links, revisions, conflicts, and review decisions | No silent replacement of established knowledge |
| Appointment preparation | Produce structured Appointment drafts linked to Cases | No calendar publishing or invitations initially |
| Outlook Calendar adapter | Later map approved appointment records to Outlook Calendar | Access and publishing deferred; invitation side effects require explicit controls |
| Communication preparation | Produce customer/employee confirmation drafts from case and appointment data | No message delivery initially |

Gmail ingestion must not use Outlook or Microsoft Graph mail, even if Outlook is the user's mail client. Microsoft Outlook Calendar is an independent scheduling boundary. No credential setup, provider calls, or simulated successful integrations are part of the foundation.

## Phase 1 Gmail decisions

Historical coverage starts at **2026-01-01 00:00:00 Europe/Vienna**, inclusive. Include received mail, sent mail, archived mail, replies, and forwarded messages. Initially exclude spam, trash, and drafts. Do not restrict ingestion by sender, technician, or presumed relevance. A coarse Gmail API query may be used for efficiency, but the application must enforce the exact cutoff using provider timestamps.

Initial authorization uses local OAuth for a desktop application with the least-privilege Gmail read-only scope `https://www.googleapis.com/auth/gmail.readonly`. Credential and token files must remain outside Git and outside all SharePoint-synchronized storage, including the Knowledgebase. Their default directory is:

```text
%LOCALAPPDATA%\ElektroViennaKnowledge\credentials\
```

Phase 1 implements these decisions using Desktop OAuth and authenticated HTTPS GETs, without a Gmail SDK service object that exposes mailbox mutation methods. The provider interface returns immutable Python discovery records; persistence and orchestration never consume Google response dictionaries.

### Implemented Phase 1 recovery and storage decisions

- `models.MailReader` is the provider boundary; `state.InventoryState` describes the operations used by discovery. `SQLiteState` implements only technical inventory persistence. No later-phase service layers are added.
- SQLite schema version 1 uses message identity `(mailbox, message_id)` and attachment occurrence identity `(mailbox, message_id, part_id)`, retaining provider thread/attachment IDs. Messages are explicitly `discovered` or `excluded`, with `archived=0`; no byte hashes or archive references are invented.
- A durable page queue precedes message reads. Each message and all its attachment descriptors commit with its work status. Pagination advances only after the entire queued page finishes. Failures retain sanitized codes/attempt counts and stop; rerunning retries pending work. OS-held locking enforces one importer per database.
- Separate validation/historical checkpoints share the same deduplicated inventory. Completed passes restart reconciliation without clearing records. Explicit pagination restart recovers expired list tokens without erasing inventory. Failed message reads remain blocking/incomplete, including source disappearance; no automatic skip policy is introduced.
- Gmail lists are not snapshots. Completed passes may be rerun to catch changes. Previously observed records remain retained; absence from later listings is not proof of deletion or current label status.
- Configuration overrides stay under Windows LocalAppData, resolved from Windows itself. Git ancestry, redirected paths, registered OneDrive sync roots, and recognizable synchronized-directory names are rejected. Credentials/state remain separate; CLI statistics use SQLite read-only mode and no provider access.
- API projection excludes message/attachment bytes, snippets, and raw source. MIME depth beyond 20 is reported incomplete via sentinel children, never silently truncated. Subject/body content is not persisted. Only selected addressing/threading/date headers are retained.
- Back up SQLite only while the importer is stopped, to private local nonsynchronized storage. No automatic backup or SharePoint copy is implemented. Future schema versions require explicit migrations; unknown schema versions fail closed.

## Storage separation

The Git repository contains code, architecture documentation, and synthetic test fixtures only. Do not commit runtime knowledge, imported customer documents, raw email, PDFs, SQLite/runtime databases, local processing state, credentials, or secrets. Git ignore rules are safeguards, not a substitute for reviewing changes before committing.

The exact authorized SharePoint-synchronized knowledge root is:

```text
C:\Users\giova\Balun Energy\Balun Energy - Dokumente\06 Elektrovienna\07 Playground\Knowledgebase
```

Future knowledge-storage adapters must verify resolved write destinations stay under that root, including traversal and link/junction considerations. Never write elsewhere in SharePoint-synchronized folders without explicit instruction. Technical state and credential files use the separate local locations documented here, outside Git and SharePoint; they are not knowledge-archive content.

Intended layout (documented, not created by this milestone):

```text
Knowledgebase/
    00_raw/
        gmail/
        attachments/
    10_documents/
        offers/
        invoices/
        plans/
        photos/
        other/
    20_extractions/
    30_cases/
    40_knowledge/
    90_manual_review/
    99_logs/
    exports/
```

`00_raw` holds immutable originals. `10_documents` provides categorized records/references or explicitly derived renditions, preserving links to raw originals; category changes must not replace source bytes. `20_extractions` holds versioned extraction results. `30_cases` holds case-linked records, including CaseEvents, offers, evidence, and appointment/communication drafts. `40_knowledge` holds reviewed knowledge revisions and evidence references. `90_manual_review` records review queues and decisions. `99_logs` holds sanitized run logs. `exports` contains derived outputs, not canonical originals.

For the initial single-machine implementation, durable technical processing state uses SQLite **outside Git and outside SharePoint**, at the default Windows location:

```text
%LOCALAPPDATA%\ElektroViennaKnowledge\state\pipeline.sqlite3
```

This database holds Gmail provider message IDs, thread IDs, attachment identities, pagination/checkpoints, processing status, retries, failures, and idempotency state. Hashes are deferred until Phase 2 acquires bytes. It is not the canonical store for business knowledge or the Case timeline. Technical-state persistence stays behind a clear interface so later multi-machine/server execution can migrate to a suitable store without redefining business entities. Transaction and recovery details are documented above. SharePoint synchronization is not a transaction mechanism or an application audit trail.

## Identity, provenance, and reprocessing

Use stable internal IDs and explicit provider references. Gmail identity includes mailbox identity plus provider message ID; retain thread ID separately. Attachment identity includes its parent message and provider attachment/part identity. Content hashes support integrity and byte deduplication but must not merge distinct messages or contextual occurrences solely because their bytes match.

Historical imports must resume safely after interruption. Persist progress only after its corresponding records are durable. Repeated imports and retries must not duplicate source records or attachment occurrences. Reprocessing produces a new versioned interpretation when extraction logic changes while preserving original bytes, prior outputs, and downstream evidence lineage. Derived work needs identity based on source identity, processor/configuration version, and operation, with retry behavior tested before use.

Every claim records its source locator, extraction reference, and processing run. Keep model/prompt/parser/configuration versions where applicable. Failure, missing attachments, conflicting facts, and uncertain associations remain explicit; do not manufacture certainty from a successful API response.

## Knowledge changes and human review

Evidence includes scope and applicable conditions. Preserve supporting and contradicting observations. Knowledge promotion creates a reviewed revision with evidence links and a recorded decision. A newer case or feedback entry may propose a change, but cannot silently rewrite an accepted revision. Retain superseded versions and explain their relationship; retrieval must distinguish current reviewed guidance from proposals and disputed claims.

Matching proposals retain candidates, rationale, uncertainty, and the eventual review decision. No match is preferable to a false match. Thresholds, authorized reviewers, and promotion criteria need validation with real workflows.

## Case chronology

A Case has zero or more first-class CaseEvents preserving how an inquiry becomes a scoped, priced, executed job and an outcome. Events cover email as well as calls, WhatsApp, manual notes, technician feedback, site visits, offer creation/sending, appointment changes, and status changes. Each event belongs to a Case and retains its timestamp, type, actor, source/channel, summary, provenance, optional original/raw text reference, and applicable SourceMessage, Document, Offer, or Appointment links. Machine-generated events also reference their ProcessingRun.

The timeline is inquiry → clarification → technical assessment → offer → scheduling → execution → outcome. CaseEvents complement Evidence: events preserve what happened and when, while Evidence records contextual claims with source support. CaseEvents do not replace the linked entities, authorize sending or status transitions, or bypass SOURCE → EXTRACTION → EVIDENCE → KNOWLEDGE. Non-email events can rely on an attributable human source without inventing a SourceMessage. Keep event types lightweight; no event-sourcing framework or elaborate state machine is selected.

## Appointment and communication separation

A Case has zero or more Appointments. Appointment is a first-class structured record, not a blob of case notes. It references confirmed case facts and retains provenance for copied values. An appointment may include customer contacts, execution address, work and technical details, confirmed date/start time, expected duration, assignee and contact details, price information, materials, access instructions, and open notes.

Missing or conflicting scheduling facts stay visible. Use explicit time-zone semantics; `Europe/Vienna` is the operational context, but date/time confirmation and daylight-saving handling must be designed before calendar integration. Do not infer a confirmed slot from an unconfirmed email suggestion.

An Appointment draft is distinct from a published Outlook event. Customer/employee communication drafts reference the Case and Appointment and contain an intentional audience-specific projection: internal notes or technician pricing must not automatically flow into customer text. Changes to facts after review invalidate or flag affected drafts for re-review. Later approved publishing must account for provider-triggered invitations and retries; a calendar write cannot be treated as harmless draft creation.

## Open decisions

- Future schema/server migrations and automated backup requirements; Phase 1 transaction/recovery and stopped-importer local backup procedures are decided above.
- Comprehensive mailbox change reconciliation and operator policy for disappeared/inaccessible messages; Phase 1 preserves incomplete work and supports explicit list-token restart.
- Archive formats, source locator conventions, identifiers, retention, access permissions, and handling of provider source disappearance.
- Actual Airtable field mappings, record ownership, case/ticket cardinality, matching rules, and any eventual write-back.
- Review ownership, evidence sufficiency, knowledge applicability, confidence representation, and conflict resolution policy.
- Extraction technology, evaluation samples, and handling of sensitive data in any later external processor.
- Calendar ownership, employee/technician identity mapping, required fields for approval, and explicit publish/send authorization controls.

Resolve decisions at their milestone and record them here before relying on them. Phase 1 does not authorize later milestones.
