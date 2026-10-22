# Current Price Book

Version **2026-10-14.1** · effective **2026-10-14** · minimal owner-confirmed
starting facts for human-reviewed preparation. All amounts below are **EUR net**.
This separate table is the complete price-book skeleton; there is no new pricing
engine or historical-frequency model.

| Policy item ID | Current amount | Unit and applicability |
| --- | ---: | --- |
| single_hour | 100.00 | One electrician, per hour of standard labor |
| team_hour | 170.00 | Two-person team, per team-hour; not per person |
| travel | 50.00 | Standard travel; only when not already included |
| single_first_hour_travel | 150.00 | First hour including travel, ordinary one-electrician time-based work |
| single_additional_half_hour | 50.00 | Each additional 30 minutes under that same model |
| consultation | 150.00 | On-site consultation/inspection where this model applies |
| consultation_credit | 150.00 | Credit against a later commissioned job only where expressly agreed for that consultation |

**Source:** the owner explicitly reaffirmed these current facts in the 2026-10-14
playbook request, preserved by the calibration result
`90_manual_review/response_calibration/773c546697d980f2b2e5720af4f249269510bc04807b1d1fdd0de8ffd1d8ad89/calibration-result.json`.
The amounts and scopes agree with `prices` in
[`operating_policy.json`](../src/elektro_vienna/operating_policy.json), version
`2026-10-09.1-draft`. Its existing status/version remain unchanged.

## Boundaries and confirmation gaps

- Historical email, supplier or technician prices never fill missing current
  prices. “Would write similarly” is not tariff confirmation.
- No fixed service prices, material/hardware allowances, effort estimates,
  rounding rules or additional minimum charges are seeded.
- Do not add travel again to the 150.00 first-hour charge. Do not automatically
  add travel to consultation, or automatically credit a consultation fee.
- Keep a conditional estimate distinct from a fixed quote. Confirm unpriced
  materials, scope-specific services and any disputed rate before commitment.
- This skeleton supplies net rates only. It does not establish a universal VAT
  rate or case-specific tax treatment. Label net/VAT clearly and confirm the
  applicable treatment before stating a gross total where needed.

## Current-price conflict check

Checked on **2026-10-14**: README, all repository Markdown documentation and the
current policy JSON. **No conflicting claimed current numerical tariff found.**
The old policy's draft status is not a rate conflict; the owner has reaffirmed
the seven facts above for this skeleton. No numeric conflict needs resolution now.

Historical fan pricing versus Irene's newer case-specific owner indication is
evidence of stale historical pricing, not a second global tariff. Neither amount
creates a fixed service price here. Unknown service/material prices and tax
applicability remain confirmation gaps, not silently resolved conflicts.

If a current-price conflict is later found, retain both claims, source/version,
scope and date; mark the item **requires Giovanni confirmation** and do not choose
one silently. Change the price-book version intentionally when adopting a change.
