# jztz_v17 v20.0.0 Release Push Script (PowerShell)
# Use this if .bat doesn't work for you, or if you prefer PowerShell.
#
# Pre-flight:
#   1. Make sure you're on the latest working copy (git pull if needed)
#   2. Make sure git credentials are cached (git config --global credential.helper manager)
#
# Run:  powershell -ExecutionPolicy Bypass -File .\push_v20_release.ps1

$ErrorActionPreference = 'Stop'
Set-Location D:\UI\jztz_v17

function Write-Section($msg) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}

Write-Section "jztz_v17 v20.0.0 Release Push"

# 1. Verify repo
Write-Host "[1/6] Verifying local state..." -ForegroundColor Yellow
$branch = git rev-parse --abbrev-ref HEAD
Write-Host "      branch: $branch" -ForegroundColor Gray
git log --oneline -3

# 2. Verify v20 commits
Write-Host "[2/6] Verifying v20 commits exist..." -ForegroundColor Yellow
$featCommit = git rev-parse --verify 2a44031 2>$null
$docsCommit = git rev-parse --verify e27a022 2>$null
if (-not $featCommit -or -not $docsCommit) {
    Write-Host "ERROR: v20 commits not found locally." -ForegroundColor Red
    Write-Host "  2a44031: $featCommit"
    Write-Host "  e27a022: $docsCommit"
    exit 1
}
Write-Host "      OK - both v20 commits present." -ForegroundColor Green

# 3. Check for uncommitted changes
Write-Host "[3/6] Checking working tree..." -ForegroundColor Yellow
$status = git status --porcelain
if ($status) {
    Write-Host "WARNING: Working tree has uncommitted changes:" -ForegroundColor Yellow
    git status --short
    $cont = Read-Host "Continue anyway? (y/N)"
    if ($cont -ne 'y') { exit 1 }
}

# 4. Push commits
Write-Host "[4/6] Pushing commits to origin/main..." -ForegroundColor Yellow
git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: git push failed." -ForegroundColor Red
    exit 1
}

# 5. Create tag
Write-Host "[5/6] Creating annotated tag v20..." -ForegroundColor Yellow
git tag -d v20 2>$null | Out-Null
$tagMsg = @"
Release v20.0.0

Phase 6 (optimization-v19) deliverables:
- Auth (bcrypt + Flask session) with CSRF + role-gated admin
- WeCom notifier with console fallback
- CSV/Excel exporter with formula-injection guard
- Portfolio tracking with weighted-average cost merge
- Backtest engine (equal-weight top-N, Sharpe, max DD, win rate)
- Prometheus /metrics (pure-Python)
- OpenAPI 3.0.3 + Swagger UI
- CI (lint + multi-Py test + e2e + GHCR release)
- 458 tests passing (up from 247), 87.5% coverage on new modules

Security fixes:
- CSV formula injection guard
- User-enumeration timing guard
- CSRF dead code consolidation
- DEFAULT_ADMIN_PASSWORD bumped to 'admin123' (8 chars)

See CHANGELOG.md for full details.
"@
git tag -a v20 -m $tagMsg

# 6. Push tag
Write-Host "[6/6] Pushing tag v20 to origin..." -ForegroundColor Yellow
git push origin v20
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: tag push failed." -ForegroundColor Red
    exit 1
}

Write-Section "v20.0.0 release pushed successfully!"
Write-Host ""
Write-Host "  Repo:  https://github.com/kingsmokez/jztz_v17" -ForegroundColor Green
Write-Host "  Tag:   https://github.com/kingsmokez/jztz_v17/releases/tag/v20" -ForegroundColor Green
Write-Host "  CI:    https://github.com/kingsmokez/jztz_v17/actions" -ForegroundColor Green
Write-Host ""
Write-Host "  Next: Trigger CI by opening the Actions tab, or wait" -ForegroundColor Gray
Write-Host "         for any tag-push workflow to auto-run." -ForegroundColor Gray
Write-Host ""
