---
name: factor-mining
description: 用于自动挖掘 A 股横截面选股 alpha 因子：LLM 生成因子公式，样本内评估 RankIC/ICIR/TopK，达标入库并可用于截面打分排名。
---

# 因子挖掘 Skill

## 使用场景

当用户要求挖掘因子、研究 alpha、做因子回测、查看因子库、用因子对股票打分排名时，使用本 Skill。

## 必须遵守的原则

1. 所有指标均为样本内评估结果，存在过拟合风险，必须在回答中声明并标记【待核验】。
2. 禁止承诺收益，禁止把样本内 TopK 年化描述为可实现的未来收益。
3. 解读指标时使用统一口径：RankIC 为因子值与未来收益的逐日截面秩相关均值（>0.02 为有效），ICIR 为 RankIC 均值/标准差（>0.2 为稳定）。
4. 挖掘依赖行情数据（QMT 桥接或本地数据），数据不可用时必须说明。

## 分析顺序

1. 调用 mine_factors 执行挖掘（可用 rounds/candidates_per_round 控制规模）。
2. 调用 list_factor_library 查看入库因子及指标。
3. 需要复评时调用 evaluate_factor。
4. 调用 scan_alpha_factors 用库内因子合成 alpha 分数对标的排名。
5. 输出因子解读，注明样本内局限与【待核验】。

## 必须调用的工具

- mine_factors
- list_factor_library
- evaluate_factor
- scan_alpha_factors

## 输出格式

### 1. 本轮挖掘概况（轮数、候选数、入库数）
### 2. 入库因子清单（公式、假设、RankIC/ICIR/TopK 指标）
### 3. 因子经济含义解读
### 4. 样本内局限与待核验声明
