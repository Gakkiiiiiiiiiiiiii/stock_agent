# 金融分析 Agent 架构详细设计文档

> 版本：v1.0  
> 日期：2026-06-26  
> 目标系统：基于 Claude 的金融分析 Agent  
> 核心组件：Claude Agent + Skills + MCP Servers + Python 投研引擎 + PostgreSQL/向量库  
> 适用场景：A 股/港股/美股投研辅助、主题投资分析、技术分析、行业逻辑沉淀、每日市场扫描、盘后复盘  
> 免责声明：本文档仅用于系统设计与投研辅助工具建设，不构成任何投资建议。

---

## 1. 项目背景

当前目标是设计一个金融分析 Agent，使用 Claude 实现 Agent 编排能力，通过 Skill 沉淀投研方法论，通过 MCP 获取外部数据、调用计算工具、查询知识库，并完成技术分析、行业逻辑存储、主题异动提醒和每日投资决策辅助。

该系统不是一个简单问答机器人，也不是单纯的 RAG 知识库，而是一个面向实盘研究流程的投研智能体系统。

核心目标包括：

1. 将个人长期积累的行业逻辑、主题投资框架、技术分析方法、交易复盘经验系统化。
2. 使用 Claude 负责多步骤推理、工具调用、归因解释和报告生成。
3. 使用 Skill 固化分析流程、输出模板、交易纪律和风控规则。
4. 使用 MCP 连接外部行情、新闻、公告、财报、数据库、技术指标计算引擎和回测系统。
5. 使用 Python 后端完成确定性计算，避免模型临时计算造成指标口径漂移。
6. 使用数据库和知识库长期存储行业逻辑、标的映射、技术信号、市场状态和复盘记录。
7. 第一阶段定位为“投研辅助 + 标的筛选 + 风险提示 + 复盘系统”，不直接自动下单。

---

## 2. 设计原则

### 2.1 Claude 不直接做确定性计算

Claude 擅长语言理解、归纳推理、多工具编排、报告生成，但不适合直接承担高一致性计算任务。

以下内容不应由 Claude 临时手算：

- MA、EMA、MACD、KDJ、RPS
- B1/B2/B3 信号识别
- 缩量、放量、突破、跌破
- 行业强度排序
- 主题打分
- 持仓风险暴露计算
- 回测结果统计
- 数据库查询和写入 SQL

这些任务应由 Python 后端或 MCP 工具完成。

### 2.2 Skill 负责流程，不负责存储

Skill 是 Claude 的领域能力说明书，用于告诉 Claude：

- 遇到什么任务时应该使用什么分析流程
- 应该调用哪些 MCP 工具
- 输出结构应该如何组织
- 哪些判断禁止主观臆断
- 哪些风险必须提示
- 如何执行复盘

Skill 不应承担长期数据存储职责。

行业逻辑、交易记录、主题库、标的映射、历史信号，应存储在数据库或 Markdown + 向量库中。

### 2.3 MCP 负责受控访问外部能力

MCP Server 应暴露受控工具，而不是把数据库、文件系统、交易权限完全开放给 Claude。

推荐原则：

- Claude 只能调用工具，不直接执行 SQL。
- 工具入参必须结构化。
- 工具出参必须结构化。
- 读写操作分离。
- 写操作必须留审计日志。
- 高风险操作需要用户确认。
- 第一版不开放自动下单。

### 2.4 金融 Agent 应先做辅助决策，而非全自动交易

第一阶段系统定位：

```text
市场扫描 → 主题识别 → 技术筛选 → 风险提示 → 组合建议 → 盘后复盘
```

不建议第一阶段接入自动交易。原因：

1. A 股主题投资对市场情绪、政策语境、资金博弈高度敏感。
2. 模型可能过度解释噪声。
3. 工具调用错误、数据延迟、行情口径不一致会放大风险。
4. 没有长期实盘验证前，不应让 Agent 自动下单。

---

## 3. 总体架构

### 3.1 架构总览

```mermaid
flowchart TD
    U[用户 / 定时任务] --> C[Claude Agent]

    C --> S1[Skill: 技术分析流程]
    C --> S2[Skill: 行业逻辑分析]
    C --> S3[Skill: 每日市场决策]
    C --> S4[Skill: 风控复盘]

    C --> M1[MCP: market-data]
    C --> M2[MCP: technical-factor]
    C --> M3[MCP: industry-knowledge]
    C --> M4[MCP: news-event]
    C --> M5[MCP: portfolio-risk]
    C --> M6[MCP: backtest]

    M1 --> E1[行情数据服务]
    M2 --> E2[Python 技术指标引擎]
    M3 --> DB[(PostgreSQL + pgvector)]
    M4 --> E4[新闻/公告/政策服务]
    M5 --> E5[组合风险引擎]
    M6 --> E6[回测引擎]

    DB --> KB[Markdown 知识库]
    DB --> LOG[决策日志 / 复盘记录]
```

### 3.2 核心分层

| 层级 | 组件 | 职责 |
|---|---|---|
| 交互层 | 用户、定时任务、Web UI、CLI | 发起分析任务、查看报告、确认写入 |
| Agent 层 | Claude | 理解任务、规划步骤、调用工具、综合结论 |
| Skill 层 | Claude Skills | 固化分析流程、输出模板、交易纪律 |
| MCP 层 | MCP Servers | 暴露行情、指标、知识库、新闻、回测、风险工具 |
| 计算层 | Python Engines | 执行确定性计算、打分、回测、信号识别 |
| 存储层 | PostgreSQL、pgvector、Markdown | 存储行情、知识库、映射、信号、日志 |
| 观测层 | 日志、审计、指标监控 | 记录工具调用、错误、耗时、写操作 |

---

## 4. Claude、Skill、MCP 的职责边界

### 4.1 Claude Agent 职责

Claude 负责：

1. 理解用户意图。
2. 判断任务类型：
   - 个股技术分析
   - 行业逻辑整理
   - 主题异动分析
   - 每日市场扫描
   - 持仓风险评估
   - 盘后复盘
   - 策略回测解释
3. 选择对应 Skill。
4. 规划工具调用顺序。
5. 调用 MCP 工具。
6. 合并多源数据。
7. 输出结构化投研结论。
8. 标记不确定性和数据缺口。
9. 根据用户反馈生成知识库更新建议。

Claude 不负责：

1. 直接计算技术指标。
2. 直接写 SQL。
3. 直接修改数据库。
4. 直接下单。
5. 未经工具验证就给出确定性行情结论。
6. 在缺少数据时强行输出买卖建议。

### 4.2 Skill 职责

Skill 负责：

