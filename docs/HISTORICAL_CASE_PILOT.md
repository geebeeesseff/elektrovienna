# Historical Case Reconstruction Pilot

This is the first product-value slice after the validated Gmail inventory and immutable archive. It starts with real Airtable tickets and reconstructs reviewable, source-backed cases. Offer mining supports the inquiry-to-outcome loop; no single quote becomes company pricing knowledge.

## Commands

Install the existing application and optional local PDF parser:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev,pilot]'

# Use a captured Airtable snapshot stored privately outside Git.
$snapshot = Join-Path $env:LOCALAPPDATA 'ElektroViennaKnowledge\pilot\airtable-snapshot.json'
.\.venv\Scripts\python.exe -m elektro_vienna pilot --snapshot $snapshot --limit 25 --dry-run
.\.venv\Scripts\python.exe -m elektro_vienna pilot --snapshot $snapshot --limit 25
```

The snapshot must already exist; the command does not simulate a live Airtable connection. `--dry-run` evaluates sources without publishing files. The normal command writes only to the already authorized Knowledgebase root. Both commands read existing SQLite/archive data, do not migrate state, and do not authenticate Gmail. `--limit` accepts 1–30 for testing/small subsets; the actual pilot target is 20–30. Console output contains counts and artifact paths, not customer text. Preserve the source snapshot for replay.

## Capture contract

Use read-only Airtable connector tools (or an explicitly recorded read-only export) to inspect the real base and its schema. Do not guess IDs. Resolve `Tickets (main)`, `Customers`, and `Craftsmen`, inspect the linked-table and select-field configurations, and page through the chosen ticket population. The initial capture enumerated the `Gewerk = Elektrotechnik` population. Required ticket fields are `Name`, `Customer`, `Craftsman`, `Gewerk`, `Service type`, `Customer request`, `Status`, `Notes`, `Customer Request Date`, `Closed Date`, and `Ticket volume (net)`.

Preserve these JSON keys:

| Key | Content |
| --- | --- |
| `format_version` | Integer `1` |
| `base_id` | Actual stable Airtable base ID |
| `captured_at`, `capture_method` | Zoned capture timestamp and how the read-only capture was made |
| `schema.tables` | Original table IDs/names and fields with stable IDs/names/types |
| `field_config` | Retrieved select choices, linked-table relationships and currency configuration where inspected |
| `selection` | Capture query/population/pagination scope and proposed selected IDs; the CLI recomputes its deterministic sample |
| `tickets`, `customers`, `craftsmen` | Records with `id`, `createdTime`, `cellValuesByFieldId`; retain original select/link values rather than flattening their IDs |

`pilot_sources.select_tickets(AirtableSnapshot(...), limit)` is the authoritative selector. Capture each selected ticket's linked Customers and Craftsmen by its actual ID before final execution. Customer fields include `Name`, `Email`, `Adress` (actual spelling), and available company/phone/notes; craftsman fields include `Name`, `Email`, and available company/phone/notes. Missing linked records remain explicit gaps, never invented identities. A larger/new sample may require another capture. This implementation deliberately uses a captured-source adapter; unattended Airtable authentication/pagination is not implemented in the CLI.

Selection favors coverage of observed service/status combinations and then available context. This helps learning but overrepresents rich records and rare service labels; it cannot estimate population-level conversion rates. Lost, commissioned, completed, offered, inquiry and standby statuses are preserved as actually reported.

## Output and provenance

All real customer content stays outside Git under:

```text
C:\Users\giova\Balun Energy\Balun Energy - Dokumente\06 Elektrovienna\07 Playground\Knowledgebase
    00_raw/airtable/<snapshot-sha256>.json
    20_extractions/historical_pilot/<extraction-sha256>.json
    30_cases/historical_pilot/<run-id>/cases.json
    30_cases/historical_pilot/<run-id>/review.md
    90_manual_review/historical_pilot/<review-sha256>.json
