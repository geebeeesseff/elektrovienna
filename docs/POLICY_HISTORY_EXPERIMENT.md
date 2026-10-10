# Policy versus history experiment

The question is whether the existing five reviewed revisions improve inquiry
handling once current operating policy is available. The live Shadow Agent,
additional reviews, matching/extraction changes and provider writes remain out
of scope. Giovanni's blinded preference is the next product decision gate.

## Protocol

Reuse the original packet
`1e26d3b85f55a3b4f975239b151fb95b1aebea1be58873901c9da956eb0165ae`.
The three inquiries, provider timestamps, exact T0 message/attachment occurrences,
NORTH_STAR text and five reviewed fact projections are copied unchanged. Original
bytes and pinned revision hashes are checked. No new selection, image downloads,
historical extraction or current CRM fields are introduced.

P receives T0, the policy snapshot and NORTH_STAR. PH receives exactly those inputs
plus the five fact cards. All three P answers freeze before the PH context is
released. The combined six-answer freeze must validate before the existing key
is opened. The original experiment and five revisions stay unchanged.

Nine sections: customer need, already known, actual blockers, internal checks,
necessary customer questions, commercial approach, next action, short customer
draft and CRM proposal. Maximum 650 words per answer, 150 per draft, 0–4 explicitly
listed questions. Every section has allowed citations; P rejects historical
citations. PH analogies require a cited fact, similarity, difference and effect.
These structural checks do not prove free-text entailment.

The current policy is applied **as of today** to the old inquiries; it is not
backdated. Two-person rates or historical travel fees do not override today's
owner-supplied rates. No historical totals or averages become current prices.

## Small artifact harness

`policy_experiment.py` reuses `blind_eval` packet validation, source verification,
canonical hashes and guarded no-replace publication. It does not change that module.
No new dependency, database, API or service is required. Run from the repository:

```powershell
python -m elektro_vienna.policy_experiment prepare --original-run <original-id>
python -m elektro_vienna.policy_experiment context --run <new-id> --variant P
python -m elektro_vienna.policy_experiment freeze-p --run <new-id> --input <private-P.json>
python -m elektro_vienna.policy_experiment context --run <new-id> --variant PH
python -m elektro_vienna.policy_experiment freeze --run <new-id> --input <private-PH.json>
python -m elektro_vienna.policy_experiment comparison --run <new-id>
python -m elektro_vienna.policy_experiment reveal --run <new-id>
python -m elektro_vienna.policy_experiment evaluate --run <new-id> --input <private-assessment.json>
```

For Unicode console output on Windows set `$env:PYTHONIOENCODING='utf-8'`.
The assistant prepares inputs outside Git; Giovanni never edits JSON.

All immutable outputs are beneath the authorized Knowledgebase:

```text
30_cases/policy_experiment/<packet-sha256>/
    packet.json       # policy, exact T0, pinned history, guidance, code fingerprints
    policy_only.json  # three P answers, checksum and earlier freeze time
    frozen.json       # all six answers plus policy-only binding
    mapping.json      # separate random A/B assignment; never embedded in primary view
    comparison.html   # exact initial inquiries/images, Answer A/B, simple choices
    answer_key.json   # post-freeze copy/binding of existing retrospective key
    evaluation.json   # source-referenced judgments, counts, totals, limitations
```

Identical retries adopt identical bytes; changes cannot overwrite an answer or
evaluation. A/B assignment is independently randomized per case once and persisted
separately. Three assignments need not be balanced. The primary view includes
neither mapping, provenance IDs, arm labels nor retrospective scores. Natural
wording may still reveal experience use to an attentive reader; this is not a
guarantee of perfect blinding.

Open `comparison.html` locally. For each case choose A, B or roughly equal, and
optionally comment. Download the small evaluation file to retain selections;
there is no autosave/server/storage or implicit acceptance of unanswered cases.
No preference has been entered on Giovanni's behalf. Inspect mapping and machine
results only after making the human comparison.

