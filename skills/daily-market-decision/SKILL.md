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
- get_market_regime
- route_strategy
- retrieve_relevant_context
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
