# Stock Agent 视频知识原子化解析与生命周期管理复核后完善文档

## 1. 复核结论

本次复核基于当前工作区代码。相较上一轮，项目已经补齐了多个关键缺口：

- `_candidate_from_row()` 已补齐返回结构，旧 `memory_record` 稀疏召回不再返回 `None`。
- `tests/test_video_knowledge_retrieval.py` 已新增旧 `memory_record` 召回和新旧混合召回测试。
- `engines/content/knowledge_schema.py` 已新增知识单元 schema 校验器。
- `tests/test_knowledge_unit_schema.py` 已覆盖非法 LLM JSON 降级、空 evidence 拒绝、OCR 证据绑定和低质量证据标记。
- 生命周期测试已覆盖人工退休、驳回、过期扫描、向量任务记录、幂等扫描、当前状态与历史查询差异。
- 管理台、API、MCP、查询路由、hydrator、hybrid retriever 的主干改造仍保持完整。

当前视频知识相关目标测试结果：

```text
40 passed, 1 warning
```

当前整体完成度评估：

- 工程实现完成度：约 88% - 92%。
- 详细设计符合度：约 82% - 88%。
- 灰度可用程度：接近可灰度，但仍建议先补完本文档中的 P1 项。

## 2. 本轮仍需完善的问题

### 2.1 P1：主体校验仍过宽，会放行无明确主体的知识

涉及文件：

- `engines/content/knowledge_schema.py`
- `engines/content/knowledge_unit_normalizer.py`
- `tests/test_knowledge_unit_schema.py`

当前问题：

`KnowledgeUnitSchemaValidator._has_subject()` 当前逻辑如下：

```python
if unit.get("subject_key") or unit.get("subject_name"):
    return True
if unit.get("entities"):
    return True
if (chapter or {}).get("entities"):
    return True
return bool((chapter or {}).get("primary_domain"))
```

这会导致只有章节领域、没有明确主体的知识被接受。例如：

```python
{
    "primary_domain": "MARKET",
    "knowledge_kind": "STATE",
    "expression_type": "AUTHOR_EXPLICIT",
    "predicate_key": "state",
    "statement": "市场偏强",
    "canonical_statement": "市场偏强",
    "evidence": [{"evidence_text": "市场偏强"}],
}
```

在 `chapter={"primary_domain": "MARKET"}` 时会被接受。

风险：

- `KnowledgeUnit` 会出现大量 `subject_key = MARKET / GENERAL` 的弱主体知识。
- 冲突键会退化为领域级冲突，导致不相关观点被放到同一组。
- 当前状态查询和历史复盘会出现泛化噪声。
- 与详细设计中“知识单元必须围绕明确主体进行管理”的要求不完全一致。

修改要求：

1. `_has_subject()` 不应仅因为 `chapter.primary_domain` 存在就返回 `True`。
2. `subject_key / subject_name / entities / chapter.entities` 至少命中一类才可接受。
3. 如果确实需要领域级知识，应显式生成主体：
   - `subject_type = "DOMAIN"`
   - `subject_key = "MARKET"` 或更细领域键
   - `attributes.domain_level = true`
   - `verification_status = "NEEDS_REVIEW"`
4. 领域级知识只允许特定类型：
   - `METHOD`
   - `CONCEPT`
   - `CAUSAL_THESIS`
5. 普通 `STATE / ACTION / TECHNICAL_SIGNAL / FORECAST / FACT` 不应仅靠 `primary_domain` 入库。

建议实现：

```python
@staticmethod
def _has_subject(unit: dict, chapter: dict | None) -> bool:
    if unit.get("subject_key") or unit.get("subject_name"):
        return True
    if unit.get("entities"):
        return True
    if (chapter or {}).get("entities"):
        return True
    return False
```

如需支持领域级知识，建议增加独立判断：

```python
DOMAIN_LEVEL_KINDS = {"METHOD", "CONCEPT", "CAUSAL_THESIS"}

def _allow_domain_level(unit: dict, chapter: dict | None) -> bool:
    return (
        str(unit.get("knowledge_kind") or "") in DOMAIN_LEVEL_KINDS
        and bool((chapter or {}).get("primary_domain"))
    )
```

新增测试：

- `test_schema_rejects_subjectless_state_even_with_chapter_domain`
- `test_schema_allows_domain_level_method_with_review_flag`
- `test_normalizer_does_not_default_state_subject_to_domain`

验收标准：

- 无明确主体的 `STATE / ACTION / FACT / FORECAST` 被拒绝。
- 领域级 `METHOD / CONCEPT / CAUSAL_THESIS` 可以保留，但必须可识别、可审校。
- `conflict_key` 不再因主体缺失退化为大量 `MARKET|STATE|MARKET|...`。

### 2.2 P1：方法类和概念类知识仍可能被自动 SUPERSEDED

涉及文件：

- `engines/content/knowledge_conflict_resolver.py`
- `storage/repositories/knowledge_repository.py`
- `tests/test_video_knowledge_lifecycle.py`
- 可新增：`tests/test_knowledge_conflict_resolver.py`

