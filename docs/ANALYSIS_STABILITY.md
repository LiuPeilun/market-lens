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

## Deterministic Fallback Matrices

Fallback routing is implemented in `market_lens/valuation/fallback_matrix.py`
and versioned as `fallback-matrix-v2`. The LLM cannot select, reorder, or skip
these routes. Each analysis exposes the executed trace both at
`fallback_matrices` and `assessment.data_quality.fallback_matrices`.

Every trace contains the declared source, admission and stop condition, timeout
budget, output method, observed status, stable reason code, selected step, and
terminal reason. `StageExecutor` enforces each timeout as a hard deadline.
Nested fund-holdings and index routes use a shared parent deadline, so child
steps receive the smaller of their own declared budget and the remaining parent
budget. Timed-out work cannot replace the current result; verified source-level
LKG writes remain independently available to later requests.

### Stock Matrix

| Order | Step | Source | Admission and stop condition | Budget | Output |
| ---: | --- | --- | --- | ---: | --- |
| 1 | `stock_valuation_history` | Eastmoney valuation history or exact validated LKG | Verified factors pass the existing scoring gates | 45s | `fundamental_valuation` |
| 2 | `stock_price_history` | Eastmoney adjusted history or exact validated LKG | Verified rows cover the requested interval | 45s | Performance history |
| 3 | `valuation_price_projection` | Positive dated closes already present in verified valuation rows | At least one valid derived price bar exists | 1s | Performance-only fallback |
| 4 | `stock_terminal` | Deterministic assessment contract | No verified price series remains, or no valuation score passes | 1s | Structured `unavailable` |

Price history is not a substitute for stock fundamental valuation. It preserves
performance output and an analysis date; when valuation factors remain
insufficient, the assessment is still `unavailable`.

### Fund Matrix

| Order | Step | Source | Admission and stop condition | Budget | Output |
| ---: | --- | --- | --- | ---: | --- |
| 1 | `exchange_fund_price_history` | Eastmoney adjusted exchange history or exact validated LKG | Verified exchange-traded series exists | 45s | Fund performance |
| 2 | `fund_nav_history` | Eastmoney Pingzhongdata/F10 NAV or exact validated LKG | Verified ordinary NAV series exists | 60s | Fund performance |
| 3 | `fund_holdings_valuation` | Verified fund, target ETF, or index holdings route | Existing weighted factor gates produce a score | 60s | `holdings_valuation` |
| 4 | `fund_index_matrix` | The independent index matrix below | Official fundamentals or a verified price proxy produces a score | 90s | Index fallback result |
| 5 | `fund_terminal` | Deterministic assessment contract | No verified fund or index method produces a score | 1s | Structured `unavailable` |

Fund performance and valuation are independent. A NAV series can preserve
return and drawdown output without authorizing a valuation score. Active funds
mark the index matrix `tracked_index_not_applicable` instead of reporting a
false index-source failure.

### Index Matrix

| Order | Step | Source | Admission and stop condition | Budget | Output |
| ---: | --- | --- | --- | ---: | --- |
| 1 | `official_index_fundamentals` | Official CSI valuation history and complete weights | Identity, date, history length, and complete-weight gates all pass | 60s | `index_fundamental_valuation` |
| 2 | `target_etf_nav_history` | Verified target ETF NAV or exact validated LKG | The resolved target ETF has a valid series | 60s | Price-proxy input |
| 3 | `sina_index_price_history` | Sina index history | Resolved index identity and valid rows are available | 45s | Price-proxy input |
| 4 | `eastmoney_index_price_history` | Eastmoney index history | Resolved quote identity and valid rows are available | 45s | Price-proxy input |
| 5 | `index_price_position_proxy` | The first verified price input above | Existing price-position sample gates produce a score | 1s | `price_position_proxy` |
| 6 | `index_terminal` | Deterministic assessment contract | Neither official fundamentals nor a price proxy produces a score | 1s | Structured `unavailable` |

Official index valuation and holdings valuation keep separate traces. Optional
fund-name failure, holdings-route failure, official-source failure, and route
rejection use normalized reason codes; volatile upstream exception messages do
not enter `fallback_reasons`.

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

## Source Health And Circuit State

