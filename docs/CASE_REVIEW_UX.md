# Single-case local review

## Current workflow: draft first, explicit completed review (2026-10-07)

Automatic saving is **work in progress only**. It creates no accepted case items and no confirmed source associations. The form still asks only for correction comments; the agent writes the next case revision. At the bottom, `Fallprüfung abschließen` requires the reviewer's name and the explicit acknowledgement that the entire displayed case has been reviewed and all uncommented case items may be treated as correct. Only that submission derives `accepted_by_default` for blank case-item comments and `correction_requested` for nonblank comments.

The optional separate checkbox `Die dargestellten Gespräche/Quellen gehören insgesamt zu diesem Fall` authorizes confirmation of the displayed source associations only. Without it, all thread/message associations remain `unreviewed`, even if case review is complete. With it, a comment on a message makes that message an exception; its thread is confirmed with exceptions. A comment on a thread flags that thread and leaves its uncommented messages unreviewed. Comments remain verbatim in both draft and completed review. No provider data or global KnowledgeItems are changed.

Storage and recovery:

- One mutable draft per view: `%LOCALAPPDATA%\ElektroViennaKnowledge\review\drafts\<view-id>.json`. It contains a checksummed `status: draft` record, technical revision, reviewer and comments only. Writes flush a temporary file and atomically replace the draft; stale changed writes conflict. No `90_manual_review` file is published by autosave. A reviewer name is required for completion, but an unnamed draft can still be recovered.
- Completed reviews: `90_manual_review/completed/<view-id>/<eight-digit-revision>.json` beneath the authorized Knowledgebase root. One immutable checksummed envelope per distinct submission records `kind: completed_case_review`, `status: completed`, view/baseline/case IDs, reviewer, server UTC completion time, both acknowledgements, comments, derived decisions, source exceptions, draft revision and prior-review/previous-completion provenance. The source membership comes from the immutable view, not browser-supplied membership.
- Repeated clicks/retries of the same draft revision and acknowledgements adopt the same completed artifact, including after a lost response. A changed draft can create a later immutable completion. The draft remains available after completion; edits invalidate the whole-case acknowledgement in the UI and require another explicit completion.
- Existing `sessions` history is never changed. If needed, its last comments/reviewer seed the new private draft; old hidden ratings, replacement prose, acknowledgements and acceptance states do not become new decisions. Optional JSON restore likewise restores comments only and requires a new completion acknowledgement. Prior historical static reviews retain their original contract.

The live service keeps the existing Woodward presentation/view ID `34ffb1628f00b7314c0782b65355a429b057189df94bfcdfe94c40c5ca8cd742`, Alectra comparison and evidence links. No new business interpretation or historical acceptance is fabricated for this workflow update. The original static HTML and older descriptions below are historical snapshots. Completed review artifacts can be bound as `prior_review` inputs to the next agent-generated view, with checksum and case/baseline validation; no automatic AI rewrite runs inside the local service.

Final verification (2026-10-08): 247 Python tests pass; JavaScript behavior/syntax, package build and dependency checks pass. Browser QA used a synthetic case: comment near the top, autosave with no acceptance of lower items, actual service restart and comment recovery, blocked completion without whole-case acknowledgement, successful explicit completion, unreviewed sources without separate acknowledgement, and repeated submission still producing exactly one immutable completion. Unit/HTTP tests additionally cover source confirmation/exceptions and failed-publish retry. The real Woodward service was restarted and inspected with both acknowledgements unchecked; no completed human review was fabricated. All 86 captured previous source/state/review files remain byte-identical, all 98 live source/navigation links remain local and correctly scoped, and the five-document comparison remains available. Use `Start-Review.cmd` to reopen the current service; its capability URL changes on restart.

## Previous workflow: comments and agent revision (superseded completion semantics)

Giovanni's next review explicitly simplifies the live form to **one correction-comment field per entry**. He does not write replacement case text or choose a rating/stage. Short notes and spelling errors are acceptable. Comments save automatically; the agent interprets them and publishes a new, source-cited case revision in the next requested iteration. The local service itself does not run an autonomous AI worker.

Under the explicitly authorized rule, a case-text item without a comment or an existing explicit objection is accepted for this case review. A comment takes precedence over a blank/correct rating; a legacy explicit objection remains unresolved until addressed. This rule neither accepts source matches nor promotes general company knowledge. Source comments remain optional. The UI states this default plainly; no replacement-text field, apply checkbox or rating selector is shown in live mode.

