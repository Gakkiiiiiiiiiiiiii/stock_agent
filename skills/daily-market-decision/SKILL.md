---
name: daily-market-decision
description: 用于每日市场扫描、主题强度分析、候选标的筛选和仓位建议。
---

# 每日市场决策 Skill

## 使用场景

盘前、盘中、盘后，需要分析当日市场状态、最强主题、可交易方向、候选标的和风险提示时，使用本 Skill。
当用户询问“最近有什么值得关注的板块/赛道/方向”时，也优先使用本 Skill，而不是只做静态主题研究。

## 分析顺序

1. 判断指数环境。
2. 判断市场情绪。
3. 判断风格偏好。
4. 判断行业/主题强度。
5. 先查询最近视频知识库，提取最新观点、催化和风险。
6. 再查询主题知识库，校验长期逻辑是否一致。
7. 筛选技术形态。
8. 检查持仓风险。
9. 输出候选标的和仓位建议。

## 必须调用的工具

- get_market_snapshot
- get_market_regime
- route_strategy
- search_video_insights
- retrieve_relevant_context
- get_sector_strength
- rank_themes_by_score
- scan_stock_signals
- evaluate_portfolio_risk

## 执行约束

1. 对于“最近/近期/当前”的问题，必须先调用 `search_video_insights`。
2. `retrieve_relevant_context` 必须优先检索视频相关来源，至少包含：
   - `bilibili_video_viewpoint`
   - `bilibili_financial_event`
   - `bilibili_video_summary`
3. 主题知识库只作为长期逻辑补充，不能替代最近视频观点。
4. 若视频知识库与 docs 主题逻辑冲突，必须明确写出冲突点与时间差异。
5. 输出结论必须标注至少一个最近视频证据来源，包括标题或发布日期。

## 输出格式

### 1. 市场环境
### 2. 风格判断
### 3. 最近视频知识库结论
### 4. 今日强主题
### 5. 主题逻辑验证
### 6. 候选标的
### 7. 当前不建议参与方向
### 8. 仓位建议
### 9. 明日观察点
