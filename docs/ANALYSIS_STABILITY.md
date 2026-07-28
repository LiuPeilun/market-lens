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

## Persistence Contract

Assessment status and persistence status are independent. A valid computed
assessment is not discarded when a post-compute Supabase write fails.

| Status | Meaning |
| --- | --- |
| `saved` | Every attempted post-compute write succeeded |
| `partial` | At least one post-compute write succeeded and at least one failed |
| `failed` | Every attempted post-compute write failed |
| `not_attempted` | No applicable post-compute write was attempted |

Persistence diagnostics include a stable `error_code`, `retryable`, and ordered
`failed_operations`. Direct analysis returns the computed result with
`analysis_id=null` when `analysis_result` cannot be saved. Synchronous chat
reports failures for analysis, session context, user message, and assistant
message writes. Streaming chat adds the current persistence report to `meta` and
`done` without replacing tokens with an error event.

This non-blocking boundary applies only after a result or answer exists.
Authentication, chat-session creation, initial streamed user-message storage,
and tool-approval state remain fail-closed because the request cannot be safely
executed or resumed without them.

## Validated Last-Known-Good Contract

Last-known-good (LKG) snapshots are stored separately from the raw HTTP cache.
Raw responses never become fallback data merely because an HTTP request
succeeded. Only normalized typed rows that pass dataset-specific validation can
replace an existing snapshot.

Each snapshot records:

- Dataset name and exact request identity.
- Fixed allowlisted source identity.
- Snapshot schema and dataset validator versions.
- Canonical normalized JSON, SHA-256, source date, retrieval time, and row count.

Reads fail closed unless dataset, request identity, source, schema version,
validator version, hash, row count, maximum age, and the current dataset
validator all match. The default retrieval-age limit is seven days and can be
configured with `MARKET_LENS_LKG_MAX_AGE_SECONDS`.

The first protected datasets are:

| Dataset | Request identity | Allowed source |
| --- | --- | --- |
| Stock price history | Symbol, start, end, period, adjustment | `eastmoney_push2his` |
| Stock valuation history | Symbol | `eastmoney_datacenter` |
| Exchange fund price history | Code, start, end, period, adjustment | `eastmoney_push2his` |
| Fund NAV history | Code, start, end | `eastmoney_pingzhongdata`, `eastmoney_f10_nav` |

Validators reject empty rows, duplicate or unordered dates, future dates,
out-of-range dates, non-finite values, invalid OHLC relationships, negative
volume or amount, mismatched stock codes, missing required NAV values, stale
tails, and inconsistent pagination. A malformed live response is removed from
the HTTP cache and cannot overwrite the last valid snapshot.

Using LKG never changes a valuation score or confidence calculation. A numeric
assessment is explicitly changed to `status=degraded` and
`method=last_known_good`; `fallback_reasons` includes
`last_known_good_snapshot`. Source date, snapshot age, row count, hash,
validator version, and request identity are exposed in
`assessment.data_quality`. If no numeric score exists, the assessment remains
`unavailable`.

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
| `last_known_good` | A numeric result used one or more explicitly disclosed validated LKG inputs |
| `unavailable` | No numeric valuation method passed |

## Current Interruption Audit

Priority definitions:

- `P0`: Can prevent a supported request from returning an analysis result.
- `P1`: Can remove a useful fallback or hide material diagnostics.
- `P2`: Does not interrupt the result but weakens observability.

| ID | Priority | Location | Current behavior | Required follow-up |
| --- | --- | --- | --- | --- |
| ST-01 | P0 (partial) | `MarketAnalysisAgent.analyze`, stock valuation load | Exact validated LKG history is used when available; no-snapshot source failure still aborts | Add the deterministic no-LKG stock degradation route |
| ST-02 | P0 (partial) | `MarketAnalysisAgent.analyze`, stock price load | Exact validated LKG or valuation-derived bars can preserve output | Return structured `unavailable` when neither route is valid |
| ST-03 | P0 (partial) | `MarketAnalysisAgent.analyze`, fund NAV load | Exchange price, ordinary NAV, and exact validated LKG routes are available | Return a structured terminal assessment when all routes fail |
| ST-04 | P1 | `MarketAnalysisAgent.analyze`, fund name load | Name lookup is unguarded after NAV succeeds | Preserve code-based analysis and mark name source unavailable |
| ST-05 | P1 | REIT profile resolution | Product classification can select REIT, then profile loading can abort | Return a REIT `unavailable` assessment with source diagnostics |
| ST-06 | P1 | Index price fallback discovery | `find_index_for_fund` can fail while attempting the final proxy | Isolate discovery and retain the existing holdings result |
| ST-07 | P0 (resolved) | `/api/analyze` tool boundary | Tool failures retain stable category and retryability; only invalid requests map to HTTP 400 | Keep category mappings covered by API and tool-boundary regression tests |
| ST-08 | P0 (resolved) | `/api/analyze` persistence | Post-compute save failures return the valid analysis with `analysis_id=null` and structured persistence diagnostics | Keep direct, synchronous-chat, and streaming-chat failure tests |
| ST-09 | P0 | Chat analysis tool call | Any finance tool error terminates the chat preparation path | Return and explain the structured degraded/unavailable assessment |
| ST-10 | P0 | Finance tool executor | The entire analysis has one 90-second timeout and discards late partial work | Add bounded stage budgets and persist usable intermediate snapshots |
| ST-11 | P0 | Frontend `requestJson` | Non-2xx responses are converted to an exception; no structured partial result can render | Render assessment status independently from transport and persistence warnings |
| ST-12 | P0 (resolved) | `ValidatedSnapshotStore` | Normalized snapshots enforce identity, source, versions, age, hash, row count, and dataset validation | Keep corruption, staleness, malformed-response, and fallback tests |
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

1. Implement stock, fund, and index deterministic fallback matrices.
2. Return structured unavailable results for terminal no-data cases.
3. Render complete, degraded, stale, unavailable, and persistence states in the frontend.
4. Add remaining route-mismatch, timeout, and partial-tool failure injection tests.

Strategy factors and weights remain frozen until these stability gates pass.
