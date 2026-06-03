# 价值投资选股系统 v19 增量优化方案（2026-06-02 制定）

> **本章节为 v18 报告（`optimization_report.md`）的迭代更新**。v18 中已落地的拆分（web_app.py → routes/）保留在 v19 计划作为基础；v19 重点处理 v18 报告中**未覆盖的稳定性/性能/DevOps/文档**维度，并修复 1 个新增 P0 bug。

> **配套详细计划**：`D:\UI\jztz_v17\.omo\plans\optimization-v19.md`（47716 bytes / 33 工作项 / 4 验收项，可直接执行）
> **配套对比文档**：`D:\UI\jztz_v17\新老版本差异对比与迁移方案.md`（v17→v18 拆分对比）

> **使用方式**：本文件可作为"执行摘要"追加到 `optimization_report.md` 末尾（替换原"## 十、总结"之后的内容），保留 GBK 编码兼容。

---

## 一、v19 新发现的问题（v18 未涵盖）

### P0 - 立即修复

- `routes/api.py:1088` 重复条件 bug：`elif c.startswith("12") or c.startswith("12"):`，疑似应为 `("12","13")` 区分深市债券与可转债
- 零字节占位文件残留：`modules/scoring_new.py` (0B)、`fix_web_app.py` (0B)、`tests/test_auction.py` (0B)
- `requirements.txt` 缺 `akshare`（被多文件 import）、缺 `urllib3` 显式声明、全用 `>=` 未锁版本
- `web_app.py.bak` 未归档

### P1 - 性能与稳定性（v18 未重点关注）

- `routes/api.py:api_market` 用 `getattr(api_market, '_cache', {})` 函数属性缓存，应统一到 `modules/cache_manager.py` 的 TTL Cache
- `modules/async_data_fetcher.py` 已实现但 `routes/api.py` 仍走 `requests` 同步调用
- `web_app.py:58` SSE `while True` 循环未捕获 `GeneratorExit`，客户端断连后 worker 不释放
- 外部 API（Tencent/Sina/Eastmoney）无熔断机制，单一故障会拖慢整页
- 无 `/api/health` 端点（容器化部署无法做 liveness/readiness probe）
- 日志仅 stdout，多 worker 部署无法串联请求

### P1 - 安全

- `routes/api.py:74` 硬编码 Eastmoney API key `[TOKEN_REMOVED]`，应移至环境变量
- 限流统一 60/min，SSE 长连接和 search 重计算应差异化

### P2 - 测试

- `routes/api.py` (52KB/最大模块) **零测试**
- `tests/` 当前 8 个测试文件但缺 E2E（Playwright）
- 无 CI（`.github/workflows/` 不存在）
- 无 pre-commit hooks

### P2 - 新功能（需求验证）

- `templates/login.html` (47KB 完整 UI) **无后端实现**（无 `routes/auth.py`、无 `/api/login`）
- `templates/backtest_report.html` (16KB 含 chart.js) **无后端实现**（无 `routes/backtest.py`、无 `/api/backtest`）
- `README.md` 提及 `WECOM_WEBHOOK` 环境变量但 `modules/` 无对应实现
- 用户常用导出 CSV/Excel 至本地，无对应 endpoint
- `routes/` 缺 `/api/portfolio` 持仓追踪端点

### P3 - DevOps & 文档

- 无 Dockerfile（部署需手动 pip install）
- 无 OpenAPI 规范（9 个 endpoint 端 REST API 契约未文档化）
- 无架构图、开发者指南、API 参考

---

## 二、v19 计划范围（7 阶段 / 33 工作项 / 4 验收）

| 阶段 | 主题 | 工作项 | 估时 | 优先级 |
|---|---|---|---|---|
| Phase 1 | P0 立即修复 | 4 | 1-2 天 | 🔴 |
| Phase 2 | P1 技术债清理 | 6 | 1 周 | 🟠 |
| Phase 3 | P1 性能与稳定性 | 6 | 1 周 | 🟠 |
| Phase 4 | P2 测试与质量 | 4 | 1 周 | 🟡 |
| Phase 5 | P2 新功能（5 项） | 5 | 2 周 | 🟡 |
| Phase 6 | P3 DevOps | 4 | 3-5 天 | 🟢 |
| Phase 7 | P3 文档 | 4 | 3 天 | 🟢 |
| Final | 验收（F1-F4） | 4 | 1-2 天 | - |
| **合计** |  | **33 + 4** | **~6 周** |  |

---

## 三、v19 vs v18 关键差异

