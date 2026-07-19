---
name: a-share-technical-analysis
description: 用于分析 A 股个股、ETF、行业指数的技术形态，包括趋势、量价、KDJ、MACD、RPS、B1/B2/B3、买卖点和风险。
---

# A 股技术分析 Skill

## 使用场景

当用户要求分析个股、ETF、行业指数的技术形态、买点、卖点、左侧机会、右侧确认、是否破位、是否适合加仓时，使用本 Skill。

## 必须遵守的原则

1. 不允许凭主观感觉直接给买入建议。
2. 指标计算必须来自 technical-factor-mcp 或本项目技术指标引擎。
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

- retrieve_relevant_context
- get_kline
- calc_technical_indicators
- detect_pattern_signal
- scan_alpha_factors
- get_market_regime
- route_strategy
- get_sector_strength
- search_theme_logic
- evaluate_portfolio_risk

## 输出格式

### 1. 当前技术状态
### 2. 买点类型
### 3. 信号强度
### 4. 风险点
### 5. 操作建议
### 6. 需要等待的确认条件
### 7. 证伪条件