1. 规定分析流程。
2. 规定工具调用清单。
3. 规定输出格式。
4. 规定禁止事项。
5. 规定风控检查项。
6. 固化你的投研方法论。

Skill 适合沉淀：

- 葛兰碧缩量回调买点
- RPS 强势池
- MACD 三金叉
- 均线粘合突破
- 行业逻辑分析框架
- 主题异动触发规则
- 每日复盘模板
- 仓位管理纪律

### 4.3 MCP Server 职责

MCP Server 负责把外部能力暴露给 Claude：

| MCP Server | 主要能力 |
|---|---|
| market-data-mcp | 获取行情、K线、指数、行业、资金流 |
| technical-factor-mcp | 计算技术指标和交易信号 |
| industry-knowledge-mcp | 查询、新增、更新行业逻辑 |
| news-event-mcp | 获取新闻、公告、政策和财报事件 |
| portfolio-risk-mcp | 查询持仓、计算风险暴露 |
| backtest-mcp | 执行策略回测和结果统计 |

MCP 工具应具备：

- 明确名称
- 明确入参 schema
- 明确出参 schema
- 明确错误码
- 调用日志
- 权限级别
- 是否允许写操作标识

---

## 5. 推荐技术栈

### 5.1 MVP 技术栈

所有中间件服务使用docker部署

| 模块 | 推荐方案 |
|---|---|
| Agent | Claude Code 本地 / Claude API |
| Skill | Claude Skills，目录式 SKILL.md |
| MCP 框架 | Python MCP SDK / FastMCP |
| Web 服务 | FastAPI |
| 数据库 | PostgreSQL |
| 向量检索 | pgvector |
| 缓存 | Redis |
| 调度 | APScheduler / cron |
| 数据处理 | pandas、numpy、duckdb |
| 技术指标 | pandas-ta / ta-lib / 自研 |
| 回测 | vectorbt / backtrader / 自研轻量框架 |
| 部署 | Docker Compose |
| 日志 | loguru / structlog |
| 监控 | Prometheus + Grafana |

### 5.2 后续生产化技术栈

| 模块 | 推荐方案 |
|---|---|
| API 网关 | Nginx / APISIX |
| 服务治理 | Docker Compose → Kubernetes |
| 任务队列 | Celery / Dramatiq |
| 消息系统 | Kafka |
| 权限 | JWT / OAuth2 / API Key |
| 审计 | PostgreSQL audit_log |
| 对象存储 | MinIO |
| 向量库 | pgvector → Milvus / Qdrant |
| 可观测性 | OpenTelemetry + Prometheus + Loki |

---

## 6. 目录结构设计

```text
financial-agent/
├── README.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── app.yaml
│   ├── data_source.yaml
│   ├── mcp_servers.yaml
│   └── risk_rules.yaml
│
├── skills/
│   ├── a-share-technical-analysis/
│   │   ├── SKILL.md
│   │   └── examples/
│   ├── industry-logic-research/
│   │   ├── SKILL.md
│   │   └── templates/
│   ├── daily-market-decision/
│   │   ├── SKILL.md
│   │   └── templates/
│   ├── portfolio-risk-review/
│   │   ├── SKILL.md
│   │   └── checklists/
│   └── post-trade-review/
│       ├── SKILL.md
│       └── templates/
│
├── mcp_servers/
│   ├── market_data_server.py
│   ├── technical_factor_server.py
│   ├── industry_knowledge_server.py
│   ├── news_event_server.py
│   ├── portfolio_risk_server.py
│   └── backtest_server.py
│
├── engines/
│   ├── market/
│   │   ├── data_provider.py
│   │   ├── sector_strength.py
│   │   └── fund_flow.py
│   ├── technical/
│   │   ├── indicators.py
│   │   ├── pattern_detector.py
│   │   ├── b1_detector.py
│   │   ├── b2_detector.py
│   │   ├── b3_detector.py
│   │   └── rps_engine.py
│   ├── theme/
│   │   ├── theme_score.py
│   │   ├── event_matcher.py
│   │   └── stock_mapping.py
│   ├── risk/
│   │   ├── portfolio_risk.py
│   │   ├── position_limit.py
│   │   └── drawdown.py
│   └── backtest/
│       ├── backtest_runner.py
│       ├── metrics.py
│       └── reports.py
│
├── storage/
│   ├── migrations/
│   ├── repositories/
│   └── schemas/
│
├── knowledge_base/
│   ├── themes/
│   │   ├── 钠离子电池.md
│   │   ├── 厄尔尼诺.md
│   │   ├── AI机房液冷.md
│   │   ├── 人形机器人.md
│   │   └── 短剧AI化.md
│   ├── strategies/
│   │   ├── B1缩量回调.md
│   │   ├── B2确认启动.md
│   │   ├── B3补票战法.md
│   │   ├── RPS强势池.md
│   │   └── MACD三金叉.md
│   └── risk/
│       ├── 仓位管理.md
│       └── 交易纪律.md
│
├── data_ingestion/
│   ├── sync_market_daily.py
│   ├── sync_sector_daily.py
│   ├── sync_news_daily.py
│   ├── sync_financial_report.py
│   └── sync_fund_flow.py
│
├── app/
│   ├── api.py
│   ├── agent_orchestrator.py
│   ├── task_scheduler.py
│   └── report_renderer.py
│
├── tests/
│   ├── test_indicators.py
│   ├── test_b1_detector.py
│   ├── test_theme_score.py
│   └── test_mcp_tools.py
│
└── docker-compose.yml
```

---

## 7. Skill 设计

### 7.1 Skill 总清单

| Skill 名称 | 触发场景 | 主要输出 |
|---|---|---|
| a-share-technical-analysis | 用户要求分析个股/ETF/行业指数技术形态 | 趋势、买点、风险、操作条件 |
| industry-logic-research | 用户输入行业逻辑或要求分析行业 | 产业链、催化、标的、证伪条件 |
| daily-market-decision | 每日盘前/盘中/盘后市场分析 | 市场环境、强主题、候选股、仓位建议 |
| portfolio-risk-review | 用户要求看持仓风险 | 风险暴露、集中度、止损、降仓建议 |
| post-trade-review | 用户要求复盘交易 | 是否符合系统、错误归因、改进规则 |
| strategy-backtest-review | 用户要求分析策略回测 | 收益、回撤、胜率、失败阶段、优化建议 |

### 7.2 a-share-technical-analysis/SKILL.md 示例

