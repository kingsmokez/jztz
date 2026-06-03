# Contributing to jztz_v17

> 智能选股系统 v17 — multi-factor A-share stock picker

Thanks for your interest in contributing. This document is the
shortest path from "I want to help" to "PR merged". Read it once
before opening an issue or PR.

---

## Table of contents
1. [Project structure](#project-structure)
2. [Dev environment setup](#dev-environment-setup)
3. [Lint / format / type-check](#lint--format--type-check)
4. [Test workflow](#test-workflow)
5. [GitFlow & commit conventions](#gitflow--commit-conventions)
6. [PR checklist](#pr-checklist)
7. [Troubleshooting](#troubleshooting)

---

## Project structure

```
jztz_v17/
├── web_app.py                  # Flask app factory + entrypoint
├── smart_stock_picker.py       # CLI entrypoint
├── start_web.bat / .sh         # One-click startup
│
├── modules/                    # Pure-Python business logic
│   ├── stock_picker.py         #   Picker orchestration
│   ├── scoring.py              #   Multi-factor scoring
│   ├── data_fetcher.py         #   Eastmoney/AKShare adapter
│   ├── cache_manager.py        #   TTL cache (with metrics)
│   ├── circuit_breaker.py      #   Per-upstream failure isolation
│   ├── notifier.py             #   WeCom / console notifier
│   ├── portfolio.py            #   File-backed portfolio store
│   ├── exporter.py             #   CSV / XLSX encoders
│   ├── metrics.py              #   Prometheus client (pure-Python)
│   └── …                       #   See docstring headers for each module
│
├── routes/                     # Flask blueprints
│   ├── daily.py                #   Morning / afternoon pick
│   ├── auction.py              #   Auction-style low-PB pick
│   ├── wp2.py                  #   Capital flow
│   ├── strong.py               #   Strong-stock screen
│   ├── ai.py                   #   LLM analysis
│   ├── api.py                  #   Misc JSON APIs
│   ├── health.py               #   /api/{live,ready,health}
│   ├── metrics.py              #   /api/metrics  (Prometheus)
│   ├── docs.py                 #   /api/openapi.json + /api/docs
│   ├── export.py               #   /api/export?type=…&format=…
│   └── portfolio.py            #   /api/portfolio*
│
├── templates/                  # Jinja2 HTML (server-rendered)
├── static/                     # CSS / JS
├── data/                       # JSON persistence
│   ├── industry_cache.json
│   ├── portfolio.json
│   └── …
│
├── tests/                      # pytest (unit + e2e)
│   ├── test_*.py               #   per-module unit tests
│   └── e2e/                    #   end-to-end (slow)
│
├── docs/                       # Project documentation
│   ├── architecture.md         # 4 Mermaid diagrams
│   ├── openapi.json            # 21-endpoint OpenAPI 3.0
│   ├── api.md                  # human-readable API reference
│   └── deployment.md
│
├── .github/workflows/ci.yml    # lint + test (Py 3.10/3.11/3.12) + e2e + build + release
├── Dockerfile / docker-compose.yml
├── DEPLOY.md                   # Production deployment guide
└── pyproject.toml              # ruff / black / mypy / pytest config
```

### Architectural rules of thumb
* **Pickers** live in `modules/`, **HTTP plumbing** lives in `routes/`.
  Routes call modules; modules never import from `routes/`.
* **All public modules expose a typed surface** — keep `from __future__ import annotations` and annotate function signatures.
* **No silent failure.** Logging via `modules.logger.log` is mandatory for unexpected paths; bare `except: pass` is rejected in review.
* **External I/O goes through `cache_manager` + `circuit_breaker`**, not raw `requests` in a picker.

---

## Dev environment setup

### Windows (PowerShell)
```powershell
git clone https://github.com/kingsmokez/jztz_v17.git
cd jztz_v17
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .[dev]            # if you maintain the pyproject
```

### Linux / macOS
```bash
git clone https://github.com/kingsmokez/jztz_v17.git
cd jztz_v17
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Verify the install
```bash
python -c "from web_app import create_app; create_app(); print('OK')"
python -m pytest tests/ -q --ignore=tests/e2e
```

---

## Lint / format / type-check

Configured in `pyproject.toml`. We run them locally before commit
(`pre-commit install`) and the same commands run in CI.

| Tool   | Command                       | What it does                                 |
|--------|-------------------------------|----------------------------------------------|
| ruff   | `ruff check .`                | Lint (E/F/W/I/B/UP)                          |
| black  | `black --line-length 100 .`   | Auto-format                                  |
| mypy   | `mypy modules routes`         | Type-check (best-effort; not strict)         |

Run them in one shot:
```bash
ruff check . && black --line-length 100 --check . && mypy modules routes
```

---

## Test workflow

We use `pytest` with `pytest-cov` and `pytest-asyncio`. Coverage
threshold is set in `pyproject.toml` and enforced in CI.

### Local development
```bash
# Fast unit suite (no E2E, no coverage) — ~5-10 s
pytest tests/ --ignore=tests/e2e -q --no-cov

# Single module
pytest tests/test_portfolio.py -v

# With coverage (mirrors CI)
pytest tests/ --ignore=tests/e2e -q --cov=modules --cov=routes
```

### E2E suite
Slow; runs against a real Flask `test_client` + filesystem + the
mocked Eastmoney fixtures. Run it before opening a PR:
```bash
pytest tests/e2e/ -v
```

### Writing tests
* Mirror the source file: `modules/portfolio.py` → `tests/test_portfolio.py`.
* Use the `client` fixture from `web_app.create_app()` for routes.
* Mock external I/O (Eastmoney / WeCom) — never hit the network in unit tests.
* One assertion concept per test; descriptive method names (`test_update_increments_weight` not `test_update_1`).
* Target ≥ 80 % coverage for any new module.

---

## GitFlow & commit conventions

### Branches
* `main`     — always shippable, protected, deploys on tag.
* `feat/*`   — new feature.
* `fix/*`    — bug fix.
* `chore/*`  — tooling / docs / refactor (no behavior change).
* `release/*` — version-bump + CHANGELOG prep.

### Commit messages
[Conventional Commits](https://www.conventionalcommits.org/) is
enforced by `commitlint` (if installed) and at least lightly
reviewed by humans. Format:

```
<type>(<scope>): <subject>

<body explaining *why*, not *what*>

<footer with "Refs: #123" or "BREAKING CHANGE: ..." if applicable>
```

Common types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`,
`perf`, `ci`.

### Atomic commits
Each commit should leave the repo in a working state (tests pass,
lint clean). Use `git rebase -i main` to squash WIP commits before
pushing. The PR review will look at the squashed history, so a
single, well-explained commit is ideal for small PRs.

---

## PR checklist

Before opening a PR, run through this list — the bot checks most
of it but humans still need to confirm intent.

- [ ] Branch is up to date with `main` (`git rebase main`).
- [ ] `ruff check .` clean.
- [ ] `black --line-length 100 --check .` clean.
- [ ] `mypy modules routes` reports no new errors.
- [ ] `pytest tests/ --ignore=tests/e2e` passes.
- [ ] Coverage for changed files ≥ 80 % (`pytest --cov=…`).
- [ ] New public APIs have a docstring and a test.
- [ ] OpenAPI spec (`docs/openapi.json`) updated if any endpoint changed.
- [ ] No secrets, no real webhook URLs, no real PII in the diff.
- [ ] PR description explains *why* and links the issue.

### Review SLA
* First response within 2 business days.
* Small PRs (< 200 lines) are reviewed same-day.
* Larger PRs may be split before review.

---

## Troubleshooting

### "I can't install any package — ConnectionAbortedError 10053"
The dev environment may be offline. Use the existing pinned
`requirements.txt` versions; if you need a new dep, propose it in
the PR description first so CI can validate it on a real network.

### "pytest collects 0 tests"
Check you're in the project root and the venv is active. The
package layout assumes `tests/` and `modules/` / `routes/` are
siblings — running pytest from elsewhere will skip discovery.

### "Flask `TEMPLATES_AUTO_RELOAD` causes TemplateNotFound"
The Flask app disables template auto-reload in non-debug mode.
For local debugging, run with `FLASK_DEBUG=1` *or* set
`TEMPLATES_AUTO_RELOAD=True` in your `.env`.

### "Eastmoney API times out"
The picker has a circuit-breaker fallback. Check the breaker state
via `/api/health` → `circuits` section, or clear the breaker with
`modules.circuit_breaker.reset_all()`.

---

## License

By contributing, you agree your contributions will be licensed
under the same terms as the project (see `LICENSE` if present, or
default to MIT unless otherwise specified by the maintainers).
