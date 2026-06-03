# 项目文件详细说明

本文档详细说明 jztz_v17 项目的所有文件和模块的功能与用途。

---

## 📁 目录结构概览

```
jztz_v17/
├── modules/              # 核心业务模块
├── routes/               # Flask 路由蓝图
├── templates/            # HTML 模板
├── static/               # 静态资源
├── tests/                # 测试文件
├── backtest/             # 回测模块
├── backtest_results/     # 回测结果
├── docs/                 # 文档
├── data/                 # 数据文件
├── logs/                 # 日志文件
├── wp2/                  # WP2辅助模块
├── archive/              # 归档文件
└── [配置文件]
```

---

## 🔧 核心模块 (`modules/`)

### 认证与安全模块

#### `auth.py` (15.8 KB)
- **功能**: 用户认证与权限管理
- **核心类**:
  - `UserStore`: 用户存储管理（基于 `data/users.json`）
  - `bcrypt` 密码加密
  - Session 会话管理
- **特性**:
  - 用户名正则验证 (`_USERNAME_RE`)
  - CSRF 保护
  - 角色权限控制（admin/user）
  - 防止用户枚举攻击（固定时间校验）

#### `errors.py` (2.9 KB)
- **功能**: 自定义异常类
- **异常类型**:
  - `ApiError`: API业务异常基类
  - `AuthenticationError`: 认证失败
  - `RateLimitError`: 速率限制
  - `DataFetchError`: 数据获取失败

### 数据获取模块

#### `data_fetcher.py` (25.2 KB) ⭐
- **功能**: A股实时行情数据获取
- **核心函数**:
  - `fetch_all_stocks()`: 获取全市场行情（5000+只）
  - `search_stock(keyword)`: 股票搜索
  - `get_stock_quote(code)`: 单只股票行情
- **数据源**: 东方财富API
- **特性**:
  - 连接池管理
  - SSL证书自动降级
  - 缓存机制（避免频繁请求）
  - 异常重试机制

#### `async_data_fetcher.py` (4.1 KB)
- **功能**: 异步数据获取（高性能版本）
- **用途**: 批量并发获取股票数据
- **特性**:
  - `asyncio` 异步IO
  - 并发请求控制

#### `async_client.py` (4.3 KB)
- **功能**: 异步HTTP客户端
- **核心类**: `AsyncHTTPClient`
- **特性**:
  - 连接池复用
  - 超时控制
  - 错误处理

### 选股策略模块

#### `scoring.py` (54.7 KB) ⭐⭐⭐
- **功能**: 多因子评分模型核心算法
- **评分因子**:
  1. **价值因子** (36%): PE、PB、ROE、股息率
  2. **质量因子** (11%): 资产负债率、现金流
  3. **成长因子** (8%): 营收增长率、净利润增长率
  4. **动量因子** (12%): 近期涨跌幅、相对强度
  5. **情绪因子** (33%): 市场热度、资金流向
- **行业PE适配**: 根据不同行业动态调整PE阈值
- **输出**: 综合评分（0-100分）

#### `stock_picker.py` (7.6 KB) ⭐
- **功能**: 每日选股策略
- **核心函数**: `run_picker()`
- **筛选条件**:
  - PE范围: 0-100（行业动态调整）
  - PB范围: 0-10
  - ROE最低: 8%
- **输出**: Top N 推荐股票列表

#### `auction_picker.py` (20.3 KB) ⭐
- **功能**: 集合竞价选股（低估值挖掘）
- **核心函数**: `run_auction_picker()`
- **策略**: 专注低估值、高潜力股票
- **特性**:
  - 竞价数据分析
  - 量价配合判断
  - 主力动向追踪

#### `wp2_picker.py` (26.6 KB)
- **功能**: WP2资金流向选股
- **核心函数**: `run_wp2_picker()`
- **策略**: 追踪大单资金流入流出
- **数据源**: 东方财富资金流向数据

