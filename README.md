# Elektro Vienna Knowledge System

Operational learning: inquiry → understanding → pricing → execution → outcome → reusable knowledge.

Phase 1 implements **read-only historical Gmail metadata inventory** for `office@elektrovienna.at`. Phase 2 adds an explicit command to archive immutable original email and attachment bytes under the authorized Knowledgebase root. Discovery is not archival completion, and sources are not established knowledge. Classification, extraction, Airtable, Outlook Calendar, and customer communication remain unimplemented. Phase 2 is live validated for the current historical inventory as of 2026-10-02.

Start with [AGENTS.md](AGENTS.md), then read:

- [North Star](docs/NORTH_STAR.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Conceptual data model](docs/DATA_MODEL.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)

## Installation (PowerShell, Python 3.11+)

Run from this repository with a locally installed Python:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
```

Runtime dependencies: `google-auth[requests]` for credential refresh and authenticated transport; `google-auth-oauthlib` for Desktop OAuth; `requests` for the GET transport and sanitized transport errors; `oauthlib` for OAuth failure handling; `tzdata` for Windows IANA time zones. Requests and oauthlib are already transitive dependencies of the Google authentication packages, but are declared explicitly because the application imports them. SQLite, configuration, CLI, locking, and persistence otherwise use the standard library. Development dependencies: `pytest` and `build`. Build backend: existing `setuptools>=68`.

## Desktop OAuth setup

1. In Google Cloud, enable the Gmail API, configure the consent screen/access for the intended account, and create an **OAuth client ID of type Desktop app**. Workspace admin approval may be required.
2. Put the downloaded Google Desktop client JSON at:
   `%LOCALAPPDATA%\ElektroViennaKnowledge\credentials\client_secret.json`
3. Run the limited validation below and authorize `office@elektrovienna.at` in the browser. Only `https://www.googleapis.com/auth/gmail.readonly` is requested. The profile must match the configured mailbox before discovery starts. The command waits up to 180 seconds for the local OAuth callback.

The token is atomically saved as `token.json` in the same private local credential directory. No secret is supplied on the command line. Existing broader-scoped tokens are rejected; use a dedicated Desktop client and reauthorize after removing an incompatible local token. Refresh/auth errors stop explicitly, without a simulated fallback. Missing credentials produce the exact required file path and exit code 1.

Keep the credential directory private to your Windows user; Windows account ACLs protect local files. This application does not encrypt files or change Windows ACLs. Never copy credentials, tokens, or state to Git, OneDrive, SharePoint, or Knowledgebase.

## Commands

```powershell
# At most 10 qualifying messages, in Gmail provider order (not guaranteed chronological).
.\.venv\Scripts\elektro-vienna.exe validate --limit 10

# Inventory all qualifying historical messages; resume an interrupted page automatically.
.\.venv\Scripts\elektro-vienna.exe inventory

# Phase 2: archive at most 10 inventoried messages and their attachment occurrences.
.\.venv\Scripts\python.exe -m elektro_vienna archive --limit 10
# Archive/resume the full existing eligible inventory after limited live validation.
.\.venv\Scripts\python.exe -m elektro_vienna archive

# Local read-only statistics; no OAuth, network request, or new database.
.\.venv\Scripts\elektro-vienna.exe stats

# If Gmail rejects an expired page token, explicitly restart pagination, retaining records.
.\.venv\Scripts\elektro-vienna.exe inventory --restart-pagination
# The same recovery is available for a validation checkpoint.
.\.venv\Scripts\elektro-vienna.exe validate --limit 10 --restart-pagination
```

Equivalent module invocation: `.\.venv\Scripts\python.exe -m elektro_vienna stats` (and the same subcommands).

## Phase 2 source archive

Phase 2 live validation completed successfully on **2026-10-02** (operator-reported): all **1375/1375 message originals** and **2950/2950 attachment occurrences** are archived locally, totaling **2,331,388,345 bytes (approximately 2.33 GB)**. `incomplete: 0`, `last_error: null`. One inline `provider_size_mismatch` anomaly is durably preserved; the two cumulative archive failures remain historical counters from earlier stopped/failed attempts, not current incomplete work. Raw source archiving is complete for the current historical inventory. The application verified the local authorized SharePoint-synchronized Knowledgebase root; remote SharePoint cloud-sync completion was **not independently verified**.