| 维度 | v18 报告 | v19 计划 | 变化 |
|---|---|---|---|
| 拆分 | 提出拆分 web_app.py | 已在 v18 落地（routes/） | ✅ 已完成 |
| 去重 | 列出 4 类重复 | 5 类（新增 wp2_picker 重复） | ➕ 更全面 |
| 认证 | 列为 P0 紧急 | P2 新功能 | ⬇️ 降级（已移到 Phase 5） |
| 限流 | 提到"限流重试" | 分级策略（按 endpoint 差异化） | ⬆️ 更细化 |
| 性能 | 未涉及 | 6 个独立工作项（缓存/异步/SSE/熔断/健康/日志） | ➕ 新增 |
| DevOps | "Docker" 1 天 | 4 工作项（Docker/CI/监控/部署文档） | ⬆️ 完整化 |
| 测试 | "添加核心函数单元测试" | 80% 覆盖率 + E2E + pre-commit + CI | ⬆️ 系统化 |
| 文档 | 未涉及 | OpenAPI + 架构图 + 开发者指南 + API 参考 | ➕ 新增 |
| 计划形式 | 表格优先级 | 详细 plan（可执行） | ⬆️ 可执行 |
| 验收 | 总结建议 | F1-F4 强制验收 | ➕ 形式化 |

---

## 四、详细计划入口

完整可执行计划（每工作项含 What/Files/Acceptance/Risk/Effort/QA）：

**`D:\UI\jztz_v17\.omo\plans\optimization-v19.md`**（47716 bytes / 33 工作项 / 4 验收项）

引用结构：
- 第 1-4 项：P0 立即修复（修 bug、清零字节、锁版本、归档 bak）
- 第 5-10 项：P1 技术债（scoring/auction 统一、错误处理、限流分级、配置化、去重）
- 第 11-16 项：P1 性能（缓存统一、异步 IO、SSE、熔断、健康、日志）
- 第 17-20 项：P2 测试（覆盖率 80%、E2E、pre-commit、CI）
- 第 21-25 项：P2 新功能（认证、回测、微信推送、数据导出、持仓追踪）
- 第 26-29 项：P3 DevOps（Docker、CI/CD、监控、部署文档）
- 第 30-33 项：P3 文档（OpenAPI、架构图、开发者指南、API 参考）
- F1-F4：强制验收（合规审计、测试覆盖、E2E 烟测、回滚演练）

---

## 五、风险与缓解（v19 新增）

| 风险 | 影响 | 缓解 |
|---|---|---|
| `routes/api.py` 52KB 拆分引入回归 | 高 | Phase 4.1 先补测试再动；git bisect |
| 异步改造事件循环阻塞 | 中 | pytest-asyncio 验证；SSE 单独 worker |
| 限流分级误伤 | 中 | 监控 4xx 比例；灰度 |
| 回测引擎算力占用 | 中 | 单 worker；v20 引入任务队列 |
| Docker 镜像过大 | 低 | multi-stage build；alpine |
| 旧 web_app.py.bak 误删 | 低 | 移到 archive/ 而非直接删 |

---

## 六、与 v18 报告的衔接

### v18 报告中**已落地**的项（v19 不重复）

- ✅ `web_app.py` 拆分 routes/（v18 §1.2）
- ✅ 多处 `print` → `logging`（v18 §3.2，部分）
- ✅ `modules/api_response.py` 存在（v18 §3.1）
- ✅ `tests/test_scoring.py` 221 行（v18 §4.1）

### v18 报告中**仍未落地**的项（v19 重点处理）

- ❌ scoring/auction_picker/stock_picker 去重 → v19 Phase 2
- ❌ 限流重试 → v19 Phase 2.4（更细化）
- ❌ 单元测试 → v19 Phase 4.1（目标 80%，v18 仅提及"添加核心函数测试"）
- ❌ Docker → v19 Phase 6.1（v18 仅 1 天，v19 完整化）
- ❌ 认证 → v19 Phase 5.1（v18 P0 紧急，v19 评估后 P2 新功能）

---

## 七、建议执行顺序

```
Week 1:  Phase 1 (P0) + Phase 2 (P1 技术债前半)
Week 2:  Phase 2 后半 + Phase 3 (性能与稳定性)
Week 3:  Phase 4 (测试)
Week 4-5: Phase 5 (新功能)
Week 5-6: Phase 6 (DevOps) + Phase 7 (文档)
```

**关键路径**：Phase 1 → 2.1（scoring 统一）→ 3.1（缓存统一）→ 4.1（覆盖率）→ 5.1（认证）→ 6.1（Docker）→ 7.1（OpenAPI）→ F1-F4

---

**手动合并步骤**：

由于路径约束，规划文件只能写在 `.omo/` 内。若要将本节内容追加到 `optimization_report.md`（GBK 编码），可执行：

```bash
# PowerShell（保留 GBK 编码）
$gbk = [System.Text.Encoding]::GetEncoding("GBK")
$report = [System.IO.File]::ReadAllText("D:\UI\jztz_v17\optimization_report.md", $gbk)
$addendum = [System.IO.File]::ReadAllText("D:\UI\jztz_v17\.omo\drafts\optimization-v19-summary.md", $gbk)
[System.IO.File]::WriteAllText("D:\UI\jztz_v17\optimization_report.md", $report + "`n`n" + $addendum, $gbk)
```

> **注意**：上例假设将 UTF-8 源文件读为 GBK（会乱码）。正确做法：用编辑器手动复制本文件内容到 `optimization_report.md` 末尾"## 十、总结"之后。

---

*详细执行计划：`D:\UI\jztz_v17\.omo\plans\optimization-v19.md`*
*对比文档：`D:\UI\jztz_v17\新老版本差异对比与迁移方案.md`*
