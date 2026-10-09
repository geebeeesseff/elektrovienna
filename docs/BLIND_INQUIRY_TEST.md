# First blind historical inquiry experiment

This bounded experiment asks whether five reviewed cases help answer a new
inquiry without seeing its later trajectory. It is an offline artifact harness,
not a production agent, a new case review, or a knowledge promotion pipeline.
The operator explicitly authorized using the five reviewed revisions, including
the sanity-checked kitchen and wallbox revisions.

## Protocol

Select three unreviewed cases for diversity using initial-inquiry information,
not successful outcomes. Keep an explicit selection/exclusion log. Exclude cases
whose future is already known to the answering agent, including information
exposed by later CRM titles. The current experiment excludes an earliest message
that already references a prior call, an earliest candidate that is an invoice,
a visibly unrelated candidate, and a case without candidate messages. These are
eligibility failures; matching and the original pilot remain unchanged.

T0 is one original customer message, identified by Gmail message ID and provider
`internal_ms`, plus exactly its attached files. It must be the earliest existing
pilot candidate and look like an original inbound inquiry. This cannot establish
the absence of an unarchived earlier phone call. Human inspection of the initial
message remains necessary; the harness does not claim to infer initial intent.
No current Airtable field is included because its contemporaneous value cannot
be established from the later snapshot. Customer identifiers used to select a
message do not authorize importing current status, request text or notes.

Original hashes/lengths are verified. Named attachments must match the initial
RFC822 message's decoded attachment filename/content-hash multiset. Later
attachments cannot be added just because they belong to the same thread.
Initial image bytes may be visually inspected; this is not new OCR. Web image
links remain text: no current download is claimed to be a historical attachment.

The permitted experience is an explicit five-case allowlist. Only state,
technical, commercial and open-question facts are projected, preserving fact
IDs, certainty, revision references and checksums. Completed-review state is
required. Raw conversations, baseline blocks, CRM observations and matching
signals are not passed through. Provenance IDs shared with selected held-out
cases/messages/threads/source hashes exclude the affected fact. This catches
known structured overlaps, not arbitrary uncited semantic contamination.

For five cases, retrieval consists of presenting the structured fact cards and
selecting attributable facts with a similarity/difference explanation. There is
no numerical ranking or vector search. Unsupported analogies may be rejected.
The only additional operating guidance is a checksummed `NORTH_STAR.md` copy.
The five revisions can postdate an inquiry because the user expressly allowed
them: this is synthetic knowledge transfer, not a recreation of all knowledge
that the company possessed on that historical date.

## Four stages and files

The separate module entry point avoids changing existing pilot/review commands:

```powershell
.venv/Scripts/python.exe -m elektro_vienna.blind_eval prepare --input <private-plan>
.venv/Scripts/python.exe -m elektro_vienna.blind_eval freeze --run <run-id> --input <private-answers>
.venv/Scripts/python.exe -m elektro_vienna.blind_eval reveal --run <run-id>
.venv/Scripts/python.exe -m elektro_vienna.blind_eval evaluate --run <run-id> --input <private-evaluation>
```

These are maintainer commands. The agent prepares the private inputs; Giovanni
reads `overview.html` and is never asked to edit JSON or source files.

All outputs are immutable, under the sole authorized Knowledgebase root:

```text
30_cases/blind_eval/<packet-sha256>/
    packet.json
    frozen.json
    answer_key.json
    evaluation.json
    overview.html
```

The store reuses atomic no-replace source-archive publication but accepts only
these evaluation paths. It cannot publish source/archive/review paths. No SQLite
connection, authentication, provider access, calendar API, sending or framework
is introduced. Customer runtime inputs remain in ignored private runtime storage,
never tracked source or synthetic tests.

1. **Prepare** builds an allowlisted packet from verified initial originals and
   pinned reviewed facts. The run ID binds the complete packet, input checksums,
   provenance, protocol and harness implementation fingerprint.
2. **Freeze** requires all three complete answers together, with all nine output
   sections and section-level citations from the packet. It persists answers,
   checksum, packet binding and UTC freeze time. A retry adopts identical bytes;
   any changed answer is rejected. Free prose is agent-authored; citation
   membership checks cannot prove that every natural-language claim is entailed.
3. **Reveal** checks the complete freeze before opening the pilot/extraction
   answer-key inputs. It verifies their pinned checksums and original later
   source bytes. Only later messages enter the trajectory, while the unchanged
   candidate case record stays separately labeled as unreviewed answer-key data.
4. **Evaluate** binds the retrospective and nine integer scores (0–2) to the
   packet, freeze and answer-key checksums. It produces one script-free HTML
   overview, retaining exact blind text, original inquiries, initial images,
   later sources, score reasons and critical failures separately. Source text is
   escaped; no remote resource is loaded. Evaluation changes cannot overwrite
   the already published experiment.

The freeze is an application artifact boundary, not an OS sandbox, cryptographic
attestation of an agent's internal state or protection against an administrator
rewriting files. The answering agent must not open the later inputs before the
freeze; the command ordering and interaction record provide the procedural
evidence. Do not regenerate this experiment's answers after reviewing its key.
Replaying stored artifacts is deterministic; generating new prose from the same
inputs is not claimed to be deterministic. This run used the current Codex
assistant interactively; the harness makes no model API calls.

## Evaluation limits

Nine dimensions: customer need, missing information, questions, historical
usefulness, commercial correctness, technical usefulness, uncertainty, next
action, and draft usefulness. Zero means poor/materially wrong; one partially
useful; two strong. Maximum 18 per case. Critical failures are tracked separately
for leakage, prohibited external actions, invented binding prices and harmful
technical instructions. Significant omissions still reduce scores even where
they do not meet that critical threshold.

The same agent wrote and scored this first sample. There is no independent human
scoring, control answer or causal estimate of improvement. Three intentionally
diverse cases are not a representative success rate. Missing later evidence is
not evidence that work failed or succeeded. Calendar acceptance is not execution;
an invoice is not payment; current CRM status is not a complete staged outcome.
Suspect associations, other projects in threads, discrepant dates, amounts and
device names remain visible. No fixes are implemented from these findings.

## First run and validation

The completed packet ID is
`1e26d3b85f55a3b4f975239b151fb95b1aebea1be58873901c9da956eb0165ae`.
All three outputs were frozen at `2026-10-09T05:57:30.869812+00:00`, before the
answer key was released. Answer checksum:
`9e01ce13f0b62537efeb0134cae993eb915e48a2042efb87833fe60c6764f59b`.
The local overview records names, exact T0 sources, exclusions and findings;
customer narratives are not copied into Git.

The 15 synthetic tests cover future/CRM canaries, overlap exclusion, wrong T0,
wrong customer, future attachments falsely assigned to the initial message,
unreviewed/draft knowledge, partial freeze, out-of-packet citations, changed
answers, source/packet integrity, reveal-before-freeze, immutable replay, score
ranges, escaped HTML, input preservation, no network connection and restricted
write paths. The full Python suite passes: **284 tests**. Packaging and dependency
checks pass. These checks validate the harness; they do not certify technical
electrical advice or replace Giovanni's assessment of the experiment.

Live verification confirms 124 original source files against their archive
checksums, all five reviewed revision hashes, 38 existing local HTML source links,
five embedded initial images and exact preservation of all 27 blind-answer
sections. The 244 previously protected files (including SQLite, source snapshots,
earlier evidence and completed reviews) retain their recorded hashes. Static HTML
structure/content/link checks pass; browser layout inspection was not performed.