## Secondary evaluation and limits

Eleven 0–2 dimensions: understanding, unnecessary questions, missed blockers,
internal/customer routing, commercial completeness, policy compliance, appropriate
history use, technical uncertainty, next step, reply usefulness and CRM usefulness.
Maximum 22 per case / 66 per arm. P can score well on appropriate history use by
excluding history; that dimension measures discipline, not incremental benefit.

Seven separately explained flags cover invented prices, historical-price misuse,
unsafe technical claims, future leakage, unnecessary visits, missed B2B controls
and missed procurement/model checks. Scores and flags are **assistant-authored
retrospective judgments**. Machine validation, aggregation, question/word counts,
bindings and replay are deterministic; there is no claim of independent semantic
scoring or automated detection of all critical failures. Reasons cite the current
answer/policy/T0 or exact later source IDs. The key contains candidate historical
associations and is not uniformly human-reviewed truth.

This adds a policy-only control and a blinded human comparison to the earlier
self-score. It remains only three selected cases. The policy was informed by the
previous misses, and the same assistant authored P first, then retained or changed
that wording for PH and scored both. Shared authorship, order, wording carry-over,
and policy-development familiarity prevent a clean causal estimate. P generation
did not inspect historical fact cards or later same-case records; application
gates and the interaction record establish that procedure, not mental isolation
or an OS access boundary. Identical answers are permitted where history adds no
justified content. Do not force changes to make history appear useful.

## Delivered run (2026-10-09)

Run: `9722ac387a1d15cef4323e550f2ef6264bb82fb01c054fdb75a173b018014bba`.
Policy: `2026-10-09.1-draft`, effective `2026-10-09`, no current-policy conflicts.
P freeze: `2026-10-09T15:52:41.752376+00:00`, answer hash
`2bb73014bc598cab8afc2eca1876375f2d197b33549bffc2c2d9f25d263cbd76`.
Combined freeze: `2026-10-09T15:53:46.062129+00:00`, answer hash
`aaea04677f799d6e0f2c4f321968944b380dacefd4465dc214c0eaf40a78b13e`.
Both precede key release in this run. Original blind answers are unchanged.

Secondary totals: **55/66 in each arm**; per-case scores 21, 17, 17 in both.
Answers contain 330–394 words and zero/one customer question. No flagged critical
failure was identified. Both arms retain a friction issue: device data can be
asked for before internal review decides whether it blocks a provisional estimate.
No material incremental benefit is established by this secondary score. Giovanni's
preferences remain pending; no live-agent decision has been made.

Validation: **307 Python tests pass** (23 new policy/experiment checks), both
JavaScript behavior suites and the new script syntax check pass. Dependency
checks and sdist/wheel builds pass; four shipped policy/experiment assets match
source bytes. All seven experiment artifacts replay byte-identically with network
and SQLite access prohibited. All 72 protected pre-existing derived/review/state
files retain their hashes, including the original blind experiment and reviewed
revisions; 335 referenced original files verify against their archived checksums.
All 54 rendered answer sections are preserved, five original images embedded,
and no remote resource or mapping/key/evaluation link occurs in the primary page.
CSP script/style hashes validate. Remote SharePoint synchronization was not checked.

The browser tool rejected the local file URL under its URL security policy.
No workaround was attempted. Visual layout and actual browser download behavior
are therefore **unverified**; static structure and synthetic download behavior pass.

Implementation files: `operating_policy.json`, `operating_policy.py`,
`policy_experiment.py`, `policy_compare.js`; package-data configuration in
`pyproject.toml`; tests in `test_operating_policy.py`, `test_policy_experiment.py`
and `test_policy_compare_js.cjs`. README, architecture, data-model and milestone
documentation link the new policy and protocol. No existing integration, review,
matching, extraction or blind-evaluation implementation was modified. No Gmail
draft/send/write, Airtable write or Calendar write occurred.
