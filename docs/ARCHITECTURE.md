# Architecture foundation

Status: Phase 1 read-only Gmail metadata inventory is implemented and live validated. Phase 2 original source archiving is live validated for the current historical inventory as of 2026-10-02. Later integrations and business processing remain conceptual.

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

- `models.MailReader` is the Phase 1 provider boundary; `state.InventoryState` describes the operations used by discovery. `SQLiteState` implements technical inventory and archival persistence, not business knowledge.
- SQLite schema version 1 uses message identity `(mailbox, message_id)` and attachment occurrence identity `(mailbox, message_id, part_id)`, retaining provider thread/attachment IDs. Messages are explicitly `discovered` or `excluded`, with `archived=0`; no byte hashes or archive references are invented.
- A durable page queue precedes message reads. Each message and all its attachment descriptors commit with its work status. Pagination advances only after the entire queued page finishes. Failures retain sanitized codes/attempt counts and stop; rerunning retries pending work. OS-held locking enforces one importer per database.
- Separate validation/historical checkpoints share the same deduplicated inventory. Completed passes restart reconciliation without clearing records. Explicit pagination restart recovers expired list tokens without erasing inventory. Failed message reads remain blocking/incomplete, including source disappearance; no automatic skip policy is introduced.
- Gmail lists are not snapshots. Completed passes may be rerun to catch changes. Previously observed records remain retained; absence from later listings is not proof of deletion or current label status.
- Configuration overrides stay under Windows LocalAppData, resolved from Windows itself. Git ancestry, redirected paths, registered OneDrive sync roots, and recognizable synchronized-directory names are rejected. Credentials/state remain separate; CLI statistics use SQLite read-only mode and no provider access.
- API projection excludes message/attachment bytes, snippets, and raw source. MIME depth beyond 20 is reported incomplete via sentinel children, never silently truncated. Subject/body content is not persisted. Only selected addressing/threading/date headers are retained.
- Back up SQLite only while the importer is stopped, to private local nonsynchronized storage. No automatic backup or SharePoint copy is implemented. Future schema versions require explicit migrations; unknown schema versions fail closed.
- Quota handling stays inside the Gmail adapter: all GET attempts are paced at least 250 ms apart using a monotonic clock, including list/profile calls and retries. The first request is immediate and slower requests need no additional pacing delay. At most approximately 4 message GETs/second leaves headroom under the 6,000-unit/minute per-user/project quota (20 units/message GET).
- Only recognized rate-limit 403 reasons, 429, and transient 500/502/503/504 receive bounded retries: six retries maximum, exponential waits starting at 1 second plus fresh 0–1 second jitter, capped at 32 seconds. Policy/daily-limit/authorization/unknown 403 errors fail immediately. Structured error reasons are mapped to fixed safe codes; raw error messages and bodies never reach state or CLI. Unknown/malformed reasons fail closed. Clock/sleep/jitter are injectable for deterministic tests.
- Retries are internal to one logical provider read. SQLite processing attempt/failure counters and checkpoints keep their existing meaning, with a final failure recorded only when that read fails. No schema change or live-state reset is needed. Resume a rate-limited import using the normal inventory command.

Live status, reported by the operator on 2026-10-02: OAuth works for `office@elektrovienna.at`, and two 10-message validation runs succeeded with zero failures. Initial historical attempts exposed a generic HTTP 403 and later a transport failure. After quota pacing/retry hardening and normal resume, the historical inventory completed successfully: `completed: true`, 1375 discovered messages, 2950 attachment occurrences, `incomplete: 0`, and `last_error: null`. The historical failures counter of 2 preserves the prior stopped/failed attempts. Phase 1 downloaded no bytes. Subsequent operator-reported limited Phase 2 runs archived 60 originals and 36 attachment occurrences without failures. The first full run exposed `attachment_bytes_unavailable` on an inventoried zero-size multipart root with no attachment ID. The prior reader explicitly rejected containers. The distinct inline-body reader described below removes that rejection for validated zero-byte bodies; the subsequent normal resume completed local archiving successfully.

