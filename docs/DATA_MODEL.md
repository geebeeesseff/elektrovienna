# Conceptual data model

Business records remain a conceptual contract; their persistence types and migrations remain undecided. The implemented technical schema is described below. It uses SQLite outside Git and SharePoint at `%LOCALAPPDATA%\ElektroViennaKnowledge\state\pipeline.sqlite3` for Gmail message/thread/attachment identities, pagination/checkpoints, processing status, retries, failures, and idempotency state, not canonical business knowledge or the Case timeline. Phase 2 adds integrity/provenance records only after original byte acquisition. The design must allow later migration for multi-machine/server execution. Unknown values remain unknown, with provenance and review status where relevant. Use stable internal IDs, timestamps, explicit provider references, and revision history for mutable interpretations.

## Implemented Phase 1 technical schema (version 1)

The conceptual business entities below remain unimplemented. These inventory tables remain unchanged in schemas 2 and 3:

| Table | Identity and purpose |
| --- | --- |
| `messages` | Primary key `(mailbox, message_id)`; provider thread ID, internal timestamp in epoch milliseconds, selected addressing/threading/date headers, labels, discovered/excluded state, first/last observation timestamps. `archived` is constrained to zero. |
| `attachments` | Primary key `(mailbox, message_id, part_id)`; parent message foreign key, optional provider attachment ID, filename, MIME type, and declared size. Part identity also covers inline parts without separate attachment IDs. No attachment bytes or fabricated content hash. |
| `scans` | One versioned checkpoint per mailbox/cutoff/mode; current and next page tokens, queued-page flag, completion flag, seen tokens for cycle detection, failure count, sanitized last error, update timestamp. Validation and historical modes are distinct. |
| `work` | Primary key `(scan_key, message_id)`; pending/failed/discovered/excluded status, cumulative attempts, sanitized last error. Retries and repeated passes update the existing technical record. |

Listed pages become durable before message retrieval. A message, attachment metadata, and work completion commit atomically; checkpoint advancement follows all queued message commits. No business entities, classification, extraction, source bytes, or canonical knowledge are stored in SQLite. Scans are resumable processing state, not a versioned business ProcessingRun history.

## Implemented Phase 2 technical additions (version 2)

An additive `BEGIN IMMEDIATE` transaction creates the following tables and advances `user_version` to 2. Existing message, attachment, work and scan rows are untouched. Unknown versions fail closed. Read-only statistics support both versions without migration. Older application versions reject schema 2; use the upgraded application after migration. Back up state locally while no writer is running before first use.

| Table | Identity and purpose |
| --- | --- |
| `archive_files` | Primary key `(mailbox, message_id, kind, part_id)`; kind is `message` or `attachment`, with empty part ID for raw message originals. A separate kind keeps root-part attachment ID `""` distinct from its message. Parent message foreign key, unique relative archive reference, content SHA-256, actual byte length, UTC archive timestamp, provider source locator and last verification flag. Attachment records join the inventoried occurrence on mailbox/message/part to retain provider attachment ID, original filename, MIME type and declared size. |
| `archive_jobs` | Primary key `(mailbox, message_id)` with parent message foreign key; cumulative logical attempt/failure counters, last sanitized error and update timestamp. Success clears the outstanding error without resetting prior failures. Independent of Phase 1 scan state. |

`messages.archived` remains the legacy zero-only column; archival counts now derive from verified `archive_files`, not this column. A complete job requires a recorded message original and every inventoried attachment occurrence. An interrupted or failed job remains incomplete even if some files are durable. Last verification is invalidated on observed missing/corrupt files. Statistics are a state snapshot and do not proactively rehash the filesystem.

Originals live under `00_raw/gmail/<prefix>/<identity-hash>.eml` and occurrence bytes under `00_raw/attachments/<prefix>/<identity-hash>.bin`, rooted only at the authorized Knowledgebase. The prefix is the first two hex characters of the identity hash. Hash inputs and immutable publication rules are defined in ARCHITECTURE. Gmail source locators use percent-encoded mailbox/message IDs and, for attachments, the exact part ID: `gmail://<mailbox>/messages/<message ID>[/parts/<part ID>]`. No customer filenames are interpolated into paths. Distinct occurrences remain distinct even with equal content hashes.

An acquisition timestamp is the UTC time a verified file is accepted for recording. If a crash left a published file without a record, resume records a new adoption timestamp; it does not invent the unknown original publication time. Provenance metadata is retained in local SQLite, not duplicate sidecars. The database is therefore required alongside archived bytes for provenance and recovery. There are no Document categories, Extractions, Evidence, or KnowledgeItem implementations in this milestone.

## Inline size anomaly addition (version 3)

