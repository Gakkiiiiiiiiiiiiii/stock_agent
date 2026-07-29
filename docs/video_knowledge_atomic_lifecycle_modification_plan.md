# Stock Agent 视频知识原子化解析与生命周期管理修改文档

## 1. 文档目的

本文档用于对照《Stock Agent 视频知识原子化解析与生命周期管理详细设计文档》，说明当前项目的实现完成度、未完成项、建议修改范围、文件级改造清单、测试方案和验收标准。

当前判断：

- 新版 `KnowledgeUnit` 数据层和视频解析入库主干已经完成主要骨架。
- 检索、API、MCP、管理台、生命周期运维和旧链路清理尚未闭环。
- 按最终详细设计整体评估，当前完成度约为 45% - 55%。
- 若仅按“原子知识入库 MVP”评估，完成度约为 65%。

## 2. 设计目标摘要

详细设计文档要求将旧的视频总结链路替换为新的“章节 + 原子知识 + 生命周期 + 关系 + 证据 + 专用向量检索”体系。

目标架构应满足：

1. `KnowledgeUnit` 是视频知识唯一事实单元。
2. `VideoChapter` 表达正式章节，不再用固定窗口 `VideoChunk` 代表知识结构。
3. 每条知识必须绑定证据，至少包含 ASR 时间范围或 OCR/视觉证据。
4. 每条知识独立维护知识性质、时间类型、生命周期状态和验证状态。
5. 生命周期变化后需要同步 Qdrant。
6. 视频知识不再写入 `financial_knowledge` 中的 `MemoryRecord`。
7. 检索需要支持方法解释、当前状态、历史复盘、产业逻辑、预测比较和交易决策等意图。
8. API、MCP 和管理台需要以 `KnowledgeUnit` 为中心重建。

## 3. 当前已实现内容

### 3.1 新数据模型

已实现：

- `VideoChapter`
- `KnowledgeUnit`
- `KnowledgeEvidence`
- `KnowledgeEntityRelation`
- `KnowledgeUnitRelation`
- `VideoAnalysisDocument`
- `KnowledgeExtractionRun`

相关文件：

- `storage/models/knowledge.py`
- `storage/migrations/007_video_knowledge_v3_schema.sql`
- `storage/repositories/knowledge_repository.py`

完成度：约 85%。

剩余问题：

- SQLite 迁移已覆盖核心字段，但 PostgreSQL 破坏性迁移和旧表删除策略未真正执行。
- 没有单独的生命周期审计表。
- `superseded_by_unit_id` 字段存在，但当前冲突处理没有回填数据库 ID 关系。

### 3.2 视频解析主流程

已实现流程：

```text
视频接入
→ 音频抽取
→ ASR
→ 关键帧/OCR/视觉解析
→ 时间窗口
→ 章节识别
→ 原子知识抽取
→ 规范化
→ 生命周期策略
→ 去重与冲突
→ 入库
→ 视频分析文档
→ knowledge_unit 向量任务入队
```

相关文件：

- `engines/content/video_ingest_service.py`
- `engines/content/temporal_window_builder.py`
- `engines/content/chapter_segmenter.py`
- `engines/content/knowledge_unit_extractor.py`
- `engines/content/knowledge_unit_normalizer.py`
- `engines/content/knowledge_temporal_policy.py`
- `engines/content/knowledge_deduplicator.py`
- `engines/content/knowledge_conflict_resolver.py`
- `engines/content/video_analysis_document_generator.py`

完成度：约 70%。

剩余问题：

- 原子知识抽取仍是 LLM 单阶段 + 规则降级，没有实现详细设计中的“两阶段抽取 + 结构校验”。
- 未实现 JSON Schema 强校验。
- 章节识别主要基于规则分数，未实现语义向量突变、图表类型切换等增强边界信号。
- 任务阶段和进度已经有新阶段，但失败降级和阶段级重试不完整。

### 3.3 原子知识与证据