### Implemented Phase 2 source archiving decisions

Phase 2 live validation completed successfully on **2026-10-02** (operator-reported): all **1375/1375 message originals** and **2950/2950 attachment occurrences** are archived locally, totaling **2,331,388,345 bytes (approximately 2.33 GB)**. `incomplete: 0`, `last_error: null`. One inline `provider_size_mismatch` anomaly is durably preserved; the two cumulative archive failures remain historical counters from earlier stopped/failed attempts, not current incomplete work. Raw source archiving is complete for the current historical inventory. The application verified the local authorized SharePoint-synchronized Knowledgebase root; remote SharePoint cloud-sync completion was **not independently verified**.

- `models.SourceReader` returns `OriginalMessage` and attachment `bytes`; provider response dictionaries remain inside `gmail.py`. `archive.py` coordinates existing eligible inventory, `archive_store.SourceArchive` owns guarded immutable publication, and SQLite holds technical provenance/completion. No classification or business entities are introduced.
- Only existing discovered messages at/after the cutoff with no stored excluded labels are selected. Archive does not enumerate Gmail or reclassify historical coverage. Gmail identity is verified before opening/migrating state. Last-observed labels remain the coverage basis; Phase 2 does not claim fresh label reconciliation.
- Preserve exact base64url-decoded Gmail raw/RFC822 source as `.eml`; do not normalize/rebuild MIME messages. Download separate attachment bodies with Gmail attachment IDs. External occurrences use `users.messages.attachments.get` unchanged. For occurrences without an ID, the distinct `get_inline_attachment` method uses `messages.get(format=full)` with only message ID and a bounded MIME tree containing part IDs, MIME types, filenames, body attachment IDs/sizes/data, and nested parts. Match the exact inventoried part ID and metadata, require absence of an attachment ID, require current Gmail size to equal inventory size, then decode base64url. Inline decoded bytes are authoritative; differences between decoded length and stable provider size are durably recorded as non-fatal anomalies. Missing parts/data, unexpected attachment IDs, duplicate IDs, malformed structure/encoding, and changes to the inventoried provider size fail with sanitized codes. External attachment decoded-size checks remain strict. A declared zero-size body produces zero bytes, including an empty multipart container body; children remain distinct parts and the complete raw source preserves the MIME subtree. Never guess Gmail part IDs by parsing raw source. These separate acquisition methods do not alter Phase 1's metadata-only projection.
- Stable paths use SHA-256 of compact JSON identity arrays: `[casefolded mailbox, message ID]` for messages and `[casefolded mailbox, message ID, part ID]` for attachment occurrences. Files are sharded by the first two hex characters under `00_raw/gmail/` or `00_raw/attachments/`; original names never enter a path. Full identity digests keep paths short enough for ordinary Windows paths under this root. Content hashes are calculated separately from downloaded bytes. Equal content does not merge source occurrences; this version stores a separate file for each occurrence.
- Flush unique same-directory temporary files with `fsync`, then publish atomically without replacing an existing path (Windows rename or POSIX hard link). Verify an existing target's size/hash before adoption; record SQLite completion only after local publication and verification. A crash between file publication and database commit is recovered by re-downloading and adopting a matching target. Completed records are checked locally on rerun without re-downloading. Missing/corrupt recorded files fail visibly; no overwrite or destructive recovery command exists. Interrupted temporary files from a hard kill are not completed artifacts and may remain for manual investigation.
- Schema 2 adds archival file and job tables in a transaction, retaining schema 1 inventory/checkpoints unchanged. Legacy `messages.archived=0` is retained for schema compatibility but is no longer an archival completion indicator; statistics use verified `archive_files`. Provenance includes retained message/thread/internal timestamp and attachment descriptor identities, `gmail://<encoded mailbox>/messages/<encoded ID>[/parts/<encoded part ID>]` locators, relative archive references, hashes, sizes, UTC archive timestamps, and sanitized job failures. SQLite remains outside SharePoint and must be preserved/backed up locally to retain this mapping.
- The CLI reuses the single-writer state lock. Pending/failed messages precede complete messages, ordered within each group by internal timestamp/message ID. `archive --limit N` checks at most N messages and all their occurrences. Unlimited reruns also verify all completed records. Failures stop the run but preserve every committed file/record and all Phase 1 checkpoints. A message is complete only after its raw original and every inventoried attachment occurrence are recorded and its job succeeds. Scan failures and archive failures are separate.
- The fixed authorized root has no CLI/environment override. Reject paths outside it, traversal, Git ancestry, LocalAppData destinations, and detectable links/junctions, including existing ancestors; recheck before directory creation/publication. No later-phase directories are created. Guards are not protection against privileged concurrent filesystem replacement. Local publication is not proof of remote SharePoint synchronization or tamper-proof retention; external edits will be detected when the record is next checked. Acquire one object in memory at a time; no large-file streaming or remote sync verification is implemented.
- Gmail remains GET-only with exactly `gmail.readonly`, existing 250 ms pacing and bounded retries. No additional runtime dependency is required. Synthetic tests establish local behavior and inline-failure resume without duplicates. After the earlier failures and inline fixes, normal resume completed the full current inventory locally.