当前问题：

`KnowledgeConflictResolver.resolve()` 对同一 `conflict_key` 下正反情绪的知识统一执行：

```python
older["lifecycle_status"] = "SUPERSEDED"
relation_type = "SUPERSEDES"
```

虽然 `_resolution_attributes()` 已对 `METHOD / CONCEPT` 返回：

```text
manual_review_before_supersede
```

但实际状态仍会被自动改成 `SUPERSEDED`。

风险：

- 方法论、概念定义、分析框架类知识通常不应被单条新视频观点自动覆盖。
- 这会破坏详细设计中“方法解释类知识长期有效、需人工或强证据处理冲突”的生命周期原则。
- 检索阶段会因 `SUPERSEDED` 降权，导致老方法论被不恰当地隐藏。

修改要求：

1. 对不同 `knowledge_kind` 使用不同冲突策略。
2. `METHOD / CONCEPT` 默认不自动设置 `SUPERSEDED`。
3. 对 `METHOD / CONCEPT` 的矛盾记录：
   - relation 可设为 `CONFLICTS_WITH` 或 `NEEDS_REVIEW`
   - lifecycle_status 保持原状态
   - verification_status 可设为 `NEEDS_REVIEW`
   - attributes 中保留 `recommended_action = "manual_review_before_supersede"`
4. `STATE / ACTION` 可以保持“新观点覆盖旧观点”的默认策略。
5. `FORECAST` 应保留历史，不建议自动覆盖。
6. `FACT` 应要求证据质量或人工验证后再覆盖。

建议策略表：

| knowledge_kind | 默认冲突动作 | 是否自动 SUPERSEDED |
| --- | --- | --- |
| STATE | keep_latest_as_current | 是 |
| ACTION | review_or_retire_stale_action | 有条件 |
| FORECAST | keep_forecast_history | 否 |
| METHOD | manual_review_before_supersede | 否 |
| CONCEPT | manual_review_before_supersede | 否 |
| FACT | require_evidence_verification | 否 |
| TECHNICAL_SIGNAL | keep_latest_if_newer | 是 |
| RISK_CONDITION | keep_latest_risk_condition | 有条件 |

建议实现：

```python
AUTO_SUPERSEDE_KINDS = {"STATE", "TECHNICAL_SIGNAL"}
CONDITIONAL_SUPERSEDE_KINDS = {"ACTION", "RISK_CONDITION"}
REVIEW_ONLY_KINDS = {"METHOD", "CONCEPT", "FORECAST", "FACT"}
```

然后在 `resolve()` 中按策略决定：

- 自动覆盖：设置旧 unit 为 `SUPERSEDED`，relation 为 `SUPERSEDES`。
- 仅标记冲突：不改 lifecycle_status，relation 为 `CONFLICTS_WITH`。
- 需要人工：设置 `verification_status = "NEEDS_REVIEW"`，relation attributes 带 recommended action。

新增测试：

- `test_method_conflict_does_not_auto_supersede`
- `test_concept_conflict_does_not_auto_supersede`
- `test_forecast_conflict_keeps_history`
- `test_state_conflict_supersedes_older_state`
- `test_action_conflict_marks_review_when_no_explicit_invalidation`

验收标准：

- 方法类和概念类知识不会被普通新观点自动覆盖。
- 状态类知识仍能保留当前有效观点。
- 冲突列表能展示推荐动作。
- 检索结果不会误隐藏长期方法知识。

### 2.3 P2：抽取校验报告只返回给调用方，未形成可查询的持久质量审计

涉及文件：

- `engines/content/video_ingest_service.py`
- `storage/models/knowledge.py`
- `storage/repositories/knowledge_repository.py`
- 可新增迁移：`storage/migrations/009_knowledge_extraction_quality_metrics.sql`

当前状态：

`video_ingest_service.py` 已将 `extraction_validation` 返回到结果中：

```python
"quality_metrics": {"extraction_validation": extraction_validation}
```

但 `KnowledgeExtractionRunRepository.finish()` 当前只保存：

- `status`
- `stage`
- `chapter_count`
- `knowledge_unit_count`
- `degraded`
- `error_message`
- `completed_at`

风险：

- 管理台和 API 后续无法查询某次抽取被拒绝、修复、降级的具体情况。
- 无法按视频追踪模型抽取质量。
- 不利于后续优化 prompt、schema 和证据绑定策略。

修改要求：

1. 给 `KnowledgeExtractionRun` 增加 `metrics_json` 字段。
2. `finish()` 增加 `metrics` 参数。
3. `_build_knowledge_result()` 在 finish 时保存：
   - schema accepted/rejected/repaired 数量
   - rejection reasons
   - LLM 是否降级为规则
   - OCR 证据命中数
   - low evidence count
4. API 的视频知识详情或任务详情中暴露该质量指标。
5. 管理台显示抽取质量摘要。

验收标准：

- 每次视频知识抽取都有可追溯质量报告。
- 管理台能看到 rejected/repaired 统计。
- 后续排查知识缺失时无需重跑解析才能知道失败原因。