Outbound market-data transport is observed per normalized upstream hostname. The
process-wide registry records one outcome for each completed network request,
after the adapter's configured retries are exhausted or one attempt succeeds.
HTTP cache hits and validated LKG reads do not count as new source successes and
remain available while a source circuit is open.

`GET /health` exposes `data_source_status` and a `data_sources` snapshot containing:

- completed request, success, failure, rejection, and success-rate counters;
- consecutive failures and the last attempt, success, and failure timestamps;
- `closed`, `open`, or `half_open` circuit state and the next probe time;
- only a stable `last_error_code`, never a URL, request parameters, or raw exception.

The default circuit opens after three completed request failures. After a 60-second
cooldown, exactly one request becomes the half-open probe; a successful probe closes
the circuit and resets consecutive failures, while a failed probe opens it again.
Both values are configurable. State is intentionally process-local and resets on
service restart, so stale historical failures cannot lock a source after deployment.

Source health describes upstream network availability. It does not replace dataset
identity, structure, date, completeness, or valuation-quality validation.

## Current Interruption Audit

Priority definitions:

- `P0`: Can prevent a supported request from returning an analysis result.
- `P1`: Can remove a useful fallback or hide material diagnostics.
- `P2`: Does not interrupt the result but weakens observability.

| ID | Priority | Location | Current behavior | Required follow-up |
| --- | --- | --- | --- | --- |
| ST-01 | P0 (resolved) | `MarketAnalysisAgent.analyze`, stock valuation load | Source and LKG failure enter the stock matrix; valid price performance is retained while valuation returns structured `unavailable` | Keep source-failure and no-score regression tests |
| ST-02 | P0 (resolved) | `MarketAnalysisAgent.analyze`, stock price load | Exact validated LKG, valuation-derived bars, and structured terminal output are deterministic | Keep price-failure and no-data regression tests |
| ST-03 | P0 (resolved) | `MarketAnalysisAgent.analyze`, fund NAV load | Exchange price, ordinary NAV, exact validated LKG, and structured terminal output are deterministic | Keep all-routes-failed regression tests |
| ST-04 | P1 (resolved) | `MarketAnalysisAgent.analyze`, fund name load | Code-based analysis continues and records an optional-source diagnostic | Keep valid-result preservation tests |
| ST-05 | P1 (resolved) | REIT profile resolution | Profile failure returns a REIT-scoped structured `unavailable` result and fund trace | Keep dedicated REIT failure injection coverage |
| ST-06 | P1 (resolved) | Index price fallback discovery | Discovery failure is isolated and cannot discard an existing holdings result | Keep holdings-result preservation tests |
| ST-07 | P0 (resolved) | `/api/analyze` tool boundary | Tool failures retain stable category and retryability; only invalid requests map to HTTP 400 | Keep category mappings covered by API and tool-boundary regression tests |
| ST-08 | P0 (resolved) | `/api/analyze` persistence | Post-compute save failures return the valid analysis with `analysis_id=null` and structured persistence diagnostics | Keep direct, synchronous-chat, and streaming-chat failure tests |
| ST-09 | P0 (resolved) | Chat analysis tool call | Classified finance-tool failures become schema-valid, persistable `unavailable` analyses; synchronous and streaming chat explain stable category and retryability without exposing raw errors | Keep upstream, data-unavailable, internal-error, streaming, and persistence regression tests |
| ST-10 | P0 (resolved) | Finance stage executor | Matrix v2 enforces independent and shared-parent hard deadlines; completed price, NAV, valuation, and validated LKG work is retained while timed-out stages emit stable diagnostics | Keep hard-timeout, parent-budget, and intermediate-result regression tests |
| ST-11 | P0 (resolved) | Frontend analysis rendering | Core analysis renders by `complete`, `degraded`, or `unavailable`; optional chart, table, and persistence failures remain separate warnings with retry actions | Keep frontend lint/build and degraded-state regression checks |
| ST-12 | P0 (resolved) | `ValidatedSnapshotStore` | Normalized snapshots enforce identity, source, versions, age, hash, row count, and dataset validation | Keep corruption, staleness, malformed-response, and fallback tests |
| ST-13 | P1 (resolved) | Fund holdings route | Route failure is preserved as `fund_holdings_route_unavailable` in route metadata, assessment, and trace | Keep stable-code regression tests |
| ST-14 | P2 (resolved) | Source health | Per-host request outcomes, last success/failure, consecutive failures, safe error codes, and circuit state are exposed through `/health`; open circuits retain cache and validated LKG access | Keep state-machine, transport-boundary, and safe-output regression tests |