#### `strong_stock_picker.py` (11.3 KB)
- **功能**: 强势股选股策略
- **策略**: 挖掘市场强势领涨股
- **指标**: 涨跌幅、换手率、量比

#### `technical.py` (10.8 KB)
- **功能**: 技术指标计算
- **指标**:
  - MA均线
  - MACD
  - RSI
  - KDJ
  - 布林带

### 回测与组合模块

#### `backtest.py` (14.6 KB) ⭐
- **功能**: 策略回测引擎
- **核心类**: `BacktestEngine`
- **回测方法**: 等权重Top-N再平衡
- **性能指标**:
  - 总收益率
  - 年化收益率
  - 夏普比率
  - 最大回撤
  - 胜率
  - 换手率
- **特性**: 纯Python实现，无外部依赖

#### `portfolio.py` (14.2 KB)
- **功能**: 投资组合管理
- **核心类**: `PortfolioManager`
- **功能**:
  - 持仓记录
  - 盈亏计算
  - 成本均价合并
  - 去重逻辑（基于 `code + buy_date`）
- **数据存储**: `data/portfolio.json`

### 数据导出与通知模块

#### `exporter.py` (11.3 KB)
- **功能**: 数据导出（CSV/Excel）
- **核心类**: `DataExporter`
- **特性**:
  - UTF-8-BOM（Excel兼容）
  - 自动列宽
  - 中文字符宽度启发式
  - **安全**: Excel公式注入防护（`=+-@` 前缀转义）

#### `notifier.py` (9.5 KB)
- **功能**: 企业微信消息推送
- **核心类**: `WeComNotifier`
- **特性**:
  - Markdown格式
  - 4096字节自动截断（不破坏字符）
  - 控制台降级输出

### 缓存与性能模块

#### `cache_manager.py` (4.4 KB)
- **功能**: 缓存管理器
- **核心类**: `CacheManager`
- **特性**:
  - TTL过期控制
  - LRU淘汰策略
  - 内存占用限制

#### `circuit_breaker.py` (5.2 KB)
- **功能**: 熔断器（容错机制）
- **核心类**: `CircuitBreaker`
- **状态**: CLOSED → OPEN → HALF_OPEN
- **用途**: 防止级联故障

#### `metrics.py` (15.1 KB)
- **功能**: Prometheus指标导出
- **指标类型**:
  - Counter: 计数器
  - Gauge: 仪表盘
  - Histogram: 直方图
- **端点**: `/metrics`
- **特性**: 纯Python实现，无外部依赖

### 配置与日志模块

#### `config.py` (3.1 KB)
- **功能**: 配置管理
- **核心类**: `Config`
- **配置项**:
  - 服务器配置（host, port, debug）
  - 缓存配置
  - 速率限制
  - 日志级别
- **加载方式**: 环境变量 + `.env` 文件

#### `logger.py` (2.6 KB)
- **功能**: 结构化日志
- **核心函数**: `log`
- **特性**:
  - `request_id` 串联追踪
  - JSON格式输出
  - 日志级别控制

#### `rate_config.py` (2.4 KB)
- **功能**: 速率限制配置
- **规则**: 按路径配置不同的请求限制
- **默认**: API 60次/分钟，静态资源无限制

#### `models.py` (4.4 KB)
- **功能**: 数据模型定义
- **数据类**:
  - `StockQuote`: 股票行情
  - `FinancialData`: 财务数据
  - `StockScore`: 评分结果
- **特性**: `@dataclass(frozen=True)` 不可变

### AI分析模块

#### `ai_analyzer.py` (1.9 KB)
- **功能**: AI辅助分析（预留接口）
- **用途**: 智能分析股票走势

#### `external_api.py` (1.7 KB)
- **功能**: 外部API集成
- **用途**: 对接第三方数据源

#### `scheduler.py` (2.2 KB)
- **功能**: 后台任务调度
- **核心类**: `Scheduler`
- **任务**:
  - 每日选股（5分钟）
  - 集合竞价（1分钟）
  - WP2选股（5分钟）
  - 强势选股（5分钟）