```markdown
---
name: a-share-technical-analysis
description: 用于分析 A 股个股、ETF、行业指数的技术形态，包括趋势、量价、KDJ、MACD、RPS、B1/B2/B3、买卖点和风险。
---

# A 股技术分析 Skill

## 使用场景

当用户要求分析个股、ETF、行业指数的技术形态、买点、卖点、左侧机会、右侧确认、是否破位、是否适合加仓时，使用本 Skill。

## 必须遵守的原则

1. 不允许凭主观感觉直接给买入建议。
2. 指标计算必须来自 technical-factor-mcp。
3. 缺少行情数据时，必须说明数据不足。
4. 必须区分左侧、右侧、趋势中继、追高和破位。
5. 必须给出证伪条件。

## 分析顺序

1. 获取行情数据。
2. 获取技术指标。
3. 获取行业强度。
4. 获取主题映射。
5. 判断买点类型。
6. 判断风险。
7. 输出操作建议。

## 必须调用的工具

- get_kline
- calc_technical_indicators
- detect_pattern_signal
- get_sector_strength
- get_stock_theme_mapping

## 输出格式

### 1. 当前技术状态
### 2. 买点类型
### 3. 信号强度
### 4. 风险点
### 5. 操作建议
### 6. 需要等待的确认条件
### 7. 证伪条件
```

### 7.3 industry-logic-research/SKILL.md 示例

```markdown
---
name: industry-logic-research
description: 用于整理、查询、更新行业逻辑和主题投资知识库，包括产业链、受益标的、催化因素、监控关键词和证伪条件。
---

# 行业逻辑研究 Skill

## 使用场景

当用户输入一个行业逻辑、主题投资观点、产业链机会，或者要求分析某个主题是否成立时，使用本 Skill。

## 分析框架

1. 核心逻辑是否清晰。
2. 产业链位置是否明确。
3. 受益标的是否与逻辑强相关。
4. 催化因素是否可监控。
5. 是否有量化触发条件。
6. 是否有证伪条件。
7. 当前市场是否已经 price in。
8. 是否存在高位拥挤风险。

## 必须调用的工具

- search_theme_logic
- get_theme_related_stocks
- upsert_theme_logic
- match_event_to_theme
- evaluate_theme_trigger

## 输出格式

### 1. 主题结论
### 2. 核心逻辑
### 3. 产业链拆解
### 4. 受益标的
### 5. 催化因素
### 6. 监控关键词
### 7. 触发规则
### 8. 证伪条件
### 9. 是否建议入库
```

### 7.4 daily-market-decision/SKILL.md 示例

```markdown
---
name: daily-market-decision
description: 用于每日市场扫描、主题强度分析、候选标的筛选和仓位建议。
---

# 每日市场决策 Skill

## 使用场景

盘前、盘中、盘后，需要分析当日市场状态、最强主题、可交易方向、候选标的和风险提示时，使用本 Skill。

## 分析顺序

1. 判断指数环境。
2. 判断市场情绪。
3. 判断风格偏好。
4. 判断行业/主题强度。
5. 查询主题知识库。
6. 筛选技术形态。
7. 检查持仓风险。
8. 输出候选标的和仓位建议。

## 必须调用的工具

- get_market_snapshot
- get_sector_strength
- rank_themes_by_score
- scan_stock_signals
- evaluate_portfolio_risk

## 输出格式

### 1. 市场环境
### 2. 风格判断
### 3. 今日强主题
### 4. 主题逻辑验证
### 5. 候选标的
### 6. 当前不建议参与方向
### 7. 仓位建议
### 8. 明日观察点
```

---

## 8. MCP Server 详细设计

### 8.1 market-data-mcp

#### 职责

提供行情、指数、行业、主题、资金流数据。

#### 工具列表

| 工具 | 说明 | 权限 |
|---|---|---|
| get_kline | 获取个股/ETF/指数 K 线 | 只读 |
| get_market_snapshot | 获取全市场快照 | 只读 |
| get_sector_strength | 获取行业强度排名 | 只读 |
| get_theme_strength | 获取主题强度排名 | 只读 |
| get_fund_flow | 获取资金流数据 | 只读 |
| get_limit_up_stats | 获取涨停、连板、炸板数据 | 只读 |

#### 示例：get_kline

入参：

```json
{
  "symbol": "300750.SZ",
  "start_date": "2025-01-01",
  "end_date": "2026-06-26",
  "freq": "1d",
  "adjust": "qfq"
}
```

出参：

```json
{
  "symbol": "300750.SZ",
  "freq": "1d",
  "adjust": "qfq",
  "records": [
    {
      "date": "2026-06-26",
      "open": 100.0,
      "high": 105.0,
      "low": 99.0,
      "close": 103.0,
      "volume": 12345678,
      "amount": 1234567890.0,
      "turnover_rate": 2.35
    }
  ]
}
```

### 8.2 technical-factor-mcp

#### 职责

计算技术指标、识别买点和风险形态。

#### 工具列表

| 工具 | 说明 | 权限 |
|---|---|---|
| calc_technical_indicators | 计算 MA、EMA、MACD、KDJ、RPS 等 | 只读 |
| detect_pattern_signal | 识别 B1/B2/B3、三金叉、平台突破 | 只读 |
| scan_stock_signals | 批量扫描市场技术信号 | 只读 |
| explain_signal | 返回信号触发明细 | 只读 |
| backfill_signals | 重新计算历史信号 | 写入技术信号表 |

#### 技术指标清单

趋势类：

- MA5、MA10、MA20、MA60、MA120、MA240
- EMA
- STL：`EMA(EMA(C,10),10)`
- LTL：`(MA(C,14)+MA(C,28)+MA(C,57)+MA(C,114))/4`
- 均线粘合
- 均线多头排列
- 扣抵压力

动量类：

- MACD
- DIF、DEA、MACD histogram
- KDJ
- RPS20、RPS50、RPS120、RPS250
- 20 日/60 日相对强弱

量价类：

- 成交额
- 换手率
- HSP10
- HSLR20
- 极致缩量
- 放量突破
- 高位放量滞涨

形态类：

- B1 缩量回调买点
- B2 确认启动买点
- B3 补票买点
- MACD 三金叉
- 均线粘合突破
- 平台突破
- 假突破
- 跌破长期趋势线
- 出货大阴线

#### 示例：detect_pattern_signal

入参：

```json
{
  "symbol": "688345.SH",
  "date": "2026-06-26",
  "patterns": ["B1", "B2", "B3", "MACD_TRIPLE_GOLDEN", "RPS_POOL"]
}
```

出参：