`archive` selects only existing `discovered` Phase 1 records that satisfy the fixed cutoff and stored label exclusions; it never lists or discovers additional mail. Mailbox identity is checked before opening/migrating state or writing originals. Stored labels reflect the last inventory observation, not a fresh mailbox snapshot. `--limit N` processes at most N messages, including all their inventoried attachment occurrences, with incomplete/failed messages first and then completed messages for local integrity verification. Within each group ordering is by internal timestamp and message ID. Repeating a limited run therefore advances pending work; an unlimited rerun also verifies every completed record. It stops on the first failure without deleting prior work.

The **only** archive root is fixed in code, with no environment or CLI override:

```text
C:\Users\giova\Balun Energy\Balun Energy - Dokumente\06 Elektrovienna\07 Playground\Knowledgebase
    00_raw/gmail/<two-hex-prefix>/<message-identity-sha256>.eml
    00_raw/attachments/<two-hex-prefix>/<occurrence-identity-sha256>.bin
```

Identity hashes are SHA-256 of compact JSON arrays containing mailbox/message ID, plus MIME part ID for attachments. They are distinct from the SHA-256 of file contents. Filenames never contain customer names or supplied attachment filenames. Original filenames, MIME types, provider IDs, mailbox, thread, internal timestamp, source locator, UTC archive timestamp, content hash, byte length, and relative archive reference remain linked in local SQLite. Equal bytes in separate occurrences retain separate records and paths; no content deduplication is performed.

Messages use Gmail `format=raw`, base64url-decoded to exact RFC822 bytes without parsing/reserialization. External attachments continue to use `users.messages.attachments.get`. A distinct Phase 2 `get_inline_attachment` method uses `messages.get(format=full)` with only message ID and MIME part IDs, types, filenames, body attachment IDs/sizes/data, and nested parts. It locates the exact part ID (including root `""`), requires no attachment ID, validates identity/structure and size, and decodes base64url `body.data`. Missing parts, unexpected attachment IDs, non-empty bodies without data, invalid encoding, and changes to the inventoried provider size fail with sanitized codes. A declared zero-byte body is preserved as an empty file, including an empty multipart container body; this does not reconstruct the container subtree or replace the canonical raw message. Phase 1 metadata projection remains unchanged. The original raw message remains available even if a separate attachment fails. These GETs retain the existing read-only scope, pacing, retries, and sanitized errors.

Files are written to a unique temporary file in their destination directory, flushed with `fsync`, then published without replacement (atomic Windows rename; atomic hard-link publication on POSIX). Existing targets must match downloaded hash and size before adoption. Recorded files are verified against stored hash/size on rerun. A mismatch or missing recorded file stops with `archive_integrity_mismatch` or `archive_file_missing`; there is no automatic overwrite/repair/reset. SQLite records completion only after publication and verification. A crash between publication and commit is recovered by downloading again and verifying/adopting the same target. Normal failures clean temporary files; a hard process kill may leave unreferenced `.archive-*.tmp` files, which are never considered complete or automatically reused.

Path guards reject traversal, other roots, Git ancestry, LocalAppData archive destinations, and detectable symlink/junction redirection, rechecking components before writes. They cannot prevent a privileged concurrent process from changing the filesystem or external editors/sync clients from altering files. Keep this archive under controlled access and run one machine/operator; SharePoint synchronization is not a transaction, remote durability guarantee, or tamper-proof store. The application verifies local bytes, not remote upload completion. Bytes are acquired one object at a time in memory; no streaming large-object downloader is implemented.

`stats` includes an `archive` section: eligible messages, verified message originals, eligible/archived attachment occurrences, bytes archived, cumulative failures, incomplete messages, and last outstanding error. A message is incomplete until its original and all inventoried occurrences are durable and its job has succeeded. File counters reflect last verification; `stats` itself does not read archive bytes or contact Gmail. Attachment bytes are counted separately even when also present within `.eml` files. Historical scan failures remain independent of archive failures.

