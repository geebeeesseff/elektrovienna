# Current Operating Policy

Version **2026-10-09.1-draft**, effective **2026-10-09** (draft adoption date).
The owner supplied the operating instructions and commercial starting facts on
that date. They are usable for this shadow experiment, not evidence of historical
validity or a new authorization to send prices. No technical standards database
or general rules engine is introduced.

The editable, machine-consumable source is
[`operating_policy.json`](../src/elektro_vienna/operating_policy.json).
[`operating_policy.py`](../src/elektro_vienna/operating_policy.py) loads and validates
it using only the standard library. Both ship in the Python package. Change the
version/effective date intentionally when adopting a new policy; Git retains old
versions and experiments retain a checksummed copy. Existing experiments never
switch silently to newer policy.

Three distinct inputs remain separate:

| Input | Authority and limit |
| --- | --- |
| Current operating policy | Owner-supplied operational/commercial guidance applicable today. Conflicts require confirmation. |
| Reviewed historical experience | Attributable facts from specific reviewed cases; not current prices or independent technical approval. |
| Current case evidence | What this customer supplied and what has been verified in this case; statements remain attributable. |

Operational guidance:

- **Customer friction:** use supplied information first, ask only material questions,
  bundle them, and separate blockers from useful information and internal questions.
  Detailed inquiries can support internal review and a provisional estimate.
- **Technical escalation:** state the exact question and send available evidence to
  electrician/Meister. Do not certify safety or ask customers to decide technical matters.
- **Workforce:** decide one/two-person requirements internally from handling, size,
  height, access, mounting, complexity and safety before pricing/execution.
- **Commercial meaning:** keep indication, estimate, fixed price, regie, consultation,
  customer quote, internal costs, material, travel, invoice and payment distinct.
- **B2B:** before commitment establish legal ordering party, invoice details, UID if
  required and scope/cost approver. Material changes require prior approval where
  applicable; record confirmed, missing or unknown approval.
- **Procurement:** explicitly assign customer, company, partner or undecided supply.
  Retain exact models/variants, check delivered against reviewed equipment, and
  re-evaluate a changed model.
- **Visits:** propose only if remote evidence cannot support the next reliable
  decision. Explain scope, fee and any expressly agreed credit.
- **Case state:** propose contact/job/address/scope, missing facts, quote, appointment,
  assignee, execution, invoice, payment and open work; never invent state or write CRM.

Current starting prices, all **EUR net**:

| Item | Amount | Applicability |
| --- | ---: | --- |
| Single electrician | 100/hour | Standard labor |
| Two-person team | 170/team-hour | Whole team, not each person |
| Travel | 50 | Standard travel |
| First hour including travel | 150 | Ordinary single-electrician time-based work |
| Additional 30 minutes | 50 | Same ordinary single-electrician model |
| On-site consultation/inspection | 150 | Where that consultation model applies |
| Consultation credit on subsequent commissioned job | 150 | Only when expressly agreed for that model |

Do not double-count travel in the first-hour price. No additional rounding,
universal minimum, consultation travel surcharge, material price, hardware price
or service-specific fixed price is inferred. Other service prices may exist but
are not established here. Historical prices/averages cannot fill those gaps.

Repository search at `baa399b`, covering README and all documentation, found no
explicit conflicting **current** numerical tariff. Historical amounts are not
current-policy conflicts. No owner confirmation is currently outstanding for a
conflict; the policy as a whole remains an owner-supplied draft.

An uncertain item uses `status: uncertain`. A conflict uses
`requires_owner_confirmation` and an entry in `conflicts` retaining at least two
claims, their source references and the affected item ID. The price accessor
rejects uncertain/conflicting items and dates before the effective date. Neither
validation nor historical frequency chooses a winner.

Run policy checks with:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_operating_policy.py -q
```
