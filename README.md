# Financial Analysis Agent

本项目根据 `artitect/金融分析Agent架构详细设计文档.md` 及优化版 v1.3 实现一个可容器化部署的金融分析 Agent：

- 技术指标引擎：MA、EMA、MACD、KDJ、STL、LTL、RPS。
- 技术信号识别：B1、B2、B3、MACD 三金叉、RPS 强势池。
- PostgreSQL 事实主库 + Qdrant 语义索引 + reranker 精排。
- vector index worker：事实写入后异步切分、embedding、索引。
- 市场状态识别、策略路由、信号升降级、组合构建。
- FastAPI 接口和 MCP 风格工具函数封装。
- 行情层统一接入 QMT bridge 实时 A 股数据，不再内置 AkShare / CSV 样例回退。
- Claude-style Agent 编排：保留 Claude Agent 风格的 skills + tools + orchestration 框架。
- 主模型使用 DeepSeek：`skill` 选择、工具调用、最终报告都由 DeepSeek `deepseek-v4-pro` 完成。
- 可插拔辅助模型：`ask_research_model` 工具默认也可继续走 DeepSeek。

## 快速开始

```bash
docker compose up --build
```

默认服务：

- API: `http://127.0.0.1:8000`
- Admin Console: `http://127.0.0.1:8000/admin`
- Qdrant: `http://127.0.0.1:6333`
- Reranker: `http://127.0.0.1:8010`
- PostgreSQL: `localhost:5433`
- Redis: `localhost:6379`

Docker 部署下，以下目录已绑定到宿主机，管理台更新会直接写回当前工程目录：

- `./knowledge_base`
- `./skills`
- `./storage`

股票行情现在统一走 QMT。默认配置会优先复用同级 `../quant` 项目的桥接运行时：

- `QMT_BRIDGE_PYTHON=../quant/.venv-qmt36/Scripts/python.exe`
- `QMT_BRIDGE_SCRIPT=../quant/scripts/qmt_bridge.py`
- `QMT_INSTALL_DIR=../quant/runtime/qmt_client/installed`
- `QMT_USERDATA_DIR=../quant/runtime/qmt_client/installed/userdata_mini`

如果你的 QMT 安装路径不同，请在 `.env` 中覆盖这些变量。QMT 不可用时，接口会明确返回错误，不会再回退到本地样例数据。

## Bilibili 视频解析

项目支持 Bilibili 视频内容接入，面向无字幕视频会走音频抽取 + ASR + 关键帧抽取 + 画面 OCR/视觉理解 + 投研摘要链路。

- `POST /api/v1/content/bilibili/ingest`：创建异步解析任务
- `POST /api/v1/content/bilibili/summarize`：同步完成单视频解析
- `GET /api/v1/content/tasks/{task_id}`：查看任务状态
- `GET /api/v1/content/videos/{video_id}`：查看视频元数据、转写和摘要

多模态增强说明：

- 默认会额外下载视频文件并抽取关键帧。
- 关键帧 OCR 统一使用 `PaddleOCR`，在支持 CUDA 的桌面环境里会自动走 GPU；如果 Paddle 运行时、CUDA DLL 或模型初始化失败，任务会直接报错，便于尽快修复环境问题。
- 当 `VISUAL_MODEL_*` 已配置为支持图片输入的 OpenAI-compatible 模型时，会把关键帧与附近口播一起做视觉语义理解。
- 如果当前视觉模型是文本模型（例如默认的 `deepseek-v4-pro`），系统会自动改走 `OCR + 关联口播` 的联合复核，不再持续报“视觉模型未成功解析画面”。
- 视觉链路失败时会自动回退到纯音频总结，不阻断现有流程。

运行前需要在宿主机安装：

- `ffmpeg`
- `yt-dlp`
- Python 依赖 `faster-whisper`

本地开发测试：

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
pytest
uvicorn app.api:app --reload
```

推荐的桌面本地运行方式已经固定到项目内 conda 环境 `./.conda-env`：

```powershell
.\scripts\bootstrap-conda.ps1
.\scripts\start-api.ps1 -Reload
.\scripts\start-content-worker.ps1
.\scripts\test.ps1 -q
```

这些脚本会默认：

- 使用 `./.conda-env/python.exe`
- 自动把 conda 环境里的 `ffmpeg`、`yt-dlp` 放进 PATH
- 自动把 `paddlepaddle-gpu` 自带的 CUDA / cuDNN DLL 目录放进 PATH，供 PaddleOCR GPU 使用
- 从 `.env` 读取 `AGENT_MODEL_*` / `ANALYSIS_MODEL_*`
- 对桌面本地运行强制使用 `sqlite:///./financial_agent.db`