```json
{
  "symbol": "688345.SH",
  "date": "2026-06-26",
  "signals": [
    {
      "pattern": "B1",
      "triggered": true,
      "score": 82,
      "evidence": [
        "股价位于 LTL 上方",
        "KDJ J 值低位拐头",
        "近 10 日缩量明显",
        "未出现高位放量出货形态"
      ],
      "risk": [
        "行业强度一般",
        "距离前高仍有压力"
      ]
    }
  ]
}
```

### 8.3 industry-knowledge-mcp

#### 职责

管理行业逻辑、主题知识库、标的映射、催化因素和证伪条件。

#### 工具列表

| 工具 | 说明 | 权限 |
|---|---|---|
| search_theme_logic | 查询主题逻辑 | 只读 |
| get_theme_related_stocks | 查询主题相关标的 | 只读 |
| upsert_theme_logic | 新增/更新主题逻辑 | 写 |
| upsert_theme_stock_mapping | 新增/更新标的映射 | 写 |
| evaluate_theme_trigger | 判断事件是否触发主题 | 只读 |
| rank_themes_by_score | 按综合分排名主题 | 只读 |

#### 示例：search_theme_logic

入参：

```json
{
  "theme_name": "AI机房液冷",
  "include_stocks": true,
  "include_trigger_rules": true
}
```

出参：

```json
{
  "theme_name": "AI机房液冷",
  "core_thesis": "AI机房单位功耗和热密度提升，高温环境下风冷效率下降，液冷渗透率有望提升。",
  "industry_chain": ["上游冷板/管路", "中游液冷系统", "下游IDC/AI算力中心"],
  "catalysts": ["极端高温", "AI资本开支", "高功率GPU服务器部署", "PUE约束"],
  "invalidation_rules": ["AI资本开支下修", "液冷订单低于预期", "核心公司财报不兑现"],
  "related_stocks": [
    {
      "symbol": "002837.SZ",
      "name": "英维克",
      "relation": "液冷温控",
      "sensitivity_score": 85,
      "certainty_score": 75
    }
  ]
}
```

### 8.4 news-event-mcp

#### 职责

抓取和结构化新闻、公告、政策、财报、事件，并映射到主题。

#### 工具列表

| 工具 | 说明 | 权限 |
|---|---|---|
| search_news | 查询新闻 | 只读 |
| search_announcements | 查询公告 | 只读 |
| search_policy_events | 查询政策事件 | 只读 |
| match_event_to_theme | 将事件映射到主题 | 只读 |
| summarize_event_impact | 评估事件影响 | 只读 |
| save_event_signal | 保存事件信号 | 写 |

#### 事件类型

```text
policy        政策
earnings      财报
order         订单
price         产品价格
capacity      产能
technology    技术突破
overseas      海外事件
weather       天气/灾害
supply_chain  供应链
regulation    监管
```

### 8.5 portfolio-risk-mcp

#### 职责

管理持仓、组合风险、主题暴露、仓位纪律。

#### 工具列表

| 工具 | 说明 | 权限 |
|---|---|---|
| get_portfolio | 查询当前持仓 | 只读 |
| evaluate_portfolio_risk | 计算组合风险 | 只读 |
| get_theme_exposure | 查询主题暴露 | 只读 |
| suggest_position_adjustment | 给出仓位调整建议 | 只读 |
| save_trade_review | 保存交易复盘 | 写 |

#### 风险维度

- 总仓位
- 单票仓位
- 行业集中度
- 主题集中度
- 高位股占比
- 流动性风险
- 回撤风险
- 财报风险
- 黑天鹅事件风险
- 与市场风格不匹配风险

### 8.6 backtest-mcp

#### 职责

执行策略历史回测、信号验证和失败阶段分析。

#### 工具列表

| 工具 | 说明 | 权限 |
|---|---|---|
| run_strategy_backtest | 执行回测 | 只读/计算 |
| get_backtest_report | 查询回测结果 | 只读 |
| compare_strategies | 对比多个策略 | 只读 |
| analyze_failure_periods | 分析失效阶段 | 只读 |
| save_strategy_version | 保存策略版本 | 写 |

#### 回测指标

- 年化收益
- 最大回撤
- 夏普比率
- 卡玛比率
- 胜率
- 盈亏比
- 换手率
- 单笔最大亏损
- 最大连续亏损
- 分年度收益
- 牛市/熊市/震荡市表现
- 超额收益
- 基准对比

---

## 9. 数据库设计

### 9.1 行情表

```sql
CREATE TABLE market_daily_bar (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    trade_date DATE NOT NULL,
    open NUMERIC(18, 4),
    high NUMERIC(18, 4),
    low NUMERIC(18, 4),
    close NUMERIC(18, 4),
    pre_close NUMERIC(18, 4),
    volume BIGINT,
    amount NUMERIC(24, 4),
    turnover_rate NUMERIC(10, 4),
    adjust_type VARCHAR(16),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, trade_date, adjust_type)
);

CREATE INDEX idx_market_daily_bar_symbol_date
ON market_daily_bar(symbol, trade_date);
```

### 9.2 股票基础信息表

```sql
CREATE TABLE stock_basic (
    symbol VARCHAR(32) PRIMARY KEY,
    name VARCHAR(128),
    exchange VARCHAR(32),
    list_date DATE,
    industry_lv1 VARCHAR(128),
    industry_lv2 VARCHAR(128),
    industry_lv3 VARCHAR(128),
    market_cap NUMERIC(24, 4),
    float_market_cap NUMERIC(24, 4),
    is_active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### 9.3 技术指标表

```sql
CREATE TABLE technical_indicator_daily (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    trade_date DATE NOT NULL,

    ma5 NUMERIC(18, 4),
    ma10 NUMERIC(18, 4),
    ma20 NUMERIC(18, 4),
    ma60 NUMERIC(18, 4),
    ma120 NUMERIC(18, 4),
    ma240 NUMERIC(18, 4),

    stl NUMERIC(18, 4),
    ltl NUMERIC(18, 4),

    macd_dif NUMERIC(18, 6),
    macd_dea NUMERIC(18, 6),
    macd_hist NUMERIC(18, 6),

    kdj_k NUMERIC(18, 6),
    kdj_d NUMERIC(18, 6),
    kdj_j NUMERIC(18, 6),

    rps20 NUMERIC(10, 4),
    rps50 NUMERIC(10, 4),
    rps120 NUMERIC(10, 4),
    rps250 NUMERIC(10, 4),

    hsp10 NUMERIC(10, 4),
    hslr20 NUMERIC(10, 4),

    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, trade_date)
);

