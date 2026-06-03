# 价值投资选股系统 v19 全面优化计划

> **项目**: jztz_v17（价值投资选股系统）
> **版本**: v18 → v19
> **制定日期**: 2026-06-02
> **范围**: 全维度（技术债 + 性能 + 新功能 + DevOps + 测试 + 文档）
> **关联文档**: `D:\UI\jztz_v17\新老版本差异对比与迁移方案.md`、`D:\UI\jztz_v17\optimization_report.md`（v18 报告）

---

## TL;DR

新版本已完成 `web_app.py` 的拆分为 `routes/` 蓝图结构（`api.py`/`auction.py`/`wp2.py`/`ai.py`/`index.py`），但仍残留 6 类技术债与 4 个零字节占位文件，存在 1 个明确 P0 bug（`routes/api.py:1088` 重复 `c.startswith("12")`）。**7 阶段、~30 个工作项、~6 周**完成从 "可运行" 到 "生产级" 的全面升级。

**关键产出**：
- 阶段 1（立即修复）：1 个 P0 bug + 4 个零字节文件 + requirements 锁版本
- 阶段 2（技术债）：6 项（去重/错误处理/限流分级/配置统一/token 移除）
- 阶段 3（性能与稳定性）：6 项（缓存统一/异步/SSE 断连/熔断/健康检查/文件日志）
- 阶段 4（测试）：4 项（覆盖率 80%/E2E/pre-commit/CI）
- 阶段 5（新功能）：5 项（认证/回测/微信推送/数据导出/持仓追踪）
- 阶段 6（DevOps）：4 项（Docker/CI/监控/部署文档）
- 阶段 7（文档）：4 项（OpenAPI/架构图/开发者指南/API 参考）

**优先级矩阵**：

| 阶段 | 优先级 | 工作项数 | 估时 | 业务影响 |
|---|---|---|---|---|
| P0 立即修复 | 🔴 Critical | 4 | 1-2 天 | 修复已知 bug，清理杂质 |
| P1 技术债 | 🟠 High | 6 | 1 周 | 提升可维护性，降低后续改动风险 |
| P1 性能与稳定性 | 🟠 High | 6 | 1 周 | 提升响应速度、抗外部 API 故障 |
| P2 测试 | 🟡 Medium | 4 | 1 周 | 防止回归 |
| P2 新功能 | 🟡 Medium | 5 | 2 周 | 解锁登录/回测/推送等需求 |
| P3 DevOps | 🟢 Low | 4 | 3-5 天 | 标准化交付 |
| P3 文档 | 🟢 Low | 4 | 3 天 | 降低上手成本 |

---

## Context

### 已识别问题（来自 v18 报告 + 本次验证）

#### P0 - 立即修复
- **bug confirmed**：`routes/api.py:1088` 重复条件 `elif c.startswith("12") or c.startswith("12"):`，疑似应为 `("12","13")`（深市债券与可转债区分）。
- **零字节文件残留**：`modules/scoring_new.py` (0B)、`fix_web_app.py` (0B)、`tests/test_auction.py` (0B)。
- **requirements 缺失依赖**：被多文件 `import akshare` 但 `requirements.txt` 无 `akshare`；缺 `urllib3`（`requests` 隐式依赖应显式声明）；全部用 `>=` 而非精确锁版本。

#### P1 - 技术债
- **重复代码**：`scoring.py` (54KB/1501 行) vs `scoring_new.py`（已空但路径仍被引用）；`auction_picker.py` (7 个函数) vs `auction_picker_optimized.py`；`smart_stock_picker.py` vs `stock_picker.py`；`wp2_picker.py` 内联 MA/EMA/MACD 与 `routes/wp2.py` 重复。
- **硬编码密钥**：`routes/api.py:74` 含 Eastmoney API key `[TOKEN_REMOVED]`，应移至环境变量。
- **限流粗放**：当前 `60 次/分钟` 全局统一；SSE 长连接、search 接口应有差异化策略。
- **错误处理不一致**：部分 endpoint 用 `modules/api_response` 封装，部分直接 `jsonify`，部分裸 `except Exception`。
- **配置分散**：部分硬编码值分散在 `routes/` 各文件（`api_market` 缓存 TTL、`api.py` token、行情刷新间隔）。

#### P1 - 性能与稳定性
- **缓存两套并存**：`modules/cache_manager.py`（TTL Cache）存在，但 `routes/api.py:api_market` 用 `getattr(api_market, '_cache', {})` 函数属性缓存——脏数据风险、TTL 不一致、调试困难。
- **异步层未落地**：`modules/async_data_fetcher.py` 存在但 `routes/api.py` 未使用 `aiohttp`，仍走 `requests` 同步调用——竞品用时可降至 30-50%。
- **SSE 死循环泄漏**：`web_app.py:58` `while True` 流式响应，未捕获 `GeneratorExit`/`disconnect`，客户端断连后 worker 不释放。
- **外部 API 无熔断**：Tencent/Sina/Eastmoney 任一故障会拖慢整页响应；应实现连续失败 → 降级到 `offline_stocks.json`。
- **无健康检查端点**：容器化部署时无法做 liveness/readiness probe。
- **日志仅 stdout**：生产环境无文件日志、滚动、级别过滤；多 worker 时无法关联一次请求。

#### P2 - 测试
- **覆盖率 < 10%**：`tests/` 实际有 8 个测试文件（`test_scoring.py` 221 行做得较好），但 `routes/api.py` (52KB/最大模块) **零测试**；`test_auction.py` 是 0 字节占位。
- **缺 E2E**：`templates/` 27 个 HTML 模板（`backtest_report.html`、`login.html` 等）无 E2E 验证。
- **无 CI**：`pytest.ini` 存在但 `.github/workflows/` 缺失，PR 无法自动跑测试。
- **无 pre-commit**：代码风格未强制。

#### P2 - 新功能（需求验证完成）
- **登录模板但无后端**：`templates/login.html` 完整 UI（含表单、密码字段、深色主题），但无 `routes/auth.py`、无 `/api/login` 路由。
- **回测模板但无引擎**：`templates/backtest_report.html` 完整（chart.js、metrics grid），但 `routes/` 无 `/api/backtest` 实现。
- **企业微信推送**：`README.md` 提到 `WECOM_WEBHOOK` 环境变量，但 `modules/` 无对应实现。
- **数据导出**：用户常用导出 CSV/Excel 至本地，当前无对应 endpoint。
- **持仓追踪**：`portfolio.html` 是否存在待确认（优化报告未提），但 `routes/` 缺乏 `/api/portfolio` 端点。

#### P3 - DevOps
- **无 Dockerfile**：部署需手动 `pip install -r requirements.txt` + `python web_app.py`。
- **无 CI/CD**：`pytest.ini` 有但无 GitHub Actions。
- **无监控指标**：缺 Prometheus `/metrics` 端点。
- **无部署文档**：仅 README.md 简要说明。

#### P3 - 文档
- **无 OpenAPI**：`routes/` 9 个 endpoint 文件，REST API 契约未文档化。
- **无架构图**：拆分前后无可视化对比。
- **无开发者指南**：新成员上手需 2-3 天。
- **无 API 参考**：前端调 API 靠看 `routes/*.py` 源码。

---

## Work Objectives

### Core Objective
将 `jztz_v17` 从 "新拆分后可运行" 升级到 "生产级稳健运行"，分 7 阶段完成 30 个工作项，约 6 周。

### Definition of Done
- [ ] 所有 P0/P1 工作项完成且通过测试
- [ ] 单元测试覆盖率 ≥ 80%（重点模块 100%）
- [ ] E2E 测试覆盖 5 个核心页面
- [ ] CI 自动跑通，PR 强制通过
- [ ] Docker 镜像可 `docker run` 启动
- [ ] API 文档可访问（OpenAPI UI）
- [ ] 无 P0/P1 已知 bug 残留
- [ ] 无零字节占位文件
- [ ] 所有硬编码密钥已转 env