OCR 相关环境变量：

- `VIDEO_OCR_BACKEND=paddleocr`：默认 OCR 后端
- `VIDEO_OCR_DEVICE=auto`：自动选择 `gpu:0` 或 `cpu`
- `VIDEO_OCR_PADDLE_LANG=ch`：PaddleOCR 语言包
- `VIDEO_OCR_SCORE_THRESH=0.75`：低于该阈值的识别结果会被过滤
- `VIDEO_OCR_DET_MODEL_NAME` / `VIDEO_OCR_REC_MODEL_NAME`：可切换 PP-OCR 模型
- 当前不再回退到 `RapidOCR` / `tesseract`，Paddle 运行时异常会直接抛错

单视频本地验证：

```powershell
.\scripts\run-bilibili-summary.ps1 `
  -Url "https://www.bilibili.com/video/BV14QKo6xEJD/?spm_id_from=333.1387.upload.video_card.click" `
  -AsrModel medium `
  -AsrProfile accurate `
  -ForceReprocess
```

默认档位现在是 `accurate`，优先准确率：`large-v3/medium + beam_size=5 + condition_on_previous_text=true`。在 8GB 显卡上会自动避免使用容易 OOM 的 batched `large-v3` 组合。

长视频如果更在意速度，可以手动切到更激进的 GPU 批量转写档位：

```powershell
.\scripts\run-bilibili-summary.ps1 `
  -Url "https://www.bilibili.com/video/BV19qNj6SEAv/" `
  -AsrModel large-v3 `
  -AsrProfile fast `
  -ForceReprocess
```

可用档位：

- `fast`：`batch_size=16`、`beam_size=1`
- `balanced`：`batch_size=12`、`beam_size=3`
- `accurate`：`batch_size=1`、`beam_size=5`、`condition_on_previous_text=true`

充电视频或大会员视频需要先给后端准备可复用的登录态，否则 `yt-dlp` 只能拿到试看片段。项目现在默认读取 `storage/runtime/bilibili.cookies.txt`：

```powershell
.\scripts\login-bilibili.ps1
```

脚本会在终端打印 B 站扫码登录二维码，用已充电的账号确认后会把 cookie 写到项目默认位置。完成后直接重新执行：

```powershell
.\scripts\run-bilibili-summary.ps1 `
  -Url "https://www.bilibili.com/video/BV19qNj6SEAv/" `
  -AsrModel large-v3 `
  -ForceReprocess
```

如果你已经从浏览器里拿到了完整的 `Cookie:` 请求头，也可以直接落盘成项目 cookie 文件：

```powershell
.\scripts\login-bilibili.ps1 -CookieHeader "SESSDATA=...; bili_jct=...; DedeUserID=..."
```

## Claude Agent 模式

配置 `AGENT_MODEL_*` 或 `ANALYSIS_MODEL_*` 为 DeepSeek 后，以下能力会切换到真正的模型编排：

- `/api/v1/analyze/stock`
- `/api/v1/analyze/theme`
- `/api/v1/market/daily-scan`
- `/api/v1/agent/run`

职责边界：

- DeepSeek 主模型负责：选 skill、规划步骤、调用本地工具、输出最终报告。
- Python 引擎负责：技术指标、信号识别、主题打分、风险评估等确定性计算。
- 框架层仍保持 Claude-style Agent 结构：skills、受控工具、MCP 风格调用、决策审计。

示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/agent/run \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"分析黄金主题当前是否值得关注，并给出触发和证伪条件\"}"
```

管理台使用：

- `Themes`：维护结构化主题知识库，并自动生成 `knowledge_base/themes/*.md`
- `Docs`：维护通用 Markdown 知识库文档
- `Skills`：维护 `skills/<slug>/SKILL.md`

DeepSeek 官方 OpenAI 兼容配置：

- `base_url`: `https://api.deepseek.com`
- `model`: `deepseek-v4-pro`

## v1.3 新增能力

- `/api/v1/retrieval/context`：Qdrant + reranker + PostgreSQL hydration 检索闭环。
- `/api/v1/market/regime`：市场状态、状态机、高位退潮风险。
- `vector_worker` 容器：异步处理 `vector_index_task`。
- `reranker` 容器：本地精排服务。
- 主题/个股知识库已扩展为多主题种子库，包括：`黄金`、`高股息`、`公用事业`、`电信运营`、`AI机房液冷`、`创新药`、`铜`。

## 免责声明

本项目仅用于投研辅助和系统建设，不构成任何投资建议，不包含自动交易能力。