已实现：

- 每条知识要求存在 `evidence`，否则规范化阶段会过滤。
- 支持 `statement / canonical_statement / claim_type / sentiment / condition_text / invalidation_text`。
- 支持实体关系表和证据表。

完成度：约 60%。

剩余问题：

- OCR 和视觉证据绑定粒度不稳定，很多知识仍主要绑定 ASR 窗口。
- 同一句多命题拆分能力依赖模型表现，规则兜底较粗。
- `MODEL_INFERENCE`、`CONCEPT` 等类型支持不足。
- 数字、单位、时间表达的结构化归一化不完整。

### 3.4 生命周期与冲突

已实现：

- 默认生命周期策略：
  - `METHOD / CONCEPT` 为长期 durable。
  - `FACT / STATE / TECHNICAL_SIGNAL / ACTION / RISK_CONDITION` 有默认有效期。
  - 过期时可标记 `EXPIRED`。
- 同 `conflict_key` 下新旧正反观点可生成 `SUPERSEDES` 或 `REINFORCES` 关系。

相关文件：

- `engines/content/knowledge_temporal_policy.py`
- `engines/content/knowledge_conflict_resolver.py`

完成度：约 40%。

剩余问题：

- 没有定时生命周期更新任务。
- 没有人工下线、恢复、拒绝、退休等 API。
- 生命周期变化后没有统一创建向量同步任务。
- 冲突规则只看同 `conflict_key` 和情绪方向，未按 `METHOD / FACT / STATE / TECHNICAL_SIGNAL / FORECAST / ACTION` 分别处理。
- 跨视频冲突与同视频内观点更新没有完整区分。

### 3.5 Qdrant 向量索引

已实现：

- 配置中已有三个视频知识 collection：
  - `financial_video_durable_v1_bge_m3`
  - `financial_video_timed_v1_bge_m3`
  - `financial_video_action_v1_bge_m3`
- `Vector Worker` 支持 `knowledge_unit`。
- `KnowledgeVectorTaskService` 可按知识类型和时间类型路由 collection。

相关文件：

- `config/qdrant.yaml`
- `config/retrieval.yaml`
- `workers/vector_index_worker.py`
- `storage/repositories/knowledge_repository.py`

完成度：约 55%。

剩余问题：

- 检索计划仍默认搜索 `financial_memory` 和 `financial_knowledge`，未使用三个视频知识 collection。
- Sparse 检索仍只查 `memory_record`，未查 `knowledge_unit`。
- 向量文本构造只包含 statement、条件、证伪和前三条证据，未完整加入领域、类型、主体、时间、验证状态等。
- `REJECTED / RETIRED` 删除逻辑存在于 worker，但没有生命周期操作入口来触发。

### 3.6 API / MCP / 管理台

已实现：

- API 可创建 Bilibili 和小鹅通 HLS 解析任务。
- 视频详情接口会返回 `analysis_document / chapters / knowledge_units`。
- 管理台仍可展示视频、Markdown、帧、旧 chunks 和旧 events。

完成度：

- API：约 30%。
- MCP：约 20%。
- 管理台：约 25%。

剩余问题：

- 缺少文档要求的新 API：
  - 章节列表
  - 章节详情
  - 知识列表
  - 知识详情
  - 知识检索
  - 重新解析
  - 生命周期操作
  - 冲突列表
- MCP 仍是旧的 `search_video_insights`，且过滤 `bilibili_video_summary`。
- 管理台仍以旧 `chunks/events/summary` 为主，没有知识单元卡片、生命周期、验证状态、证据详情、冲突管理、人工操作、向量状态。

## 4. 必须修改的问题清单

### P0：检索未切换到新视频知识入口

问题：

- `engines/retrieval/query_understanding.py` 默认 collection 为 `financial_memory`、`financial_knowledge`。
- 配置实际 collection 是 `financial_memory_v2_bge_m3`、`financial_knowledge_v2_bge_m3` 和三个 `financial_video_*` collection。
- 视频知识即使入库并入向量任务，也不会被当前主检索稳定召回。

