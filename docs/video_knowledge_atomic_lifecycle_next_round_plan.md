# Stock Agent 视频知识原子化解析与生命周期管理下一轮修改文档

## 1. 文档目的

本文档用于指导下一轮修改，将当前已经完成的“视频知识原子化解析与生命周期管理”实现推进到可验收、可上线状态。

本轮检查结论：

- 当前整体完成度约为 80% - 85%。
- 数据模型、解析入库、API、MCP、生命周期审计、向量任务和视频知识检索主干已经落地。
- 当前存在一个必须优先修复的检索回归：`memory_record` 稀疏召回分支返回 `None`。
- 若严格对照详细设计文档，仍需补齐结构化抽取校验、证据绑定、冲突策略细化、管理台审校体验和生产级验收测试。

下一轮目标：

1. 修复当前已发现的 P1 回归，保证新旧知识检索链路共存稳定。
2. 补足详细设计中尚未完全闭环的知识抽取、证据、生命周期和冲突处理能力。
3. 增强测试覆盖，避免当前目标测试只覆盖新 `knowledge_unit`、未覆盖旧 `memory_record` 兼容路径。
4. 形成可上线验收标准，覆盖 API、MCP、向量索引、过期处理、人工审校和回归测试。

## 2. 当前实现状态

### 2.1 已完成能力

当前代码已经覆盖以下主干能力：

- `KnowledgeUnit`、`VideoChapter`、`KnowledgeEvidence`、`KnowledgeEntityRelation`、`KnowledgeUnitRelation` 等核心模型。
- `KnowledgeLifecycleAudit` 生命周期审计模型及迁移。
- 视频解析流程中已包含章节识别、知识抽取、归一化、生命周期策略、冲突解析、入库和向量任务入队。
- `KnowledgeVectorTaskService` 已支持按知识类型路由到：
  - `financial_video_durable_v1_bge_m3`
  - `financial_video_timed_v1_bge_m3`
  - `financial_video_action_v1_bge_m3`
- 查询意图已开始按视频知识场景选择 collection。
- `PostgresHydrator` 已支持从 `knowledge_unit` 回填记录。
- `HybridRetriever` 已支持过期过滤和知识冲突过滤。
- API 已新增章节、知识单元、搜索、冲突、当前状态、历史、重新解析、生命周期更新和审计接口。
- MCP 已新增视频知识相关工具，并对旧 `search_video_insights` 做了兼容转发。
- 目标测试通过：`28 passed, 1 warning`。

### 2.2 当前主要风险

当前不是“大块未实现”，而是存在以下上线风险：

- 稀疏检索旧数据兼容路径存在明确 bug。
- 新旧检索混合场景测试不足。
- 生命周期状态变化后的向量删除、重建和过期逻辑还需要端到端验证。
- 冲突策略可用但较粗，尚未达到详细设计要求的分类型治理。
- 抽取阶段缺少强 schema 校验和更稳定的两阶段抽取机制。
- 管理台更偏展示，距离完整审校工作台还有差距。

## 3. 下一轮修改范围

### 3.1 P0：修复稀疏检索兼容回归

问题位置：

- `engines/retrieval/sparse_retriever.py`

问题描述：

`_candidate_from_row()` 当前只构造了 `payload`，但没有返回候选对象。`PostgresSparseRetriever.search()` 对 `memory_rows` 执行：

```python
candidates = [_candidate_from_row(row) for row in memory_rows]
```

当旧 `memory_record` 被召回时，`candidates` 中会出现 `None`。随后执行排序：

```python
candidates.sort(key=lambda item: float(item.get("sparse_recall_score") or 0.0), reverse=True)
```

会因为 `None.get` 触发异常。

修改要求：

1. 给 `_candidate_from_row()` 补齐返回结构。
2. 返回字段需与 `_candidate_from_knowledge_row()` 对齐，至少包含：
   - `chunk_id`
   - `text`
   - `payload`
   - `dense_score`
   - `score`
   - `sparse_recall_score`
   - `recall_sources`
3. 保持 `payload.postgres_table = "memory_record"`。
4. 不改变 `knowledge_unit` 稀疏召回逻辑。

