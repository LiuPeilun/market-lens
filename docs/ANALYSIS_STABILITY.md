# Market Analysis Stability Contract

## Purpose

The market analysis module must return a deterministic, auditable result for every
valid supported asset request. Third-party data availability cannot be guaranteed,
so stability means:

1. Upstream failures never produce a silent blank result.
2. Invalid, mismatched, future-dated, or structurally changed data never enter scoring.
3. A numeric score is returned only when a verified primary or fallback method has
   enough data.
4. When no numeric score is defensible, the API returns a structured
   `unavailable` assessment instead of inventing a value.

This contract does not change valuation factors, weights, thresholds, or model
versions.

## Assessment Contract

Every newly generated `assessment` contains:

| Field | Meaning |
| --- | --- |
| `status` | `complete`, `degraded`, or `unavailable` |
| `method` | The deterministic method that produced the valuation result |
| `fallback_reasons` | Stable machine-readable reason codes without volatile upstream messages |
| `dimensions` | Valuation, quality, and applicable product dimensions |
| `overall_confidence` | Conservative confidence across available dimensions |
| `data_quality` | Source, source date, retrieval time, warnings, and diagnostics |

Historical records created before this contract remain readable because the new
fields are optional at the API schema boundary. New analysis results must always
populate them.

## Stable Error Taxonomy

Analysis transport failures use a separate stable contract:

| Category | HTTP | Retryable | Meaning |
| --- | ---: | --- | --- |
| `invalid_request` | 400 | No | The code, asset type, date range, or other user input is invalid |
| `upstream_unavailable` | 503 | Yes | A required market-data source disconnected, timed out, or returned an unusable response |
| `data_unavailable` | 422 | No | The request is valid, but no verified dataset can support an assessment |
| `internal_error` | 500 | No | An unexpected application or tool-output failure occurred |
| `persistence_error` | 502 | Yes | Supabase could not save or load application state |

JSON errors retain the existing string `detail` field and add stable `code`,
`category`, and `retryable` fields. SSE error events expose the same metadata with
`message` in place of `detail`. Internal exception details are logged and replaced
with a fixed public message.

`ToolInvocationError` is a runtime execution failure, not a `ValueError`. Tool
results preserve error category and retryability through the finance capability,
API, SSE, LLM tool payload, and frontend `ApiRequestError`.

### Status Semantics

| Status | Rule |
| --- | --- |
| `complete` | A numeric valuation score was produced by the selected primary method |
| `degraded` | A numeric score exists, but a declared fallback or limited snapshot was used |
| `unavailable` | No verified method produced a numeric valuation score |

`complete` does not mean every optional factor is present. Optional factor
coverage continues to affect dimension confidence. It means that the selected
primary valuation method passed its own scoring gates.

### Method Semantics

| Method | Usage |
| --- | --- |
| `fundamental_valuation` | Stock historical and industry fundamental valuation |
| `index_fundamental_valuation` | Verified official index fundamental history |
| `holdings_valuation` | Weighted verified fund, ETF, or index holdings |
| `price_position_proxy` | Historical price or NAV position, explicitly not fundamental valuation |
| `last_known_good` | Reserved for a later audited snapshot fallback |
| `unavailable` | No numeric valuation method passed |

## Current Interruption Audit

Priority definitions:

- `P0`: Can prevent a supported request from returning an analysis result.
- `P1`: Can remove a useful fallback or hide material diagnostics.
- `P2`: Does not interrupt the result but weakens observability.

| ID | Priority | Location | Current behavior | Required follow-up |
| --- | --- | --- | --- | --- |
| ST-01 | P0 | `MarketAnalysisAgent.analyze`, stock valuation load | `get_stock_valuation` is unguarded; an upstream error aborts the analysis | Isolate source failure and continue to verified price-position or last-known-good fallback |
| ST-02 | P0 | `MarketAnalysisAgent.analyze`, stock price load | Empty history and empty valuation-derived bars raise `ValueError` | Return structured `unavailable`, or use an audited cached snapshot |
| ST-03 | P0 | `MarketAnalysisAgent.analyze`, fund NAV load | Fund NAV fallback is unguarded; empty NAV raises `ValueError` | Add primary/secondary/LKG NAV route and structured terminal result |
| ST-04 | P1 | `MarketAnalysisAgent.analyze`, fund name load | Name lookup is unguarded after NAV succeeds | Preserve code-based analysis and mark name source unavailable |
| ST-05 | P1 | REIT profile resolution | Product classification can select REIT, then profile loading can abort | Return a REIT `unavailable` assessment with source diagnostics |
| ST-06 | P1 | Index price fallback discovery | `find_index_for_fund` can fail while attempting the final proxy | Isolate discovery and retain the existing holdings result |
| ST-07 | P0 (resolved) | `/api/analyze` tool boundary | Tool failures retain stable category and retryability; only invalid requests map to HTTP 400 | Keep category mappings covered by API and tool-boundary regression tests |
| ST-08 | P0 | `/api/analyze` persistence | Supabase save failure returns HTTP 502 after a valid analysis was already computed | Return the analysis with `analysis_id=null`; report persistence separately |
| ST-09 | P0 | Chat analysis tool call | Any finance tool error terminates the chat preparation path | Return and explain the structured degraded/unavailable assessment |
| ST-10 | P0 | Finance tool executor | The entire analysis has one 90-second timeout and discards late partial work | Add bounded stage budgets and persist usable intermediate snapshots |
| ST-11 | P0 | Frontend `requestJson` | Non-2xx responses are converted to an exception; no structured partial result can render | Render assessment status independently from transport and persistence warnings |
| ST-12 | P0 | `SQLiteCache` | Cache only returns entries inside TTL; there is no validated stale/LKG read path or snapshot metadata | Add an immutable validated snapshot store with maximum stale ages |
| ST-13 | P1 | Fund holdings route | Holdings failures are isolated, but the caught error is dropped when the route object is absent | Preserve stable source failure codes in the assessment |
| ST-14 | P2 | Source health | No aggregate source success rate, last success time, or circuit state is exposed | Add source health diagnostics after fallback routes are implemented |

## Existing Safe Isolation

The following paths already degrade without aborting the main result:

- Stock profile, financial factors, peers, dividends, industry snapshot, and detailed
  statements.
- Fund product information, exchange-price fallback, holdings route, official index
  data route, and per-holding stock analysis.
- Individual REIT price, financial, distribution, and notice datasets after the REIT
  profile has been resolved.

These paths still need stable reason codes and source-health reporting, but they are
not the first interruption targets.

## Next Implementation Order

After this contract and audit:

1. Make analysis persistence non-blocking for an already computed result.
2. Add validated last-known-good snapshots with source identity and age limits.
3. Implement stock, fund, and index deterministic fallback matrices.
4. Return structured unavailable results for terminal no-data cases.
5. Render complete, degraded, stale, and unavailable states in the frontend.
6. Add outage, malformed response, route mismatch, future-data, and timeout tests.

Strategy factors and weights remain frozen until these stability gates pass.