---

## 🛣️ 路由模块 (`routes/`)

### 核心API路由

#### `api.py` (56.9 KB) ⭐⭐⭐
- **蓝图**: `api`
- **前缀**: `/api`
- **端点** (21个):
  - `GET /api/stocks`: 全市场选股
  - `GET /api/search`: 股票搜索
  - `GET /api/quote/<code>`: 单只股票行情
  - `GET /api/daily`: 每日推荐
  - `GET /api/auction`: 集合竞价
  - `GET /api/wp2`: 资金流向
  - `GET /api/strong`: 强势股
  - 更多端点见 `docs/openapi.json`
- **响应格式**:
  ```json
  {
    "success": true,
    "data": [...],
    "error": null,
    "code": "OK"
  }
  ```

#### `daily.py` (10.5 KB)
- **蓝图**: `daily`
- **路由**: `/daily`
- **功能**: 每日推荐选股结果展示

#### `auction.py` (25.8 KB)
- **蓝图**: `auction`
- **路由**: `/auction`
- **功能**: 集合竞价选股界面

#### `wp2.py` (23.6 KB)
- **蓝图**: `wp2`
- **路由**: `/wp2`
- **功能**: 资金流向分析

#### `strong.py` (3.1 KB)
- **蓝图**: `strong`
- **路由**: `/strong`
- **功能**: 强势股选股

### 认证路由

#### `auth.py` (6.4 KB)
- **蓝图**: `auth`
- **路由**:
  - `POST /auth/login`: 用户登录
  - `POST /auth/logout`: 用户登出
  - `GET /auth/me`: 当前用户信息
- **安全**: bcrypt密码加密，Session会话

### 功能路由

#### `backtest.py` (8.4 KB)
- **蓝图**: `backtest`
- **路由**: `/backtest`
- **功能**: 策略回测界面

#### `portfolio.py` (7.8 KB)
- **蓝图**: `portfolio`
- **路由**: `/portfolio`
- **功能**: 投资组合管理

#### `export.py` (6.3 KB)
- **蓝图**: `export`
- **路由**: `/export`
- **功能**: 数据导出（CSV/Excel）

#### `ai.py` (8.3 KB)
- **蓝图**: `ai`
- **路由**: `/ai`
- **功能**: AI辅助分析界面

### 监控路由

#### `health.py` (3.3 KB)
- **蓝图**: `health`
- **路由**:
  - `GET /health`: 健康检查
  - `GET /ready`: 就绪检查
- **用途**: K8s探针、负载均衡

#### `metrics.py` (2.4 KB)
- **蓝图**: `metrics`
- **路由**: `/metrics`
- **功能**: Prometheus指标导出

#### `docs.py` (3.0 KB)
- **蓝图**: `docs`
- **路由**: `/api/docs`
- **功能**: Swagger UI文档界面
- **文档**: OpenAPI 3.0.3规范

---

## 🧪 测试文件 (`tests/`)

### 测试结构
- `test_unit_*.py`: 单元测试
- `test_integration_*.py`: 集成测试
- `test_e2e_*.py`: 端到端测试

### 测试覆盖
- **模块覆盖率**: 87.5%
- **总测试数**: 458 passed, 6 skipped
- **关键模块**:
  - `backtest.py`: 94.3%
  - `notifier.py`: 96.6%
  - `metrics.py`: 89.8%
  - `portfolio.py`: 79.7%
  - `exporter.py`: 79.2%

---

## 📄 配置文件

### Python配置
- `requirements.txt`: 生产依赖
  - flask
  - requests
  - urllib3
  - akshare
- `requirements-dev.txt`: 开发依赖
- `pyproject.toml`: 项目元数据
- `pytest.ini`: 测试配置
- `.pre-commit-config.yaml`: Git钩子配置

