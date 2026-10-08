# Tests

Run `.\.venv\Scripts\python.exe -m pytest -q` after installing `.[dev,pilot]`.

Pilot tests cover source hash verification, exact text-span provenance, contextual amount semantics, unknown/ambiguous matching, MIME alternatives and quoted history, PDF/HTML/DOCX parsing and explicit unsupported/scanned-document handling, immutable publication/recovery, idempotent replay, versioned reprocessing, attributable matching review, source/state preservation, no network/authentication, and restricted output paths. Pilot test inputs are synthetic; real runs are outside Git under the authorized Knowledgebase root.

Phase 1 tests cover exact Vienna cutoff boundaries, included/excluded labels, provider identities, recursive MIME metadata, idempotency, transactional recovery after failure/interruption, pagination, persistence, path restrictions, Desktop OAuth scope, limited validation, statistics, and absence of mailbox mutation capabilities.

All responses, credential fixtures, and messages are synthetic; SQLite files and synthetic OAuth JSON are created only in temporary test directories. No live Gmail access is exercised. Never commit customer documents, raw emails, PDFs, credentials, or runtime databases.

## Offline review UX

Current local workflow tests in `test_review_local.py` distinguish private mutable drafts from immutable completed reviews. They cover early autosave without lower-item acceptance, restart/reopen recovery, atomic-replace failure, reviewer/whole-case validation, independent source acknowledgement and thread/message exceptions, legacy comments-only migration without hidden statuses, stale requests, corrupt files, lost-acknowledgement retry, one completion per submission, changed-review history, completion-backed agent revisions and loopback HTTP boundaries. Real human reviews are never synthesized by tests. The older delivery notes below describe historical versions.

Final delivery validation (2026-10-08): full suite 247 passed; JavaScript behavior/syntax and package/dependency checks passed. Synthetic browser testing verified partial draft save, actual service restart/recovery, explicit completion guard, accepted case defaults only after completion, no implicit source confirmation and idempotent repeated completion. Real Woodward remains uncompleted for the human reviewer.

`test_review_ui.py` uses synthetic pilot fixtures to check the additive HTML presentation, quote folding with inline replies, exact-span provenance, immutable versioned feedback/import, escaping/CSP, stale and malformed exports, thread acknowledgment plus mixed-topic message exceptions, deterministic replay and absence of provider/auth/state writes. Run `node tests/test_review_js.cjs` for synthetic export/import script tests. Browser usability and human business interpretation are separate acceptance steps. `test_archive.py`'s explicit CLI command inventory includes all additive review commands; Phase 1/2 contracts remain unchanged.

`test_review_local.py` covers durable append-only drafts, reload/retry/conflict behavior, input scope/type validation, checksum corruption, loopback HTTP boundaries, original-source integrity, bounded PDF import, exact quote validation and exclusion of foreign-case documents. Only this synthetic HTTP test enables loopback sockets; it makes no provider requests. Run `node --check src/elektro_vienna/review_live.js` as an additional syntax check. The 2026-10-07 full suite has 230 passing tests. A separate actual browser check used a synthetic case to verify autosave, visible correction, reload and source navigation without creating fake real-case reviews.

The comment-only follow-up adds coverage for accepted-by-default case text versus unreviewed source associations, precedence of comments and old objections, durable comments, absence of replacement/rating controls, preserved static behavior, and checksum/case-bound prior-review links in agent revisions. Browser verification must exercise the new comment-only autosave and reload workflow; the earlier overlay check describes the superseded UI.