The inclusive cutoff is **2026-01-01 00:00:00 Europe/Vienna**, equal to **2025-12-31 23:00:00 UTC**. A query beginning one second earlier avoids the exclusive `after` boundary; final eligibility uses Gmail `internalDate` in milliseconds. Received, sent, archived, reply, and forwarded messages are eligible without sender/content/relevance filtering. Spam, trash, and drafts are excluded in both the query and metadata checks. The Date header is never used to enforce the cutoff.

Validation and historical inventory have separate checkpoints but share deduplicated messages and attachment occurrences. Validation reads at most N qualifying messages per invocation, counting previously inventoried qualifying messages too; excluded candidates may require additional reads. It resumes its own pending page on the next validation run. It does not claim the historical inventory is complete. After a scan completes, its next invocation starts a fresh reconciliation pass without deleting records. Interruptions preserve committed progress. Exit codes: 0 successful/limited run, 1 failure, 2 invalid CLI arguments, 130 interruption.

## State, failure recovery, and limitations

Default state: `%LOCALAPPDATA%\ElektroViennaKnowledge\state\pipeline.sqlite3`.

`EV_CREDENTIALS_DIR` and `EV_STATE_PATH` override these paths, **only within the actual Windows LocalAppData directory**. Paths inside Git, known registered sync roots, or recognizable OneDrive/SharePoint/Knowledgebase paths are rejected, as are symlink/junction redirections. On non-Windows development hosts set `LOCALAPPDATA` to a private nonsynchronized local directory. The fixed mailbox/cutoff/scope are centralized and intentionally not exposed as casual CLI overrides.

One OS-held lock allows one writer per database, and is released on process exit/crash. SQLite commits each listed page before fetching messages. Each message, attachment metadata, and work status commit together; a page token advances only after every queued item is durable. Identities are `(mailbox, Gmail message ID)` and `(mailbox, message ID, MIME part ID)`; thread ID and provider attachment ID are retained separately. Repeated imports upsert the same records. Metadata-only and inline attachments retain part identity even without a Gmail attachment ID. No content hashes are invented without bytes.

