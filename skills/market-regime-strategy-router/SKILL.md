---
name: market-regime-strategy-router
description: 用于识别市场状态、高位退潮风险，并根据状态路由策略权重和仓位纪律。
---

# 市场状态与策略路由 Skill

## 使用场景

当用户要求判断当前市场是抱团、轮动、退潮、下跌还是震荡，并给出可做策略和仓位纪律时使用。

## 必须调用的工具

- get_market_regime
- route_strategy
- retrieve_relevant_context

## 输出格式

### 1. 当前市场状态
### 2. 状态切换与置信度
### 3. 高位退潮风险
### 4. 当前优先策略
### 5. 仓位纪律