## Storage separation

The Git repository contains code, architecture documentation, and synthetic test fixtures only. Do not commit runtime knowledge, imported customer documents, raw email, PDFs, SQLite/runtime databases, local processing state, credentials, or secrets. Git ignore rules are safeguards, not a substitute for reviewing changes before committing.

The exact authorized SharePoint-synchronized knowledge root is:

```text
C:\Users\giova\Balun Energy\Balun Energy - Dokumente\06 Elektrovienna\07 Playground\Knowledgebase
```

The implemented source archive verifies resolved write destinations stay under that root, including traversal and link/junction considerations. Never write elsewhere in SharePoint-synchronized folders without explicit instruction. Technical state and credential files use the separate local locations documented here, outside Git and SharePoint; they are not knowledge-archive content.

Layout: Phase 2 creates only `00_raw/gmail` and `00_raw/attachments` as needed. Other directories remain conceptual:

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

This database holds Gmail provider message IDs, thread IDs, attachment identities, pagination/checkpoints, processing status, retries, failures, and idempotency state. Phase 2 adds actual byte hashes, lengths, archive references, acquisition timestamps and source locators after acquiring bytes. It is not the canonical store for business knowledge or the Case timeline. Technical-state persistence stays behind explicit operations so later multi-machine/server execution can migrate to a suitable store without redefining business entities. Transaction and recovery details are documented above. SharePoint synchronization is not a transaction mechanism or an application audit trail.

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
- Retention, access permissions, and operator recovery policy for provider source disappearance or externally damaged archives. Phase 2 formats, source locators and identifiers are decided above; unavailable bytes fail explicitly.
- Actual Airtable field mappings, record ownership, case/ticket cardinality, matching rules, and any eventual write-back.
- Review ownership, evidence sufficiency, knowledge applicability, confidence representation, and conflict resolution policy.
- Extraction technology, evaluation samples, and handling of sensitive data in any later external processor.
- Calendar ownership, employee/technician identity mapping, required fields for approval, and explicit publish/send authorization controls.

Resolve decisions at their milestone and record them here before relying on them. Phase 1 does not authorize later milestones.

### Inline size authority and schema 3

Observed live: a non-external part with unchanged identity had inventoried/current Gmail size 1111, while valid decoded `body.data` contained 1118 bytes. Preserve those exact decoded source bytes. Only this inline decoded-length discrepancy is non-fatal; current provider size must still match inventory and all identity/encoding checks remain mandatory. Schema 3 adds a nullable, constrained `archive_files.anomaly` column in an additive transaction. `provider_size_mismatch` commits atomically with archival completion. Provider size remains in unchanged `attachments.size`; actual length/hash remain in `archive_files`. No inventory metadata, previous files or existing completion values are rewritten. Statistics expose a mailbox-wide anomaly count, independent of later verification status. The subsequent completed live run durably retained one such anomaly.
