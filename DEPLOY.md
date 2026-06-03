# Deployment Guide

This document covers four deployment paths for the **jztz_v17** Flask
application, from local development to production-grade Docker.

> **TL;DR** — for a single-host production deploy:
> ```bash
> cp .env.example .env  # edit WECOM_WEBHOOK etc.
> docker compose up -d
> curl http://localhost:5559/api/live
> ```

---

## 1. Local development

### 1.1 Python virtualenv

```bash
git clone https://github.com/kingsmokez/jztz_v17.git
cd jztz_v17

# Windows
python -m venv .venv
.venv\Scripts\activate

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -r requirements-dev.txt   # ruff / mypy / playwright / pytest-cov
cp .env.example .env                 # then edit secrets

python web_app.py                    # http://localhost:5559
```

### 1.2 Configuration (`.env`)

The app reads configuration from environment variables via
`python-dotenv` at startup.  See `.env.example` for the full list.
**Do not commit your real `.env` to git.**

| Variable | Required | Description |
|---|---|---|
| `FLASK_SECRET_KEY` | yes | Session cookie signing key (32+ random bytes) |
| `WECOM_WEBHOOK_URL` | no | Enterprise WeChat bot webhook for daily-pick push |
| `LOG_LEVEL` | no | One of `DEBUG` / `INFO` / `WARNING` / `ERROR` (default `INFO`) |
| `HOST` / `PORT` | no | Bind address (default `0.0.0.0:5559`) |

### 1.3 Running tests

```bash
pytest                        # unit + integration
pytest tests/e2e/ -m e2e      # end-to-end (needs a running server)
pytest --cov=modules,routes   # coverage report
```

---

## 2. Production deploy

### 2.1 Docker (single host, recommended)

```bash
docker build -t jztz_v17:v20 .
docker run -d --name jztz-app \
    -p 5559:5000 \
    --env-file .env \
    -v $(pwd)/logs:/app/logs \
    --restart unless-stopped \
    jztz_v17:v20

docker logs -f jztz-app
curl http://localhost:5559/api/live
```

Image size: **~250 MB** (python:3.11-slim + 11 wheels + curl).
Built-in `HEALTHCHECK` hits `/api/live` every 30s.

### 2.2 Docker Compose

```bash
cp .env.example .env   # edit secrets
docker compose up -d
docker compose ps      # confirm 'healthy'
```

Logs rotate automatically (JSON driver, 5 × 20 MB files).
Edit `docker-compose.yml` to enable the optional `redis` sidecar for
distributed caching.

### 2.3 systemd (bare metal / VM)

For hosts without Docker, run gunicorn behind a systemd unit:

`/etc/systemd/system/jztz.service`:
```ini
[Unit]
Description=jztz_v17 stock picker
After=network.target

[Service]
Type=simple
User=jztz
WorkingDirectory=/opt/jztz_v17
EnvironmentFile=/opt/jztz_v17/.env
ExecStart=/opt/jztz_v17/.venv/bin/gunicorn \
    --workers 2 --threads 4 \
    --bind 0.0.0.0:5559 \
    --worker-class gthread \
    --access-logfile - --error-logfile - \
    web_app:app
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jztz
sudo systemctl status jztz
```

### 2.4 Windows (waitress)

`waitress` is a pure-Python WSGI server with no native deps; ideal on
Windows where gunicorn is unsupported.

```bash
pip install waitress==3.0.2
waitress-serve --port=5559 --threads=4 web_app:app
```

For auto-start, register as a Windows Service with `nssm.exe` or
`sc.exe create`.

---

## 3. Nginx reverse proxy

For TLS termination and buffering.  The SSE endpoint at `/api/sse`
**must** have `proxy_buffering off` and `proxy_read_timeout` raised.

`/etc/nginx/sites-available/jztz`:
```nginx
upstream jztz_app {
    server 127.0.0.1:5559 fail_timeout=0;
}

server {
    listen 80;
    server_name stocks.example.com;
    client_max_body_size 16M;

    # Static assets — let nginx serve them directly if you copy /static out
    location /static/ {
        alias /opt/jztz_v17/static/;
        expires 7d;
        access_log off;
    }

    # SSE — critical: disable buffering and raise timeouts
    location /api/sse {
        proxy_pass         http://jztz_app;
        proxy_http_version 1.1;
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 24h;
        proxy_set_header   Connection        "";
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass         http://jztz_app;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## 4. TLS (Let's Encrypt)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d stocks.example.com
# Auto-renewal is enabled by certbot.timer
sudo systemctl status certbot.timer
```

---

## 5. Monitoring

The app exposes three health endpoints (see `routes/health.py`):

| Endpoint | Purpose | Probe type |
|---|---|---|
| `GET /api/live` | Process is up | Liveness (returns 200 always) |
| `GET /api/ready` | Dependencies OK | Readiness (503 if cache/filesystem down) |
| `GET /api/health` | Detailed report | Status page / dashboard |

### 5.1 Prometheus scrape

The app exposes Prometheus metrics at `GET /metrics` (text/plain,
version 0.0.4 format). It implements `Counter`, `Gauge`, and
`Histogram` primitives in pure Python (no `prometheus_client`
dependency), with these exported series:

| Metric | Type | Labels | What it measures |
|---|---|---|---|
| `jztz_cache_hits_total` | counter | — | Cache lookups served from memory |
| `jztz_cache_misses_total` | counter | — | Cache lookups that fell through |
| `jztz_cache_evictions_total` | counter | — | Items evicted (TTL or LRU) |
| `jztz_cache_size` | gauge | — | Current items in the cache |
| `jztz_circuit_breaker_state` | gauge | name | 0=closed, 1=open, 2=half_open |
| `jztz_circuit_breaker_opens_total` | counter | name | Times the breaker tripped |
| `jztz_circuit_breaker_short_circuits_total` | counter | name | Calls rejected while open |
| `jztz_http_request_duration_seconds` | histogram | method, path, status | Latency distribution |

Example `prometheus.yml`:
```yaml
scrape_configs:
  - job_name: jztz
    metrics_path: /metrics
    static_configs:
      - targets: ['jztz-app.internal:5559']
```

For rich application health (cache contents, circuit-breaker details,
disk + memory), prefer `GET /api/health` (JSON).

---

## 6. Log aggregation

Logs are written to `logs/app.log` (rotated, 10 MB × 10 backups).
Each line includes a `request_id` that traces the call across handlers:

```
[2026-06-02 12:34:56] INFO [a1b2c3d4] stock_picker modules.scoring:42 - score=85
```

### 6.1 Vector → Loki

`vector.toml`:
```toml
[sources.jztz_files]
type = "file"
include = ["/opt/jztz_v17/logs/app.log"]

[transforms.jztz_parse]
type = "remap"
inputs = ["jztz_files"]
source = '''
  .request_id = parse_regex!(.message, r"\[(?P<rid>[a-f0-9]+)\]") ?? "-"
  .level = parse_regex!(.message, r"(?P<lvl>DEBUG|INFO|WARNING|ERROR)") ?? "INFO"
'''

[sinks.loki]
type = "loki"
inputs = ["jztz_parse"]
endpoint = "http://loki.internal:3100"
labels.level  = "{{ .level }}"
labels.app    = "jztz_v17"
```

---

## 7. Rollback

```bash
# Docker: pull an older image tag and restart
docker pull jztz_v17:v18
docker tag  jztz_v17:v18 jztz_v17:current
docker compose up -d

# Bare metal: git checkout the previous tag
cd /opt/jztz_v17
git fetch --tags
git checkout v18
sudo systemctl restart jztz
```

Keep at least **3 previous tags** in your registry / git history.

### 7.1 Pre-flight checklist (before triggering a rollback)

1. **Confirm the failure is real** — check `/api/health` and
   `docker logs jztz-app` / `journalctl -u jztz -n 200`. Distinguish
   between an app bug (rollback) and a transient upstream issue
   (e.g. akshare rate limit — wait, don't rollback).
2. **Snapshot current state** — `docker commit jztz-app
   jztz_v17:bad-v20-debug` so you can diff later.
3. **Check the rollback target's health** — pull the old image and
   run its tests in CI before promoting it. The old tag's git
   commit SHA is recorded in the image label.
4. **Communicate** — post in #ops with the incident ID, the
   symptom, the rollback target version, and ETA.

### 7.2 Post-rollback verification

After `docker compose up -d` or `systemctl restart jztz`:

```bash
# 1. Liveness
curl -fsS http://localhost:5559/api/live
# Expect: {"status":"live",...}

# 2. Readiness (will be 503 if cache/FS are broken)
curl -fsS http://localhost:5559/api/ready

# 3. Detailed health
curl -fsS http://localhost:5559/api/health | jq .

# 4. Smoke a real endpoint
curl -fsS http://localhost:5559/api/live_quotes | jq '.count'

# 5. Confirm the rolled-back version is running
docker inspect jztz-app --format '{{ index .Config.Labels "version" }}'
# or
curl -fsS http://localhost:5559/api/version
```

If the smoke tests fail after rollback, the previous version is also
broken — escalate rather than ping-ponging between two bad releases.

### 7.3 Rollback drill (no-prod environment)

Practice rollback safely in any environment with a running app:

```bash
# 1. Record the current "good" version
GOOD=$(curl -s http://localhost:5559/api/version | jq -r .version)

# 2. Make a hypothetical bad change (e.g. a syntax error in a module)
echo 'def syntax_error(:' > modules/_test_bad.py

# 3. Restart — should fail to boot
docker compose restart app
docker logs jztz-app --tail 20  # expect ImportError

# 4. Roll back
git checkout $GOOD -- modules/  # or: docker compose down && \
                                  #   docker tag jztz_v17:previous jztz_v17:current
docker compose up -d

# 5. Verify recovery
curl -fsS http://localhost:5559/api/live
rm -f modules/_test_bad.py   # clean up
```

Run this drill **at least once per release** to confirm your team can
execute the procedure under pressure.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `502 Bad Gateway` from nginx | App process crashed | `docker logs jztz-app` or `journalctl -u jztz -n 200` |
| `/api/health` returns 503 | Cache or filesystem unavailable | Check disk space (`df -h`); restart: `docker compose restart app` |
| SSE connection drops every 5s | nginx buffering enabled | Set `proxy_buffering off` in the `/api/sse` location |
| Slow first request after deploy | akshare cache cold | Hit `/api/market` once to warm up; consider pre-warming in Dockerfile CMD |
| `ModuleNotFoundError: akshare` | requirements not installed | `pip install -r requirements.txt` (in venv or container build) |
| Container exits immediately | gunicorn can't find `web_app:app` | Verify `WORKDIR=/app` and `web_app.py` is at `/app/web_app.py` |

For deeper diagnostics, enable `LOG_LEVEL=DEBUG` in `.env` and tail
`logs/app.log` for the full request trace.
