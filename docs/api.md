# jztz_v17 — HTTP API Reference

> Machine-readable spec: [`openapi.json`](./openapi.json)
> Interactive docs: **<http://localhost:5559/api/docs>**

This is the human-readable companion to the OpenAPI 3.0 spec. It
focuses on the *what* and *why* of each endpoint, the response
shapes you'll see in practice, and concrete curl / Python snippets.

---

## Table of contents
1. [Conventions](#conventions)
2. [Error model](#error-model)
3. [Rate limits & caching](#rate-limits--caching)
4. [Endpoints](#endpoints)
   - [Health & observability](#health--observability)
   - [Picker](#picker)
   - [Market & search](#market--search)
   - [System](#system)
   - [Portfolio CRUD](#portfolio-crud)
   - [Export](#export)
5. [Realtime stream (`/api/sse`)](#realtime-stream-apisse)
6. [Examples](#examples)

---

## Conventions

| Item        | Value                                          |
|-------------|------------------------------------------------|
| Base URL    | `http://localhost:5559` (dev) / your prod host |
| Content type| `application/json; charset=utf-8`              |
| Auth        | None today (single-user local install). Add `X-API-Key` header support in v20. |
| Date format | ISO-8601 (`2026-06-02` or `2026-06-02T09:30:00`) |
| Money       | CNY ¥, floats unless otherwise noted            |
| Encoding    | All non-ASCII input/output is UTF-8             |
| IDs         | Position IDs are `p_<timestamp>_<8hex>`, e.g. `p_1717344000_abcd1234` |

All responses share the same envelope (defined in `openapi.json#/components/schemas/ApiResponse`):

```json
{
  "ok": true,
  "data": { ... },
  "request_id": "4a7b4d90bd824e1d"
}
```

`request_id` is a per-request correlation id — copy it into
bug reports and you'll get a fast lookup in the logs.

---

## Error model

When something goes wrong, `ok` flips to `false` and `data` is
replaced by `error`:

```json
{
  "ok": false,
  "error": "missing `type` query parameter",
  "request_id": "8442bf6a16b944a9"
}
```

| Status | Meaning                                                  |
|--------|----------------------------------------------------------|
| 200    | Success                                                  |
| 400    | Bad request (validation, missing param)                  |
| 404    | Resource not found (e.g. unknown stock code)             |
| 500    | Internal error — see logs with the returned `request_id` |
| 503    | Service unavailable (only on `/api/ready`)                |

**Best practice:** treat 5xx as retryable (with backoff) and 4xx as
client bugs that won't be fixed by retrying.

---

## Rate limits & caching

There are no application-level rate limits in v20 — the deploy
target is single-user / small-team behind a trusted network. If
you expose the API publicly, front it with nginx and add
`limit_req_zone` (see `DEPLOY.md`).

The picker endpoints cache their results in `data/industry_cache.json`
and `data/state_store.db` (SQLite) for 5–60 minutes depending on
endpoint. Cached responses are essentially free (≪ 100 ms). Cold
scans of 5000+ stocks take 10–30 s.

Set `Cache-Control: no-store` if you need live data on every
request (or hit the `*_run` variants — see below).

---

## Endpoints

### Health & observability

#### `GET /api/live` — liveness
Always `200` if the process is alive. Used by Docker / k8s.

```bash
curl -s localhost:5559/api/live
# {"status":"alive","ts":1717344000}
```

#### `GET /api/ready` — readiness
`200` only when filesystem + cache are healthy. Eastmoney is
**non-critical** — the system can still serve cached picks when
the upstream is down.

Returns `503` with the same body when something is wrong; the
orchestrator should keep the pod out of the load-balancer pool.

#### `GET /api/health` — detailed health
Never returns 5xx; the worst you'll get is `status: "degraded"`.
Includes circuit-breaker state per upstream:

```json
{
  "status": "ok",
  "version": "v20",
  "components": {
    "filesystem": {"status": "ok", "logs": "writable", "data": "writable"},
    "cache":      {"status": "ok", "keys": 42},
    "eastmoney":  {"status": "ok", "http": 200, "latency_ms": 138}
  },
  "circuits": {
    "eastmoney_quote": {"state": "CLOSED",   "failures": 0, "opened_at": null},
    "akshare":         {"state": "HALF_OPEN","failures": 3, "opened_at": "2026-06-02T09:00:12"}
  },
  "ts": 1717344000
}
```

#### `GET /api/metrics` — Prometheus exposition
Returns metrics in the Prometheus text exposition format (v0.0.4).
Scrape with a Prometheus server or just `curl … | grep …`.

Currently exposed:

| Metric                         | Type      | Notes                                                |
|--------------------------------|-----------|------------------------------------------------------|
| `stockpicker_cache_hits_total` | counter   | `key_prefix` label                                   |
| `stockpicker_cache_misses_total` | counter | `key_prefix` label                                   |
| `stockpicker_circuit_state`    | gauge     | 0=closed, 1=half-open, 2=open; `name` label          |
| `stockpicker_circuit_trips_total` | counter | `name` label                                         |
| `stockpicker_picker_runs_total` | counter  | `picker` label                                       |
| `stockpicker_sse_connections`  | gauge     | current count of open SSE streams                    |
| `stockpicker_http_requests_total` | counter | `method`, `path`, `status` labels                    |
| `stockpicker_http_request_duration_seconds` | histogram | `method`, `path` labels              |
| `stockpicker_external_api_latency_seconds` | histogram | `upstream` label                  |
| `stockpicker_picker_duration_seconds` | histogram | `picker` label                               |
| `process_*`                    | various   | CPU, RSS, open FDs, threads, uptime                  |

A starter Grafana dashboard lives in `docs/dashboards/` (TODO v20).

---

### Picker

#### `GET /api/market?top=20` — full-market top N
Scan 5000+ A-shares, score via the 5-factor model
(value 36 % / quality 11 % / growth 8 % / momentum 12 % / sentiment 33 %),
return the top N. Default `top=20`, max 100.

```bash
curl -s 'localhost:5559/api/market?top=5' | jq '.data.results[].code'
```

Each result includes `code`, `name`, `price`, `change_pct`,
`score`, factor sub-scores, fundamentals, and the timestamp of
the underlying quote.

#### `GET /api/quote?code=000001.SZ` — single quote
Realtime price for a single code. The `.SZ` / `.SH` suffix is
optional — if omitted, the route looks up the right exchange from
the `code` prefix.

#### `GET /api/stock_detail?code=000001` — fundamentals
Comprehensive single-stock view: fundamentals, technicals, news
snippets, and last 30 days of OHLCV.

#### `GET /api/daily_pick` — daily pick (cached)
Returns the morning (9:26) and afternoon (14:30) sessions'
top-5 picks. Cached all day; first request after the slot
triggers a fresh run.

#### `POST /api/daily_pick_run` — trigger daily pick
Synchronously re-runs the picker (5–30 s). Use this if the cache
is stale (e.g. after a manual rebalance). Body:

```json
{ "session_type": "morning" }   // or "afternoon"
```

#### `GET /api/auction_pick` / `POST /api/auction_preselect`
Auction-style low-PB low-PE screen. The preselect endpoint runs
the full 5000-stock scan and returns ~100 candidates; the cached
endpoint returns the trimmed top-10 with sector breakdown.

#### `GET /api/strong_pick`, `GET /api/wp2_pick`
Two complementary screens:
* `strong_pick` — momentum + volume + break-out detection.
* `wp2_pick`   — capital flow (主力 / 散户 net flow).

---

### Market & search

#### `POST /api/search` — name / code search
```bash
curl -s -X POST localhost:5559/api/search \
  -H 'Content-Type: application/json' \
  -d '{"keyword": "平安"}'
```

#### `GET /api/search_stock?q=000001` — code lookup (GET)
A GET version of the above for browser-side use. Returns the
same payload.

#### `GET /api/industries` — industry list
List of SW-level industries with median PE/PB and the per-industry
PE threshold the picker uses for valuation scoring.

---

### System

#### `GET /api/cache/stats`
Hit / miss / size stats from `modules/cache_manager`. Useful for
debugging "why is this request so slow".

#### `GET /api/news?limit=20`
Latest market news aggregated from Eastmoney + Sina + CLS. Each
item has `title`, `url`, `source`, `published_at`, and a
`summary` field that's 1–3 sentences.

#### `POST /api/ai/analyze`
Body: `{"code": "000001"}`. Calls the LLM-backed analyzer
(`modules.ai_analyzer`) for a short qualitative report. May be
slow (5–20 s) and is rate-limited by your LLM provider.

#### `GET /api/openapi.json`, `GET /api/docs`
The spec and Swagger UI. Useful for client generation:

```bash
npx @openapitools/openapi-generator-cli generate \
  -i http://localhost:5559/api/openapi.json \
  -g python -o ./client-python
```

---

### Portfolio CRUD

All under `/api/portfolio`. Stored in `data/portfolio.json` (a
plain JSON file with an atomic temp-file replace). Positions are
keyed by a short, sortable, URL-safe ID.

#### `GET /api/portfolio`
List all positions. Add `?pnl=true` to augment with floating
P&L fields (uses the latest live-quote snapshot from cache).

#### `POST /api/portfolio`
Add a new position:

```json
{
  "code": "000001",
  "name": "平安银行",
  "shares": 1000,
  "cost": 10.50,
  "buy_date": "2026-05-01",
  "notes": "value pick"
}
```

**Validation:** `code` must be 6 digits, `shares > 0`,
`cost ≥ 0`, `buy_date` is `YYYY-MM-DD` and a real calendar date.
Two adds for the same `(code, buy_date)` merge: shares add and
cost becomes the weighted average.

Response: `201 Created` with the new position including its
auto-assigned `id`.

#### `GET /api/portfolio/<id>`
Fetch one position. `?pnl=true` adds current-price valuation.

#### `PUT /api/portfolio/<id>`
Partial update — only fields you send are changed. Returns the
updated position.

#### `DELETE /api/portfolio/<id>`
Remove. Idempotent; returns 404 if the ID doesn't exist.

#### `GET /api/portfolio/summary`
Aggregates: count, total shares, total cost, total market value,
total profit, profit %, and how many positions were successfully
valued.

#### `GET /api/portfolio/export?format=csv|xlsx`
Quick download shortcut. Same encoders as `/api/export`; supports
the same `?pnl=true` augmentation.

---

### Export

#### `GET /api/export?type=…&format=…`
Generic exporter. Types: `daily_quotes`, `live_quotes`, `auction_quotes`.
Formats: `csv` (UTF-8 with BOM) or `xlsx`.

```bash
# Morning picks as Excel
curl -sOJ 'localhost:5559/api/export?type=daily_quotes&format=xlsx&session=morning'

# All live quotes as CSV
curl -sOJ 'localhost:5559/api/export?type=live_quotes&format=csv'
```

The response `Content-Disposition` uses RFC 5987 so the filename
is correct in both English and Chinese browsers.

#### `GET /api/export/types`
Lightweight metadata about what types / formats / sessions are
supported. Useful for building a download dialog without hard-coding.

---

## Realtime stream (`/api/sse`)

```bash
curl -N localhost:5559/api/sse
```

Long-lived Server-Sent Events stream. Emits a snapshot of picker
data availability (which sessions are filled, last update time,
circuit-breaker state). Heartbeat every 30 s. Close the connection
on the client when navigating away.

> ⚠️ **Reverse proxy note**: nginx / Caddy MUST set
> `proxy_buffering off;` and `proxy_read_timeout 3600s;` for this
> endpoint or you'll see buffering + 504s. See `DEPLOY.md §3`.

```js
// Browser
const es = new EventSource('/api/sse');
es.addEventListener('pick', e => console.log(JSON.parse(e.data)));
es.addEventListener('heartbeat', () => {/* still alive */});
```

---

## Examples

### Top 5 picks for today, with all factor sub-scores
```bash
curl -s 'localhost:5559/api/market?top=5' \
  | python -c "import json,sys; \
r=json.load(sys.stdin); \
[print(f\"{s['code']} {s['name']:<8} score={s['score']:.1f}  PE={s['pe']:.1f}  ROE={s['roe']:.1f}%\") \
 for s in r['data']['results']]"
```

### Add a position, then download the portfolio as Excel
```bash
# Add
curl -s -X POST localhost:5559/api/portfolio \
  -H 'Content-Type: application/json' \
  -d '{"code":"000001","name":"平安银行","shares":1000,"cost":10.5}'

# Download
curl -sOJ 'localhost:5559/api/portfolio/export?format=xlsx'
```

### Prometheus: rate of cache misses in the last 5 min
```promql
rate(stockpicker_cache_misses_total[5m])
```

### Python client (using the OpenAPI spec)
```bash
pip install openapi-python-client
openapi-python-client generate --path docs/openapi.json
```

```python
from jztz_v17_client import AuthenticatedClient
from jztz_v17_client.api.market import get_market

client = AuthenticatedClient(base_url="http://localhost:5559", token="")
res = get_market.sync(client=client, top=10)
if res.ok:
    for s in res.data["results"]:
        print(s["code"], s["name"], s["score"])
```

---

## Versioning

* API version lives in `openapi.json#/info/version` (currently
  `19.0.0`) and in the Flask `version` field returned by
  `/api/health`.
* Breaking changes bump the major version, add a `Sunset` header
  to the old endpoint for 90 days, and are documented in
  `CHANGELOG.md` with a migration snippet.

## See also
* [`architecture.md`](./architecture.md) — 4 Mermaid diagrams of the system
* [`deployment.md`](./deployment.md) — production deployment guide
* [`../DEPLOY.md`](../DEPLOY.md) — single-host quickstart
* [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — dev workflow + PR checklist