### Fund index routing and partial holdings

Tracked-index requests must be routed only to a provider that is known to cover
the index namespace. In particular, Shenzhen `399xxx` indices such as `399006`
must not be sent to the CSI official provider. Until a verified Shenzhen
official adapter is available, the route records
`official_index_provider_not_supported` and continues through the validated
target-ETF fallback.

The fund-holdings stage uses a shared deadline. If that deadline expires,
member analyses completed before the timeout remain eligible for aggregation;
the timeout reason is retained in the execution trace. Overall confidence is
the conservative minimum across dimensions that have a finite numeric score.
An unscored optional dimension is diagnostic absence, not zero-confidence
evidence, and is excluded from the aggregate. When no dimension is scored, the
result is `unavailable` and clients must display it as not assessable rather
than as `0%`.

## Existing Safe Isolation

The following paths already degrade without aborting the main result:

- Stock profile, financial factors, peers, dividends, industry snapshot, and detailed
  statements.
- Fund product information, exchange-price fallback, holdings route, official index
  data route, and per-holding stock analysis.
- Individual REIT price, financial, distribution, and notice datasets after the REIT
  profile has been resolved.

These paths retain stable per-analysis reason codes, while their network hosts are
also covered by aggregate source-health reporting.

## Next Implementation Order

After the deterministic matrices:

1. Complete: render complete, degraded, stale, unavailable, and persistence states in the frontend.
2. Complete: add route-mismatch, timeout, and partial-tool failure injection tests.
3. Complete: enforce declared timeout budgets as independent and shared-parent stage deadlines.
4. Complete: make chat explain a structured unavailable assessment when the finance preparation path fails.
5. Complete: add source-health and circuit-state diagnostics without changing valuation scoring.

Strategy factors and weights remain frozen until these stability gates pass.

## Fault Injection Coverage

The V2-5D fault-injection gate is covered by deterministic unit and boundary
tests. These tests verify the returned contract, fallback trace, enforced hard
deadline, shared parent budget, and preservation of completed intermediate data.

| Failure mode | Verified behavior | Primary coverage |
| --- | --- | --- |
| Disconnect | Critical source failure uses validated LKG when eligible, otherwise continues through the deterministic matrix | `tests/test_validated_snapshots.py`, `tests/test_fallback_matrix.py` |
| Timeout | Hard deadlines and shared parent budgets are enforced; completed stock, fund, and index inputs survive later-stage timeout without exposing raw upstream details | `tests/test_stage_executor.py`, `tests/test_fallback_matrix.py` |
| Empty response | Empty stock valuation and price rows reach the stock terminal contract with explicit stable reason codes | `tests/test_fallback_matrix.py` |
| Malformed response | Malformed live rows cannot replace validated LKG; malformed index-provider output does not block the next provider | `tests/test_validated_snapshots.py`, `tests/test_fallback_matrix.py` |
| Stale snapshot | Stale LKG is rejected by exact identity, source, version, and maximum-age gates | `tests/test_validated_snapshots.py` |
| Persistence failure | Direct analysis, synchronous chat, and streaming chat preserve computed output and expose persistence diagnostics | `tests/test_api_app.py` |
| Partial tool failure | One failed tool result and one successful sibling result are both returned to the LLM, which can continue answering from available evidence | `tests/test_fault_injection.py` |
| Chat finance-tool failure | Upstream, data-unavailable, and internal failures return schema-valid `unavailable` analysis; streaming completes and API persistence retains the diagnostic | `tests/test_chat_agent.py`, `tests/test_api_app.py` |
| Repeated source failure | Completed request failures open a per-host circuit; cooldown permits one half-open probe, successful recovery closes it, and `/health` exposes only safe aggregate diagnostics | `tests/test_source_health.py` |

The ST-01 through ST-14 reliability gates are complete. Future source adapters
must use the same request boundary or provide equivalent health reporting before
they are enabled in production routing.
