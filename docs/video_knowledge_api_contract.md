# 视频知识 API / MCP 契约

本文档冻结视频知识 v3 灰度发布期的主要接口结构。列表类接口统一包含：

```json
{
  "items": [],
  "limit": 50,
  "next_cursor": null,
  "filters": {},
  "warnings": []
}
```

## API

- `GET /api/v1/content/videos/{video_id}/chapters`
  - 返回 `items` 和兼容字段 `chapters`，元素为章节详情。
- `GET /api/v1/content/videos/{video_id}/chapters/{chapter_id}`
  - 返回单章节详情，含 `knowledge_units`。
- `GET /api/v1/content/videos/{video_id}/knowledge`
- `GET /api/v1/content/videos/{video_id}/knowledge-units`
  - 支持筛选：`primary_domain`、`knowledge_kind`、`temporal_class`、`lifecycle_status`、`verification_status`、`subject_key`、`valid_only`。
- `GET /api/v1/content/knowledge/{unit_id}`
- `GET /api/v1/content/knowledge-units/{unit_id}`
  - 返回单个 `KnowledgeUnit`，含 `evidence`、`entities`、`relations`、`vector_status`。
- `POST /api/v1/content/knowledge/search`
  - 请求：`{"query": "券商", "filters": {}, "limit": 20}`。
  - 返回列表契约结构，另含 `query`。
- `GET /api/v1/content/knowledge/conflicts`
  - 返回冲突组，元素含 `conflict_group_id`、`conflict_key`、`recommended_action`、`units`。
- `GET /api/v1/content/knowledge/subjects/{subject_key}/current`
  - 仅返回未过期、未驳回、未退休的当前知识。
- `GET /api/v1/content/knowledge/subjects/{subject_key}/history`
  - 返回主体历史知识，保留过期观点。
- `PATCH /api/v1/content/knowledge-units/{unit_id}/lifecycle`
  - 请求字段：`lifecycle_status`、`verification_status`、`valid_to`、`note`、`operator`。
  - 返回更新后的知识、`lifecycle_audit` 和 `vector_tasks`。
- `GET /api/v1/content/knowledge-units/{unit_id}/lifecycle-audits`
  - 返回生命周期审计列表。

## 校验

`limit` 会被限制在服务端上限内，返回 `warnings`，例如 `limit_clamped_to_200`。

非法枚举返回稳定 `400`：

```json
{"detail": "invalid lifecycle_status: bad"}
```

受校验字段：

- `knowledge_kind`
- `temporal_class`
- `lifecycle_status`
- `verification_status`

## MCP

可用工具：

- `search_video_knowledge`
- `get_current_subject_state`
- `get_subject_history`
- `get_video_knowledge_units`
- `get_knowledge_unit`
- `list_knowledge_conflicts`

旧工具 `search_video_insights` 保留兼容，但返回：

```json
{"deprecated": true, "replacement": "search_video_knowledge"}
```

MCP 工具非法参数不抛异常，返回稳定错误：

```json
{
  "error": {"code": "INVALID_FILTER", "message": "invalid knowledge_kind: mystery"},
  "items": [],
  "warnings": []
}
```