修改建议：

1. 增加视频知识意图识别：
   - `method_explanation`
   - `current_state`
   - `historical_review`
   - `industry_logic`
   - `forecast_compare`
   - `trading_decision`
2. 按意图选择 collection：
   - 方法解释：`financial_video_durable_v1_bge_m3`
   - 当前状态：`financial_video_timed_v1_bge_m3` + `financial_video_action_v1_bge_m3`
   - 历史复盘：三个视频 collection 均可检索，允许过期知识
   - 交易决策：优先 `financial_video_action_v1_bge_m3`
3. 修复默认 collection 名称，使用配置中的 `_v2_bge_m3`。

涉及文件：

- `engines/retrieval/query_understanding.py`
- `engines/retrieval/hybrid_retriever.py`
- `engines/retrieval/postgres_hydrator.py`
- `tests/test_hybrid_retriever.py`

验收：

- 当前状态查询过滤 `EXPIRED`。
- 历史查询允许返回 `EXPIRED`。
- 方法查询 durable 结果占比达到测试要求。
- 交易决策查询优先返回 `ACTION` 和带条件/证伪的知识。

### P0：Sparse 检索未支持 KnowledgeUnit

问题：

- `PostgresSparseRetriever` 只从 `memory_record` + `vector_index_mapping` 召回。
- 新视频知识的稀疏召回缺失。

修改建议：

1. 为 PostgreSQL 增加 `knowledge_unit` sparse SQL。
2. SQLite fallback 也支持 `knowledge_unit`。
3. 允许同时召回 `memory_record` 和 `knowledge_unit`。
4. filters 支持：
   - `primary_domain`
   - `knowledge_kind`
   - `temporal_class`
   - `lifecycle_status`
   - `verification_status`
   - `subject_key`
   - `predicate_key`

涉及文件：

- `engines/retrieval/sparse_retriever.py`
- `tests/test_hybrid_retriever.py`

验收：

- 不依赖 Qdrant 时，也能通过关键词召回 `knowledge_unit`。
- filters 对 `knowledge_unit` 生效。

### P0：MCP 工具缺失

问题：

- 文档要求新视频知识必须成为 Agent 可调用能力。
- 当前只有 `search_video_insights`，仍使用旧 `bilibili_video_summary`。

修改建议：

新增 MCP 工具：

```python
search_video_knowledge(query, intent=None, filters=None, top_k=10)
get_current_subject_state(subject_key, domains=None, top_k=10)
get_subject_history(subject_key, date_from=None, date_to=None, include_expired=True)
get_video_knowledge_units(video_id, filters=None)
get_knowledge_unit(unit_id)
list_knowledge_conflicts(subject_key=None, status=None)
```

涉及文件：

- `mcp_servers/content_server.py`
- `app/tool_registry.py`
- `tests/test_mcp_content_tools.py`

验收：

- Agent 可通过 MCP 查到 `KnowledgeUnit`，返回证据、生命周期和验证状态。
- `search_video_insights` 迁移或标记 deprecated。

### P1：API 不完整

建议新增 API：

```http
GET /api/v1/content/videos/{video_id}/chapters
GET /api/v1/content/videos/{video_id}/chapters/{chapter_id}
GET /api/v1/content/videos/{video_id}/knowledge-units
GET /api/v1/content/knowledge-units/{unit_id}
POST /api/v1/content/knowledge/search
POST /api/v1/content/videos/{video_id}/reparse
PATCH /api/v1/content/knowledge-units/{unit_id}/lifecycle
GET /api/v1/content/knowledge/conflicts
```

涉及文件：

- `app/api.py`
- `engines/content/video_ingest_service.py`
- `storage/repositories/knowledge_repository.py`
- `tests/test_content_api.py`

验收：