建议实现：

```python
def _candidate_from_row(row) -> dict:
    payload = {
        ...
    }
    return {
        "chunk_id": row["chunk_id"],
        "text": row["content"],
        "payload": payload,
        "dense_score": 0.0,
        "score": 0.0,
        "sparse_recall_score": float(row["sparse_recall_score"] or 0.0),
        "recall_sources": ["sparse"],
    }
```

新增测试：

- 文件建议：`tests/test_video_knowledge_retrieval.py` 或新增 `tests/test_sparse_retriever_compat.py`
- 用 SQLite 构造：
  - 一条 `MemoryRecord`
  - 一条对应的 `VectorIndexMapping`
  - collection 为 `financial_memory_v2_bge_m3`
- 调用 `PostgresSparseRetriever().search(...)`
- 断言：
  - 不抛异常
  - 返回结果数量为 1
  - `payload.postgres_table == "memory_record"`
  - `payload.qdrant_collection == "financial_memory_v2_bge_m3"`

验收标准：

- 旧 `memory_record` 稀疏召回正常返回。
- `knowledge_unit` 稀疏召回仍正常。
- 新旧混合召回排序不报错。

## 4. P1：补齐新旧检索混合场景测试

涉及文件：

- `engines/retrieval/sparse_retriever.py`
- `engines/retrieval/hybrid_retriever.py`
- `engines/retrieval/postgres_hydrator.py`
- `tests/test_video_knowledge_retrieval.py`
- `tests/test_hybrid_retriever.py`

修改目标：

验证以下场景：

1. 只存在 `knowledge_unit` 时可召回。
2. 只存在 `memory_record` 时可召回。
3. `knowledge_unit` 与 `memory_record` 同时存在时可排序、可 hydrate。
4. `knowledge_unit` 为 `REJECTED / RETIRED` 时不应进入有效检索结果。
5. `valid_only=True` 时过滤已过期知识。
6. 历史复盘类查询允许召回过期知识。

建议测试用例：

- `test_sparse_retriever_recalls_memory_record`
- `test_sparse_retriever_merges_memory_and_knowledge_rows`
- `test_sparse_retriever_filters_invalid_knowledge_status`
- `test_hybrid_retriever_filters_expired_video_knowledge_for_current_query`
- `test_hybrid_retriever_keeps_expired_video_knowledge_for_history_query`

验收标准：

- 新增测试稳定通过。
- 不因旧数据存在影响新视频知识检索。

## 5. P1：生命周期向量同步端到端验证

涉及文件：

- `engines/content/knowledge_lifecycle_service.py`
- `storage/repositories/knowledge_repository.py`
- `workers/vector_index_worker.py`
- `tests/test_video_knowledge_lifecycle.py`

当前状态：

- 生命周期更新接口已经存在。
- `REJECTED / RETIRED` 会创建 delete 类型向量任务。
- 非终止状态会创建 upsert 类型向量任务。
- `expire_due_units()` 会将到期知识标记为 `EXPIRED` 并入队同步任务。

下一轮需要补齐：

1. 验证 `ACTIVE -> RETIRED` 会对已索引 collection 创建 delete 任务。
2. 验证 `ACTIVE -> REJECTED` 会创建 delete 任务。
3. 验证 `EXPIRED` 知识不会在当前状态查询中出现。
4. 验证 `EXPIRED` 知识仍可在历史查询中出现。
5. 验证生命周期审计中记录了向量任务 ID。
6. 验证重复过期扫描不会重复生成大量无意义任务。

建议新增或扩展测试：

- `test_retired_unit_enqueues_vector_delete_for_indexed_collections`
- `test_rejected_unit_enqueues_vector_delete`
- `test_expire_due_units_records_vector_task_ids`
- `test_expire_due_units_is_idempotent_for_already_expired_units`

验收标准：

- 生命周期状态和向量任务状态可追踪。
- 人工下线后不会继续从 Qdrant 召回旧知识。
- 到期知识不会污染当前状态类查询。

## 6. P1：完善结构化抽取与 Schema 校验

涉及文件：