Session records retain `review.mode = correction_comments_v1` and per-field effective interpretations. Old drafts, ratings, comments and optional overlays remain readable and byte-preserved; old metadata is retained when a legacy session is resumed, but no silent text overlay is applied. Static export/import continues to support its original rating contract. A revised view binds the exact prior session reference and record checksum; publication verifies the checksum and same-case/baseline context. Processed comments remain in immutable feedback and visible item notes. New comments concern the newly rendered text.

The first such iteration uses Giovanni's saved revision 11 plus the subsequent chat clarification: no follow-up work occurred; the E-Befund was deliberately not performed for the economic reason he supplied; the technician received cash and later corrected his VAT mistake. The historical mail's differing payment wording and technical alternatives remain source evidence rather than being erased. The 21 previously unmarked entries are recorded as accepted by the user's default rule, distinct from agent changes and explicit corrections. Earlier source and feedback files remain intact.

Current view: `34ffb1628f00b7314c0782b65355a429b057189df94bfcdfe94c40c5ca8cd742`; feedback: `de1f1898e2385802d692534f8c3795b17c3247e9310016f5b051c1cd8552af1b`. The configured launcher opens this view. Validation: 233 Python tests pass, JavaScript checks pass, package/dependency checks pass, and wheel assets equal source files. Synthetic browser testing confirms comment-only autosave and restoration, with commented items flagged for revision, unmarked case items accepted by default and source associations still unreviewed. The real view was inspected without entering synthetic feedback. Replay produces the same IDs; 98 view links resolve; all 82 captured previous files, including the user's 11 review revisions, retain their hashes.

## Initial local-service delivery (2026-10-07, superseded interaction)

Giovanni explicitly selected a local service with a start file. Open `Start-Review.cmd` in the repository; it starts the loopback service or reopens its running view. The private `%LOCALAPPDATA%\ElektroViennaKnowledge\review\launch.json` selects the immutable view. Runtime URL and diagnostic files remain there, outside Git. The service runs on this computer only (`127.0.0.1:8765`); the old directly opened HTML remains a static snapshot.

Enter a reviewer name once. Changes save automatically after a short pause; the visible saved revision confirms disk persistence. `Jetzt speichern` saves immediately. Reopening loads the latest revision. Comments are saved even before choosing a judgment. Under an item's `Belege & Bewertung`, a replacement text plus its apply checkbox updates the displayed interpretation. Optional stage corrections apply with that replacement. A comment alone does not trigger an AI rewrite. Original interpretations remain expandable, and previous feedback revisions remain immutable. Reviewer names are self-declared, not authenticated signatures.

The service appends `90_manual_review/sessions/<view-id>/<eight-digit-revision>.json`. Each envelope has a canonical record checksum, previous-record checksum, UTC save time, reviewer, view ID, revision and complete field snapshot. Stale writes conflict rather than overwrite; identical retries do not create duplicates. Draft associations do not silently accept source matches or promote knowledge. JSON download remains an optional backup, with same-view restore; it is no longer the required persistence mechanism.

Source citations now link directly to the relevant readable mail and open its conversation. Original EML/PDF links retain byte-checked provenance. A separate linked Alectra overview covers only the five supplied PDFs: two documents for the reviewed case and three for separate customer contexts. The original visit invoice and the later archived correction remain different versions; an old invoice remainder is not treated as current debt. The newer human recollection suggests no follow-up work, but the final execution stage stays uncertain. No additional pilot cases were rendered or automatically associated.

Manual originals use `00_raw/manual_documents/<sha256>.pdf`, parser-version-bound text uses `20_extractions/manual_documents/<sha256>.json`, and the comparison uses `30_cases/document_review/<collection-id>/index.{json,html}`. Exact quoted page text supports curated claims. Original download paths, supplier documents and customer content remain outside Git.

Delivery references:

- View: `5b6970985db6b60eb36f85139558ef3107a409b5ab81568e832c9d8f1572df4b`.
- Document collection: `431718b31f7891b5e87ce18deb1eb7b2f7dff4d3ea109c98f45574786dc5ef9d`.
- Updated attributable feedback: `7f71d209f13ebe397506a20a0bd78b56d4ba85a39142740752c8e9423d799ffa`.