CREATE INDEX idx_technical_indicator_symbol_date
ON technical_indicator_daily(symbol, trade_date);
```

### 9.4 技术信号表

```sql
CREATE TABLE technical_signal (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    trade_date DATE NOT NULL,
    signal_type VARCHAR(64) NOT NULL,
    signal_name VARCHAR(128),
    triggered BOOLEAN DEFAULT FALSE,
    score NUMERIC(10, 4),
    evidence JSONB,
    risk JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, trade_date, signal_type)
);

CREATE INDEX idx_technical_signal_date_type
ON technical_signal(trade_date, signal_type);
```

### 9.5 主题逻辑表

```sql
CREATE TABLE theme_logic (
    id BIGSERIAL PRIMARY KEY,
    theme_name VARCHAR(128) NOT NULL UNIQUE,
    core_thesis TEXT,
    industry_chain JSONB,
    catalysts JSONB,
    monitoring_keywords JSONB,
    trigger_rules JSONB,
    invalidation_rules JSONB,
    positive_factors JSONB,
    negative_factors JSONB,
    confidence_score NUMERIC(10, 4),
    version INTEGER DEFAULT 1,
    source_doc_path VARCHAR(512),
    embedding VECTOR(1536),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### 9.6 主题-标的映射表

```sql
CREATE TABLE theme_stock_mapping (
    id BIGSERIAL PRIMARY KEY,
    theme_id BIGINT REFERENCES theme_logic(id),
    symbol VARCHAR(32) NOT NULL,
    stock_name VARCHAR(128),
    relation_type VARCHAR(128),
    relation_description TEXT,
    sensitivity_score NUMERIC(10, 4),
    certainty_score NUMERIC(10, 4),
    elasticity_score NUMERIC(10, 4),
    evidence TEXT,
    risk_note TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(theme_id, symbol)
);
```

### 9.7 主题每日打分表

```sql
CREATE TABLE theme_daily_score (
    id BIGSERIAL PRIMARY KEY,
    theme_id BIGINT REFERENCES theme_logic(id),
    trade_date DATE NOT NULL,

    price_strength_score NUMERIC(10, 4),
    volume_score NUMERIC(10, 4),
    fund_flow_score NUMERIC(10, 4),
    news_score NUMERIC(10, 4),
    technical_score NUMERIC(10, 4),
    heat_score NUMERIC(10, 4),
    risk_score NUMERIC(10, 4),
    final_score NUMERIC(10, 4),

    rank INTEGER,
    explanation TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(theme_id, trade_date)
);
```

### 9.8 事件信号表

```sql
CREATE TABLE event_signal (
    id BIGSERIAL PRIMARY KEY,
    event_date DATE NOT NULL,
    event_time TIMESTAMP,
    event_type VARCHAR(64),
    title TEXT,
    content TEXT,
    source VARCHAR(128),
    url TEXT,
    matched_themes JSONB,
    matched_symbols JSONB,
    importance_score NUMERIC(10, 4),
    sentiment VARCHAR(32),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_event_signal_date_type
ON event_signal(event_date, event_type);
```

### 9.9 持仓表

```sql
CREATE TABLE portfolio_position (
    id BIGSERIAL PRIMARY KEY,
    account_id VARCHAR(64),
    symbol VARCHAR(32),
    stock_name VARCHAR(128),
    position_qty NUMERIC(24, 4),
    available_qty NUMERIC(24, 4),
    cost_price NUMERIC(18, 4),
    latest_price NUMERIC(18, 4),
    market_value NUMERIC(24, 4),
    pnl NUMERIC(24, 4),
    pnl_ratio NUMERIC(10, 4),
    position_ratio NUMERIC(10, 4),
    trade_date DATE,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### 9.10 决策日志表

```sql
CREATE TABLE agent_decision_log (
    id BIGSERIAL PRIMARY KEY,
    decision_date DATE NOT NULL,
    task_type VARCHAR(64),
    user_query TEXT,
    tools_called JSONB,
    input_snapshot JSONB,
    output_summary TEXT,
    suggested_actions JSONB,
    risk_warnings JSONB,
    confidence_score NUMERIC(10, 4),
    created_at TIMESTAMP DEFAULT NOW()
);
```

### 9.11 交易复盘表

```sql
CREATE TABLE trade_review (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE,
    symbol VARCHAR(32),
    stock_name VARCHAR(128),
    action VARCHAR(32),
    trade_price NUMERIC(18, 4),
    trade_qty NUMERIC(24, 4),
    reason TEXT,
    matched_strategy VARCHAR(64),
    expected_scenario TEXT,
    invalidation_condition TEXT,
    actual_result TEXT,
    mistake_type VARCHAR(128),
    improvement_note TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 10. 行业知识库设计

### 10.1 存储形态

行业知识库采用三层结构：

```text
Markdown 原文 + PostgreSQL 结构化表 + 向量检索
```

三者职责：

| 存储方式 | 作用 |
|---|---|
| Markdown | 人类可读、便于版本管理 |
| PostgreSQL | 结构化查询、主题打分、标的映射 |
| pgvector | 语义检索、相似主题查找、历史案例召回 |

### 10.2 Markdown 模板

```markdown
# 主题名称

## 1. 核心结论

## 2. 核心逻辑

## 3. 产业链拆解

### 3.1 上游
### 3.2 中游
### 3.3 下游
### 3.4 应用场景

## 4. 受益标的

| 标的 | 环节 | 受益逻辑 | 弹性 | 确定性 | 风险 |
|---|---|---|---|---|---|

## 5. 催化因素

## 6. 监控关键词

## 7. 触发规则

## 8. 证伪条件

## 9. 风险因素

## 10. 历史案例

## 11. 更新记录
```

### 10.3 主题打分框架

主题最终分数：

```text
theme_final_score =
    price_strength_score * 0.25
  + volume_score         * 0.15
  + fund_flow_score      * 0.15
  + news_score           * 0.20
  + technical_score      * 0.15
  + knowledge_score      * 0.10
  - risk_score           * 0.20
```

各分项说明：

| 分项 | 含义 |
|---|---|
| price_strength_score | 板块涨幅、相对指数强度、持续性 |
| volume_score | 成交额放大、量能持续性 |
| fund_flow_score | 主力资金、北向、ETF、融资等 |
| news_score | 新闻/公告/政策催化强度 |
| technical_score | 板块和核心标的技术形态 |
| knowledge_score | 与知识库核心逻辑匹配程度 |
| risk_score | 高位、拥挤、证伪、财报风险 |

### 10.4 主题触发规则示例

以 AI 机房液冷为例：

```json
{
  "theme": "AI机房液冷",
  "positive_triggers": [
    {
      "type": "weather",
      "condition": "极端高温新闻显著增加",
      "score_add": 10
    },
    {
      "type": "market",
      "condition": "液冷板块成交额较20日均值放大50%以上",
      "score_add": 15
    },
    {
      "type": "technical",
      "condition": "核心标的突破60日新高数量增加",
      "score_add": 10
    },
    {
      "type": "news",
      "condition": "AI数据中心、GPU服务器、液冷订单相关新闻增加",
      "score_add": 15
    }
  ],
  "negative_triggers": [
    {
      "type": "earnings",
      "condition": "核心公司业绩低于预期",
      "score_minus": 20
    },
    {
      "type": "market",
      "condition": "板块高位放量滞涨",
      "score_minus": 15
    }
  ]
}
```

---

## 11. 技术分析引擎设计

### 11.1 指标口径统一

所有技术指标必须固定参数和口径。

#### 均线

```text
MA5   = close.rolling(5).mean()
MA10  = close.rolling(10).mean()
MA20  = close.rolling(20).mean()
MA60  = close.rolling(60).mean()
MA120 = close.rolling(120).mean()
MA240 = close.rolling(240).mean()
```

#### 用户体系中的 STL 和 LTL

```text
STL = EMA(EMA(C, 10), 10)
LTL = (MA(C, 14) + MA(C, 28) + MA(C, 57) + MA(C, 114)) / 4
```

#### MACD

默认参数：

```text
fast = 12
slow = 26
signal = 9
```

#### KDJ

默认参数：

```text
N = 9
M1 = 3
M2 = 3
```

#### RPS

```text
RPS_N = 当前股票 N 日涨幅在全市场中的百分位排名
```

常用窗口：

```text
RPS20
RPS50
RPS120
RPS250
```

### 11.2 B1 策略识别

B1 定义：强势股缩量回调低吸买点。

核心条件示例：

```text
1. 中长期趋势未破坏
2. 股价接近或站上 LTL
3. KDJ J 值处于低位或低位拐头
4. 回调过程缩量
5. 未出现明显出货形态
6. 所在行业或主题不弱
7. 个股仍在强势池或接近强势池
```

可量化条件：

```text
C >= LTL * 0.975
L >= LTL * 0.94
COUNT(C < LTL, 10) <= 4
HSP10 <= 6
HSLR20 <= 1.10
KDJJ < 30
COUNT(PBIG, 12) <= 2
COUNT(PDIS, 12) <= 2
```

输出：

```json
{
  "signal": "B1",
  "triggered": true,
  "score": 82,
  "entry_type": "左侧试探",
  "confirm_condition": "重新放量站上短期均线",
  "stop_condition": "有效跌破 LTL 或平台下沿"
}
```

### 11.3 B2 策略识别

B2 定义：确认启动买点。

核心条件：

```text
1. 前期有整理平台
2. 出现 4% 以上中大阳线
3. 放量突破关键均线或平台
4. 行业/主题同步走强
5. 不是高位末端加速
```

输出：

```json
{
  "signal": "B2",
  "triggered": true,
  "score": 88,
  "entry_type": "右侧确认",
  "risk": "若次日缩量回落至突破位下方，信号失败"
}
```

### 11.4 B3 策略识别

B3 定义：主升阶段补票买点。

核心条件：

```text
1. 个股已经完成主升确认
2. 短期回踩不破关键均线
3. 量能没有明显失控
4. 主题仍在持续
5. 不追连续加速后的高位放量长上影
```

适用场景：

```text
错过 B1 和 B2 后，寻找主升中继的低风险补票位置。
```

### 11.5 MACD 三金叉识别

三金叉：

```text
1. 均线金叉
2. MACD 金叉
3. 成交量均线金叉
```

增强条件：

```text
1. MA120/MA240 上行
2. 5/10/20 均线粘合后向上发散
3. 股价站上粘合区
4. DDX 或资金流出现正向信号
5. 所属行业同步走强
```

### 11.6 RPS 强势池

入池条件示例：

```text
RPS50 >= 85 或 RPS120 >= 85
且股价未明显跌破中期趋势
```

出池条件示例：

```text
RPS50 < 75
或股价跌破 LTL
或行业强度显著下降
```

输出：

```json
{
  "symbol": "300xxx",
  "in_rps_pool": true,
  "first_enter_date": "2026-06-20",
  "rps50": 91,
  "rps120": 88
}
```

---

## 12. 每日市场决策流程

### 12.1 流程图

```mermaid
flowchart TD
    A[定时任务/用户触发] --> B[获取市场快照]
    B --> C[判断指数环境]
    C --> D[判断市场情绪]
    D --> E[获取行业/主题强度]
    E --> F[匹配主题知识库]
    F --> G[扫描个股技术信号]
    G --> H[组合风险评估]
    H --> I[Claude 综合判断]
    I --> J[输出日报/候选股/风险提示]
    J --> K[写入决策日志]
```

### 12.2 市场环境判断

输入：

```text
上证指数、创业板指、科创50、中证1000、恒生科技、纳斯达克
成交额
涨跌家数
涨停/跌停数量
连板高度
行业涨跌幅
ETF资金流
北向/南向资金
```

输出：

```json
{
  "market_regime": "震荡偏强",
  "risk_appetite": "中等",
  "style": "小盘成长占优",
  "suggested_position": "50%-70%",
  "warnings": [
    "成交额未明显放大",
    "强主题集中度较高，追高风险上升"
  ]
}
```

### 12.3 主题强度排序

输入：

```text
行业涨幅
主题涨幅
成交额变化
涨停数量
核心标的技术形态
新闻事件
资金流
知识库触发条件
```

输出：

```json
{
  "date": "2026-06-26",
  "top_themes": [
    {
      "theme": "AI机房液冷",
      "score": 86,
      "reason": "高温催化 + 成交额放大 + 核心标的突破"
    },
    {
      "theme": "钠离子电池",
      "score": 74,
      "reason": "产业逻辑明确，但板块技术强度一般"
    }
  ]
}
```

### 12.4 候选标的筛选

筛选顺序：

```text
1. 主题强度 Top N
2. 主题相关标的
3. 剔除高位过热标的
4. 技术信号扫描
5. 流动性过滤
6. 财报/公告风险过滤
7. 输出候选池
```

输出字段：

```json
{
  "symbol": "002837.SZ",
  "name": "英维克",
  "theme": "AI机房液冷",
  "technical_signal": "B2",
  "entry_score": 84,
  "risk_score": 45,
  "suggested_action": "等待回踩确认，不追高",
  "invalidation": "跌破突破位且成交额不能恢复"
}
```

---

## 13. 盘后复盘流程

### 13.1 复盘目标

复盘不是简单记录盈亏，而是判断：

1. 当日判断是否符合系统。
2. 买入/卖出是否有明确依据。
3. 亏损是系统内亏损还是纪律错误。
4. 行业逻辑是否需要修正。
5. 技术信号是否误判。
6. 是否需要调整打分权重。
7. 是否需要加入新的证伪条件。

### 13.2 复盘模板

```markdown
# 交易复盘

## 1. 基本信息

- 日期：
- 标的：
- 操作：
- 价格：
- 仓位：

## 2. 操作原因

## 3. 对应策略

- B1 / B2 / B3 / RPS / MACD三金叉 / 其他

## 4. 当时预期

## 5. 证伪条件

## 6. 实际走势

## 7. 是否符合系统

## 8. 错误类型

- 追高
- 左侧过早
- 没有等待确认
- 主题判断错误
- 风控执行错误
- 正常系统内亏损

## 9. 改进措施

## 10. 是否更新知识库/规则
```

---

## 14. Agent 任务编排设计

### 14.1 用户提问：个股分析

用户输入：

```text
分析一下海博思创当前是否适合左侧买入
```

执行流程：

```text
1. Claude 识别任务类型：个股技术分析 + 行业逻辑 + 买点判断
2. 加载 a-share-technical-analysis Skill
3. 调用 get_kline
4. 调用 calc_technical_indicators
5. 调用 detect_pattern_signal
6. 调用 get_stock_theme_mapping
7. 调用 search_theme_logic
8. 调用 get_sector_strength
9. Claude 综合输出：
   - 技术位置
   - 左侧买点质量
   - 行业逻辑
   - 风险
   - 等待确认条件
   - 证伪条件
```

### 14.2 用户输入：新增行业逻辑

用户输入：

```text
钠电池处于从0到1阶段，正极材料、铝箔、储能环节受益……
```

执行流程：

```text
1. Claude 识别任务类型：行业逻辑入库
2. 加载 industry-logic-research Skill
3. Claude 整理成标准主题知识库格式
4. 调用 search_theme_logic 检查是否已存在
5. 调用 upsert_theme_logic 写入主题逻辑
6. 调用 upsert_theme_stock_mapping 写入标的映射
7. 返回入库结果和待补充字段
```

### 14.3 每日自动扫描

触发方式：

```text
每日 08:30 盘前
每日 11:45 午间
每日 15:30 盘后
```

执行流程：

```text
1. 获取市场快照
2. 获取行业/主题强度
3. 获取新闻/公告事件
4. 匹配主题知识库
5. 计算主题综合分
6. 扫描技术信号
7. 评估持仓风险
8. 生成报告
9. 写入 agent_decision_log
```

---

## 15. API 设计

### 15.1 FastAPI 对外接口

#### 个股分析

```http
POST /api/v1/analyze/stock
```

请求：

```json
{
  "symbol": "300750.SZ",
  "date": "2026-06-26",
  "analysis_type": "full"
}
```

响应：

```json
{
  "symbol": "300750.SZ",
  "technical": {},
  "theme": {},
  "risk": {},
  "summary": "..."
}
```

#### 主题分析

```http
POST /api/v1/analyze/theme
```

请求：

```json
{
  "theme_name": "钠离子电池",
  "date": "2026-06-26"
}
```

#### 每日市场扫描

```http
POST /api/v1/market/daily-scan
```

请求：

```json
{
  "date": "2026-06-26",
  "mode": "after_close"
}
```

#### 知识库更新

```http
POST /api/v1/knowledge/theme
```

请求：

```json
{
  "theme_name": "AI机房液冷",
  "core_thesis": "...",
  "catalysts": [],
  "related_stocks": []
}
```

#### 复盘写入

```http
POST /api/v1/review/trade
```

请求：

```json
{
  "trade_date": "2026-06-26",
  "symbol": "300xxx.SZ",
  "action": "buy",
  "reason": "...",
  "matched_strategy": "B1"
}
```

---

## 16. 权限和安全设计

### 16.1 工具权限分级

| 等级 | 权限 | 示例 |
|---|---|---|
| L1 | 只读查询 | 查询行情、查询知识库 |
| L2 | 计算任务 | 计算指标、回测 |
| L3 | 写入知识库 | 新增主题、写复盘 |
| L4 | 修改关键配置 | 修改策略参数、打分权重 |
| L5 | 交易操作 | 下单、撤单、调仓 |

第一阶段只开放 L1-L3。

L4 需要人工确认。

L5 第一阶段不开放。

### 16.2 安全原则

1. Claude 不直接访问数据库。
2. Claude 不直接访问生产服务器。
3. Claude 不直接执行任意 shell。
4. MCP 工具必须 allowlist。
5. 写操作必须记录审计日志。
6. 工具入参必须校验。
7. 所有外部新闻和网页内容进入 Agent 前要标记为不可信。
8. 防止 Prompt Injection 污染工具调用。
9. 密钥只放在服务端环境变量，不进入 Claude 上下文。
10. 高风险结论必须提示数据来源和不确定性。

### 16.3 审计日志字段

```sql
CREATE TABLE mcp_tool_audit_log (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(128),
    user_id VARCHAR(128),
    tool_name VARCHAR(128),
    permission_level VARCHAR(16),
    input_args JSONB,
    output_summary TEXT,
    status VARCHAR(32),
    error_message TEXT,
    latency_ms INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 17. 可观测性设计

### 17.1 需要监控的指标

| 指标 | 说明 |
|---|---|
| agent_task_count | Agent 任务数量 |
| mcp_tool_call_count | MCP 工具调用次数 |
| mcp_tool_error_count | MCP 工具错误次数 |
| mcp_tool_latency_ms | 工具调用耗时 |
| data_sync_success_count | 数据同步成功次数 |
| data_sync_error_count | 数据同步失败次数 |
| theme_signal_count | 主题信号数量 |
| technical_signal_count | 技术信号数量 |
| decision_log_count | 决策日志数量 |

### 17.2 日志分类

```text
agent.log       Claude 编排日志
mcp.log         MCP 工具调用日志
data_sync.log   数据同步日志
risk.log        风险评估日志
audit.log       写操作审计日志
error.log       异常日志
```

---

## 18. 部署方案

### 18.1 本地 MVP

```yaml
version: "3.9"

services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: financial_agent_postgres
    environment:
      POSTGRES_USER: agent
      POSTGRES_PASSWORD: agent_pass
      POSTGRES_DB: financial_agent
    ports:
      - "5432:5432"
    volumes:
      - ./data/postgres:/var/lib/postgresql/data

  redis:
    image: redis:7
    container_name: financial_agent_redis
    ports:
      - "6379:6379"

  api:
    build: .
    container_name: financial_agent_api
    env_file:
      - .env
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - redis
```

### 18.2 服务启动顺序

```text
1. PostgreSQL
2. Redis
3. 数据同步服务
4. Python API
5. MCP Servers
6. Claude Code / Claude API Agent
7. 定时任务
```

### 18.3 环境变量

```env
DATABASE_URL=postgresql://agent:agent_pass@localhost:5432/financial_agent
REDIS_URL=redis://localhost:6379/0
ANTHROPIC_API_KEY=your_key
QMT_BRIDGE_PYTHON=../quant/.venv-qmt36/Scripts/python.exe
QMT_BRIDGE_SCRIPT=../quant/scripts/qmt_bridge.py
QMT_INSTALL_DIR=../quant/runtime/qmt_client/installed
QMT_USERDATA_DIR=../quant/runtime/qmt_client/installed/userdata_mini
NEWS_DATA_PROVIDER=custom
LOG_LEVEL=INFO
```

---

## 19. MVP 版本规划

### 19.1 第 1 阶段：本地可用原型

目标：实现最小闭环。

功能：

1. 建立 PostgreSQL 表。
2. 接入基础行情数据。
3. 实现 K 线读取。
4. 实现 MA、MACD、KDJ、RPS。
5. 实现 B1/B2/B3 初版识别。
6. 建立主题知识库 Markdown 模板。
7. 建立 industry-knowledge-mcp。
8. 建立 technical-factor-mcp。
9. 创建 3 个 Skill：
   - 技术分析
   - 行业逻辑
   - 每日市场扫描
10. Claude 能回答：
   - 某个股票是否有 B1/B2/B3 信号
   - 某个主题逻辑是否成立
   - 今日哪些主题值得关注

### 19.2 第 2 阶段：每日扫描和复盘

功能：

1. 每日市场快照。
2. 行业强度排名。
3. 主题综合打分。
4. 候选股自动生成。
5. 交易复盘写入。
6. 决策日志保存。
7. 盘后报告生成。
8. 支持用户反馈更新知识库。

### 19.3 第 3 阶段：组合风险和回测

功能：

1. 导入持仓。
2. 计算组合风险。
3. 主题暴露分析。
4. 策略回测。
5. 参数版本管理。
6. 策略失效阶段分析。
7. 对 B1/B2/B3、RPS、MACD 三金叉做组合对比。

### 19.4 第 4 阶段：产品化

功能：

1. Web UI。
2. 多用户权限。
3. MCP Server 远程部署。
4. 安全审计。
5. 监控面板。
6. 报告导出。
7. 预警系统。
8. 与现有量化系统集成。

---

## 20. 开发优先级

### 20.1 优先做

1. 数据库表结构。
2. 行情数据同步。
3. 技术指标计算。
4. B1/B2/B3 信号识别。
5. 行业知识库结构化。
6. MCP 工具封装。
7. 技术分析 Skill。
8. 行业逻辑 Skill。
9. 每日扫描报告。

### 20.2 暂缓做

1. 自动交易。
2. 复杂多 Agent 协作。
3. 大规模 Web UI。
4. 高频盘中交易。
5. 机器学习选股。
6. 复杂权限系统。
7. 多账户交易管理。

---

## 21. 关键实现建议

### 21.1 第一版不要过度抽象

第一版不要一上来做复杂的“通用 Agent 平台”。应该先围绕你的真实交易流程做窄而深的闭环：

```text
主题逻辑 → 板块强度 → 技术形态 → 风险检查 → 候选标的 → 复盘
```

### 21.2 技术指标必须可测试

每个指标都应有单元测试：

```text
test_ma()
test_macd()
test_kdj()
test_rps()
test_b1_signal()
test_b2_signal()
test_b3_signal()
```

### 21.3 每个信号必须输出 evidence

不要只输出：

```json
{"B1": true}
```

必须输出：

```json
{
  "B1": true,
  "evidence": [
    "C >= LTL * 0.975",
    "KDJJ < 30",
    "HSP10 <= 6",
    "COUNT(C < LTL, 10) <= 4"
  ],
  "risk": [
    "行业强度不足",
    "距离前高压力较近"
  ]
}
```

这样 Claude 才能做高质量解释，也方便你复盘。

### 21.4 知识库要有证伪条件

每个主题必须有：

```text
触发条件
证伪条件
风险因素
监控关键词
核心标的
```

否则 Agent 只会强化已有观点，容易变成“多头解释机器”。

### 21.5 决策日志必须保存

每次 Agent 输出建议都应保存：

```text
用户问题
调用了哪些工具
用了哪些数据
输出了什么建议
信心分是多少
风险提示是什么
后续结果如何
```

这会成为后续优化系统的重要数据。

---

## 22. 官方资料参考

1. Claude Code Skills 文档：  
   https://docs.anthropic.com/en/docs/claude-code/skills

2. Claude Code SDK / Skills 位置说明：  
   https://docs.anthropic.com/en/docs/claude-code/sdk

3. Claude API MCP Connector 文档：  
   https://platform.claude.com/docs/en/agents-and-tools/mcp-connector

4. Claude Managed Agents MCP Connector 文档：  
   https://platform.claude.com/docs/en/managed-agents/mcp-connector

5. Model Context Protocol 官方介绍：  
   https://modelcontextprotocol.io/docs/getting-started/intro

6. MCP 2025-06-18 Specification：  
   https://modelcontextprotocol.io/specification/2025-06-18

7. MCP Tools Specification：  
   https://modelcontextprotocol.io/specification/2025-06-18/server/tools

---

## 23. 最终架构结论

该方案可行，推荐最终架构为：

```text
Claude Agent
  ↓
Skills：投研流程、技术分析 SOP、行业逻辑模板、风控复盘规范
  ↓
MCP Servers：行情、技术指标、知识库、新闻事件、组合风险、回测
  ↓
Python Engines：确定性计算、打分、回测、数据清洗
  ↓
PostgreSQL + pgvector + Markdown：长期存储、语义检索、结构化分析
```

第一版最应该实现的产品形态是：

```text
A 股主题投资 Agent
=
行业逻辑知识库
+ 技术分析引擎
+ 每日市场扫描
+ 主题异动提醒
+ 持仓风险评估
+ 盘后复盘系统
```

该系统的价值不在于让 Claude 替代交易员，而在于让 Claude 把你的投研框架稳定执行、把行业逻辑结构化、把交易复盘沉淀下来，并通过 MCP 工具连接真实数据和确定性计算，逐步形成一个可验证、可迭代、可复盘的金融分析智能体。