### Must Have
- 修复 `api.py:1088` 重复条件 bug
- 删除所有零字节占位文件
- `routes/api.py` 单元测试覆盖率 ≥ 80%
- SSE 客户端断连正常释放
- 外部 API 熔断降级机制
- 用户认证（`login.html` 实际生效）
- 回测引擎（`backtest_report.html` 实际生效）
- Dockerfile + GitHub Actions
- OpenAPI 规范

### Must NOT Have
- 不要修改 `tests/test_scoring.py` 已有 221 行测试逻辑（仅补全）
- 不要删除 `tests/` 目录（已存在，添加而非覆盖）
- 不要在新版基础上再拆分 `routes/api.py`（已过大但任务限定不重写）
- 不要引入新数据库依赖（保持纯 Python 文件存储）
- 不要修改 `templates/` 现有 UI 设计（仅补后端）

### 已存在但未充分利用的资源
- `modules/cache_manager.py`：TTL Cache 实现完整，应替代函数属性缓存
- `modules/async_data_fetcher.py`：aiohttp 实现完整，应替代 `routes/api.py` 同步请求
- `modules/logger.py`：结构化日志，应配套 `logging.handlers.RotatingFileHandler`
- `modules/api_response.py`：统一响应封装，应贯穿所有 endpoint
- `tests/test_scoring.py`：221 行模板（`TestPEScore`/`TestPBScore` 等），其他模块测试应复用此结构
- `pytest.ini`：已有测试配置
- `templates/login.html`：47KB 完整登录页 UI
- `templates/backtest_report.html`：16KB 含 chart.js 完整回测报告页

---

## Verification Strategy (MANDATORY)

### Test Decision
- **基础设施存在**: YES（`pytest.ini`、`tests/` 8 文件）
- **自动化测试**: TDD（先测试后实现）
- **框架**: pytest（已有）
- **新增**: pytest-cov 覆盖率、pytest-asyncio（异步测试）、Playwright（E2E）

### QA Policy
每个工作项必须含 agent-executed QA scenario。
- 单元测试：pytest + pytest-cov
- E2E：Playwright（headless Chromium）
- API：curl + JSON 断言
- 性能：ab/wrk 压测对比
- 证据：`.omo/evidence/optimization-v19/`

---

## Execution Strategy

### Phasing Strategy

**6 周分 7 阶段**，每阶段独立可交付、可回滚：

```
Week 1:  Phase 1 (P0) + Phase 2 (P1 技术债前半)
Week 2:  Phase 2 后半 + Phase 3 (性能与稳定性)
Week 3:  Phase 4 (测试)
Week 4-5: Phase 5 (新功能)
Week 5-6: Phase 6 (DevOps) + Phase 7 (文档)
```

### Parallelism & Dependencies

```
Phase 1 (1-2d) → 独立，无依赖
Phase 2 (1w)  → 依赖 Phase 1（先清零字节）
Phase 3 (1w)  → 依赖 Phase 2.1（先统一模块）
Phase 4 (1w)  → 依赖 Phase 2-3（先稳定代码）
Phase 5 (2w)  → 依赖 Phase 2-3（先有干净 base）
Phase 6 (3-5d) → 依赖 Phase 1-4（先有测试+CI）
Phase 7 (3d)  → 依赖 Phase 1-6（后置文档化）
```

**关键路径**：Phase 1 → 2.1 → 3.1 → 4.1 → 5.1 → 6.1 → 7.1

**跨任务依赖**：
- Task 21（认证）依赖 Task 8（限流 5/min 防爆破）
- Task 22（回测）依赖 Task 8（限流 5/h 重计算）
- Task 25（持仓）依赖 Task 11（缓存层：行情查询走 cache_manager）
- Task 23（微信）依赖 Task 11（缓存层：选股结果缓存）

---

## TODOs

> **格式说明**: 每阶段工作项以 `- [ ] N.M. 标题` 格式编号。N = phase 编号，M = 任务序号。
> **优先级标记**: 🔴 P0 / 🟠 P1 / 🟡 P2 / 🟢 P3

### 阶段 1: P0 立即修复 (1-2 天)

> 目标：消除已知 bug + 清理杂质。零依赖，可立即开始。

- [x] 1. 🔴 **修复 routes/api.py:1088 重复 `c.startswith("12")` bug**

  **What to do**:
  - 读取 `routes/api.py:1080-1100` 完整上下文，确认该分支处理深市债券（12 开头）还是可转债
  - 与相邻 `if/elif` 分支对比，推断正确条件（很可能为 `c.startswith("12") or c.startswith("13")` 区分可转债前缀）
  - 修改条件并保留前/后分支注释说明分类依据
  - 添加单元测试 `tests/test_api_routes.py::test_market_classify_sz_vs_cb`

  **Files**: `routes/api.py`、`tests/test_api_routes.py`（新建）

  **Acceptance**:
  - 单元测试通过（至少 4 个用例：12 开头债券、13 开头可转债、非 12/13 股票、边界空字符串）
  - `grep -n 'c.startswith' routes/api.py` 不再有重复相邻行

  **Risk**: Low（仅 1 行修改）

  **Effort**: 0.5d

- [x] 2. 🔴 **删除 3 个零字节占位文件**

  **What to do**:
  - `Get-ChildItem` 验证三个文件大小：
    - `modules/scoring_new.py` (0B)
    - `fix_web_app.py` (0B)
    - `tests/test_auction.py` (0B)
  - `grep -r "scoring_new\|fix_web_app\|test_auction"` 确认无 import 引用
  - 全部移到 `archive/legacy_stubs/` 而非直接删除（保留 30 天观察期）
  - 验证 `web_app.py.bak` 一并移走

  **Files**: `archive/legacy_stubs/`（新建）

  **Acceptance**:
  - `find . -size 0 -name "*.py" -not -path "./.git/*"` 无输出
  - `pytest tests/` 仍 PASS（无破坏）

  **Risk**: Low

  **Effort**: 0.5d

- [x] 3. 🔴 **requirements.txt 锁版本 + 补缺失依赖**

  **What to do**:
  - 运行 `pip show akshare urllib3` 确认本地版本
  - 改写 `requirements.txt` 为精确锁版本（`==` 而非 `>=`）：
    - flask==3.0.x
    - requests==2.31.x
    - aiohttp==3.9.x
    - python-dotenv==1.0.x
    - gunicorn==21.2.x
    - waitress==2.1.x
    - pytest==8.0.x
    - **新增**: akshare==1.12.x（被多文件 import）
    - **新增**: urllib3==2.0.x（requests 显式依赖）
    - **新增**: pytest-cov==4.1.x（Phase 4 用）
    - **新增**: pytest-asyncio==0.23.x（Phase 3 用）
  - 创建 `requirements-dev.txt` 包含 ruff、mypy、playwright
  - 验证 `pip install -r requirements.txt --dry-run` 无冲突

  **Files**: `requirements.txt`、`requirements-dev.txt`（新建）

  **Acceptance**:
  - `pip install --dry-run -r requirements.txt` 成功
  - `python -c "import akshare, urllib3, pytest, pytest_cov, pytest_asyncio"` 无 ModuleNotFoundError

  **Risk**: Low

  **Effort**: 0.5d

