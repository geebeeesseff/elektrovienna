# Tests

Run `.\.venv\Scripts\python.exe -m pytest -q` after installing `.[dev]`.

Phase 1 tests cover exact Vienna cutoff boundaries, included/excluded labels, provider identities, recursive MIME metadata, idempotency, transactional recovery after failure/interruption, pagination, persistence, path restrictions, Desktop OAuth scope, limited validation, statistics, and absence of mailbox mutation capabilities.

All responses, credential fixtures, and messages are synthetic; SQLite files and synthetic OAuth JSON are created only in temporary test directories. No live Gmail access is exercised. Never commit customer documents, raw emails, PDFs, credentials, or runtime databases.