### 2.4 P2：全量测试仍有非视频知识模块失败，需要单独收敛

涉及文件：

- `financial_agent/research_config.py`
- `engines/factor/research_window.py`
- `engines/factor/walkforward.py`
- `tests/test_factor_walkforward.py`

当前测试结果：

使用项目内临时目录运行完整测试后，Windows Temp 权限问题已绕过，但出现 14 个 `tests/test_factor_walkforward.py` 失败：

```text
14 failed, 547 passed, 2 skipped, 16 warnings
```

核心错误：

```text
ValueError: paper_trading.mining_panel_days must be >=
data_split.max_warmup_days + data_split.discovery_days +
data_split.final_oos_days + evaluation.horizon_days (60 < 75)
```

判断：

- 这些失败不属于视频知识原子化链路。
- 但如果要做全仓库上线验收，需要单独修复或隔离。

修改建议：

1. 检查测试环境中的默认 research config。
2. 确认 `paper_trading.mining_panel_days` 默认值是否应从 60 调整到至少 75。
3. 若生产配置必须保持 60，则测试应显式传入满足 walkforward 要求的 config。
4. 对 `test_walkforward_rejects_too_small_manual_window` 区分：
   - runtime config 不合法
   - manual window 太小
5. 增加配置校验单测，避免异常类型和异常信息互相覆盖。

验收标准：

- `pytest -q` 可全量通过，或 CI 明确将视频知识测试与因子测试分组。
- walkforward 测试失败不再遮蔽视频知识链路验收。

## 3. 建议执行顺序

### 第一阶段：修复知识质量高风险点

1. 收紧 `KnowledgeUnitSchemaValidator._has_subject()`。
2. 增加领域级知识的显式标记。
3. 修改 `KnowledgeUnitNormalizer`，避免普通知识默认退化为领域主体。
4. 增加主体缺失相关测试。

完成标准：

- 无主体普通知识不入库。
- 领域级知识可审计、可筛选。

### 第二阶段：修复冲突策略

1. 给 `KnowledgeConflictResolver` 增加按 `knowledge_kind` 的策略表。
2. 禁止 `METHOD / CONCEPT / FORECAST / FACT` 默认自动 `SUPERSEDED`。
3. 新增 `CONFLICTS_WITH` 或 `NEEDS_REVIEW` 关系类型。
4. 补充冲突策略单测。

完成标准：

- 状态类知识可自动迭代。
- 方法类知识不会被误覆盖。
- 冲突列表提供推荐处理动作。

### 第三阶段：补质量审计

1. 增加 `KnowledgeExtractionRun.metrics_json`。
2. 持久化 `extraction_validation`。
3. API 和管理台展示质量指标。
4. 增加迁移和测试。

完成标准：

- 每次抽取过程的接受、拒绝、修复、降级指标可查询。

### 第四阶段：全量测试收敛

1. 固定项目内临时目录环境。
2. 修复或隔离因子 walkforward 配置失败。
3. 跑全量测试。
4. 输出最终验收报告。

完成标准：

- 视频知识目标测试通过。
- 全量测试通过，或明确记录非本模块失败的隔离策略。

## 4. 推荐测试命令

### 4.1 视频知识目标测试

```powershell
.\.conda-env\python.exe -m pytest `
  tests\test_video_knowledge_v3.py `
  tests\test_video_knowledge_retrieval.py `
  tests\test_video_knowledge_lifecycle.py `
  tests\test_hybrid_retriever.py `
  tests\test_content_api.py `
  tests\test_mcp_content_tools.py `
  tests\test_knowledge_unit_schema.py `
  -q
```

### 4.2 全量测试

```powershell
$env:TMPDIR="D:\project\stock_agent\.pytest-tmp"
$env:TEMP="D:\project\stock_agent\.pytest-tmp"
$env:TMP="D:\project\stock_agent\.pytest-tmp"
.\.conda-env\python.exe -m pytest -q
```

## 5. 下一轮验收清单

- [ ] `KnowledgeUnitSchemaValidator` 不再放行无主体普通知识。
- [ ] 领域级知识必须显式标记并进入待审状态。
- [ ] `METHOD / CONCEPT / FORECAST / FACT` 冲突不自动覆盖旧知识。
- [ ] `STATE / TECHNICAL_SIGNAL` 冲突仍能自动保留最新有效观点。
- [ ] 抽取质量指标持久化到 `KnowledgeExtractionRun`。
- [ ] 管理台可展示抽取质量摘要。
- [ ] 视频知识目标测试全部通过。
- [ ] 完整测试中的因子 walkforward 配置失败被修复或明确隔离。

## 6. 完成后的预期状态

完成本文档修改后，项目可达到：

- 工程实现完成度：92% - 96%。
- 详细设计符合度：88% - 92%。
- 视频知识链路灰度上线可验收。

剩余未覆盖内容将主要是长期增强项，例如更复杂的视觉语义边界识别、跨视频全局冲突图谱、人工审校批量工作流和真实视频样本集评测。