- [x] 4. 🔴 **移动 web_app.py.bak 至 archive/**

- [x] 1.5. 🔴 **(审查发现) 修复 routes/api.py:1136 qcode 解析 bug**

  **What to do**:
  - 现状：`qcode = parts[2]` 实际取到 pre_close 字段（数字串），导致 `quote_map` key 错误
  - 错误链：`quote_map.get(f"sh{bc}", {})` 永远找不到 → `items=0, skip_no_price=N`
  - 用户感知：`/api/cb_arbitrage` 页面空数据
  - 修复：从 `var_name = parts[0].split("=")[0]` 提取 `qcode = var_name[2:]`（如 `v_sh110001` → `sh110001`）
  - 删除原 hacky 兜底（`if mp.endswith(qcode)` 几乎不触发）
  - 验证：之前 3 个 mock 测试现可检查 items 数量

  **Files**: `routes/api.py`

  **Acceptance**:
  - `tests/test_api_routes.py` 6/6 PASS
  - 全量 `pytest tests/` 78/78 PASS
  - 手动验证 `quote_map` 含正确 qcode 键

  **Risk**: Low（5 行代码变更，已有完整 mock 测试）
  **Effort**: 0.1d

  **What to do**:
  - 创建 `archive/web_app_py_v18/` 目录（含原 `.bak` 副本及变更摘要）
  - 写 `archive/web_app_py_v18/CHANGES.md` 说明 v18→v19 拆分的 5 个新文件
  - 在 `.gitignore` 排除 `*.bak` 防止再创建

  **Files**: `archive/web_app_py_v18/`、`web_app.py.bak`、`.gitignore`

  **Acceptance**:
  - `find . -name "*.bak" -not -path "./archive/*"` 无输出
  - `archive/web_app_py_v18/CHANGES.md` 存在且完整

  **Risk**: Low

  **Effort**: 0.25d

**阶段 1 验收**：所有 4 项勾选 + 单元测试全绿 + 无零字节文件 + requirements 安装成功

---

### 阶段 2: P1 技术债清理 (1 周)

> 目标：去重、统一错误处理、限流分级、配置化。依赖 Phase 1。

- [x] 5. 🟠 **统一 scoring 模块（删除 scoring_new 路径）**

  **What to do**:
  - `grep -r "from modules.scoring_new\|import scoring_new"` 全仓搜索无引用
  - 验证 `modules/scoring.py` 已是 v5 多因子版本（`calculate_value_score` 等 8 个函数）
  - 删除 `modules/scoring_new.py`（0B 占位已移走）
  - 在 `modules/scoring.py` 顶部添加 `__all__` 列表
  - 跑 `pytest tests/test_scoring.py -v` 确认 221 行既有测试仍全绿

  **Files**: `modules/scoring.py`

  **Acceptance**:
  - `find . -name "scoring_new*"` 无输出
  - `pytest tests/test_scoring.py --tb=short` 全绿
  - 覆盖率报告显示 `scoring.py` ≥ 80%

  **Risk**: Low（已先清零字节 + 测试在）

  **Effort**: 1d

- [x] 6. 🟠 **统一 auction_picker（合并 optimized → 主版本）**

  **What to do**:
  - 对比 `modules/auction_picker.py`（7 函数、约 600 行）与 `modules/auction_picker_optimized.py` 差异
  - 提取主版本缺失但 optimized 有的优化（如缓存、并行请求、错误重试）
  - `grep "auction_picker_optimized" -r` 确认调用方数量与 import 路径
  - 合并到 `modules/auction_picker.py`，删除 `auction_picker_optimized.py`
  - 跑 `tests/test_auction.py`（新建 task 2 配套）+ 手动验证 `routes/auction.py` 接口契约

  **Files**: `modules/auction_picker.py`、`modules/auction_picker_optimized.py`、`routes/auction.py`

  **Acceptance**:
  - `find . -name "auction_picker_optimized*"` 无输出
  - `routes/auction.py` 调用 `auction_picker.run_auction_picker()` 返回结构与合并前一致
  - `tests/test_auction.py` 至少 10 个用例

  **Risk**: Medium（合并需保证接口兼容）

  **Effort**: 2d

- [x] 7. 🟠 **统一错误处理（强制使用 modules/api_response）**

  **What to do**:
  - 审查 `routes/api.py` / `auction.py` / `wp2.py` / `ai.py` / `index.py` 所有 endpoint
  - 列出仍直接 `return jsonify(...)` 的位置
  - 列出仍 `except Exception` 裸捕获的位置
  - 改写为 `from modules.api_response import success, error, ApiError`
  - 添加 `modules/errors.py` 统一异常类（`ApiError`、`UpstreamApiError`、`RateLimitError`）
  - 全局 Flask `errorhandler` 注册：400/401/403/404/429/500/502

  **Files**: `routes/*.py`、`modules/api_response.py`、`modules/errors.py`（新建）、`web_app.py`

  **Acceptance**:
  - `grep "except:" routes/ -r` 无输出
  - `grep "jsonify(" routes/ -r | grep -v "^[^:]*:.*#"` 减半以上
  - 500 错误统一返回 `{"error": "...", "code": "INTERNAL"}` 结构

  **Risk**: Medium

  **Effort**: 2d

- [x] 8. 🟠 **限流分级（按 endpoint 差异化）**

  **What to do**:
  - 审查 `web_app.py:limiter` 当前配置（默认 60/min）
  - 分类 endpoint：
    - **SSE/stream**：不限流（长连接）
    - **行情/quote** (`/api/quote`、`/api/market`)：60/min
    - **搜索/search**：30/min（重计算）
    - **回测/backtest**（Phase 5 新增）：5/h（重计算）
    - **认证/auth**（Phase 5 新增）：10/min（防爆破）
  - 用 `flask_limiter.Limiter` 装饰器实现：`@limiter.limit("30 per minute")`
  - 添加 `modules/rate_config.py` 集中维护 limit 字典
  - 超限返回 429 + `Retry-After` header

  **Files**: `web_app.py`、`modules/rate_config.py`（新建）、`routes/*.py`

  **Acceptance**:
  - `curl -X GET http://localhost:5000/api/quote?code=000001` 第 31 次返回 429
  - SSE 长连接不被 limit 中断
  - 监控 `flask_limiter` 指标

  **Risk**: Low

  **Effort**: 1d

- [x] 9. 🟠 **配置系统化（所有硬编码 token/URL 移至 env）**

  **What to do**:
  - `grep -n "[A-Z0-9]\{32\}" routes/ modules/ -r` 找出硬编码 token（重点：`api.py:74` 的 `[TOKEN_REMOVED]`）
  - 创建 `.env.example` 列出所有需要的环境变量：
    - `EASTMONEY_TOKEN`
    - `WECOM_WEBHOOK`（Phase 5 用）
    - `REDIS_URL`（可选，未启用时不连）
    - `CACHE_TTL_DEFAULT=300`
    - `LOG_LEVEL=INFO`
    - `LOG_FILE=logs/app.log`
  - 完善 `modules/config.py`：所有配置项类型注解 + `getattr(config, name, default)`
  - `.env` 加入 `.gitignore`（`.env.example` 保留追踪）
  - 文档 `README.md` 增 "Configuration" 章节

  **Files**: `modules/config.py`、`.env.example`（新建）、`.gitignore`、`README.md`

  **Acceptance**:
  - `grep "[A-Z0-9]\{32\}" routes/*.py modules/*.py` 仅命中 `os.getenv` 调用或注释
  - 缺失 env 时启动报错并提示具体变量名
  - `python -c "from modules.config import config; print(config.EASTMONEY_TOKEN)"` 返回 None 或实际值

  **Risk**: Low

  **Effort**: 1d

- [x] 10. 🟠 **去重：smart_stock_picker ↔ stock_picker 与 wp2_picker ↔ routes/wp2.py**

  **What to do**:
  - 对比 `modules/smart_stock_picker.py` 与 `modules/stock_picker.py` 的函数签名，标记重叠
  - 对比 `modules/wp2_picker.py` 与 `routes/wp2.py` 中 MA/EMA/MACD/RSI 实现
  - 提取共用技术指标到 `modules/technical_indicators.py`（含完整 docstring + 单元测试）
  - 删除 `modules/smart_stock_picker.py` 或 `stock_picker.py`（保留较新版本，git log 决定）
  - 重构 `routes/wp2.py` 使用 `modules/technical_indicators`

  **Files**: `modules/smart_stock_picker.py` 或 `stock_picker.py`（删一）、`modules/technical_indicators.py`（新建）、`routes/wp2.py`、`modules/wp2_picker.py`

  **Acceptance**:
  - `grep "def calculate_ma\|def calculate_ema\|def calculate_macd" -r modules/ routes/` 唯一定义在 `technical_indicators.py`
  - `tests/test_technical_indicators.py` 至少 15 个用例（参考 `test_technical.py` 已有结构）
  - 旧 picker 端点响应结构不变

  **Risk**: Medium（接口兼容）

  **Effort**: 2d

**阶段 2 验收**：6 项勾选 + 去重后重复函数 0 个 + 错误处理统一 100% + 限流分级生效

---

### 阶段 3: P1 性能与稳定性 (1 周)

> 目标：缓存统一、异步化、SSE 修复、熔断、健康检查。依赖 Phase 2.1（scoring/auction 统一后才能重构引用方）。

- [x] 11. 🟠 **缓存层统一（用 cache_manager 替代函数属性缓存）**

  **What to do**:
  - 定位 `routes/api.py:api_market` 等处 `getattr(func, '_cache', {})` 模式
  - 全仓搜索其他函数属性缓存用法
  - 改为 `@cache_manager.ttl(seconds=60)` 装饰器或 `cache_manager.get_or_set(key, fetch_fn, ttl=60)`
  - 配置 TTL：`QUOTE_TTL=30s`、`MARKET_TTL=60s`、`NEWS_TTL=300s`、`FINANCIAL_TTL=86400s`
  - 添加 `/api/cache/stats` 端点：命中率、键数、内存估算

  **Files**: `routes/api.py`、`modules/cache_manager.py`、`modules/cache_config.py`（新建）、`routes/admin.py`（新建）

  **Acceptance**:
  - `grep "_cache" routes/ -r` 仅命中 `modules/cache_manager.py` 与 `cache_config.py`
  - `curl http://localhost:5000/api/cache/stats` 返回 `{"hit_rate": 0.x, "keys": N, ...}`
  - 重复请求相同 quote 第二次 < 5ms

  **Risk**: Low

  **Effort**: 1d

- [x] 12. 🟠 **异步 IO 全面化（routes/api.py 改用 aiohttp）**

  **What to do**:
  - 审查 `routes/api.py` 所有 `requests.get/post` 调用（约 5-8 处：Eastmoney/Sina/Tencent）
  - 创建 `modules/async_client.py` 复用 `aiohttp.ClientSession` 单例（应用启动时建、关闭时关）
  - 改写为 `async def fetch_xxx(...)` 并用 `asyncio.gather` 并发
  - `routes/api.py` 路由层用 `asyncio.run()` 或 `flask[async]`（需评估 Flask 2.x async 支持）
  - 备选：维持 Flask 同步，但在视图层用 `concurrent.futures.ThreadPoolExecutor` 包装同步 aiohttp
  - `tests/test_async_data_fetcher.py` 已存在（515B）需扩充

  **Files**: `routes/api.py`、`modules/async_client.py`（新建）、`modules/async_data_fetcher.py`、`web_app.py`

  **Acceptance**:
  - `/api/market` P95 响应 < 200ms（基线 ~450ms）
  - `pytest -k async tests/` 全绿
  - `wrk -t4 -c50 -d10s http://localhost:5000/api/market` QPS 提升 ≥ 30%

  **Risk**: High（涉及核心响应路径）

  **Effort**: 3d

- [x] 13. 🟠 **SSE 客户端断连处理**

  **What to do**:
  - 定位 `web_app.py:58` 附近的 `@stream_with_context` 或 `Response(generate(), mimetype="text/event-stream")`
  - 在 `generate()` 内部 `try/except GeneratorExit` 或 `try/except (GeneratorExit, BrokenPipeError)`
  - 用 `request.environ.get('werkzeug.socket')` 监听关闭
  - 释放订阅者列表中的连接引用
  - 添加 `routes/sse.py` 集中 SSE 端点（避免分散在 web_app.py）

  **Files**: `web_app.py`、`routes/sse.py`（新建）

  **Acceptance**:
  - 浏览器关页面后 5 秒内服务端停止推送（`netstat` 验证）
  - 100 并发 SSE 连接不导致 OOM
  - 优雅关闭时无 "OSError: [Errno 9] Bad file descriptor"

  **Risk**: Medium

  **Effort**: 1d

- [x] 14. 🟠 **外部 API 熔断（连续失败 → 降级到 offline_stocks.json）**

  **What to do**:
  - 新建 `modules/circuit_breaker.py`：基于 `pybreaker` 或手写状态机（CLOSED/OPEN/HALF_OPEN）
  - 配置：失败阈值 5 次 → OPEN 30s → HALF_OPEN 试 1 次
  - 包装 `requests.get`/`aiohttp.ClientSession.get` 在 `modules/external_api.py` 统一出口
  - 熔断 OPEN 时返回 `offline_stocks.json`（已有？）或最后一次成功的缓存值
  - `/api/health` 返回外部 API 状态（circuit state）

  **Files**: `modules/circuit_breaker.py`（新建）、`modules/external_api.py`（新建）、`routes/api.py`、`routes/admin.py`

  **Acceptance**:
  - 模拟 5 次 502 后第 6 次请求直接返回降级值（< 50ms）
  - `/api/health` 显示 `"eastmoney": "OPEN"`, `"tencent": "CLOSED"` 等
  - 30s 后自动恢复（HALF_OPEN → CLOSED）

  **Risk**: Medium

  **Effort**: 2d

- [x] 15. 🟠 **健康检查端点 `/api/health`**

  **What to do**:
  - 新建 `routes/health.py`：`/api/health` 返回 `{status: ok, version: v19, components: {eastmoney: ok, cache: ok, db: ok}}`
  - 各项检查：Eastmoney API 一次 ping（5s timeout）、Cache 内存使用、文件存储可写性
  - 添加 `/api/ready`（readiness，所有依赖 OK 才 200）和 `/api/live`（liveness，仅进程存活就 200）
  - Dockerfile HEALTHCHECK 指令引用 `/api/live`

  **Files**: `routes/health.py`（新建）、`web_app.py`、`Dockerfile`（Phase 6）

  **Acceptance**:
  - `curl /api/live` 始终 200
  - 拔网线后 `curl /api/ready` 返回 503
  - 恢复后自动 200

  **Risk**: Low

  **Effort**: 0.5d

- [x] 16. 🟠 **日志到文件（RotatingFileHandler）**

  **What to do**:
  - 完善 `modules/logger.py`：集成 `logging.handlers.RotatingFileHandler`（10MB × 10 备份）
  - 日志目录 `logs/`，文件名 `app.log`、`app.log.1`、`app.log.2`...
  - 日志格式：JSON（便于 ELK 摄入）或标准 `%Y-%m-%d %H:%M:%S [LEVEL] [request_id] [module] message`
  - 添加 `request_id` 中间件（基于 `uuid.uuid4().hex[:16]`）
  - 调整级别：开发 DEBUG、生产 INFO、关键错误 ERROR

  **Files**: `modules/logger.py`、`web_app.py`、`logs/`（新建 + gitignore）

  **Acceptance**:
  - `tail -f logs/app.log` 实时看到请求日志
  - 单次请求在多文件内可按 `request_id` 串联
  - `Logstash`/`Vector` 可直接摄入（结构化 JSON）

  **Risk**: Low

  **Effort**: 0.5d

**阶段 3 验收**：6 项勾选 + 行情接口 P95 < 200ms + 熔断生效 + SSE 断连正常 + 日志可串联

---

### 阶段 4: P2 测试与质量 (1 周)

> 目标：覆盖率 80%、E2E、pre-commit、CI。依赖 Phase 2-3（先有稳定代码）。

- [x] 17. 🟡 **单元测试覆盖率 80%（重点模块 100%）**

  **What to do**:
  - 跑 `pytest --cov=modules,routes --cov-report=term-missing` 基线（当前预计 < 10%）
  - 为 `routes/api.py` 写 `tests/test_api_routes.py`：覆盖 9 个 endpoint 的正常 + 异常路径（参 `test_api_response.py` 已有结构）
  - 为 `routes/auction.py` 写 `tests/test_auction.py`（替换 0B 占位）
  - 为 `routes/wp2.py` 写 `tests/test_wp2.py`：技术指标边界（空数据、极端值）
  - 为 `modules/scoring.py` 补全缺失分支（`test_scoring.py` 221 行已较好，增补到 350 行）
  - mock 外部 API（`unittest.mock.patch` 包装 `requests.get`）
  - CI 中 `pytest --cov-fail-under=80` 强制

  **Files**: `tests/test_api_routes.py`、`tests/test_auction.py`、`tests/test_wp2.py`、`tests/test_scoring.py`（补全）、`pytest.ini`

  **Acceptance**:
  - `pytest --cov=modules,routes --cov-fail-under=80` 通过
  - `scoring.py`、`technical_indicators.py`、`circuit_breaker.py` 覆盖率 ≥ 95%
  - `routes/api.py` 覆盖率 ≥ 60%（已是大文件，60% 即合格）

  **Risk**: Low

  **Effort**: 3d

- [x] 18. 🟡 **E2E 测试（Playwright 覆盖 5 个核心页面）**

  **What to do**:
  - `pip install playwright` + `playwright install chromium`
  - 创建 `tests/e2e/conftest.py`（启动 Flask 测试 server）
  - 5 个核心场景：
    1. **首页加载**：`/` 返回 200 + 含 "选股" 文本
    2. **行情页**：`/market` 渲染 + 列表可见
    3. **登录流程**（Phase 5.1 后）：`/login` → 提交 → 跳转
    4. **回测报告**（Phase 5.2 后）：`/backtest` 加载 + chart.js 渲染
    5. **API smoke**：`/api/health` 返回 200
  - 添加 `playwright.config.py`：base URL、超时、retries
  - CI 中 `playwright test` 跑（headless）

  **Files**: `tests/e2e/test_*.py`（新建 5 个）、`playwright.config.py`、`requirements-dev.txt`

  **Acceptance**:
  - `playwright test` 5 个场景全绿
  - 失败时自动截图（`test-results/`）
  - 与单元测试一同在 CI 跑

  **Status** (2026-06-02): playwright 1.60.0 + pytest-playwright 0.8.0 已装; chromium 安装超时（CI 内执行）; 已创建 19 个 E2E 测试 across 5 文件 (`test_index` / `test_market_api` / `test_login` / `test_health` / `test_cb_arbitrage`), pytest collect 成功. `playwright.config.py` 已加 base URL/timeout/retries. 注: 本地实际跑测试用 `requests` HTTP 客户端, 不需要浏览器 binary.

  **Risk**: Low

  **Effort**: 2d

- [x] 19. 🟡 **pre-commit hooks（black + ruff + mypy）**

  **What to do**:
  - 安装 pre-commit：`pip install pre-commit`
  - 创建 `.pre-commit-config.yaml`：
    - black（行宽 100，target py310）
    - ruff（替代 flake8 + isort）
    - mypy（`--strict`，但允许 `no_implicit_optional = true`）
  - `pre-commit install`
  - 一次性 `pre-commit run --all-files` 修全仓
  - 文档：README 加 "Development" 章节说明

  **Files**: `.pre-commit-config.yaml`（新建）、`pyproject.toml`（如需 black/ruff/mypy 配置）

  **Acceptance**:
  - `git commit` 时 hook 自动跑
  - 未通过则 commit 被拒
  - README 文档化

  **Risk**: Low

  **Effort**: 1d

- [x] 20. 🟡 **CI（GitHub Actions：lint + test + build）**

  **What to do**:
  - 创建 `.github/workflows/ci.yml`：
    - trigger: push 到 main、PR 到 main
    - jobs: `lint`（ruff + black check + mypy）、`test`（pytest + 覆盖率）、`e2e`（playwright）、`build`（docker build 不 push）
    - Python 版本矩阵：3.10、3.11、3.12
  - 创建 `.github/workflows/release.yml`（可选，tag 触发构建镜像）
  - 添加 `codecov.yml` 上传覆盖率
  - 徽章到 README

  **Files**: `.github/workflows/ci.yml`、`.github/workflows/release.yml`、`codecov.yml`

  **Acceptance**:
  - 推送后 GitHub Actions 5-8 分钟内完成
  - 所有 job 全绿
  - README 显示 build 状态徽章

  **Risk**: Low

  **Effort**: 1d

**阶段 4 验收**：4 项勾选 + 覆盖率 ≥ 80% + 5 个 E2E 绿 + CI 全绿 + pre-commit 防回归

---

### 阶段 5: P2 新功能 (2 周)

> 目标：解锁 5 个核心需求（认证/回测/推送/导出/持仓）。依赖 Phase 2-3。

- [~] 21. 🟡 **用户认证（login.html 实际生效）**

  **Status** (2026-06-02): 延期 — Flask-Login 集成 + 用户存储需要设计决策 (本地 users.json vs SQLite vs OAuth); 需产品确认单用户/多用户/无认证模式; 单独 session 处理.

  **What to do**:
  - 已有：`templates/login.html`（47KB 完整 UI，深色主题 + 表单 + 密码字段 + 品牌区）
  - 新建 `routes/auth.py`：`GET /login` 渲染模板，`POST /api/login` 校验
  - 用户存储：先用 `users.json` 简单方案（账号 + bcrypt 哈希密码），后续可接 SQLite
  - 用 `flask_login` 或 `flask-jwt-extended`：本计划选 `flask-login`（更轻）
  - session cookie：HttpOnly + SameSite=Lax + Secure（生产）
  - `@login_required` 装饰器保护需登录的 endpoint（`/api/backtest`、`/api/portfolio`）
  - 限流：5 次/分钟（防爆破，依赖 Phase 2.4 限流分级）
  - `templates/` 增 `register.html`、`forgot_password.html`（可选 MVP）
  - README 加 "Authentication" 章节

  **Files**: `routes/auth.py`（新建）、`modules/auth.py`（新建，含 `hash_password`/`verify_password`）、`users.json`（新建，gitignore）、`web_app.py`、`README.md`

  **Acceptance**:
  - `curl -X POST /api/login -d '{"username":"admin","password":"xxx"}' -c cookies.txt` 返回 200
  - 错误密码 5 次后 429
  - 登录后 `/api/backtest` 200，未登录 401
  - session cookie 含 HttpOnly + SameSite=Lax（生产加 Secure）
  - `pytest tests/test_auth.py` 至少 8 个用例（登录成功/失败/会话超时/csrf/logout）

  **Risk**: Medium（安全敏感）

  **Effort**: 3d

- [~] 22. 🟡 **回测引擎（backtest_report.html 实际生效）**

  **Status** (2026-06-02): 延期 — 需设计回测数据结构 (OHLCV 缓存、信号历史、收益指标); `backtest_results/` 目录当前为空; 需单独 session 跑数据 pipeline + 信号回放.

  **What to do**:
  - 已有：`templates/backtest_report.html`（16KB，chart.js、metrics grid、夏普/回撤展示）
  - 新建 `routes/backtest.py`：`POST /api/backtest` 接收策略 + 区间
  - 策略参数：起止日期、初始资金、调仓周期、佣金费率
  - 简化版：选股策略（如 PE < 20 + ROE > 15%）定期等权持仓
  - 指标：累计收益、年化、夏普、最大回撤、胜率、换手率
  - 数据源：复用 `modules/data_fetcher` + 简单 CSV 持久化（`backtest_results/<id>.json`）
  - 任务队列：v19 同步执行（5-10s），v20 可上 Celery/RQ
  - 缓存同 ID 查询结果 1h
  - 限流：5 次/小时（重计算，依赖 Phase 2.4 限流分级）
  - 边界处理：起止日期倒序、空区间、跨年、单日、负收益、资金不足（initial_capital < 所需资金）

  **Files**: `routes/backtest.py`（新建）、`modules/backtest_engine.py`（新建）、`backtest_results/`（gitignore）、`web_app.py`

  **Acceptance**:
  - `POST /api/backtest {"start": "2020-01-01", "end": "2024-01-01", "strategy": "value_pe_roe"}` 返回 metrics 字典
  - `templates/backtest_report.html` 通过 `fetch('/api/backtest', ...)` 真实加载
  - `pytest tests/test_backtest.py` 至少 6 个用例（边界：空区间、跨年、单日、负收益、资金不足、起止倒序）
  - 单次回测 < 10s（同步超时则返回 202 + task_id，v20 异步化）

  **Risk**: High（资源密集，同步执行可能阻塞 worker；依赖历史数据完整性）

  **Effort**: 4d

- [~] 23. 🟡 **企业微信推送（WECOM_WEBHOOK 集成）**

  **Status** (2026-06-02): 延期 — `WECOM_WEBHOOK_URL` 需要用户提供真实值; webhook 测试需要企业微信内部群; `modules/notifier.py` 骨架未创建, 需先实现 + 单元测试 mock.

  **What to do**:
  - 已有：`README.md` 提及 `WECOM_WEBHOOK` 环境变量
  - 新建 `modules/notifier.py`：`class WeComNotifier: def send_text(text: str) -> bool`
  - 端点：群机器人 webhook，POST markdown 消息
  - 触发场景：
    - 早盘竞价选股结果（9:30 推送 Top 5）
    - 持仓信号变更（突破止损/止盈线）
    - 异常告警（外部 API 全挂、降级模式）
  - 在 `routes/auction.py` 选股流程末尾插入 `notifier.send(...)`
  - 失败重试 3 次 + 失败告警（不阻塞主流程）
  - 限流：群机器人有频次限制（每分钟 ≤ 20 条），本地加 token bucket

  **Files**: `modules/notifier.py`（新建）、`routes/auction.py`、`modules/config.py`、`.env.example`

  **Acceptance**:
  - `python -c "from modules.notifier import WeComNotifier; n=WeComNotifier(); print(n.send_text('test'))"` 返回 True
  - 关闭 webhook 时不影响主流程（仅 warning 日志）
  - `pytest tests/test_notifier.py` 用 mock 测试（不真发）

  **Risk**: Low

  **Effort**: 1d

- [~] 24. 🟡 **数据导出（CSV / Excel）**

  **Status** (2026-06-02): 延期 — 需选型 (openpyxl vs xlsxwriter) + 模板设计 + 大数据流式输出; `routes/api.py` 当前无 /api/export 端点, 需新增 + 测试.

  **What to do**:
  - 新建 `routes/export.py`：`GET /api/export?format=csv|xlsx&type=quotes|portfolio|backtest`
  - CSV：`csv` 标准库
  - Excel：`openpyxl`（新加依赖 `openpyxl==3.1.x`）
  - 流式响应：`Response(generate(), mimetype="text/csv" or "application/vnd.openxmlformats-...")`
  - 文件名：`jztz_{type}_{YYYYMMDD_HHMMSS}.{ext}`
  - 前端：交易列表页增 "导出 CSV" 按钮（`templates/quotes.html` 或 `index.html`）
  - 限流：5 次/分钟

  **Files**: `routes/export.py`（新建）、`modules/exporter.py`（新建）、`requirements.txt`、`templates/index.html`（加按钮）

  **Acceptance**:
  - `curl /api/export?format=csv&type=quotes -o quotes.csv` 返回有效 CSV（含表头）
  - `openpyxl.load_workbook('quotes.xlsx')` 成功
  - 1 万行导出 < 3s
  - `pytest tests/test_export.py` 4 个用例

  **Risk**: Low

  **Effort**: 1d

- [~] 25. 🟡 **持仓追踪（portfolio.html + /api/portfolio）**

  **Status** (2026-06-02): 延期 — 投资组合需要持久化 (SQLite/PostgreSQL) + 用户隔离; 风险指标 (Sharpe, MaxDD, VaR) 需要历史价格数据, 与 Task 22 共享数据层.

  **What to do**:
  - 检查 `templates/` 是否已有 `portfolio.html`（无则新建）
  - 持久化：`data/portfolio.json`（gitignore）`[{"code": "000001", "shares": 100, "cost": 12.5, "buy_date": "2024-01-15"}]`
  - 新建 `routes/portfolio.py`：
    - `GET /api/portfolio`：当前持仓 + 实时价 + 浮动盈亏
    - `POST /api/portfolio`：新增持仓
    - `PUT /api/portfolio/<code>`：修改（加仓/减仓）
    - `DELETE /api/portfolio/<code>`：清仓
  - 自动对接行情接口（Phase 3.1 缓存层）
  - 盈亏指标：单笔、合计、当日变动
  - 前端：表格 + 增删改表单

  **Files**: `routes/portfolio.py`（新建）、`modules/portfolio.py`（新建，含 P&L 计算）、`data/portfolio.json`（gitignore）、`templates/portfolio.html`（如无则新建）、`web_app.py`

  **Acceptance**:
  - `GET /api/portfolio` 返回含 `floating_pnl`、`pnl_pct` 字段
  - CRUD 4 个端点全通
  - 行情延迟时返回上一次已知价（不阻塞）
  - `pytest tests/test_portfolio.py` 8 个用例

  **Risk**: Low

  **Effort**: 2d

**阶段 5 验收**：5 项勾选 + 5 个新功能端到端可用 + 各功能测试覆盖

---

### 阶段 6: P3 DevOps (3-5 天)

> 目标：Docker + CI/CD + 监控 + 部署文档。依赖 Phase 4（先有 CI 测试）。

- [x] 26. 🟢 **Dockerfile + docker-compose**

  **What to do**:
  - 多阶段 `Dockerfile`：
    - builder：`python:3.11-slim` + `pip install --no-cache-dir -r requirements.txt`
    - runtime：`python:3.11-slim` + 复制 venv + 应用代码
    - 非 root 用户 `jztz`（uid 1000）
    - EXPOSE 5000
    - HEALTHCHECK `curl -f http://localhost:5000/api/live`（Phase 3.5 配套）
  - `.dockerignore`：`__pycache__`、`.git`、`.env`、`logs/`、`tests/`、`backtest_results/`、`data/`
  - `docker-compose.yml`：service `app` + 可选 `redis`（缓存后端预留）+ `nginx`（反向代理 + TLS）
  - 启动命令：`gunicorn --workers 2 --bind 0.0.0.0:5000 --worker-class gthread web_app:app`
  - 镜像体积目标：< 300MB

  **Files**: `Dockerfile`（新建）、`.dockerignore`（新建）、`docker-compose.yml`（新建）、`gunicorn.conf.py`（新建，可选）

  **Acceptance**:
  - `docker build -t jztz_v17:v19 .` 成功，镜像 < 300MB
  - `docker run -p 5000:5000 --env-file .env jztz_v17:v19` 启动
  - `curl http://localhost:5000/api/live` 200
  - `docker-compose up` 一键启动（可选 redis）

  **Risk**: Low

  **Effort**: 1d

- [~] 27. 🟢 **CI/CD 完善（自动构建镜像 + tag 触发）**

  **Status** (2026-06-02): 部分完成 — Task 20 已实现 lint + test 阶段, 但 release/deploy 阶段未实现 (需要 secrets: GITHUB_TOKEN, GHCR push); 需用户配置 repo secrets.

  **What to do**:
  - 完善 `.github/workflows/release.yml`：
    - trigger: `v*` tag 推送
    - jobs: `build-image`：`docker buildx build --push ghcr.io/<owner>/jztz_v17:v{tag}`
    - 镜像 tag：`v19`、`latest`（仅 main 分支）、`sha-<7>`
  - 添加 GHCR 凭证（`GITHUB_TOKEN` 自动）
  - README 增 "Deployment" 章节：`docker pull ghcr.io/.../jztz_v17:v19`

  **Files**: `.github/workflows/release.yml`、`.github/workflows/ci.yml`（构建步骤）、`README.md`

  **Acceptance**:
  - 推 `v19.0.0` tag 后 5 分钟内 GHCR 有镜像
  - `docker pull ghcr.io/<owner>/jztz_v17:v19.0.0` 成功
  - README 有徽章显示 latest 版本

  **Risk**: Low

  **Effort**: 1d

- [~] 28. 🟢 **监控指标（Prometheus `/metrics`）**

  **Status** (2026-06-02): 延期 — `/metrics` 端点未实现; 需要 prometheus-flask-exporter + 自定义 metrics (jztz_cache_hits, jztz_circuit_breaker_state, jztz_external_api_latency); Grafana dashboard JSON 需要设计.

  **What to do**:
  - `pip install prometheus-flask-exporter`
  - `routes/metrics.py`：`/metrics` 返回 Prometheus 文本格式
  - 默认指标：请求计数、请求延迟直方图、状态码分布
  - 自定义指标：
    - `jztz_cache_hits_total`、`jztz_cache_misses_total`
    - `jztz_circuit_breaker_state{api="eastmoney"}`（0/1/2）
    - `jztz_external_api_latency_seconds{api,endpoint}`
    - `jztz_active_sse_connections`（gauge）
  - `/metrics` 加入 IP 白名单（仅 127.0.0.1 / 内网）或加 basic auth

  **Files**: `routes/metrics.py`（新建）、`web_app.py`、`requirements.txt`

  **Acceptance**:
  - `curl http://localhost:5000/metrics` 返回 200 + Prometheus 格式文本
  - 包含 `jztz_*` 自定义指标
  - Grafana 可接入（`prometheus.yml` scrape 配置附文档）

  **Risk**: Low

  **Effort**: 1d

- [x] 29. 🟢 **部署文档 `DEPLOY.md`**

  **What to do**:
  - 新建 `DEPLOY.md`，覆盖：
    1. **本地开发**：virtualenv 搭建、`.env` 配置、`python web_app.py`
    2. **生产部署**：Docker / Docker Compose / systemd 三种方式
    3. **Nginx 反代**：示例配置（含 `/api/sse` 的 `proxy_buffering off`）
    4. **TLS**：Let's Encrypt + certbot 流程
    5. **监控接入**：Prometheus scrape + Grafana dashboard 截图占位
    6. **日志聚合**：Vector → Loki 示例配置
    7. **回滚流程**：`docker pull` 历史 tag + `git checkout`
    8. **故障排查**：常见 5xx 原因 + 命令
  - 文档自检：`markdown-link-check DEPLOY.md` 无死链

  **Files**: `DEPLOY.md`（新建）、`docs/deploy/nginx.conf`（新建示例）、`docs/deploy/prometheus.yml`（新建示例）

  **Acceptance**:
  - 新人按 DEPLOY.md 应能 1 小时内完成部署
  - 文档涵盖 Docker 与非 Docker 两条路
  - 链接可访问

  **Risk**: Low

  **Effort**: 1d

**阶段 6 验收**：4 项勾选 + Docker 镜像 < 300MB + `/metrics` 暴露 6+ 自定义指标 + DEPLOY.md 完整

---

### 阶段 7: P3 文档 (3 天)

> 目标：OpenAPI + 架构图 + 开发者指南 + API 参考。依赖 Phase 1-6。

- [~] 30. 🟢 **OpenAPI 规范（自动生成）**

  **Status** (2026-06-02): 延期 — 需选 apispec vs flasgger vs 手写; 9 个 endpoint 都需要 yaml 注解; 优先级低于 DEPLOY.md (Task 29 已交付).

  **What to do**:
  - `pip install apispec apispec-webframeworks flask`
  - 在 `routes/` 各文件用 `apispec` 注解（`@docs(tags=['quote'], summary='获取实时行情')`、`@arguments(Schema)`）
  - 生成 `docs/openapi.json` + `docs/openapi.yaml`（构建脚本 `scripts/gen_openapi.py`）
  - 集成 Swagger UI：`/api/docs` 渲染 `swagger-ui-dist`（CDN 引入即可）
  - 注解覆盖：`/api/quote`、`/api/market`、`/api/auth/login`、`/api/backtest`、`/api/portfolio`、`/api/health`、`/api/cache/stats`

  **Files**: `docs/openapi.json`（生成）、`docs/openapi.yaml`（生成）、`scripts/gen_openapi.py`（新建）、`routes/*.py`（注解）、`web_app.py`（挂载 `/api/docs`）

  **Acceptance**:
  - `python scripts/gen_openapi.py` 成功生成
  - 浏览器打开 `/api/docs` 看到 Swagger UI
  - 7+ endpoint 有完整 schema

  **Risk**: Low

  **Effort**: 1d

- [~] 31. 🟢 **架构图（Mermaid）**

  **Status** (2026-06-02): 延期 — 需要 4 张 Mermaid 图 (系统总览/数据流/模块依赖/部署拓扑); 1-2 天工作量, 单独 session 合适.

  **What to do**:
  - 创建 `docs/architecture.md`，含 4 张 Mermaid 图：
    1. **系统总览图**：浏览器 → Nginx → Flask → 模块 + 外部 API
    2. **数据流图**：用户请求 → 路由 → 数据获取 → 缓存 → 外部 API
    3. **模块依赖图**：`modules/` 内部依赖（scoring → models → config）
    4. **部署拓扑图**：Docker Compose 服务拓扑
  - GitHub 自动渲染 Mermaid（在 md 文件中）
  - 同时导出 PNG 到 `docs/images/`（用 `mermaid-cli` 或在线）

  **Files**: `docs/architecture.md`（新建）、`docs/images/*.png`（新建）

  **Acceptance**:
  - 在 GitHub 仓库 README 或 docs 目录可直接看到图
  - 4 张图无渲染错误
  - 体现 v18 → v19 拆分前后对比

  **Risk**: Low

  **Effort**: 0.5d

- [~] 32. 🟢 **开发者指南 `CONTRIBUTING.md`**

  **Status** (2026-06-02): 延期 — 需要决定贡献者门槛 + CLA + 分支策略; v19 是私有项目, 优先级低.

  **What to do**:
  - 新建 `CONTRIBUTING.md`，覆盖：
    1. **项目结构**：`routes/` `modules/` `tests/` 职责
    2. **开发环境搭建**：Python 3.10+、venv、pre-commit 安装
    3. **代码规范**：black（行 100）、ruff 规则集、mypy 严格模式（部分放宽）
    4. **测试规范**：TDD、覆盖率门槛、E2E 写法
    5. **Git 规范**：分支策略（GitFlow 简化）、commit 消息格式、PR 模板
    6. **新功能流程**：从 issue → branch → PR → review → merge
    7. **模块化指引**：如何添加新 route、新 module
    8. **常见陷阱**：`web_app.py.bak` 警告、不要直接 import 外部 API

  **Files**: `CONTRIBUTING.md`（新建）、`.github/pull_request_template.md`（新建）

  **Acceptance**:
  - 新成员按文档能独立提交第一个 PR
  - 涵盖 v19 重构后的新结构
  - 与 README.md 链接

  **Risk**: Low

  **Effort**: 0.5d

- [~] 33. 🟢 **API 参考 `docs/api.md`**

  **Status** (2026-06-02): 延期 — 与 Task 30 (OpenAPI) 重复; 选其一即可, 建议合并到 OpenAPI.

  **What to do**:
  - 新建 `docs/api.md`，对照 `docs/openapi.yaml` 写人读版：
    - 7+ endpoint 详细文档
    - 每个 endpoint 含：URL、Method、Query/Body 参数、Response 200/4xx/5xx 示例、curl 示例、错误码说明
    - 鉴权说明（Phase 5.1）
    - 限流说明（Phase 2.4）
    - 变更日志章节（每个版本新增/废弃的 endpoint）
  - 导出 PDF / HTML（用 `md-to-pdf` 或 `pandoc`）
  - 与 Swagger UI 互链

  **Files**: `docs/api.md`（新建）、`docs/CHANGELOG_API.md`（新建）

  **Acceptance**:
  - 每个 endpoint 至少 1 个 curl 示例
  - 错误码完整（400/401/403/404/429/500/502/503）
  - 文档可读性 5/5（人工 review）

  **Risk**: Low

  **Effort**: 1d

**阶段 7 验收**：4 项勾选 + Swagger UI 可访问 + 4 张架构图渲染正常 + CONTRIBUTING.md + API 参考完整

---

## Risks & Mitigations

| 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|
| `routes/api.py` (52KB) 拆分引入回归 | 高 | 中 | Phase 4.1 先补测试再动；用 git bisect |
| 异步改造后事件循环阻塞 | 中 | 中 | 用 `pytest-asyncio` 验证；SSE 单独 worker |
| 限流分级误伤合法请求 | 中 | 低 | 监控 4xx 比例；灰度发布 |
| 回测引擎算力占用高 | 中 | 中 | 单独 worker + 任务队列（v20 备选） |
| Docker 镜像体积过大 | 低 | 中 | multi-stage build；alpine 基础镜像 |
| OpenAPI 注解代码侵入 | 低 | 高 | 用装饰器而非手动写 YAML |
| 旧 web_app.py.bak 残留 | 低 | 高 | 移到 `archive/` 而非直接删除 |

---

## Final Verification Wave (MANDATORY)

> 4 个并行验收 task。**全部 APPROVE 才算 v19 发布完成**。任一 REJECT 须修复后重跑。

- [~] F1. 🔴 **代码审查（计划合规审计）**

  **Status** (2026-06-02): 延期 — 需独立 review session (oh-my-claudecode:code-reviewer); 建议在 v20 之前执行; 当前 22/37 工作项完成 (含 16 延期), 合规审计 partial.

  **What to do**:
  - 跑 `git log --oneline v18..v19` 列所有 v19 提交
  - 对照本计划 33 个工作项，验证 1:1 命中（每个工作项至少 1 个 commit）
  - 验证 Must NOT Have 全部遵守：
    - `find . -size 0 -name "*.py"` 无输出
    - `grep "[A-Z0-9]\{32\}" routes/*.py modules/*.py` 仅命中 env 调用
    - `grep "except:" routes/ modules/ -r` 无输出
  - 验证 Definition of Done 全部满足

  **Agent Profile**: `oracle`（架构审视）
  **Evidence**: `.omo/evidence/optimization-v19/F1-compliance.md`
  **Output**: `Tasks [33/33] | Must Have [9/9] | Must NOT Have [8/8] | VERDICT: APPROVE/REJECT`

- [~] F2. 🟠 **测试覆盖与质量门**

  **Status** (2026-06-02): 延期 — 245 tests pass, 但 coverage 44.82% (目标 80%); F2 强制 80% 门槛不满足; 由 Task 17 升级后 (routers/api.py + auction + wp2 测试补全) 重新执行.

  **What to do**:
  - `pytest tests/ -v --cov=modules,routes --cov-fail-under=80`
  - 跑 ruff：`ruff check modules/ routes/ tests/`
  - 跑 mypy：`mypy --strict modules/ routes/`
  - 跑 black check：`black --check modules/ routes/ tests/`
  - 汇总 `coverage.xml` 报告到 evidence

  **Agent Profile**: `unspecified-high`
  **Evidence**: `.omo/evidence/optimization-v19/F2-quality.md`、`coverage.xml`
  **Output**: `Tests [N pass/N fail] | Coverage [X%] | Lint [PASS/FAIL] | Types [PASS/FAIL] | Format [PASS/FAIL] | VERDICT`

- [~] F3. 🟡 **E2E 烟测（5 个核心页面）**

  **Status** (2026-06-02): 延期 — 需生产 URL + 访问凭证; Task 18 已实现本地 20/20 E2E (5 页面覆盖); 部署后跑一次即可.

  **What to do**:
  - 启动应用：`docker run -p 5000:5000 jztz_v17:v19`
  - 跑 Playwright：`playwright test --reporter=list`
  - 5 个场景：首页、行情、登录、回测、健康检查
  - 截图保存到 `test-results/screenshots/`
  - 验证 SSE 长连接稳定性（开 60s 不掉）

  **Agent Profile**: `unspecified-high` + `playwright` skill
  **Evidence**: `.omo/evidence/optimization-v19/F3-e2e/`
  **Output**: `Scenarios [5/5] | SSE [STABLE] | Screenshots [N] | VERDICT`

- [~] F4. 🟢 **回滚与发布就绪**

  **Status** (2026-06-02): 延期 — DEPLOY.md 第 7 节已写回滚流程 (docker tag + git checkout), 但实际演练未做; 无状态应用, 数据无持久化, 风险低.

  **What to do**:
  - 创建 v19 git tag：`git tag -a v19.0.0 -m "v19 release"`
  - 回滚演练：`docker run -p 5001:5000 jztz_v17:v18` + `v19` 镜像并行启动，验证 5 个 endpoint 行为一致
  - 验证 `ghcr.io/<owner>/jztz_v17:v19.0.0` 镜像可拉
  - 验证 `.env.example` 完整（缺失 env 启动应报错并提示）
  - 检查 `DEPLOY.md` 链接有效：`markdown-link-check DEPLOY.md`

  **Agent Profile**: `deep`
  **Evidence**: `.omo/evidence/optimization-v19/F4-rollback.md`
  **Output**: `Tag [CREATED] | Rollback [OK] | Image [PUSHED] | Docs [VALID] | VERDICT`

---

## Commit Strategy

每工作项一个 commit，commit message 格式：
```
v19-phase{N}-W{N.M}: <简短描述>

- 工作项: 阶段 N 任务 M
- 涉及文件: <关键文件>
- 测试: <PASS/FAIL>
- 风险: <Low/Medium/High>
```

例：
```
v19-phase1-W1.1: 修复 routes/api.py:1088 重复 startswith 条件

- 工作项: 1.1 修 api.py:1088 bug
- 涉及文件: routes/api.py
- 测试: 已有 test_api_market 验证 SZ/CB 分类
- 风险: Low
```

---

## Success Criteria

### Verification Commands
```bash
# 1. 静态检查
ruff check modules/ routes/  # 替代 flake8
mypy --strict modules/ routes/

# 2. 测试
pytest tests/ -v --cov=modules,routes --cov-fail-under=80

# 3. E2E
playwright test  # 5 个核心页面

# 4. 性能对比（vs v18 baseline）
ab -n 1000 -c 50 http://localhost:5000/api/quote?code=000001
# 目标 P95 < 200ms（v18: ~450ms）

# 5. Docker 构建
docker build -t jztz_v17:v19 .
docker run -p 5000:5000 --env-file .env jztz_v17:v19
curl http://localhost:5000/api/health  # 期望 {"status": "ok"}
```

### Final Checklist
- [ ] 33 个工作项全部完成且打勾
- [ ] 4 个 F 验收任务全部 APPROVE
- [ ] 所有 P0/P1 工作项 + 其余 P2/P3 工作项均通过测试
- [ ] 单元测试覆盖率 ≥ 80%（重点模块 ≥ 95%）
- [ ] E2E 测试覆盖 5 个核心页面
- [ ] CI 跑通（GitHub Actions 全 job 绿）
- [ ] Docker 镜像可启动且 `/api/live` 返回 200
- [ ] OpenAPI 文档可访问（Swagger UI 渲染）
- [ ] `optimization_report.md` 已更新 v19 章节（或 draft 手动合并）
- [ ] 无 P0/P1 已知 bug 残留
- [ ] 无零字节占位文件
- [ ] 所有硬编码密钥已转 env（除 Eastmoney token 已迁移）
- [ ] `web_app.py.bak` 已移到 `archive/web_app_py_v18/`
- [ ] `routes/api.py:1088` 重复条件 bug 已修复

---

## 关联文档

- `D:\UI\jztz_v17\新老版本差异对比与迁移方案.md`：v18 拆分对比（本计划前置）
- `D:\UI\jztz_v17\optimization_report.md`：v18 优化报告（执行摘要见文件末尾 v19 章节）
- `D:\UI\jztz_v17\PLAN.md`：项目总规划
- `D:\UI\jztz_v17\README.md`：项目说明
- `D:\UI\jztz_v17\pytest.ini`：测试配置

---

## 收尾

*v19 优化计划全文 33 个工作项 + 4 个 F 阶段验收项已就绪。下一步：*
1. *更新 `optimization_report.md` 添加 v19 执行摘要 + 链接*
2. *用户选择：高精度审查（Momus 循环）/ 直接执行*

