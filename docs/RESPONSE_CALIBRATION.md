# First-response calibration (2026-10-14)

**Completed later on 2026-10-14:** Giovanni returned all twelve decisions:
eleven same, one different (Irene), retaining style while rejecting old prices.
The exact export and separate follow-up instruction are preserved; the
[playbook and three-case validation](FIRST_RESPONSE_VALIDATION.md) describe the
next explicitly authorized step. The remaining text records the earlier
calibration-set delivery; its pending-review statements apply to that checkpoint.

This bounded phase preserves Giovanni's completed three-case human A/B review
and prepares twelve historical inquiry/first-response pairs for his review.
Historical replies are evidence of style and process, not current policy.
No more full historical cases are reconstructed or reviewed. Current Operating
Policy and historical matching remain unchanged. Distilling a compact First
Response Playbook is a separate next step after human calibration.

## Completed human A/B review

The exact bytes of `antwortvergleich-bewertung.json` were published before
reading the blinded mapping. The attributable result binds the original export,
frozen experiment and mapping checksums, and separate current owner feedback.
Original comments and equal preferences are preserved without reinterpretation.

| Case | Policy-only | Policy + Reviewed History | Human preference |
| --- | --- | --- | --- |
| Irene | B | A | Equal |
| Rebekah | A | B | B: Policy + Reviewed History |
| Christoph | B | A | Equal |

One History preference and two ties in this tiny, selected sample do not
establish a meaningful or generalizable History advantage. Neither secondary
assistant scores nor these three choices justify building the live agent or
expanding historical reconstruction.

The owner's case-specific fan estimate, immediate address/availability request,
preference for sufficient explanation, and body/sign-off vs Outlook footer rule
are preserved as **owner feedback**. They are not inferred from historical
behavior, promoted into general prices or applied to policy.

## Selection and source boundary

`response_calibration.scan_archive` uses the existing read-only SQLite snapshot
reader, immutable original-message checksums and unchanged local MIME parser.
It scans the already archived 2026 Gmail cohort: 1,375 messages in this run.
No provider request, new extraction pipeline or CRM association is introduced.

Selection is agent-curated and nonrandom. The twelve examples include direct pricing,
customer-supplied components, one/two-person considerations, technical questions,
internal Meister review, site visits, wallbox work, B2B, English replies and
complex kitchen work. Short, long and commercially incomplete responses are
retained. Later outcomes are not used to select or grade examples. Some inquiries
appeared in earlier experiments/reviews; only the original inquiry and original
first outbound response are used here.

Every admitted pair requires:

- Original inbound followed directly by the first archived thread outbound,
  matching RFC Message-ID/In-Reply-To and a customer recipient.
- No intervening message or earlier archived contact with that exact customer
  address across threads. Internal forwards and apparent follow-ups are excluded.
- Full `Giovanni Superti-Furga` signature in newly written text, before quoted
  history, with no Josef/Schenk/Sascha/representation attribution there.
- Source inspection by the selecting agent of the first reply as substantive and attribution as
  unambiguous; the shared Office sender alone is insufficient.

All twelve selected responses have the full Giovanni signature; zero are Josef
or unclear. This is signature-based attribution, not forensic proof of the
keyboard operator. Archive coverage cannot exclude calls, aliases or missing
correspondence outside the cohort.

Original emails remain immutable. Display text projects one decoded MIME section:
CRCRLF/CRLF/CR become LF; a recorded offset excludes quoted history. Exact source
hash, length, message/thread IDs, headers, MIME locator, parser version and
projection rule remain in `set.json`. Historical signatures are retained.
Attachment names are listed; photos/documents are not rendered or technically
reinterpreted. This page calibrates response text, not the correctness of unseen
attachments or historical technical/pricing claims.

## Simple local page

One static `calibration.html` shows both emails, Vienna-local dates and signature
attribution. Each example has only two radio choices and an optional comment.
No rewritten email, later history, grading or new review server is requested.

`Bewertung herunterladen` exports `erstantwort-kalibrierung-bewertung.json`.
Partial reviews are allowed and explicitly incomplete. Unanswered choices remain
null; comments are verbatim. No autosave or remote transmission occurs. The page
indicates unsaved changes and asks the browser to warn before leaving. The human
must retain the file: download success cannot be inferred from a click event.
Review ingestion/playbook derivation awaits Giovanni returning that file.

## Artifacts and replay

Runtime/customer content stays under the authorized Knowledgebase, outside Git.
`CalibrationStore` reuses the guarded atomic no-replace publisher:

- `90_manual_review/response_calibration/<sha256>/ab-original.json`
- `90_manual_review/response_calibration/<sha256>/owner-feedback.json`
- `90_manual_review/response_calibration/<sha256>/ab-result.json`
- `30_cases/response_calibration/<set-sha256>/calibration.html`
- `30_cases/response_calibration/<set-sha256>/set.json` (published last)

The result manifest is
`90_manual_review/response_calibration/02159e2e6f66e3d38fdda0792a0de71b15e0b23fc800dfbe169a8764b7011827/ab-result.json`.
The published set ID is
`9fc5398acee8c57c9e1081245a7c0a354eda1e62c1d7c264acb902902cb1a561`.
Its identity binds inventory/state hashes, source projections, selection reasons,
prior feedback and publisher/JavaScript hashes. Replay is byte-identical. A
change creates a separate immutable set. All 1,458 protected pre-existing
source/review/policy/state files retained their hashes after publication.

The narrow Python API is `ingest_ab(store, raw_bytes, owner_feedback)`, then
`scan_archive(state_path, archive)` and
`publish_set(store, archive, inventory, selections, ab_result_reference)`.
Selection entries contain `inquiry`, `reply`, `title`, and `selection_reason`.
There is no broad selection automation or provider-enabled CLI mode.

Validation covers source integrity, first-reply/recipient boundaries, earlier
cross-thread contact, signature contamination, escaping, future-history
exclusion, immutable replay, and partial/complete JavaScript exports. No Gmail,
Airtable/CRM or Calendar write occurs. Remote SharePoint sync remains unverified.
The local-file browser restriction remains in effect; static HTML and synthetic
export checks do not prove visual layout or actual browser download behavior.

Technical checks: 325 Python tests pass, including 18 calibration tests. All
three JavaScript behavior suites pass. Dependency checks and sdist/wheel builds
using installed build dependencies pass; the isolated build was cancelled while
waiting for dependency installation. Static inspection verifies all 24 displayed
email bodies against the manifest, twelve two-choice/comment controls, the CSP
script hash and absence of remote resource links. No human review choice has
been filled in on Giovanni's behalf.