Phase 2 live validation completed successfully on **2026-10-02** (operator-reported): all **1375/1375 message originals** and **2950/2950 attachment occurrences** are archived locally, totaling **2,331,388,345 bytes (approximately 2.33 GB)**. `incomplete: 0`, `last_error: null`. One inline `provider_size_mismatch` anomaly is durably preserved; the two cumulative archive failures remain historical counters from earlier stopped/failed attempts, not current incomplete work. Raw source archiving is complete for the current historical inventory. The application verified the local authorized SharePoint-synchronized Knowledgebase root; remote SharePoint cloud-sync completion was **not independently verified**.

A transactional `ALTER TABLE archive_files ADD COLUMN anomaly` adds a nullable field constrained to `provider_size_mismatch` or NULL. Existing rows acquire NULL; their prior values and Phase 1 metadata/checkpoints are preserved. Versions 1/2 migrate automatically on the next authenticated writer opening state; read-only statistics support all three versions. Older application versions reject schema 3.

For validated inline occurrences, `attachments.size` is the preserved inventoried provider size, required to equal current Gmail size at acquisition. `archive_files.byte_length` and `sha256` describe actual decoded source bytes. If these lengths differ, the explicit anomaly is inserted in the same transaction as the archive record. Query by joining mailbox/message/part ID; no separate logging table or fabricated size replaces provider metadata. External attachments retain strict size validation. Statistics count anomalies across the mailbox, including records later flagged unverified. Reruns verify actual archived length/hash and leave provenance unchanged.

## Source and interpretation records

| Entity | Intended information and relationships |
| --- | --- |
| SourceMessage | Mailbox identity, provider message ID, thread ID, sender/recipients, subject, provider timestamps, direction, original archive reference/checksum when archived, attachment references, and discovery ProcessingRun. Mailbox plus message ID provides source identity. Original bytes are immutable once archived. Metadata-only discovery before archiving is explicit. |
| Document | Original artifact identity, filename, media type, checksum, raw archive reference, category, source-message/attachment occurrence references, and derived rendition references. A document may have multiple source occurrences; equal bytes do not erase occurrence provenance. |
| Extraction | SourceMessage/Document or recorded human-source reference, processor/parser/model/prompt/configuration versions as applicable, ProcessingRun, extracted values, exact source locators, uncertainty, errors, and prior/superseded interpretation links. A new version does not overwrite an older extraction. |
| ProcessingRun | Operation, start/end times, status, processor/configuration versions, input scope, checkpoint references, counts, failure details, and outputs. Operational history is distinct from source identity and deduplication state. Logs must not contain credentials or unnecessary customer content. |

Source locators should identify the relevant message section, attachment, page, span, or other addressable evidence location. Exact locator formats are a later decision. Manual feedback is stored as an attributable source with author, timestamp, case context, and original wording, with an interpretation record feeding evidence through the same hierarchy.

Classifications are versioned derived results, not mutable source truth. Initial candidate labels include `customer_inquiry`, `customer_reply`, `technician_quote`, `customer_offer`, `invoice`, `technical_discussion`, `appointment`, `supporting_document`, `irrelevant`, and `unknown`. Do not assume every offer comes from a technician, or force mixed-content messages into a prematurely chosen single-label schema.

## Operational and knowledge records

| Entity | Intended information and relationships |
| --- | --- |
| Case | Inquiry, missing information, technical understanding, scope, execution context, outcome, source/evidence links, proposed/confirmed Airtable Customer/Ticket references, zero or more CaseEvents, and zero or more Appointments. Reuse Airtable identities rather than duplicating its customer master. |
| CaseEvent | Attributable timeline entry belonging to one Case, with event time/type, actor, source/channel, summary, optional raw source and operational entity links, and ProcessingRun where machine-generated. See the field details below. |
| Offer | Case reference when resolved, issuer/recipient and direction, scope, line items where available, amounts, currency, tax treatment if known, validity, assumptions/exclusions, source/extraction references, and revision status. Distinguish technician quotes from customer offers; values do not become global pricing rules. |
| Evidence | Contextual claim or observation, source and Extraction references, Case/Offer references where applicable, supporting/contradicting relationships, applicability, uncertainty, and review history. Must trace back to an original source. |
| KnowledgeItem | Reusable guidance or claim, scope/applicability, supporting and conflicting Evidence references, revision, review status/decision, and supersession links. No accepted revision without explicit evidence support and recorded review. |
| ReviewDecision | Target entity/revision, reviewer, timestamp, decision, rationale, and references to evidence or recorded feedback. Decisions preserve the history of matching, extraction corrections, and knowledge promotion. |
| CommunicationDraft | Case reference, Appointment reference where relevant, intended audience/recipient, channel, drafted content, source fact revisions, review status, and approval record if applicable. No delivery is implied by approval or storage. |

