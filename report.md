# India POD — Pre-flight Report
Source: `India POD - Accounts.xlsx` → `India POD - Accounts (cleaned).xlsx`
Run: read-only Phase 0 against `gtm-prod` postgres.

## Summary
| Metric | Count |
|---|---|
| Total non-empty rows in source | 221 |
| Rows with Final Qualification = Yes | 219 |
| Unique domains (qualified) | 219 |
| **Included in cleaned xlsx (NEW)** | **208** |
| Skipped: already exist in prod | 8 |
| Skipped: unqualified / no domain | 2 |
| Skipped: duplicate domain within file | 3 |

## AE/SDR resolution (in included rows)
- AE first names resolved to email: 111
- AE blank: 97
- SDR first names resolved to email: 130
- SDR blank: 78

## Mapping table (used)
| First name (xlsx) | Resolved user | Email | Role |
|---|---|---|---|
| Sandeep | Sandeep Sinha | sandeep@beacon.li | ae |
| Yashveer | Yashveer Singh | yash@beacon.li | ae |
| Bhavya | Bhavya Mukkera | bhavya@beacon.li | ae |
| Dyuthith | Dyuthith Din | dyuthith@beacon.li | sdr |
| Annie | Annie Gupta | annie@beacon.li | sdr |

## Region rule applied
- Every included row has `Region` overwritten to `Rest of the World` (per instruction).

## SKIPPED — already exist in prod (NOT touched)
These 8 companies are already in the prod DB with assigned owners from a different pod. We are NOT re-uploading them, so their existing region/owner/data is preserved untouched.

| xlsx name | xlsx domain | Existing prod record |
|---|---|---|
| Icertis | icertis.com | Icertis (owner: Pravalika Jamalpur) |
| Tradeshift | tradeshift.com | Tradeshift (owner: Pulkit Anand) |
| Papaya Global | papayaglobal.com | Papaya Global (owner: Pulkit Anand) |
| Esker | esker.com | Esker (owner: Pulkit Anand) |
| Sage Intacct | sage.com | Sage (owner: Shahruk) |
| Solifi (fka White Clarke Group / IDS) | solifi.com | Solifi (owner: Pulkit Anand) |
| Deputy | deputy.com | Deputy (owner: Pravalika Jamalpur) |
| Move In Sync | moveinsync.com | Moveinsync (no owner) |

## SKIPPED — unqualified or missing domain
- Bizongo (bizongo.com): Final Qualification != Yes
- Factorial HR (factorialhr.com): Final Qualification != Yes

## SKIPPED — duplicate domain within xlsx
- Comarch EDI (comarch.com) — first occurrence already included
- Lucanet (lucanet.com) — first occurrence already included
- iGTB (Intellect Global Transaction Banking) (intellectdesign.com) — first occurrence already included

## Output file
`C:/Users/sarthu/Downloads/India POD - Accounts (cleaned).xlsx`
- Single sheet: Accounts
- 208 data rows
- All columns preserved verbatim except Region (forced) and AE/SDR (first name → email)

## What's NOT in this script
- No DB writes occurred. Phase 0 was two read-only SELECTs.
- The cleaned xlsx is created locally; nothing has been uploaded.
- Next step: you upload the cleaned xlsx via the existing Account Sourcing UI at https://gtm.beacon.li/, same flow as previous imports.
