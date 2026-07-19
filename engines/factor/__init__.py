"""公式化因子挖掘引擎：借鉴 AlphaGPT 的 DSL + 栈式 VM + 适应度 + LLM 进化循环。

横截面选股场景：LLM 生成 RPN 因子表达式 -> StackVM 在 (标的, 特征, 日期)
面板上求值 -> RankIC/topK 回测适应度 -> 结果反馈 LLM 迭代 -> 沉淀因子库。
"""
