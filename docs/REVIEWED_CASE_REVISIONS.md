# Reviewed case revisions

This bounded milestone follows acceptance of the Woodward review workflow and completed human reviews of Woodward, Boldrino-Teichgrab, Martysiuk, Zafirakis and BUWOG. It adds an evidence-linked case state above the immutable pilot. The review UI, completion/draft architecture, matching and source integrations remain unchanged. No other cases are processed.

## Contract and trust

`case_revision.py` consolidates an agent-prepared plan with the latest **completed** human review for that case and baseline, across all review views. Mutable drafts and legacy autosaves never establish acceptance. A stale review reference, broken completion chain, changed original, invalid citation, missing correction resolution or incomplete stages fails before batch publication.

The revision contains:

- Identity fields for customer/account, particular project, address (including unknown/conflicts) and service type. Existing Airtable identities remain references, not a new customer master.
- Six stages: inquiry, consultation, quote, execution, invoice/payment and remaining work. Every stage supports the existing `unknown` outcome alongside the existing reviewed stage vocabulary. Broad CRM completion cannot prove execution.
- Operational CaseEvents with event time and precision separated from human reporting time. Unknown call, visit and payment dates remain null. Baseline events and matching proposals remain available unchanged.
- Consolidated technical facts with original qualifications; commercial roles explicitly distinguish estimate, customer quote, final invoice, payment, labor/time, material, travel/site visit and internal/technician costs. Unknown amounts/hours are null, not zero. An invoice does not prove payment.
- Actors, separate project scopes, unresolved facts, and CRM observations containing both the exact baseline field and a human-readable proposal. CRM comparison refers to the **captured snapshot**, never a claim to have fetched current live values.
- Complete human comments, per-comment resolution targets, review decisions (including separate source acknowledgements/exceptions), raw-source references/checksums, prior review/feedback links and processing version.

This is a case-scoped canonical consolidation for inspection, not global KnowledgeItems. New agent wording and newly found source associations are explicitly not another human approval. The completed review accepts the text that was actually reviewed; it does not retroactively accept every subsequent inference or new document. Human statements remain attributable to the reviewer and completion time, with exact original wording. Unsupported details remain unknown.

BUWOG illustrates account versus project: one customer organization may produce several unrelated requests. Project scopes have distinct message membership; no source is dropped or silently reassigned in the historical baseline. Contact IDs are not invented organization IDs. No automatic new ticket creation or customer merging exists.

## Missing documents

Search already available evidence first: saved Airtable snapshots, existing pilot text, immutable local archived emails and attachment occurrences. No Gmail ingestion or new Airtable snapshot capture is authorized here. Targeted original documents absent from the pilot can be attached as a revision-local supplement. It reuses the unchanged `pilot_parse` code, verifies original hash/length/identity, includes the original parent message, and stores parser code/dependency versions with parsed text in the revision. This is bounded evidence retrieval, not a matching-algorithm change or a rerun of the pilot.

New source references retain exact parsed page/MIME spans. Repeated copies preserve their occurrences. Missing documents remain missing, even when a reviewer reliably reports their existence. Likewise a human report of payment is not converted to a bank receipt or an invented payment date.

## Publication and use

The agent prepares the private plan; Giovanni does not edit JSON. An operator can validate and publish it with:

```powershell
python -m elektro_vienna case-revise --plan <private-local-plan.json> --dry-run
python -m elektro_vienna case-revise --plan <private-local-plan.json>
```

The entire batch (one to five unique cases) validates first. Each revision publishes atomically, without replacement, beneath:

```text
30_cases/reviewed/<airtable-case-id>/<revision-sha256>/comparison.html
30_cases/reviewed/<airtable-case-id>/<revision-sha256>/case.json
```

`case.json` publishes last as the per-case completion marker. Identical retries return identical paths/bytes. Changed interpretations create a new hash and may name `previous_revision`; no old case or review changes. Publication of the five cases is resumable per case, not a cross-file transaction. A later completed review requires another explicit revision run; there is no watcher or mutable current-state pointer.

The script-free, form-free comparison shows **before review → human corrections → reviewed case state → possible CRM updates**, plus facts, operational chronology and provenance. It is an inspection artifact, not another review application. Existing review views/services continue unchanged.

## Verification

Synthetic tests cover immutable/idempotent replay, latest completion across views, unfinished draft isolation, comment coverage, unknown event time, scoped source membership, typed commercial roles, original and review corruption, exact citations, supplemental parent/identity validation, no provider authentication/SQLite writer path, HTML escaping and bounded batch scope. Live execution additionally verifies input hashes, original links and preserved matching membership for exactly the five reviewed cases. A test pass does not independently prove human recollections or remote SharePoint sync.

Live validation on 2026-10-08: all five revisions published with identical replay bytes, 244 protected files unchanged, all six latest-review correction comments resolved (Woodward's earlier corrections retained through its reviewed view), and 161 comparison links resolved. Three additional archived messages supplied the missing Wallbox visit invoice and two occurrences of the later offer. The other reported later invoice was not located in the available archive/snapshots. The bounded search inspected 262 existing messages and 12 invoice/generic PDF occurrences; one image-only invoice was visually excluded as unrelated, without OCR. Review UX and source algorithms remained unchanged. The Python suite had 268 passing tests and one outdated CLI-command allowlist expectation; after adding the authorized command to that test, its targeted rerun passed (269 checks verified). JavaScript checks, dependency checks and packaging passed. Browser visual inspection of the new file URLs was blocked by browser policy; static structure and links were checked directly.