- 前端不再通过旧 `events/chunks` 读取核心知识。
- 知识详情接口返回完整证据和实体关系。
- 生命周期操作写审计并触发向量同步任务。

### P1：生命周期更新任务缺失

问题：

- 当前只有解析时应用生命周期策略。
- 时间过期、人工下线、替代关系变化后没有统一后台任务处理。

修改建议：

新增 `KnowledgeLifecycleService`：

```python
expire_due_units(now)
transition_unit(unit_id, target_status, reason, operator)
sync_vector_for_lifecycle_change(unit_id)
list_conflicts(...)
```

新增 worker 或集成现有 job worker：

```text
knowledge_lifecycle_sweep
knowledge_vector_resync
```

涉及文件：

- `engines/content/knowledge_lifecycle_service.py`
- `workers/job_worker.py`
- `storage/repositories/knowledge_repository.py`
- `storage/models/knowledge.py`
- `storage/migrations/008_knowledge_lifecycle_audit.sql`
- `tests/test_video_knowledge_lifecycle.py`

验收：

- 到期知识会被标记 `EXPIRED`。
- `REJECTED / RETIRED` 后 Qdrant point 被删除。
- `ACTIVE / SUPERSEDED / EXPIRED` 的向量 payload 状态可同步更新。

### P1：管理台未改为知识单元中心

问题：

- 当前管理台仍展示旧 `chunks/events`。
- 没有知识单元卡片和生命周期操作。

修改建议：

1. 视频详情页增加：
   - 章节时间轴
   - 知识单元列表
   - 证据展开
   - 实体关系
   - 生命周期状态
   - 验证状态
   - 向量索引状态
2. 增加冲突管理页：
   - 同主体冲突
   - 同指标数值冲突
   - 新旧状态替代
   - 人工确认 `SUPERSEDES / REINFORCES / CONFLICTS`
3. 增加人工操作：
   - 标记有效
   - 标记过期
   - 下线
   - 恢复
   - 拆分知识
   - 合并知识
   - 重新抽取章节知识

涉及文件：

- `app/static/admin.html`
- `app/api.py`
- `storage/repositories/knowledge_repository.py`

验收：

- 管理台可以直接查看和操作 `KnowledgeUnit`。
- 不再把旧 events 作为核心视频知识展示。

### P1：视频列表未适配新管线

问题：

- `ContentQueryRepository.list_videos` 目前依赖 `VideoSummary` join。
- 新 v3 管线不再写 `VideoSummary`，导致只有 `VideoAnalysisDocument` 的视频可能不出现在列表。

修改建议：

1. 列表改为以 `VideoAsset` 为主表。
2. 左连接 `VideoAnalysisDocument` 和可选 `VideoSummary`。
3. `summary_ready` 改为：
   - `analysis_document_ready`
   - `legacy_summary_ready`
4. 排序优先使用视频更新时间、发布日和 analysis document 更新时间。

涉及文件：

- `storage/repositories/content_repository.py`
- `tests/test_content_service.py`
- `tests/test_content_api.py`

验收：

- 只有 `analysis_document`、没有 `video_summary` 的新视频也能在管理台列表出现。

### P2：旧链路清理

问题：

- 新测试已确认新管线不再写旧表，但旧模型、仓储、API、管理台和删除逻辑仍存在。

建议分两步处理：

第一步：隔离旧链路。

- 重命名旧 API 显示为 legacy。
- 管理台默认隐藏旧 events/chunks。
- 文档标记 `VideoSummary` 为历史兼容表。

第二步：删除旧链路。

- 删除 `VideoChunkRepository`、`FinancialEventRepository`、`VideoSummaryRepository` 的视频知识主路径。
- 删除旧 `bilibili_video_summary / bilibili_video_viewpoint / bilibili_financial_event` memory 写入和检索。
- 删除旧 summary markdown 作为事实源的路径。

涉及文件：

