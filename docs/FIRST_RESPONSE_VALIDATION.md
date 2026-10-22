# First Response Playbook: three-case validation

**Completed 2026-10-15:** Giovanni approved 3/3 as “Würde ich so schicken”,
with no requested edits and no rejected response. Exact export and owner-request
bytes are preserved, bound to the unchanged packet and frozen answers, by
`90_manual_review/live_shadow/374a94e9abaeeafc95315e8b0d17ee647c109d165036ee4202ec164903f909ca/result.json`.
This meets the owner's gate to stop historical testing, without statistical
claims. The [first live shadow pilot](LIVE_SHADOW_PILOT.md) supersedes the pending
review/live-agent restrictions in the preparation record below only within its
explicit read-only, manually executed scope.

Checkpoint **2026-10-14**. The owner completed calibration: eleven approximately
same responses, one different response (Irene), explicitly retaining style while
rejecting the historical price. Exact export bytes, including original comment
typos, and the separate owner request are preserved under
`90_manual_review/response_calibration/773c546697d980f2b2e5720af4f249269510bc04807b1d1fdd0de8ffd1d8ad89/calibration-result.json`.
No current price is inferred from a same-style choice.

The resulting [First Response Playbook](FIRST_RESPONSE_PLAYBOOK.md) has eighteen
practical rules. The separate [Current Price Book](CURRENT_PRICE_BOOK.md) contains
only the seven owner-reaffirmed existing policy entries. Both are version
`2026-10-14.1`; Current Operating Policy JSON and documentation remain unchanged.
The repository current-price check found no conflicting numerical tariff.

## Bounded inputs

Exactly three initial inquiries: bathroom ventilation electrical connection,
B2B office lighting, and a shared-garage wallbox. All are outside the twelve
calibration examples and the previous three blind cases. Previously screened
Giovanni reply threads are also excluded. A phone-follow-up candidate was rejected.

Selection uses archived thread/sender metadata and initial-source contents only.
The answer writer receives the immutable T0 packet: original inquiry, its own
initial attachments and checksummed current policy/playbook/price-book/NORTH_STAR.
No outbound body, later attachment, later CRM state or retrospective is accessed
for these cases. No additional reviewed-case cards were needed. Metadata used
for selection is not an OS-level blind sandbox or evidence of unseen outcomes.

The initial-message checks require no reply/reference headers, no forward/reply
subject prefix, earliest archived thread position and no earlier exact-address
contact. To, CC or original Delivered-To may establish inbound delivery: the
bathroom inquiry was addressed to undisclosed recipients, with office delivery
explicitly preserved in its original header. Attachment filename/hash occurrences
must exactly match the initial MIME payload, and each source is byte-verified.
Calls, aliases and unarchived correspondence cannot be excluded absolutely.

The B2B initial supplier PDF and room photo were inspected, including all three
PDF pages. They inform equipment/scope only. Their historical commercial values
do not seed the price book or appear as current labor/material prices. The page
embeds the original photo and offers the original PDF as a local data download.

## Outputs and feedback

Runtime content stays outside Git beneath the authorized Knowledgebase:

`30_cases/first_response_validation/bfade37d798e1f4d78884d7e3e4fe7ee47e85716c8fc927ea0ddf97c1f6ba509/`

- `packet.json`: exact T0 sources, cutoff, exclusions, guidance and provenance.
- `answers.json`: all three frozen drafts, source references, internal checks
  and explicit current policy price-item IDs. Changed drafts cannot overwrite it.
- `review.html`: one static inquiry → proposed-response page, with three choices
  and one optional comment per case. No scoring or later case history.

The downloaded `erstantwort-validierung-bewertung.json` records the packet ID,
frozen answers checksum, reviewer, timestamp, per-case `send`, `minor_change`,
`reject` or null, exact comments and completeness. Partial downloads are allowed.
There is no autosave, local review service or automatic transmission. The UI
labels downloads as started, not proven saved. The existing calibration export
behavior remains compatible. Human review ingestion is deferred until return.

The module reuses original archive checks, existing MIME parsing, immutable
publication and the small calibration export script. It does not change matching,
extraction, policy authority, SQLite state or provider integrations. A calibration
address helper now omits absent optional headers before parsing, preventing strict
address parsing from hiding a real recipient; the original twelve contact checks
still return zero earlier contacts. This is not a historical matching change.

No Gmail/Airtable/Calendar write occurred. No live Shadow Agent is built.
Human acceptance of these three drafts is pending. Browser rendering/download
verification remains unavailable under the local-file URL restriction; static
structure and synthetic export checks are separate from visual/browser QA.

## Verification

341 Python tests pass, including sixteen new playbook/validation checks; the
34 focused calibration/validation tests also pass. All three JavaScript suites,
dependency checks and sdist/wheel builds with installed dependencies pass.
The shipped modules/export asset match source bytes. Static checks verify all
six displayed texts, nine unselected rating controls, three comments, five
original source checksums, both embedded attachment payloads, frozen-answer
binding, CSP script hash and absence of remote resources. Identical publication
replays unchanged. All 88 protected prior derived/review/policy/state files
retain their hashes. Technical completion is separate from Giovanni's pending
review of the three proposed responses.