```

The report links to original sources, the preserved snapshot and full machine-readable records. Each Case retains ticket/customer/craftsman references, source-reported scope/notes/status, chronology, inferred outcome, missing information, match proposals and contextual technical/commercial candidates. The JSON retains every candidate; the report shows up to 20 per technical/commercial section and states when more exist.

To answer “Where exactly did this value come from?”:

1. Follow its `source_refs` entry in `cases.json`.
2. For Airtable, follow `snapshot_reference` and `json_pointer` to the original captured field. The locator also retains stable base/table/record/field IDs.
3. For email/documents, resolve `source_id` in the extraction's `sources` manifest, which carries the immutable archive reference, source locator, actual SHA-256, byte length, occurrence identity and anomaly if any.
4. Open the corresponding decoded section/page/paragraph in the extraction. `start`/`end` are Unicode character offsets and `quote` is the exact cited span. Parser locators are not raw-byte offsets or Gmail part IDs.
5. For provider chronology/thread signals, consult the extraction's `inventory_scope`. Timestamp conversion is a declared derivation; it does not change the original.

Explicit source facts describe what the source says. Inferred outcomes remain inferred. Technical/commercial mentions are uncertain candidates, with net/gross labels and labor/rate/material/travel/price-type words retained as context. Ticket volume is not treated as an offer total. No inferred issuer, automatic VAT calculation, accepted customer price, global rate or knowledge rule is created.

## Human matching review

Review the source signals and competing ticket IDs before accepting an association. Save a review JSON outside Git in private LocalAppData storage. Example (replace placeholders with IDs from the actual output; do not use this as a completed review):

```json
{
  "base_run_id": "COPY_BASELINE_RUN_ID",
  "reviewer": "Your name",
  "timestamp": "2026-10-02T18:00:00+02:00",
  "decisions": [
    {
      "case_id": "COPY_TICKET_ID",
      "message_id": "COPY_MESSAGE_ID",
      "decision": "accept",
      "rationale": "Explain which source identifies this inquiry and why competing cases do not apply."
    }
  ]
}
```

```powershell
$review = Join-Path $env:LOCALAPPDATA 'ElektroViennaKnowledge\pilot\match-review.json'
.\.venv\Scripts\python.exe -m elektro_vienna pilot --snapshot $snapshot --limit 25 --review $review
```

Accept/reject decisions must be unique, attributable, and target the exact unreviewed baseline generated by the same snapshot, code, dependencies and source state. A changed baseline rejects stale review. To revise a decision, submit a new complete decision set against that baseline; both immutable review sources and resulting Case revisions remain. The CLI never edits an older review. Decisions do not confirm extracted prices, technical meaning or knowledge. Rejected sources remain in history but leave the new conditional timeline/evidence.

## Limits and evaluation

- Local lexical matching scans archived email text, including quoted history, for the selected customer identities. This can find forwarded requests but also older/unrelated work for repeat customers. All matches remain proposals; the date window is heuristic, not a validated matching rule.
- Only candidate messages and attachment occurrences have persisted pilot text extraction. No external AI processing or whole-mailbox classification runs.
- PDF text, DOCX body paragraphs, UTF-8 text and HTML text are supported. Images/photos/plans, calendar files and other formats remain explicit exceptions. Textless PDF pages need visual review and may require OCR. Existing OCR text layers may contain errors. The pilot does not infer that image attachments are irrelevant signatures.
- MIME depth/section limits, 40 MiB message/20 MiB document limits, 80 PDF pages, 10 MiB decoded page content, DOCX expansion limits and 500,000-character section limits are visible. Some third-party parsers must decompress a stream to measure it; this is not a hardened hostile-file sandbox.
- Current Airtable snapshots are not historical field-edit logs. An unknown outcome reason stays unknown. Undocumented calls, visits, offers, scheduling or execution are not fabricated.
- Human review should assess false/ambiguous matches, missing sources, field accuracy and how useful the case is for the next similar inquiry. Generating the artifacts is engineering validation; that product evaluation remains open.

The local PDF implementation follows the [pypdf text extraction documentation](https://pypdf.readthedocs.io/en/stable/user/extract-text.html), including its distinction between text extraction, layout and OCR.

## Live execution evidence

On 2026-10-02, read-only inspection confirmed the existing 1,375-message / 2,950-attachment inventory, 2,331,388,345 archived bytes, zero incomplete records, null last error and the one preserved provider-size anomaly. The Airtable capture enumerated 174 electrical tickets; the pilot selected 25 across 17 observed service labels and six statuses. Source snapshot, extracted text and review artifacts are stored only under the authorized root. Human matching/semantic review has not been performed, and no knowledge has been promoted.

The initial run exposed one missing linked customer in the capture; that customer was fetched read-only before the final replay. Earlier immutable pilot artifacts remain as processing history. The final baseline (`d6569e837b4556c290555531466c4ed0ae7eadf577af66d01c9b4d78a30f942c`) produced 253 distinct candidate emails across 23 of 25 Cases, 306 case/message proposals including 106 ambiguous proposals, and zero confirmed matches. Two Cases have no email candidate. There are 1,221 technical and 918 commercial literal candidates; these counts include quoted/repeated content and are not counts of validated unique facts or offers.

The candidate scope includes 1,066 attachment occurrences: 37 contain readable extracted text and 989 are images requiring visual review if relevant. Other exceptions include calendar/unsupported formats, textless PDF pages and two encrypted PDFs. No original was skipped for a size limit and no candidate attachment lacked its archive record. Every emitted factual/matching citation and quoted text span was resolved before publication. All 198 synthetic tests, source/wheel builds, editable installation, dependency checks and whitespace checks passed. A second real execution produced the identical run ID and identical report, cases and extraction bytes; the SQLite file hash remained unchanged across both runs. Phase 1/2 source modules are unchanged. Human review remains outstanding.
