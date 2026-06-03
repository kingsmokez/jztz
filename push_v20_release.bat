@echo off
chcp 65001 >nul
setlocal

echo ============================================================
echo     jztz_v17 v20.0.0 Release Push Script
echo ============================================================
echo.

cd /d D:\UI\jztz_v17

echo [1/6] Verifying local state...
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: Not a git repository. Aborting.
    pause
    exit /b 1
)

echo       branch: %CD%
git rev-parse --abbrev-ref HEAD
git log --oneline -3
echo.

echo [2/6] Verifying v20 commits exist...
git rev-parse --verify 2a44031 >nul 2>&1
if errorlevel 1 (
    echo ERROR: Commit 2a44031 (v20 feat) not found locally.
    echo        Are you on the right branch / working copy?
    pause
    exit /b 1
)
git rev-parse --verify e27a022 >nul 2>&1
if errorlevel 1 (
    echo ERROR: Commit e27a022 (v20 docs) not found locally.
    pause
    exit /b 1
)
echo       OK - both v20 commits present.
echo.

echo [3/6] Checking for unstaged / untracked changes...
git status --porcelain | findstr /R "^" >nul
if not errorlevel 1 (
    echo WARNING: Working tree has uncommitted changes:
    git status --short
    echo.
    set /p cont="Continue anyway? (y/N): "
    if /i not "%cont%"=="y" (
        echo Aborted.
        pause
        exit /b 1
    )
)
echo.

echo [4/6] Pushing commits to origin/main...
git push origin main
if errorlevel 1 (
    echo ERROR: git push failed. Check network / credentials.
    pause
    exit /b 1
)
echo.

echo [5/6] Creating annotated tag v20...
git tag -d v20 >nul 2>&1
git tag -a v20 -m "Release v20.0.0

Phase 6 (optimization-v19) deliverables:
- Auth (bcrypt + Flask session) with CSRF + role-gated admin
- WeCom notifier with console fallback
- CSV/Excel exporter with formula-injection guard
- Portfolio tracking with weighted-average cost merge
- Backtest engine (equal-weight top-N, Sharpe, max DD, win rate)
- Prometheus /metrics (pure-Python)
- OpenAPI 3.0.3 + Swagger UI
- CI (lint + multi-Py test + e2e + GHCR release)
- 458 tests passing (up from 247), 87.5%% coverage on new modules

Security fixes:
- CSV formula injection guard
- User-enumeration timing guard
- CSRF dead code consolidation
- DEFAULT_ADMIN_PASSWORD bumped to 'admin123' (8 chars)

See CHANGELOG.md for full details."

if errorlevel 1 (
    echo ERROR: tag creation failed.
    pause
    exit /b 1
)
echo.

echo [6/6] Pushing tag v20 to origin...
git push origin v20
if errorlevel 1 (
    echo ERROR: tag push failed.
    pause
    exit /b 1
)
echo.

echo ============================================================
echo     v20.0.0 release pushed successfully!
echo ============================================================
echo.
echo     Repo:  https://github.com/kingsmokez/jztz_v17
echo     Tag:   https://github.com/kingsmokez/jztz_v17/releases/tag/v20
echo     CI:    https://github.com/kingsmokez/jztz_v17/actions
echo.
echo     Next:  Trigger CI by opening the Actions tab, or wait
echo            for any tag-push workflow to auto-run.
echo ============================================================
pause
endlocal