- `storage/models/content.py`
- `storage/repositories/content_repository.py`
- `engines/content/video_ingest_service.py`
- `engines/content/video_summarizer.py`
- `engines/content/financial_event_extractor.py`
- `mcp_servers/content_server.py`
- `app/static/admin.html`
- `tests/*content*`

验收：

- 新视频知识只以 `knowledge_unit` 作为事实源。
- `financial_knowledge` 不再保存视频知识投影。
- 旧接口被删除或明确标记 legacy。

## 5. 文件级修改清单

### 5.1 新增文件

建议新增：

```text
engines/content/knowledge_lifecycle_service.py
engines/retrieval/video_knowledge_query.py
storage/migrations/008_knowledge_lifecycle_audit.sql
tests/test_video_knowledge_api.py
tests/test_video_knowledge_retrieval.py
tests/test_video_knowledge_lifecycle.py
tests/test_video_knowledge_mcp.py
```

可选新增：

```text
config/video_knowledge_retrieval.yaml
docs/video_knowledge_migration_runbook.md
```

### 5.2 修改文件

必须修改：

```text
engines/retrieval/query_understanding.py
engines/retrieval/hybrid_retriever.py
engines/retrieval/sparse_retriever.py
engines/retrieval/postgres_hydrator.py
workers/vector_index_worker.py
storage/repositories/knowledge_repository.py
storage/repositories/content_repository.py
engines/content/video_ingest_service.py
mcp_servers/content_server.py
app/tool_registry.py
app/api.py
app/static/admin.html
config/retrieval.yaml
tests/test_hybrid_retriever.py
tests/test_content_api.py
tests/test_content_service.py
tests/test_mcp_content_tools.py
```

### 5.3 待删除或降级为 Legacy 的文件/类

需要确认兼容窗口后处理：

```text
engines/content/financial_event_extractor.py
engines/content/event_conflict_resolver.py
engines/content/video_summarizer.py
engines/content/video_summary_exporter.py
storage.repositories.content_repository.VideoChunkRepository
storage.repositories.content_repository.FinancialEventRepository
storage.repositories.content_repository.VideoSummaryRepository
```

注意：

- `video_segment` 仍可保留，用作原始 ASR 时间片。
- `video_frame` 仍应保留，用作视觉证据来源。
- 不建议立即删除所有旧表，建议先迁移数据和接口，再做破坏性迁移。

## 6. 推荐实施顺序

### 阶段一：让新知识可检索

目标：

- 主检索能召回 `knowledge_unit`。
- 按意图路由到视频知识专用 collection。

任务：

1. 修复 collection 名称。
2. 增加视频知识检索意图。
3. Dense 检索使用三个 `financial_video_*` collection。
4. Sparse 检索支持 `knowledge_unit`。
5. Hydrator 返回完整知识单元字段。

建议测试：

```powershell
.\.conda-env\python.exe -m pytest tests\test_hybrid_retriever.py tests\test_video_knowledge_v3.py -q
```

### 阶段二：补 API 和 MCP

目标：

- Agent 和前端能使用新知识系统。

任务：

1. 新增知识列表、详情、搜索接口。
2. 新增生命周期操作接口。
3. 新增冲突列表接口。
4. 新增 MCP 工具。
5. `search_video_insights` 标记 legacy 或迁移到新实现。

建议测试：

```powershell
.\.conda-env\python.exe -m pytest tests\test_content_api.py tests\test_mcp_content_tools.py -q
```

### 阶段三：生命周期治理

目标：

- 生命周期状态不只在抽取时计算，而是持续可维护。

任务：

1. 新增生命周期服务。
2. 新增审计表。
3. 实现到期扫描。
4. 实现人工状态流转。
5. 生命周期变化时触发向量重同步。

建议测试：

```powershell
.\.conda-env\python.exe -m pytest tests\test_video_knowledge_lifecycle.py -q
```

### 阶段四：管理台改造

目标：

- 管理台以章节和知识单元为中心。

任务：

