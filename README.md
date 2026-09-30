# Elektro Vienna Knowledge System

Operational learning: inquiry → understanding → pricing → execution → outcome → reusable knowledge.

Phase 1 implements **read-only historical Gmail metadata inventory** for `office@elektrovienna.at`. Discovery is not original archiving or established knowledge. No email bodies, attachment bytes, source archives, classification, extraction, Airtable, SharePoint writes, Outlook Calendar, or customer communication are implemented.

Start with [AGENTS.md](AGENTS.md), then read:

- [North Star](docs/NORTH_STAR.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Conceptual data model](docs/DATA_MODEL.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)

## Installation (PowerShell, Python 3.11+)

Run from this repository with a locally installed Python:

```powershell
py -3 -m venv .venv
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

# Local read-only statistics; no OAuth, network request, or new database.
.\.venv\Scripts\elektro-vienna.exe stats

# If Gmail rejects an expired page token, explicitly restart pagination, retaining records.
.\.venv\Scripts\elektro-vienna.exe inventory --restart-pagination
# The same recovery is available for a validation checkpoint.
.\.venv\Scripts\elektro-vienna.exe validate --limit 10 --restart-pagination
```

Equivalent module invocation: `.\.venv\Scripts\python.exe -m elektro_vienna stats` (and the same subcommands).

The inclusive cutoff is **2026-01-01 00:00:00 Europe/Vienna**, equal to **2025-12-31 23:00:00 UTC**. A query beginning one second earlier avoids the exclusive `after` boundary; final eligibility uses Gmail `internalDate` in milliseconds. Received, sent, archived, reply, and forwarded messages are eligible without sender/content/relevance filtering. Spam, trash, and drafts are excluded in both the query and metadata checks. The Date header is never used to enforce the cutoff.

Validation and historical inventory have separate checkpoints but share deduplicated messages and attachment occurrences. Validation reads at most N qualifying messages per invocation, counting previously inventoried qualifying messages too; excluded candidates may require additional reads. It resumes its own pending page on the next validation run. It does not claim the historical inventory is complete. After a scan completes, its next invocation starts a fresh reconciliation pass without deleting records. Interruptions preserve committed progress. Exit codes: 0 successful/limited run, 1 failure, 2 invalid CLI arguments, 130 interruption.

## State, failure recovery, and limitations

Default state: `%LOCALAPPDATA%\ElektroViennaKnowledge\state\pipeline.sqlite3`.

`EV_CREDENTIALS_DIR` and `EV_STATE_PATH` override these paths, **only within the actual Windows LocalAppData directory**. Paths inside Git, known registered sync roots, or recognizable OneDrive/SharePoint/Knowledgebase paths are rejected, as are symlink/junction redirections. On non-Windows development hosts set `LOCALAPPDATA` to a private nonsynchronized local directory. The fixed mailbox/cutoff/scope are centralized and intentionally not exposed as casual CLI overrides.

One OS-held lock allows one writer per database, and is released on process exit/crash. SQLite commits each listed page before fetching messages. Each message, attachment metadata, and work status commit together; a page token advances only after every queued item is durable. Identities are `(mailbox, Gmail message ID)` and `(mailbox, message ID, MIME part ID)`; thread ID and provider attachment ID are retained separately. Repeated imports upsert the same records. Metadata-only and inline attachments retain part identity even without a Gmail attachment ID. No content hashes are invented without bytes.

API errors stop immediately and save a sanitized failure code and attempt count. Retry by rerunning the same command; there is no unbounded automatic retry. An inaccessible/deleted message (including HTTP 404) remains failed and blocks that page rather than being silently skipped. Expired list tokens can be restarted using the command above, provided no queued messages remain unfinished. Unresolved message failures require operator investigation; no destructive skip/reset command is supplied. Statistics show discovered/excluded counts, attachment occurrences, incomplete work, failure counts, and `originals_archived: 0`.

Gmail pagination is not a transactional snapshot. Rerun completed inventories to discover changes during or after a pass. Previously observed messages/attachments are retained even if absent from later lists; no deletion or comprehensive label-change reconciliation is claimed. A candidate seen as excluded is explicitly marked, but messages no longer returned by Gmail cannot have their current status inferred.

The metadata GET uses `format=full` with a field projection that excludes all `body.data`, raw bodies, and snippets. MIME metadata is requested through depth 20 plus a child sentinel; deeper/incomplete structures fail explicitly rather than silently omitting attachments. Subject and body content are not persisted. Only sender/recipient and threading/date header metadata is retained.

For backups, stop the importer and make a private local copy of the SQLite file; never copy a live SQLite file or sync it to SharePoint. No automated backup job or remote storage is implemented. Schema version 1 is checked; future incompatible schemas require a documented migration. Customer metadata remains sensitive even without bodies.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m elektro_vienna --help
git diff --check
```

Tests use synthetic providers, responses, and temporary local state only. They do not validate live Gmail access. A real validation still requires the Desktop OAuth file and successful consent for the target mailbox. Phase 2 archiving remains unimplemented.

Provider references: [Gmail timestamp and MIME metadata](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages), [Gmail query/time-zone semantics](https://developers.google.com/workspace/gmail/api/guides/filtering), and [Desktop OAuth flow](https://googleapis.dev/python/google-auth-oauthlib/latest/reference/google_auth_oauthlib.flow.html).