Unresolved source and offer records may exist before a Case is known. Matching candidates and decisions must be preserved independently of confirmed links. Airtable Customers, Tickets, and Craftsmen remain external entities referenced by stable record IDs. Their exact cardinality relative to internal Cases is not yet settled.

## CaseEvent: first-class entity

Each CaseEvent belongs to exactly one Case; a Case may have zero or more CaseEvents. Its purpose is to preserve the operational timeline: inquiry → clarification → technical assessment → offer → scheduling → execution → outcome, including information that never appears in Gmail.

| Field | Intended information, where applicable |
| --- | --- |
| `id` | Stable event identity |
| `case_id` | Owning Case reference |
| `timestamp` | Event time with time-zone context; preserve uncertainty when the exact time is unknown |
| `event_type` | Lightweight event classification |
| `actor` | Person or system involved in the event |
| Source/channel | Email, phone, WhatsApp, manual entry, or other origin |
| Summary | Concise description of what happened |
| Original/raw text reference | Optional reference preserving original wording separately from the summary |
| Linked SourceMessage | Email source reference where applicable; not required for non-email events |
| Linked Document | Relevant document reference where applicable |
| Linked Offer | Relevant offer reference where applicable |
| Linked Appointment | Relevant appointment reference where applicable |
| Provenance / attributable human source | Basis for the entry, including who recorded or reported a non-email event |
| ProcessingRun | Required reference when the event is machine-generated |

Initial candidate event types are `email`, `call`, `whatsapp`, `note`, `technician_feedback`, `offer_created`, `offer_sent`, `appointment`, `site_visit`, `status_change`, `outcome`, and `other`. Appointment changes can be recorded with the `appointment` type and an explanatory summary. These are candidate labels, not a state-transition model.

CaseEvents complement Evidence: events preserve what happened and when, while Evidence records contextual claims with source support. CaseEvents preserve chronology and links; they do not replace SourceMessage, Document, Offer, Appointment, or Evidence. Recording an `offer_sent` event describes an attributable occurrence and does not authorize sending. A manual note or feedback event is not automatically knowledge: preserve its attributable source and carry derived claims through extraction/interpretation and Evidence before knowledge promotion.

## Appointment: first-class entity

Each Appointment belongs to exactly one Case; a Case may have zero or more Appointments. Multiple visits remain separate records.

| Field group | Information retained where available |
| --- | --- |
| Identity | Appointment ID, Case ID, revision, state, optional Outlook calendar/event IDs only after a later authorized publication |
| Customer | Customer name, phone number, email, external Customer reference |
| Location | Execution address, access information |
| Work | Service/job description, technical details, material notes, relevant open notes |
| Schedule | Confirmed date, confirmed start time, time zone, expected duration if known, confirmation provenance, unresolved scheduling questions |
| Assignment | Technician/employee identity or external Craftsman reference; relevant phone/email |
| Commercial context | Price or price information, currency/tax basis if known, related Offer/Evidence references; no inference that a quoted price is confirmed |
| Review and lineage | Field-level fact references/confirmation status, source Case revision, reviewer decisions, draft staleness, change history |

Partial drafts can exist with missing fields. Their completeness and confirmation must be visible. Define the minimum approval requirements before implementation; do not invent required commercial rules now. Conceptually distinguish preparation, reviewed preparation, and later publication, while cancellation/rescheduling behavior and exact states remain to be designed.

The structured appointment is the basis of the employee's operational job record and customer appointment confirmation. CommunicationDraft remains separate because audience, contents, approval, and delivery lifecycle differ. A prepared Appointment is not an Outlook event, and a CommunicationDraft is not a sent message.

## Relationship and integrity rules

- SourceMessage → attachment occurrence → Document preserves message and document provenance.
- Sources → versioned Extractions → Evidence → versioned KnowledgeItems preserves the trust hierarchy.
- Offers and Cases reference sources and interpretations; an unresolved association must not be presented as confirmed.
- Every derived entity links to its ProcessingRun or attributable human action, and every reusable knowledge claim links to Evidence.
- KnowledgeItem revisions may use many Evidence records, and one Evidence record may support or contradict several knowledge revisions.
- Evidence and KnowledgeItems retain historical references when new extraction versions appear; reprocessing does not silently redirect existing provenance.
- Case → zero or more Appointments; each Appointment → one Case; CommunicationDraft → Case and optionally Appointment.
- Case → zero or more CaseEvents; each CaseEvent → one Case, with optional links to SourceMessage, Document, Offer, and Appointment. Preserve provenance and a ProcessingRun for machine-generated events.
- Changes to referenced facts flag dependent prepared appointments and communications for review rather than silently changing approved output.
- Idempotency applies to source occurrences and retryable derived operations. Byte deduplication never erases business context.