- `engines/content/knowledge_unit_extractor.py`
- `engines/content/knowledge_unit_normalizer.py`
- `engines/content/video_ingest_service.py`
- 可新增：`engines/content/knowledge_schema.py`
- 可新增测试：`tests/test_knowledge_unit_schema.py`

当前不足：

- 知识抽取仍偏单阶段。
- LLM 返回结构缺少统一强校验。
- 同一句多命题拆分质量依赖模型自觉。
- 缺字段时主要靠后续归一化兜底，错误较晚暴露。

修改目标：

1. 定义明确的知识单元输入 schema。
2. 在 LLM 输出后立即校验字段。
3. 对缺失主体、谓词、证据、时间类型、知识类型的记录给出明确降级或丢弃原因。
4. 将抽取拆成两个逻辑阶段：
   - 阶段一：候选命题识别和拆分。
   - 阶段二：规范化为 `KnowledgeUnit` 字段。
5. 将丢弃、降级、修复记录写入 `KnowledgeExtractionRun.metrics` 或类似字段。

建议 schema 必填字段：

- `primary_domain`
- `knowledge_kind`
- `temporal_class`
- `expression_type`
- `subject`
- `predicate_key`
- `statement`
- `canonical_statement`
- `as_of_time`
- `evidence`

验收标准：

- LLM 返回非法 JSON 时可降级，不中断整个视频解析任务。
- 无证据知识不会入库。
- 缺主体或缺谓词的知识不会进入正式 `KnowledgeUnit`。
- 测试覆盖非法 JSON、缺字段、空 evidence、多命题拆分。

## 7. P1：增强证据绑定质量

涉及文件：

- `engines/content/knowledge_unit_normalizer.py`
- `engines/content/knowledge_unit_extractor.py`
- `engines/content/video_ingest_service.py`
- `storage/repositories/knowledge_repository.py`

当前状态：

- 已有 `KnowledgeEvidence`。
- 当前证据主要依赖 ASR 时间范围。
- OCR/视觉证据可进入流程，但知识到 OCR/视觉证据的绑定粒度还需要增强。

修改目标：

1. 每条知识至少绑定一个 ASR 时间范围证据。
2. 若命题涉及图表、指标、价格、技术形态，优先尝试绑定关键帧/OCR/视觉证据。
3. 证据中保存：
   - `evidence_type`
   - `text`
   - `start_ms`
   - `end_ms`
   - `frame_path`
   - `confidence`
4. 对证据不足的知识设置 `verification_status = NEEDS_REVIEW`，而不是直接默认为高可信。

建议测试：

- `test_knowledge_unit_requires_evidence`
- `test_ocr_evidence_is_attached_when_statement_mentions_chart`
- `test_low_evidence_quality_marks_needs_review`

验收标准：

- 抽取出的正式知识都能追溯到视频时间点。
- 管理台和 API 可展示证据。
- 证据质量不足时不会被当作已验证知识。

## 8. P2：细化冲突与替代策略

涉及文件：

- `engines/content/knowledge_conflict_resolver.py`
- `engines/retrieval/hybrid_retriever.py`
- `storage/repositories/knowledge_repository.py`
- `tests/test_video_knowledge_lifecycle.py`
- `tests/test_hybrid_retriever.py`

当前状态：

- 已支持同 `conflict_key` 下按情绪方向生成 `SUPERSEDES / REINFORCES`。
- 检索阶段可按 `conflict_group_id` 保留较优记录。

下一轮增强：

1. 按知识类型设置冲突策略：
   - `STATE`：新观点优先，旧观点可 `SUPERSEDED` 或保留历史。
   - `ACTION`：更严格过滤过期和被替代操作建议。
   - `FORECAST`：保留预测历史，不直接删除旧预测。
   - `METHOD / CONCEPT`：默认不因新视频覆盖旧方法，除非明确否定。
   - `FACT`：需要更强证据或人工验证后替代。
2. 完善 `superseded_by_unit_id` 回填。
3. 区分同视频内更新和跨视频更新。
4. 冲突列表中返回推荐处理动作。

建议新增字段或返回内容：

- `conflict_resolution_reason`
- `recommended_action`
- `superseded_by_unit_id`
- `conflict_scope`