1. 视频列表适配 `VideoAnalysisDocument`。
2. 视频详情增加章节时间轴。
3. 增加知识单元卡片。
4. 增加证据查看。
5. 增加冲突和生命周期操作。

建议测试：

- API 单元测试。
- 管理台人工冒烟测试。
- 如引入前端自动化，再补 Playwright 截图检查。

### 阶段五：旧链路清理和迁移

目标：

- 视频知识事实源只保留 `KnowledgeUnit`。

任务：

1. 停止旧视频 summary memory 写入。
2. 清理旧 Qdrant 视频 points。
3. 历史视频重算。
4. 删除或隐藏旧 events/chunks 展示。
5. 更新 README 和部署文档。

## 7. 验收标准

### 7.1 数据库验收

必须满足：

- 每个完成解析的视频有 `VideoAnalysisDocument`。
- 每个有效视频至少有一个 `VideoChapter`。
- 每个有效知识单元有至少一条 `KnowledgeEvidence`。
- 时间敏感知识有 `as_of_time`。
- 短期知识有 `valid_to` 或明确生命周期策略。
- 不再写入旧 `VideoSummary / FinancialEvent / VideoChunk` 作为新视频知识事实源。

### 7.2 向量验收

必须满足：

- 每个可检索的 `KnowledgeUnit` 有向量映射。
- `DURABLE` 进入 durable collection。
- `ACTION` 进入 action collection。
- 其他时间敏感知识进入 timed collection。
- `REJECTED / RETIRED` 不存在有效 Qdrant point。

### 7.3 检索验收

必须满足：

- 方法查询优先 durable 知识。
- 当前状态查询过滤过期知识。
- 历史复盘允许读取过期知识。
- 交易决策优先带条件和证伪的知识。
- 冲突无法判定时同时展示，而不是静默丢弃。

### 7.4 API/MCP 验收

必须满足：

- API 能查询章节、知识、证据、生命周期、冲突。
- MCP 能面向 Agent 查询视频知识。
- Agent 不再依赖 `bilibili_video_summary` 作为主要视频知识入口。

### 7.5 测试验收

建议最低测试集：

```powershell
.\.conda-env\python.exe -m pytest `
  tests\test_video_knowledge_v3.py `
  tests\test_video_knowledge_retrieval.py `
  tests\test_video_knowledge_lifecycle.py `
  tests\test_video_knowledge_api.py `
  tests\test_video_knowledge_mcp.py `
  tests\test_hybrid_retriever.py `
  tests\test_content_api.py `
  -q
```

## 8. 当前风险

### 8.1 新数据写入但旧检索读取

这是当前最大风险。新管线已经把知识写入 `knowledge_unit`，但 Agent 检索仍可能主要读取旧 `MemoryRecord` 或旧 collection，导致新改造在真实问答中不可见。

### 8.2 管理台与后端数据结构错位

后端详情已经包含 `knowledge_units`，但前端仍展示 `chunks/events`。用户会误以为没有新知识抽取结果。

### 8.3 旧删除接口可能误导

当前删除接口仍是“删除视频总结和向量知识”的语义，但新管线不再写 `VideoSummary`。需要重新定义删除范围：

- 删除阅读文档？
- 删除知识单元？
- 删除向量投影？
- 删除视频原始数据？

### 8.4 生命周期只有静态策略

没有后台更新任务时，`EXPIRED` 只能在解析时判断。随着时间推进，旧知识不会自动过期。

## 9. 建议结论

当前修改已经完成了“视频知识原子化入库”的核心骨架，但还没有完成详细设计要求的系统级替换。

建议优先级：

1. 先让 `KnowledgeUnit` 成为检索主入口。
2. 再补 API/MCP，使 Agent 可用。
3. 再补管理台和生命周期运维。
4. 最后清理旧链路和历史数据。

只有完成前三步后，才能认为“新视频知识系统可用”；完成第四步后，才能认为“按详细设计文档完整实现”。