### Docker配置
- `Dockerfile`: 多阶段构建镜像
- `docker-compose.yml`: 容器编排
- `.dockerignore`: 构建排除

### CI/CD配置
- `.github/workflows/ci.yml`: GitHub Actions工作流
  - 自动测试
  - Docker镜像构建
  - 多架构支持（amd64/arm64）
  - GHCR镜像推送

### 环境变量
- `.env.example`: 环境变量模板
- `.env`: 实际环境变量（不提交）
- 支持变量:
  - `SECRET_KEY`: Flask密钥
  - `WECOM_WEBHOOK_URL`: 企业微信Webhook
  - `LOG_LEVEL`: 日志级别

---

## 📊 数据文件 (`data/`)

### 用户数据
- `users.json`: 用户信息存储
  - 格式: `{"username": {"password_hash": "...", "role": "user"}}`
  - 权限: 原子写入（write-then-rename）

### 缓存数据
- `portfolio.json`: 投资组合数据
- `daily_pick_cache.json`: 每日选股缓存
- `auction_pick_cache.json`: 集合竞价缓存
- `wp2_pick_cache.json`: WP2缓存

---

## 📚 文档 (`docs/`)

### 技术文档
- `architecture.md`: 架构设计文档（含4个Mermaid图）
- `api.md`: API参考文档（人类可读）
- `openapi.json`: OpenAPI 3.0.3规范（机器可读）

### 用户文档
- `README.md`: 项目简介与快速开始
- `CHANGELOG.md`: 版本变更日志
- `CONTRIBUTING.md`: 贡献指南
- `DEPLOY.md`: 部署指南
- `FILE_DOCUMENTATION.md`: 本文档

---

## 🎨 模板文件 (`templates/`)

### HTML模板
- `index.html`: 主页
- `daily_pick.html`: 每日推荐
- `auction_pick.html`: 集合竞价
- `wp2_pick.html`: 资金流向
- `backtest_report.html`: 回测报告
- `login.html`: 登录页

---

## 🔧 辅助脚本

### 启动脚本
- `start_web.bat`: Windows一键启动
- `start_web.sh`: Linux/macOS一键启动
- `start.py`: Python启动入口

### 发布脚本
- `push_v20_release.bat`: Windows发布脚本
- `push_v20_release.ps1`: PowerShell发布脚本

---

## 📦 其他目录

### `wp2/`
- WP2辅助模块（遗留）

### `backtest/`
- `preselect_backtest.py`: 预选回测
- `quick_backtest.py`: 快速回测

### `archive/`
- 归档的旧版本文件

### `logs/`
- 应用日志输出目录

---

## 🔐 安全特性

### 认证与授权
- bcrypt密码哈希
- Session会话管理
- CSRF保护
- 角色权限控制

### 输入验证
- 用户名正则验证
- 参数类型检查
- SQL注入防护

### 输出安全
- Excel公式注入防护
- XSS防护（自动转义）
- 敏感信息脱敏

### 速率限制
- API: 60次/分钟
- 登录: 5次/分钟
- 静态资源: 无限制

---

## 📈 性能优化

### 缓存策略
- 数据缓存（TTL 5分钟）
- 连接池复用
- 异步IO（可选）

### 监控指标
- 请求计数
- 响应时间
- 错误率
- 缓存命中率

---

## 🚀 部署架构

### 单机部署
```
用户 → Flask App (localhost:5559)
     → 东方财富API
     → 企业微信推送
```

### Docker部署
```
用户 → Nginx → Flask Container → 东方财富API
              ↓
              Redis (缓存，可选)
```

### Kubernetes部署
```
Ingress → Service → Pods (Flask)
       → ConfigMap (配置)
       → Secret (密钥)
       → PVC (持久化存储)
```

---

## 📞 技术支持

- **GitHub**: https://github.com/kingsmokez/jztz
- **Issues**: https://github.com/kingsmokez/jztz/issues

---

**最后更新**: 2026-06-02
**版本**: v20.0.0
