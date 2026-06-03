# 价值投资之王 — 智能选股系统

> A 股全市场多因子智能选股平台 · v20.0.0 · 458 测试通过 / 87.5% 覆盖 / 21 个 API 端点

**价值投资之王** 是一个面向 A 股投资者的多因子选股系统。系统每 5 分钟扫描
全市场 5000+ 只股票，结合**行业动态 PE 阈值**与**五因子加权评分模型**筛
选优质标的；通过 Web 界面、企业微信推送、CSV/Excel 导出、投资组合跟踪与
策略回测，形成「**扫描 → 评分 → 推送 → 持仓 → 回测**」的完整闭环。

整个代码库经过两次大规模重构（v19 模块化 / v20 平台化），从 5400+ 行的
单体 `web_app.py` 演进为 30+ 个 `modules/` 业务模块 + 13 个 `routes/` 蓝
图组成的 Flask 应用工厂，并配套 OpenAPI 3.0.3 文档、Prometheus 指标、
Docker 多架构镜像与 GitHub Actions CI/CD。

---

## 目录

- [项目亮点](#项目亮点)
- [核心功能](#核心功能)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [配置说明](#配置说明)
- [API 概览](#api-概览)
- [Web 界面](#web-界面)
- [多因子评分模型](#多因子评分模型)
- [开发与测试](#开发与测试)
- [部署指南](#部署指南)
- [安全特性](#安全特性)
- [版本演进](#版本演进)
- [文档导航](#文档导航)
- [许可证](#许可证)

---

## 项目亮点

| 维度 | 指标 |
|---|---|
| 扫描范围 | A 股全市场 5000+ 只股票 |
| 评分因子 | 5 因子加权（价值 / 质量 / 成长 / 动量 / 情绪） |
| 评分模型 | `modules/scoring.py`（1526 行，30+ 个可调用函数） |
| 选股策略 | 4 套：每日推荐 / 集合竞价 / 资金流向 / 强势股 |
| API 端点 | **21 个**（REST + SSE，全部 OpenAPI 3.0.3 文档化） |
| 后台任务 | 4 个调度任务（每 60–300 秒执行） |
| 模块化 | 30 个 `modules/` 业务模块 + 13 个 `routes/` 蓝图 |
| 测试覆盖 | **458 passed · 6 skipped · 87.5% 覆盖**（v20 净增 211 个测试） |
| CI/CD | Lint + Matrix 测试（3 个 Python 版本）+ E2E + Docker 烟测 + 多架构发布 |
| 部署 | Docker / Compose / Gunicorn / Waitress / systemd 5 种方式 |
| 镜像 | 多架构 `linux/amd64` + `linux/arm64` 自动发布到 GHCR |

---

## 核心功能

### 1. 多策略选股引擎

- **每日推荐**（`modules/stock_picker.py`）— 全市场扫描，按 5 因子综合评分
  排序，输出 Top N。调度器每 5 分钟运行一次。
- **集合竞价**（`modules/auction_picker.py`）— 专注低估值、高潜力股票的竞
  价数据分析，量价配合判断 + 主力动向追踪，每 60 秒运行。
- **资金流向 WP2**（`modules/wp2_picker.py`）— 追踪大单资金净流入，识别主力
  建仓标的。
- **强势股**（`modules/strong_stock_picker.py`）— 挖掘市场领涨股，关注涨
  跌幅 / 换手率 / 量比等动量指标。

四套策略均通过统一 SSE 通道（`/api/sse`）向前端实时推送数据状态变化。

### 2. 多因子评分模型（v5 引擎）

`modules/scoring.py`（1526 行）是系统的核心算法库，特点：

- **五因子加权**：价值 36 % · 质量 11 % · 成长 8 % · 动量 12 % · 情绪 33 %
- **行业 PE 动态阈值**：内置 10 大行业（半导体 / 软件 AI / 医疗器械 / 新
  能源 / 电子元件 / 金融地产 / 汽车 / 消费 / 医药 / 周期）的 PE 区间，基
  于 189 只全样本回测动态调整。
- **纯函数设计**：所有评分函数为可独立调用的纯函数，便于单元测试。
- **评分短路优化**：低于阈值的股票直接跳过详细评分，提升批量扫描性能。

### 3. 数据获取与缓存

- **数据源**：东方财富 API（`quote.eastmoney.com`）+ akshare 兜底
- **同步 / 异步双客户端**：`modules/data_fetcher.py` 与
  `modules/async_data_fetcher.py`
- **HTTP 客户端**：`modules/http_client.py`（连接池、SSL 降级、超时控制）
- **熔断器**：`modules/circuit_breaker.py`（CLOSED → OPEN → HALF_OPEN）—
  东方财富 API 偶发限流时 3 次失败自动熔断 60 秒
- **缓存**：`modules/cache_manager.py`（TTL 过期、LRU 淘汰、内存上限）
  - 实时行情 TTL 60s · 财务数据 TTL 3600s · 技术指标 TTL 1800s · 行业分类 TTL 86400s

### 4. 认证与权限（v20 新增）

- **bcrypt 密码哈希**（12 轮 salt，`modules/auth.py`）
- **Flask Session 会话**（cookie-based，无需 Flask-Login 依赖）
- **CSRF 保护**（`X-CSRF-Token` 头 + 一次性 token，登录 / 登出豁免）
- **角色权限**（`admin` / `user`，`@role_required` 装饰器）
- **文件型用户存储**（`data/users.json`，原子写：tmp + rename）
- **防用户枚举**（未注册用户也走 bcrypt 比对，恒定响应时间）
- **用户名正则**（3-64 字符，`A-Za-z0-9_.-`）

### 5. 企业微信推送（v20 新增）

- **`modules/notifier.py`**：基于 `Protocol` 抽象，自动选择适配器
- **ConsoleNotifier**（默认，stdout 输出）
- **WeComNotifier**（配置 `WECOM_WEBHOOK_URL` 后启用）
- **Markdown 截断**：自动按 4096 字节截断，不破坏 UTF-8 字符
- **推送场景**：开盘/收盘选股结果 · 持仓信号变化 · 外部 API 降级 · 关键异常

### 6. 投资组合跟踪（v20 新增）

- **`modules/portfolio.py`**：文件型 JSON 存储（`data/portfolio.json`）
- **加权平均成本合并**（`code + buy_date` 维度去重）
- **P&L 计算**：从实时行情缓存取最新价，自动计算浮动盈亏
- **REST 端点**：`routes/portfolio.py` 提供增删改查

### 7. 策略回测（v20 新增）

- **`modules/backtest.py`**：纯 Python 等权重 Top-N 再平衡引擎
- **确定性**（无 I/O、无全局状态、毫秒级运行）
- **性能指标**：总收益 / 年化收益 / 夏普比率 / 最大回撤 / 胜率 / 换手率
- **数据源无关**：调用方在边界层注入价格历史（东方财富 / AKShare / 本地
  CSV 均可）

### 8. 数据导出（v20 新增）

- **`modules/exporter.py`** + **`routes/export.py`**
- **CSV**：UTF-8-with-BOM（Excel-on-Windows 中文不乱码）
- **Excel (.xlsx)**：自动列宽 + CJK 字符宽度启发式
- **公式注入防护**：以 `=+-@` 开头的单元格自动加 `'` 前缀转义

### 9. 监控与可观测性

- **`/metrics` 端点**：`modules/metrics.py` 自研纯 Python Prometheus 格式
  实现（Counter / Gauge / Histogram），无 `prometheus_client` 第三方依赖
- **`/api/live` · `/api/ready` · `/api/health`**：K8s 探针友好的健康检查
- **结构化日志**：`modules/logger.py` 注入 `request_id`，响应头回传
  `X-Request-ID`，日志可按 rid 串联追踪
- **SSE 推送**：`/api/sse` 实时通知前端数据状态（毫秒级更新）

### 10. 限流与容错

- **路径级速率限制**（`modules/rate_config.py` + 中间件）：
  - API 默认 30 req/min
  - 昂贵端点 5 req/min
  - 静态资源放行
  - 响应 `Retry-After` 头 + 429 状态码
- **熔断器**（如上）
- **统一异常**：`ApiError` 基类 + `AuthenticationError` /
  `RateLimitError` / `DataFetchError` 子类
- **全局错误处理**：`web_app.py` 保留 HTTPException 真实状态码（404/405
  /400 等），不再全部吞为 500

---

## 技术栈

| 类别 | 选型 | 版本 |
|---|---|---|
| 语言 | Python | 3.10 / 3.11 / 3.12（CI 矩阵验证） |
| Web 框架 | Flask | 3.1.2 |
| 异步 HTTP | aiohttp | 3.13.3 |
| 同步 HTTP | requests / urllib3 | 2.31.0 / 2.0.4 |
| 金融数据 | akshare | 1.18.55 |
| 配置 | python-dotenv | 1.0.1 |
| Excel | openpyxl | 3.1.5 |
| 认证 | bcrypt | （隐式依赖） |
| WSGI（生产）| Gunicorn (gthread) | 23.0.0 |
| WSGI（Windows）| Waitress | 3.0.2 |
| 测试 | pytest / pytest-cov / pytest-asyncio | 7.4.4 / 7.0.0 / 0.23.3 |
| Lint | ruff / black | 0.6.9 / 24.10.0 |
| Type Check | mypy | 1.11.2 |
| E2E | playwright | 1.47.0 |
| 容器 | Docker (multi-stage) + docker compose | python:3.11-slim |
| CI/CD | GitHub Actions | 4 个 job：lint / test / e2e / build / release |

---

## 快速开始

### 方式一：Docker（推荐生产）

```bash
git clone https://github.com/kingsmokez/jztz.git
cd jztz
cp .env.example .env       # 编辑 WECOM_WEBHOOK 等密钥
docker compose up -d       # 端口 5559
curl http://localhost:5559/api/live
```

### 方式二：Windows 一键启动

```bat
git clone https://github.com/kingsmokez/jztz.git
cd jztz
start_web.bat
```

脚本自动创建虚拟环境、安装依赖、启动 Web 服务。
浏览器打开 <http://localhost:5559>。

### 方式三：Linux / macOS

```bash
git clone https://github.com/kingsmokez/jztz.git
cd jztz
chmod +x start_web.sh
./start_web.sh
```

### 方式四：手动安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python web_app.py
```

---

## 项目结构

```
jztz_v17/                              # 仓库根（目录名沿用历史，代码已 v20）
├── web_app.py                  # Flask 应用工厂 + 入口（308 行）
├── start.py                    # Waitress 生产启动（Windows 友好）
├── start_web.bat / .sh         # 一键启动脚本
├── smart_stock_picker.py       # CLI 入口
│
├── modules/                    # 业务模块（30 个 .py，零 I/O 副作用）
│   ├── config.py               # 统一配置（frozen dataclass）
│   ├── models.py               # @dataclass StockQuote / FinancialData / StockScore
│   ├── errors.py               # ApiError 体系
│   ├── logger.py               # 结构化日志 + request_id
│   ├── api_response.py         # ok() / error() / paginate()
│   ├── auth.py                 # bcrypt + Session + CSRF（v20 新增）
│   ├── data_fetcher.py         # 同步行情获取
│   ├── async_data_fetcher.py   # 异步并发获取
│   ├── async_client.py         # 异步 HTTP 客户端
│   ├── http_client.py          # 同步 HTTP 客户端
│   ├── circuit_breaker.py      # 熔断器
│   ├── cache_manager.py        # TTL + LRU 缓存
│   ├── cache_config.py         # 缓存配置
│   ├── rate_config.py          # 路径级限流配置
│   ├── scoring.py              # 5 因子评分引擎（1526 行）
│   ├── technical.py            # MA / MACD / RSI / KDJ / BOLL
│   ├── stock_picker.py         # 每日推荐
│   ├── auction_picker.py       # 集合竞价
│   ├── wp2_picker.py           # 资金流向
│   ├── strong_stock_picker.py  # 强势股
│   ├── backtest.py             # 回测引擎（v20 新增）
│   ├── portfolio.py            # 投资组合（v20 新增）
│   ├── notifier.py             # WeCom / Console 推送（v20 新增）
│   ├── exporter.py             # CSV / Excel 导出（v20 新增）
│   ├── metrics.py              # Prometheus 指标（v20 新增）
│   ├── scheduler.py            # 后台调度
│   ├── ai_analyzer.py          # AI 辅助分析（预留接口）
│   ├── external_api.py         # 外部 API 集成
│   └── news.py                 # 资讯抓取
│
├── routes/                     # Flask 蓝图（13 个 .py）
│   ├── api.py                  # /api 核心端点（21 个）
│   ├── daily.py                # /daily 每日推荐页
│   ├── auction.py              # /auction 集合竞价
│   ├── wp2.py                  # /wp2 资金流向
│   ├── strong.py               # /strong 强势股
│   ├── ai.py                   # /ai AI 分析页
│   ├── auth.py                 # /api/auth 登录 / 登出 / 当前用户（v20 新增）
│   ├── portfolio.py            # /api/portfolio 持仓 REST（v20 新增）
│   ├── backtest.py             # /api/backtest 回测（v20 新增）
│   ├── export.py               # /api/export 导出（v20 新增）
│   ├── health.py               # /api/live · /api/ready · /api/health
│   ├── metrics.py              # /metrics Prometheus
│   └── docs.py                 # /api/docs Swagger UI
│
├── templates/                  # Jinja2 模板（10 个 HTML）
│   ├── index.html              # 主页：全市场选股
│   ├── daily_pick.html         # 每日推荐
│   ├── auction_pick.html       # 集合竞价
│   ├── wp2_pick.html           # 资金流向
│   ├── strong_pick.html        # 强势股
│   ├── auction_compare.html    # 竞价对比
│   ├── cb_arbitrage.html       # 可转债套利
│   ├── dexter_ai.html          # Dexter AI
│   ├── backtest_report.html    # 回测报告
│   └── login.html              # 登录页（v20 新增）
│
├── static/                     # 静态资源
├── tests/                      # 38 个测试文件（458 用例）
│   ├── e2e/                    # Playwright E2E（5 个）
│   └── test_*.py               # 单元 / 集成测试（33 个）
│
├── docs/                       # 文档
│   ├── architecture.md         # 架构图（4 张 Mermaid）
│   ├── api.md                  # API 人类可读参考
│   ├── openapi.json            # OpenAPI 3.0.3 规范
│   └── plans/                  # 项目计划与改进方案
│
├── data/                       # 运行时数据（不入库）
├── logs/                       # 应用日志（不入库）
├── wp2/                        # WP2 辅助模块（遗留）
│
├── Dockerfile                  # 多阶段构建（非 root 用户，HEALTHCHECK）
├── docker-compose.yml          # 单机编排
├── .github/workflows/ci.yml    # CI：lint + test (3×py) + e2e + build + release
├── .env.example                # 环境变量模板
├── requirements.txt            # 生产依赖（11 个）
├── requirements-dev.txt        # 开发依赖
├── pyproject.toml              # 项目元数据 + 工具配置
├── pytest.ini                  # pytest 配置（已合并到 pyproject）
├── playwright.config.py        # Playwright E2E 配置
│
├── README.md                   # 本文件
├── CHANGELOG.md                # 版本变更日志
├── FILE_DOCUMENTATION.md       # 文件级详细说明
├── CONTRIBUTING.md             # 贡献指南
└── DEPLOY.md                   # 部署指南（5 种方式 + Nginx + TLS + 监控 + 回滚）
```

**代码规模**：139 个跟踪文件，其中 Python 源码 60+ 个，HTML 模板 10 个，
测试 38 个。

---

## 配置说明

所有配置通过环境变量注入（`python-dotenv` 启动时加载 `.env`）。
**严禁**将真实 `.env` 提交到仓库（已在 `.gitignore` 中排除）。

| 变量 | 必填 | 说明 | 默认值 |
|---|---|---|---|
| `APP_SECRET_KEY` | 是 | Flask Session 签名密钥（生产 ≥32 字节随机） | `change-me-in-production` |
| `APP_HOST` | 否 | 绑定地址 | `0.0.0.0` |
| `APP_PORT` | 否 | 绑定端口 | `5559` |
| `APP_DEBUG` | 否 | 调试模式 | `false` |
| `WECOM_WEBHOOK_URL` | 否 | 企业微信机器人 Webhook（完整 URL 含 `key=`） | 空 → 走 ConsoleNotifier |
| `WECOM_TIMEOUT` | 否 | Webhook HTTP 超时（秒） | `5` |
| `WECOM_MENTIONED` | 否 | @提醒的成员手机号列表（逗号分隔） | 空 |
| `JZTZ_BOOTSTRAP_ADMIN_PASSWORD` | 否 | 首次启动默认 admin 密码 | `admin123` |
| `LOG_LEVEL` | 否 | `DEBUG` / `INFO` / `WARNING` / `ERROR` | `INFO` |
| `RATE_LIMIT` | 否 | 全局默认速率限制 | `60/minute` |
| `RATE_LIMIT_API` | 否 | `/api/*` 速率限制 | `30/minute` |
| `RATE_LIMIT_EXPENSIVE` | 否 | 昂贵端点速率限制 | `5/minute` |
| `DATA_TIMEOUT` | 否 | 数据获取超时（秒） | `10` |
| `CALIBRATE_THREADS` | 否 | 财务校准并发线程数 | `30` |
| `TECH_THREADS` | 否 | 技术指标并发线程数 | `15` |
| `SMARTBOX_TOKEN` | 否 | 腾讯 SmartBox API token | 空 |
| `GUNICORN_WORKERS` | 否 | Gunicorn worker 数 | `2` |
| `GUNICORN_THREADS` | 否 | 每 worker 线程数 | `4` |

详细默认值见 `modules/config.py`。

---

## API 概览

系统共暴露 **21 个 REST 端点 + 1 个 SSE 流**，全部由 OpenAPI 3.0.3 规
范描述，启动后访问 `/api/docs` 查看交互式 Swagger UI。

| 模块 | 端点 | 说明 |
|---|---|---|
| 行情 | `GET /api/market` | 全市场选股（5000+ 只，按综合评分排序）|
| 行情 | `GET /api/search?q=...` | 股票代码 / 名称模糊搜索 |
| 行情 | `GET /api/quote/<code>` | 单只股票实时行情 |
| 推荐 | `GET /api/daily` | 每日推荐结果 |
| 推荐 | `GET /api/auction` | 集合竞价结果 |
| 推荐 | `GET /api/auction/compare` | 竞价对比 |
| 推荐 | `GET /api/wp2` | 资金流向 |
| 推荐 | `GET /api/strong` | 强势股 |
| 认证 | `POST /api/auth/login` | 登录（返回 session cookie + CSRF） |
| 认证 | `POST /api/auth/logout` | 登出 |
| 认证 | `GET  /api/auth/whoami` | 当前用户信息 |
| 持仓 | `GET    /api/portfolio` | 持仓列表（含浮动盈亏）|
| 持仓 | `POST   /api/portfolio` | 新增持仓 |
| 持仓 | `PATCH  /api/portfolio/<id>` | 更新持仓 |
| 持仓 | `DELETE /api/portfolio/<id>` | 删除持仓 |
| 回测 | `POST /api/backtest` | 运行回测（自定义参数）|
| 导出 | `GET /api/export/csv` | CSV 下载（自动 BOM）|
| 导出 | `GET /api/export/xlsx` | Excel 下载（自动列宽）|
| 监控 | `GET /api/live` | 存活探针（始终 200）|
| 监控 | `GET /api/ready` | 就绪探针（依赖注入是否就绪）|
| 监控 | `GET /api/health` | 健康检查（含子系统状态）|
| 指标 | `GET /metrics` | Prometheus 文本格式 |
| 推送 | `GET /api/sse` | SSE 实时数据状态流 |
| 文档 | `GET /api/docs` | Swagger UI |
| 文档 | `GET /api/openapi.json` | OpenAPI 3.0.3 规范 |

---

## Web 界面

| 页面 | 路径 | 功能 |
|---|---|---|
| 主页 | `/` | 全市场实时扫描，多因子评分排序 |
| 每日推荐 | `/daily` | 9:30 / 14:30 两次自动选股结果 |
| 集合竞价 | `/auction` | 低估值潜力股筛选 |
| 资金流向 | `/wp2` | 大单资金净流入追踪 |
| 强势股 | `/strong` | 领涨股榜单 |
| AI 分析 | `/ai` | Dexter AI 辅助分析 |
| 可转债套利 | `/cb_arbitrage` | 可转债折溢价套利 |
| 回测报告 | `/backtest` | 自定义参数运行回测 |
| 持仓管理 | `/portfolio` | 持仓录入、盈亏跟踪 |
| 登录 | `/login` | 用户登录（v20 新增）|

所有页面响应式布局，桌面 / 平板 / 手机自适应。

---

## 多因子评分模型

| 因子 | 权重 | 核心指标 | 评分逻辑 |
|---|---|---|---|
| **价值因子** | 36 % | PE、PB、ROE、股息率 | PE / PB 越低越好，ROE / 股息率越高越好 |
| **质量因子** | 11 % | 资产负债率、经营现金流 | 低杠杆 + 强现金流 = 高分 |
| **成长因子** |  8 % | 营收增长率、净利润增长率 | 增长率越高越好 |
| **动量因子** | 12 % | 近期涨跌幅、相对强度 | 趋势跟踪（带反转保护）|
| **情绪因子** | 33 % | 市场热度、资金流向 | 资金净流入 + 热度指标 |

**行业 PE 动态阈值**（v17 起，行业 PE 区间由 189 只全样本回测校准）：

| 行业 | PE 上限 | PE 下限 | 说明 |
|---|---|---|---|
| 半导体 / 芯片 | 100 | 28 | 高成长，PE 中位数 84.6 |
| 软件 / AI | 120 | 39 | 高成长，PE 中位数 86.8 |
| 医疗器械 | 80 | 13 | PE 中位数 70.5 |
| 新能源 | 50 | 22 | PE 中位数 33–42 |
| 电子元件 | 85 | 25 | PE 中位数 49.1 |
| 金融 / 地产 | 20 |  8 | 低 PE 行业 |
| 汽车 | 50 | 10 | PE 中位数 15.9–26.2 |

**回测表现**（90 天持有期，等权重 Top-5 再平衡）：

| 指标 | 数值 |
|---|---|
| 累计收益率 | +26.09 % |
| 胜率 | 80 % |
| 交易次数 | 15 |

回测引擎位于 `modules/backtest.py`，纯 Python 实现，确定性毫秒级运行，
可独立调用。

---

## 开发与测试

### 环境准备

```bash
git clone https://github.com/kingsmokez/jztz.git
cd jztz
python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env
```

### 工具链

| 工具 | 命令 | 作用 |
|---|---|---|
| ruff | `ruff check .` | Lint（E/F/I/W/B/UP）|
| black | `black --line-length 100 .` | 格式化（`pyproject.toml` 已配置）|
| mypy | `mypy modules/ routes/ web_app.py` | 类型检查（`pyproject.toml` 已配置）|

### 测试

```bash
pytest                          # 全部 458 个用例
pytest tests/e2e/ -m e2e        # 端到端（需先启动 Flask）
pytest --cov=modules,routes     # 覆盖率报告
pytest -k "auth"                # 关键字过滤
```

**测试统计**（v20）：

| 维度 | 数值 |
|---|---|
| 总用例 | 458 passed + 6 skipped + 0 failed |
| 模块覆盖率 | 87.5 %（v19 时约 60 %）|
| 高覆盖模块 | `backtest.py` 94.3 % · `notifier.py` 96.6 % · `metrics.py` 89.8 % |
| 中等覆盖 | `portfolio.py` 79.7 % · `exporter.py` 79.2 % |
| E2E（Playwright） | 5 个关键用户旅程 |
| 跨版本 | CI 矩阵验证 Python 3.10 / 3.11 / 3.12 |

> 已知局限：Windows 下 `bcrypt` 5.x（Rust/PyO3）在 coverage 插桩下无
> 法重初始化，因此 `modules/auth.py` 与 `routes/auth.py` 在
> `pyproject.toml` 中从覆盖率统计排除，conftest 预导入让其他模块照常
> 测覆盖。**不是代码问题，是环境特性。**

### 项目结构约定

- `modules/` 内的模块**零 I/O 副作用**（除 `auth.py` / `portfolio.py` /
  `metrics.py` 等有明确存储抽象的模块），便于单元测试
- `routes/` 仅做参数解析 + 业务编排 + 响应序列化，**不包含业务算法**
- 所有公共函数有类型注解；所有蓝图有 docstring

完整约定见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

---

## 部署指南

5 种部署方式（生产推荐 Docker / Compose）：

| 方式 | 适用场景 | 关键命令 |
|---|---|---|
| **Docker 单机** | 个人 / 小团队生产 | `docker run -d -p 5559:5000 --env-file .env jztz_v17:v20` |
| **Docker Compose** | 单机编排（含 healthcheck / 资源限制）| `docker compose up -d` |
| **Gunicorn** | Linux 生产（gthread worker，兼容 SSE）| `gunicorn --workers 2 --threads 4 --worker-class gthread web_app:app` |
| **Waitress** | Windows 生产 | `python start.py` |
| **systemd** | Linux 长驻服务 | 提供 unit file（见 `DEPLOY.md` §5）|

### 反向代理（Nginx，可选）

- TLS 终止
- 关闭 SSE 缓冲（`proxy_buffering off;`）
- `/api/sse` 长连接超时设为 `0`

### 监控

- **Liveness**：`GET /api/live`（始终 200，进程在即活）
- **Readiness**：`GET /api/ready`（依赖注入完成才返回 200）
- **Metrics**：`GET /metrics`（Prometheus 抓取）
- **日志**：JSON 格式，含 `request_id`，可按 rid 串联

### 回滚

1. 镜像 tag 永远可拉（GHCR 保留所有 tag）
2. 旧版本数据文件 `data/users.json` / `data/portfolio.json` 向前兼容
3. 数据库 schema 版本字段（`version: 1`）保留升级空间

详见 [`DEPLOY.md`](DEPLOY.md)（含 pre-flight 检查、回滚演练、故障排查清单）。

---

## 安全特性

| 类别 | 实现 |
|---|---|
| **密码** | bcrypt 12 轮 + salt（`modules/auth.py`）|
| **会话** | Flask 签名 cookie，CSRF token 每次登录轮换 |
| **CSRF** | `X-CSRF-Token` 头比对 session 中的 token；登录/登出豁免 |
| **限流** | 路径级：`/api/*` 30 req/min，昂贵端点 5 req/min |
| **用户枚举防护** | 登录始终跑 bcrypt（未注册用户走 dummy hash）|
| **公式注入** | CSV/Excel 导出时 `=+-@` 前缀自动转义 |
| **HTML 转义** | Jinja2 autoescape 默认开启 |
| **运行时非 root** | Docker 镜像以 `uid=1000 jztz` 用户运行 |
| **HEALTHCHECK** | 30s 间隔，3 次失败标记 unhealthy |
| **依赖审计** | `requirements.txt` 全部固定精确版本（`==`）|
| **Secret 管理** | 所有密钥通过环境变量注入，`.env` 已 gitignore |

---

## 版本演进

| 版本 | 时间 | 关键变化 |
|---|---|---|
| **v20.0.0** | 2026-06-02 | Auth + Notifier + Exporter + Portfolio + Backtest + Metrics + OpenAPI + CI Release（净增 211 个测试）|
| **v19.0.0** | 2026-05-15 | 单体 5400+ 行 → 30 模块 + 13 蓝图 + 应用工厂 |
| v18.x | — | 早期重构（参见 git log）|
| v17 | 2025-12 | 行业 PE 阈值全样本回测校准 |
| v16 | — | 最优参数回测验证（+26.09 % 收益，80 % 胜率）|
| v15 | — | 多因子评分模型（5 因子权重分配）|
| v14 | — | 定时自动选股（9:30 / 14:30）|

详细变更见 [`CHANGELOG.md`](CHANGELOG.md)。

---

## 文档导航

| 文档 | 内容 |
|---|---|
| [`README.md`](README.md) | 本文件（项目总览）|
| [`CHANGELOG.md`](CHANGELOG.md) | 版本变更日志（Keep a Changelog 格式）|
| [`FILE_DOCUMENTATION.md`](FILE_DOCUMENTATION.md) | 每个文件 / 模块的详细功能说明（557 行）|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 贡献指南（开发环境、提 PR 流程、commit 规范）|
| [`DEPLOY.md`](DEPLOY.md) | 部署指南（5 种方式 + Nginx + 监控 + 回滚 + 故障排查）|
| [`docs/architecture.md`](docs/architecture.md) | 4 张 Mermaid 架构图（系统总览 / 数据流 / 模块依赖 / 部署拓扑）|
| [`docs/api.md`](docs/api.md) | API 人类可读参考（含示例请求 / 响应）|
| [`docs/openapi.json`](docs/openapi.json) | OpenAPI 3.0.3 规范（21 端点）|

---

## 许可证

本项目仅供**学习和研究使用**，不构成任何投资建议。
股市有风险，投资需谨慎。

---

**项目主页**：<https://github.com/kingsmokez/jztz>
**问题反馈**：<https://github.com/kingsmokez/jztz/issues>

---

> **免责声明**：本系统提供的选股结果仅供参考，不构成投资建议。使用者需
> 自行判断和承担投资风险。