验收标准：

- 同一主题的新旧状态查询只返回当前有效观点。
- 历史复盘可看到观点演变。
- 方法类知识不会被普通状态更新误覆盖。

## 9. P2：管理台审校体验补强

涉及文件：

- `app/static/admin.html`
- `app/api.py`

当前状态：

- 管理台已有一定展示增强。
- 但还不是完整的知识审校工作台。

下一轮目标：

1. 视频详情页新增知识单元列表，支持按以下字段筛选：
   - `primary_domain`
   - `knowledge_kind`
   - `temporal_class`
   - `lifecycle_status`
   - `verification_status`
   - `subject_key`
2. 知识单元详情展示：
   - 原始陈述
   - 规范陈述
   - 时间范围
   - 证据
   - 实体
   - 关系
   - 向量状态
   - 生命周期审计
3. 支持人工操作：
   - 激活
   - 验证
   - 驳回
   - 退休
   - 恢复
   - 修改有效期
4. 冲突列表支持一键查看冲突组内全部知识。

验收标准：

- 管理员无需查数据库即可完成知识审校。
- 生命周期变更能看到审计记录。
- 操作后能触发对应向量同步任务。

## 10. P2：API 与 MCP 契约稳定化

涉及文件：

- `app/api.py`
- `app/tool_registry.py`
- `mcp_servers/content_server.py`
- `tests/test_content_api.py`
- `tests/test_mcp_content_tools.py`

下一轮目标：

1. 为新增 API 固定响应结构。
2. 为 MCP 工具补充参数校验和默认值。
3. 旧工具保持兼容，但返回中标记 deprecated。
4. 给知识搜索接口增加分页或游标，避免单次返回过大。
5. 对 `limit` 做上限限制。

建议统一响应结构：

```json
{
  "items": [],
  "limit": 50,
  "next_cursor": null,
  "filters": {},
  "warnings": []
}
```

验收标准：

- API 测试覆盖主要状态码。
- MCP 工具对空参数、非法状态、过大 limit 有稳定响应。
- 旧工具调用不报错。

## 11. P2：生产迁移与旧链路清理

涉及文件：

- `storage/migrations/007_video_knowledge_v3_schema.sql`
- `storage/migrations/008_knowledge_lifecycle_audit.sql`
- `config/qdrant.yaml`
- `config/retrieval.yaml`
- `workers/vector_index_worker.py`
- `engines/content/video_ingest_service.py`

当前注意事项：

- 新模型已添加，但旧 `MemoryRecord` 视频摘要链路仍存在兼容逻辑。
- 设计要求长期目标是视频知识不再写入旧 `financial_knowledge` 中的 `MemoryRecord`。

下一轮目标：

1. 明确旧视频 summary/chunk/event 的保留策略。
2. 给旧数据迁移到 `KnowledgeUnit` 提供脚本或任务。
3. 明确 Qdrant collection 创建、重建、删除策略。
4. 对旧 collection 做只读兼容或灰度下线。
5. 增加迁移回滚说明。

验收标准：

- 新视频不再默认写入旧视频知识 memory。
- 老数据可继续检索，且不会影响新知识检索。
- 可重建三个视频知识 collection。
- 迁移失败时不会破坏旧数据。

## 12. 测试计划

### 12.1 必跑目标测试

```powershell
.\.conda-env\python.exe -m pytest `
  tests\test_video_knowledge_v3.py `
  tests\test_video_knowledge_retrieval.py `
  tests\test_video_knowledge_lifecycle.py `
  tests\test_hybrid_retriever.py `
  tests\test_content_api.py `
  tests\test_mcp_content_tools.py `
  -q
```

验收要求：

- 全部通过。
- 覆盖旧 `memory_record` 稀疏召回。
- 覆盖新旧混合召回。

### 12.2 建议补充测试

```powershell
.\.conda-env\python.exe -m pytest `
  tests\test_knowledge_unit_schema.py `
  tests\test_sparse_retriever_compat.py `
  tests\test_video_knowledge_lifecycle.py `
  tests\test_video_knowledge_retrieval.py `
  -q
```

