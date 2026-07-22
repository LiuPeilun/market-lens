# Eastmoney Fixture Coverage

These fixtures are minimal, sanitized snapshots of upstream Eastmoney responses.
They preserve field names, dates, units, route identifiers, and representative
missing values needed by parsers. They must not contain credentials, cookies,
request headers, user data, or unrelated response fields.

## Coverage Matrix

| Data path | Representative fixtures | Success semantics | Derived failure and degradation tests |
| --- | --- | --- | --- |
| F10 detailed balance sheet | `f10_balance_sheet.json` | Real-estate contract liabilities, inventory, cash, debt, report date, and notice date | Empty date list, malformed top-level shape, missing date, missing/wrong security code, unrequested report date, request chunking, and duplicate report revisions |
| F10 detailed income statement | `f10_income_statement.json` | Revenue, profit, research expense, optional null fields, report date, and notice date | Same route/schema checks as the shared F10 adapter; missing and future notice dates are excluded by research-context tests |
| F10 detailed cash flow | `f10_cash_flow_statement.json` | Operating cash flow, capital expenditure, financing cash flow, and ending cash | Same route/schema checks as the shared F10 adapter |
| Commodity main-continuous history | `commodity_main_rebar.json` | Route code, market, name, units, daily/monthly periods, and unadjusted prices | All eight explicit contract specifications, empty/malformed payloads, route drift, invalid values, duplicate dates, and unsupported products |
| REIT profile and exchange price | `reit_profile_180101.json`, `reit_price_180101.json` | Exact `FTYPE=Reits`, exchange/quote route, and unadjusted exchange prices | Ordinary fund type, unsupported route, code/market/name drift, invalid period, and invalid date range |
| REIT financials and periodic notices | `reit_financial_180101.json`, `reit_periodic_notices_180101.json` | Parallel-array validation, report-kind matching, notice availability date, pagination, and deduplication | Missing notice, unequal arrays, conflicting duplicate reports, noncanonical notices, and future/unknown availability exclusion |
| REIT distributions | `reit_distributions_180101.html`, `reit_distribution_notices_180101.json` | Cash per unit, announcement matching, ex-dividend date, and payment date | Missing table, malformed distribution unit, unmatched announcement, and point-in-time exclusion |

## Fixture Policy

- Keep one representative success fixture when multiple products share the same
  response schema. Parameterized contract and route tests cover product-specific
  identifiers without duplicating payloads.
- Derive route drift, missing fields, malformed values, and future dates from a
  success fixture when the upstream shape is otherwise identical.
- Add a separate fixture only when it represents a materially different upstream
  shape or parsing route.
- Historical research may use a disclosure only when its known publication or
  notice date is no later than the analysis date. “Stale” is not a scoring state
  for this research-only layer; unknown and future availability are explicit
  exclusion diagnostics.
- Fixture coverage does not make a field scoring-eligible. Sector and REIT
  research datasets remain `scoring_eligible=false` until model backtesting and
  a separate production release.