Validation: 230 Python tests pass, JavaScript behavior/syntax checks pass, package build and dependency checks pass. Actual browser testing on a synthetic case verified automatic saving without download, a visible applied correction, persistence after reload, and a direct mail jump. The actual case and document overview were opened and inspected without inventing human review decisions. This supersedes the previous file-URL browser-validation limitation for the new local service. Remote SharePoint synchronization is not independently verified.

Final replay returned the same view, comparison and feedback IDs. All 69 captured prior files retained their hashes, including the previous view/feedback, baseline/extraction/snapshot, SQLite and 62 original sources. All five supplied PDFs match their preserved copies byte-for-byte. All 98 links in the static case and six comparison links resolve. The PowerShell launcher passes syntax validation; the running service returns both pages and the current draft successfully. The live case had revision zero at delivery validation: only the separate synthetic case received test feedback.

## Previous static delivery (2026-10-06)

The following records the initial static delivery and its validation boundary. Its download-only persistence and earlier work interpretation are historical; the local service above is the current workflow.

The first human review found the technical Markdown unsuitable for product evaluation. Pilot expansion is paused. Render and evaluate the Woodward case first; do not render the remaining 24 cases until the operator finds this experience satisfactory. This iteration does not complete human validation.

## Experience and interpretation

`review.html` is a self-contained local file with embedded CSS/JavaScript. Open it directly in a browser; no server, external assets, authentication, API calls, telemetry or external customer-data transmission are required. A restrictive content security policy permits only the embedded script/style hashes. Email/source content is escaped text, never executable HTML. Original EML downloads are local relative links inside expanded provenance.

The primary order is identity/current CRM status, customer intent, short chronology, important technical facts, commercial concepts, staged outcomes, uncertainties, then collapsed conversation sources. Evidence and evaluation controls are inside each item's “Belege & Bewertung”. The page shows certainty and qualifiers before asking for a judgment. Source associations remain candidate associations; the supplied human feedback does not silently accept messages.

The initial presentation is **manually curated**, with verified exact extraction spans and attributable human feedback. It is not a new general-purpose semantic extractor. It consolidates repeated candidate occurrences by their meaning, scope, tax basis and commercial role, above the unchanged raw candidate layer. An estimate, invoice, hourly rate and potential saving are different concepts even when their numbers coincide. A technician's customer quote is not automatically an internal cost or company-wide price rule.

The stage statuses are `completed`, `commissioned`, `declined`, `lost`, `not_performed`, and `unknown`. Completion applies to the named stage only. In the validation case, inquiry clarification, visit and offer preparation are completed; later execution remains `unknown`, with possible limited work attributed to Giovanni. The historical pilot's broad inferred `completed` value is preserved in its original artifact and is **not used as proof of execution in this view**. No database or general Case schema migration is introduced.

## Readable conversations

Messages group by the existing Gmail thread ID, with sender/recipient names, subject, date range, count and chronological message bodies. Same-subject independent threads are not merged. Display dates explicitly use Europe/Vienna. Message IDs, archive hashes, locators, competing-case signals and all attachment occurrence metadata remain expandable.

Display-only normalization handles CRLF. Explicit `>` quote blocks collapse. After a recognizable reply-history header, paragraphs are compared with earlier messages in that conversation after quote-prefix/formatting normalization; repeats collapse, unmatched paragraphs remain visible so new inline replies are not discarded. Forwarded-only content stays visible when no earlier source supports collapsing it. Folding is conservative and heuristic, not lossless semantic thread reconstruction: all text is retained and expandible; original EMLs and extraction spans never change. The existing extraction's selected readable MIME section is displayed; other sections and unparsed attachment issues remain in the preserved extraction, not re-extracted here.

## Review and immutable persistence

An item supports correct / incorrect / partially correct / unsure / important information missing plus a comment. A conversation or message supports belongs / does not belong / unsure. A definite conversation decision requires a checkbox explicitly applying it to every shown message except individual overrides. A mixed-topic conversation can therefore have message-level exclusions; grouping alone never accepts a thread. The importer expands only the message membership stored in the exact immutable view, not membership supplied by the browser.

Name and timestamp identify each exported review. Empty choices remain unreviewed, and partial review is allowed. Comments require a selected judgment so they cannot silently disappear. Download creates a timestamped JSON file; resuming a same-view JSON restores choices/comments. Inputs live in browser memory until downloaded; there is no automatic localStorage or autosave. The browser cannot itself append to the archive. Import the downloaded JSON to preserve the exact original bytes and a separately content-addressed evaluation envelope. Reimporting identical bytes is idempotent; corrections produce a new record. Evaluations do not update baseline candidates, Airtable, prices or KnowledgeItems, and contradictory versions coexist for later human resolution. Reviewer names are declarations, not authenticated signatures.