Gmail GET attempts are spaced at least 250 ms apart (at most approximately 4/second), including messages, list/profile overhead, and retries. The first request is immediate; network time counts toward the spacing, with no catch-up burst after a pause. At 20 quota units per message GET this uses at most approximately 4,800 units/minute, below the new-project 6,000-unit per-user/project limit; list/profile requests cost less and share the same pacing. Small validations use the same short spacing, without a startup cooldown. Other applications sharing the account/project can still consume quota. See [Google's current quotas](https://developers.google.com/workspace/gmail/api/reference/quota).

HTTP 403 `rateLimitExceeded` / `userRateLimitExceeded`, HTTP 429, and transient HTTP 500/502/503/504 receive up to **6 retries** (7 attempts total per logical GET). Backoff is `min(2^n + random(0..1), 32)` seconds, starting at 1–2 seconds; each retry draws new jitter. Daily-limit, domain-policy, authorization, unknown 403, and other non-transient errors stop immediately. Transport failures retain the existing stop-and-resume behavior. Only allowlisted reasons such as `gmail_rate_limit_exceeded`, `gmail_user_rate_limit_exceeded`, `gmail_daily_limit_exceeded`, `gmail_domain_policy`, or numeric `gmail_http_*` codes escape the adapter; provider error text/bodies are never logged or persisted.

After retry exhaustion or a non-retryable error, the importer saves a sanitized failure code and attempt count. SQLite counts logical processing attempts/final failures; individual HTTP retries remain internal to the adapter. Resume with the same `inventory` command, **without `--restart-pagination` for rate-limit recovery**. An inaccessible/deleted message (including HTTP 404) remains failed and blocks that page rather than being silently skipped. Expired list tokens can be restarted using the command above, provided no queued messages remain unfinished. Unresolved message failures require operator investigation; no destructive skip/reset command is supplied. Statistics distinguish discovery, scan failures/incomplete work, and actual original archival records. Resume Phase 2 with `archive`, without changing inventory checkpoints.

Gmail pagination is not a transactional snapshot. Rerun completed inventories to discover changes during or after a pass. Previously observed messages/attachments are retained even if absent from later lists; no deletion or comprehensive label-change reconciliation is claimed. A candidate seen as excluded is explicitly marked, but messages no longer returned by Gmail cannot have their current status inferred.

The Phase 1 metadata GET uses `format=full` with a field projection that excludes all `body.data`, raw bodies, and snippets. MIME metadata is requested through depth 20 plus a child sentinel; deeper/incomplete structures fail explicitly rather than silently omitting attachments. Subject and body content are not persisted by inventory. Only sender/recipient and threading/date header metadata is retained. Phase 2 byte acquisition is a separate explicit command.

For backups, stop the importer and make a private local copy of the SQLite file; never copy a live SQLite file or sync it to SharePoint. Back up before first using this version against existing state. The first authenticated writer opening state automatically migrates versions 1/2 to version 3 through additive transactions, preserving all inventory and checkpoints. `stats` can read versions 1, 2 and 3 without migrating them. Keep using the upgraded application afterward: older code rejects schema 3. No automated backup job is implemented. SQLite is required to interpret archive provenance; do not discard it after copying source files. Customer metadata remains sensitive even without bodies.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m elektro_vienna --help
git diff --check
```

Tests use synthetic providers, responses, fake clocks, and temporary local state/archive roots only. Live status (operator-reported, 2026-10-02): OAuth works for `office@elektrovienna.at`; two `validate --limit 10` runs succeeded with zero failures. Initial historical attempts exposed a generic HTTP 403 and later a transport failure. After quota pacing/retry hardening and normal resume, the historical inventory completed successfully: `completed: true`, **1375 discovered messages**, **2950 attachment occurrences**, `incomplete: 0`, and `last_error: null`. The historical failures counter remains **2**, recording prior stopped/failed attempts rather than current incomplete work. Phase 1 itself downloaded no bytes. Subsequent operator-reported Phase 2 limited runs archived 60 originals and 36 attachment occurrences with zero failures. The first full run then stopped with `attachment_bytes_unavailable`: the first pending occurrence was a zero-size multipart root without a provider attachment ID, which the previous inline path explicitly rejected. The inline reader now accepts a validated empty container body and retrieves non-empty inline data by exact part ID. After the inline fixes and normal resume, the operator reported full local archival completion on 2026-10-02.

Provider references: [Gmail raw source, timestamps and MIME metadata](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages), [attachment and inline body bytes](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.attachments), [Gmail query/time-zone semantics](https://developers.google.com/workspace/gmail/api/guides/filtering), and [Desktop OAuth flow](https://googleapis.dev/python/google-auth-oauthlib/latest/reference/google_auth_oauthlib.flow.html).

### Inline provider-size anomaly

A live inline part had stable MIME/filename/attachment identity and both inventoried and current Gmail size of **1111 bytes**, but valid base64url `body.data` decoded to **1118 bytes**. For inline/non-external parts only, successfully decoded bytes are authoritative once exact identity and unchanged provider size are verified. Bytes are never truncated, padded, normalized or otherwise transformed. A decoded-length discrepancy is recorded as non-fatal `provider_size_mismatch`; changed provider size, missing non-empty data, malformed encoding and identity mismatches still fail. External `attachments.get` size checks remain strict.

Schema 3 adds nullable `archive_files.anomaly`. The unchanged `attachments.size` retains the provider-reported size; `archive_files.byte_length` and `sha256` protect the actual bytes. Both records join on mailbox/message/part ID. The anomaly commits with the file record and remains on idempotent reruns. `stats` reports `archive.anomalies.provider_size_mismatch`, counting recorded anomalies even if a file later fails verification. Existing file verification still uses actual length/hash. The completed live run retained one such anomaly; it does not indicate incomplete archival work.