### 12.3 完整测试

```powershell
.\.conda-env\python.exe -m pytest -q
```

当前完整测试在本机存在 `C:\Users\Administrator\AppData\Local\Temp\pytest-of-Administrator` 权限错误。下一轮如果需要完整回归，建议先指定项目内临时目录：

```powershell
$env:TMPDIR="D:\project\stock_agent\.pytest-tmp"
$env:TEMP="D:\project\stock_agent\.pytest-tmp"
$env:TMP="D:\project\stock_agent\.pytest-tmp"
.\.conda-env\python.exe -m pytest -q
```

验收要求：

- 若仍有 PermissionError，应先排除测试环境问题。
- 视频知识相关测试不得失败。
- 检索、生命周期、API、MCP 不得出现业务回归。

## 13. 验收清单

### 13.1 功能验收

- [ ] 新视频解析后生成章节。
- [ ] 新视频解析后生成 `KnowledgeUnit`。
- [ ] 每条正式知识至少有一条证据。
- [ ] 知识按 durable/timed/action 路由到正确 collection。
- [ ] 当前状态查询优先返回有效知识。
- [ ] 历史复盘查询可返回过期知识。
- [ ] 人工退休或驳回知识后，创建向量 delete 任务。
- [ ] 到期知识扫描可将知识标记为 `EXPIRED`。
- [ ] 冲突组中当前查询只保留最佳有效知识。
- [ ] MCP 工具可正常查询视频知识。
- [ ] 管理台可查看知识详情和审计记录。

### 13.2 质量验收

- [ ] 目标测试全部通过。
- [ ] 新增旧 `memory_record` 兼容测试。
- [ ] 新增新旧混合召回测试。
- [ ] 新增生命周期向量同步测试。
- [ ] 新增 schema 校验失败测试。
- [ ] 完整测试中无视频知识相关失败。

### 13.3 上线验收

- [ ] 迁移脚本可重复执行或具备幂等保护。
- [ ] Qdrant collection 已创建并可写入。
- [ ] Worker 能处理 `knowledge_unit` upsert/delete。
- [ ] 回滚路径明确。
- [ ] 旧视频知识链路兼容策略明确。

## 14. 建议执行顺序

### 第一阶段：稳定性修复

1. 修复 `_candidate_from_row()` 缺失返回。
2. 补旧 `memory_record` 稀疏召回测试。
3. 补新旧混合召回测试。
4. 跑目标测试。

完成后预期：

- 当前 P1 回归消除。
- 检索主链路可稳定兼容旧数据。

### 第二阶段：生命周期闭环

1. 补充人工状态切换向量 delete/upsert 测试。
2. 补充过期扫描幂等测试。
3. 验证审计记录中 vector task 信息。
4. 验证 `EXPIRED / REJECTED / RETIRED` 在检索中的行为。

完成后预期：

- 生命周期变化与向量索引一致。
- 当前/历史查询语义清晰。

### 第三阶段：抽取质量增强

1. 定义知识抽取 schema。
2. 增加 LLM 输出校验。
3. 拆分候选命题识别和规范化阶段。
4. 增加无效输出降级测试。

完成后预期：

- 原子知识质量更稳定。
- 入库数据更接近详细设计要求。

### 第四阶段：管理台和上线准备

1. 补知识单元审校视图。
2. 补冲突处理入口。
3. 明确旧链路迁移策略。
4. 跑完整回归和手工验收。

完成后预期：

- 管理员可以直接审校知识。
- 项目具备灰度上线条件。

## 15. 下一轮完成标准

下一轮修改完成后，建议将项目完成度提升到：

- 工程实现完成度：90% - 95%。
- 详细设计符合度：85% - 90%。
- 可上线稳定度：达到灰度发布标准。

必须满足：

1. `memory_record` 和 `knowledge_unit` 混合检索不崩溃。
2. 生命周期状态变化后向量索引同步可验证。
3. 当前状态和历史复盘查询结果符合时间语义。
4. 新增 API/MCP 有稳定测试覆盖。
5. 至少一个真实或仿真视频知识样例能完成解析、入库、检索、审校、过期和冲突查看全流程。

