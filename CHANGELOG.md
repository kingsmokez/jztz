# Changelog

All notable changes to **jztz_v17** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/) — `MAJOR.MINOR.PATCH`.

---

## [20.0.0] — 2026-06-02

### Added (major features)
- **Auth (bcrypt + Flask session)** — `modules/auth.py` + `routes/auth.py`.
  Session-based login, CSRF token on unsafe verbs, role-gated admin endpoints,
  file-backed user store at `data/users.json` with atomic write-then-rename.
- **Wecom (企业微信) notifier** — `modules/notifier.py`. Console fallback
  when `WECOM_WEBHOOK_URL` is unset; markdown truncation respects WeCom's
  4096-byte limit without splitting characters.
- **CSV / Excel export endpoints** — `modules/exporter.py` + `routes/export.py`.
  UTF-8-BOM for Excel-on-Windows, auto-width columns, CJK width heuristic,
  Excel formula-injection guard (prefixes `=+-@` with `'`).
- **Portfolio tracking** — `modules/portfolio.py` + `routes/portfolio.py`.
  File-backed store with de-dup on `(code, buy_date)` and weighted-average
  cost merge, P&L computed against the live-quote cache.
- **Backtest engine** — `modules/backtest.py` + `routes/backtest.py`.
  Pure-Python equal-weight top-N rebalance, deterministic, no I/O. Metrics:
  total/annualized return, Sharpe, max drawdown, win rate, turnover.
- **Prometheus `/metrics`** — `modules/metrics.py` + `routes/metrics.py`.
  Pure-Python Counter / Gauge / Histogram (no `prometheus_client` dep);
  cache and circuit-breaker emit metrics.
- **OpenAPI 3.0.3 + Swagger UI** — `routes/docs.py` + `docs/openapi.json`.
  21 endpoints documented; CDN-hosted Swagger UI at `/api/docs`.
- **CI release job** — `.github/workflows/ci.yml`. Multi-arch image
  (amd64/arm64) pushed to GHCR on tag.
- **Docs** — `docs/architecture.md` (4 Mermaid diagrams), `docs/api.md`
  (human-readable reference), `CONTRIBUTING.md`, expanded `DEPLOY.md` §7
  (pre-flight, post-rollback verification, no-prod drill).

### Changed
- Bumped version from `v19` → `v20` across `routes/health.py`,
  `docs/openapi.json`, `docs/architecture.md`, `docs/api.md`, `DEPLOY.md`.

### Security
- Fixed CSV formula injection in exports.
- Fixed user-enumeration timing in `UserStore.authenticate()` (always
  runs bcrypt via a dummy hash on unknown users).
- Cleaned up CSRF dead code in `modules/auth.py` (duplicate
  `register_csrf_protection`, typo'd `_CsrfReception`).
- Dedicated `_USERNAME_RE` exported from `modules/auth.py` (was
  duplicated in routes).

### Test coverage
- **458 passed, 6 skipped, 0 failed** (up from 247 at v19).
- New pure-Python modules at **87.5% coverage** (backtest 94.3,
  notifier 96.6, metrics 89.8, portfolio 79.7, exporter 79.2).
- bcrypt+coverage instrumentation blocked on Windows (known env
  limitation; workaround: omit + pre-import). Not a code issue.

---

## [19.0.0] — 2026-05-15

- Refactored monolithic `web_app.py` (~220 KB, 5400+ lines) into a
  Flask app-factory with 5 blueprints (`daily`, `auction`, `wp2`,
  `strong`, `ai`) and a thin `web_app.py` (~100 lines).
- Added `modules/config.py` for typed configuration.
- Added `modules/models.py` dataclasses for `StockQuote` /
  `FinancialData` / `StockScore`.
- Added `modules/logger.py` with structured `request_id` tracing.

---

## [18.x] and earlier

See git history (`git log --oneline --reverse`) and
`archive/web_app_py_v18/CHANGES.md`.