```powershell
# Curation JSON and browser exports must be in private LocalAppData, outside Git.
.\.venv\Scripts\python.exe -m elektro_vienna review-render --baseline <existing-run-id> --case-id <one-ticket-id> --presentation "$env:LOCALAPPDATA\ElektroViennaKnowledge\review\presentation.json"
.\.venv\Scripts\python.exe -m elektro_vienna review-import --view-id <view-id> --decisions "$env:LOCALAPPDATA\ElektroViennaKnowledge\review\downloaded-review.json"
```

The HTML footer's technical section contains the view's source references; its JSON export fills IDs automatically. Giovanni does not need to author JSON or copy message IDs. An operator may perform the optional archive import after receiving his downloaded file. These commands run before runtime configuration/authentication and do not open SQLite or rerun the pilot.

## Artifacts and contract

Only beneath the existing authorized Knowledgebase root:

- `90_manual_review/historical_pilot/<sha256>.json`: attributable human evaluation containing the verbatim supplied feedback, its input hash, received date, case/baseline context and an explicitly unknown original review timestamp; later raw browser exports and evaluation envelopes use the same immutable namespace.
- `30_cases/review_ux/<view-sha256>/review.html`: local primary review artifact.
- `30_cases/review_ux/<view-sha256>/view.json`: completion manifest, published last. Its canonical bytes determine the view ID. It binds the baseline file hash, extraction/snapshot references, feedback hash, renderer and asset hashes, item spans, current CRM identity, conversation membership and message/source provenance.

The private presentation input contains `case_id`, `baseline_id`, `human_feedback` (`reviewer`, `received_on`, `verbatim`), optional `curation`, and lists under `sections`: `summary`, `narrative`, `technical`, `commercial`, `outcome`, `uncertainties`. Each item has a unique safe `id`, `title`, `text`, `certainty`, and nonempty `source_refs`; outcomes also have `status`. Each reference contains `source_id`, `locator`, `start`, `end`, and exact `quote`. The reserved source ID `human_feedback` and locator `verbatim` resolve against the preserved feedback text; other spans must resolve against sources belonging to the selected case. Source/extraction bytes are never rewritten. No whole-mailbox scan or additional parsing is performed.

## Validation boundary

Synthetic tests cover publication/replay, immutable imports, exact spans, out-of-case sources, unsafe content escaping, CSP, stale/malformed reviews, timestamp attribution, scope acknowledgments, mixed-topic exceptions, quoted-history retention and prohibited network/auth/state side effects. Run `node tests/test_review_js.cjs` for the shipped script's export/import behavior against synthetic DOM doubles; this is not a browser rendering test. Package builds must include both assets. Human reading-time/usability and correctness remain for Giovanni to judge; automated checks cannot certify the 2–5 minute goal.

Delivery verification (2026-10-06): 220 Python tests pass; the JavaScript synthetic behavior checks and syntax check pass; sdist/wheel build and `pip check` succeed. The wheel contains the exact shipped renderer, CSS and JavaScript. One Woodward view contains 24 curated entries (797 primary words), 7 conversations and 23 messages. Quoted-history folding conserves every normalized message text while reducing initially visible body text from 192,773 source characters to 31,027 characters. The view is a manual consolidation of 103 technical and 72 commercial candidates, not a claim of automated semantic deduplication quality.

Immutable view ID: `a9d77e56f12641b82bc468a2c9dadbc09e7954d8189de92b412d8c722dd4aafe`. Feedback ID: `f331cbb0d210a48d9e188c328f517c4bd8fef27505bdcae1f866764466f346a3`. Both use the paths above. A repeat render adopted identical files. All 62 associated original source files plus the baseline JSON/Markdown, extraction, snapshot and SQLite file (67 files total) retained their prior hashes. All 23 local original-message links resolve, CSP hashes match embedded assets, and the human feedback text matches the supplied prompt verbatim. No new browser review was invented or imported on Giovanni's behalf.

Automated visual/browser interaction validation was **not completed**: the browser tool's URL security policy rejected the local `file://` URL. No workaround server or alternate browser automation was used. Structural HTML and script tests passed, but browser rendering/download behavior and the operator's actual reading experience remain explicitly unverified. Remote SharePoint synchronization is also not independently verified.
