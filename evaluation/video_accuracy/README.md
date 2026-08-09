# Golden Video Accuracy Dataset（P2-1 / P2-2 / P2-3）

设计文档 §72-75、§86 要求的视频知识准确率评测脚手架。目标是回答：

> `SOURCE_SUPPORTED` 的知识到底有多少真实正确率？

当前状态：**脚手架已落地，真实标注集待人工完成**（§73 要求 50~100 个视频）。
`sample_annotations.jsonl` 为合成示例（`"synthetic": true`），仅用于 CI 冒烟与格式示范，
不能作为真实准确率证据。

## 数据集格式

JSONL，一行一个样本（一个视频一条），结构由 `schema.py` 校验（单一事实来源）：

```json
{
  "id": "synth-001",
  "synthetic": true,
  "video_id": "SYNTH001",
  "video_type": "单人口播",
  "ground_truth_transcript": "人工校对后的完整转写文本",
  "entities": [{"name": "宁德时代", "ticker": "300750", "entity_type": "EQUITY"}],
  "numbers": [{"raw_expression": "20%", "value": 20.0, "unit": "PERCENT", "metric": "PROFIT"}],
  "speakers": [{"speaker_id": "speaker_1", "name": "主播"}],
  "claims": [
    {
      "claim_id": "synth-001-c1",
      "statement": "宁德时代净利润增长20%",
      "support_label": "SUPPORTED",
      "truth_status": "EXTERNALLY_VERIFIED",
      "negation": false,
      "condition": null,
      "speaker_id": "speaker_1",
      "critical": false,
      "evidence_span": {"start_ms": 1000, "end_ms": 5000, "text": "……原文片段……"}
    }
  ]
}
```

标注字段（§73）：ground truth transcript、entities、numbers、speaker、claim、
evidence span、condition、negation、truth status，另加 support_label
（SUPPORTED / CONTRADICTED / NOT_ENOUGH_EVIDENCE）。

- `video_type` 必须覆盖 §73 清单（schema.VIDEO_TYPES）：单人口播、K线技术分析、
  PPT/财报、行业研究、宏观、访谈、直播、低音质。
- `numbers[].unit` 取值：PERCENT / MULTIPLE / CNY / CNY_YI / CNY_WAN / POINT；
  约数/区间用 `min_value`/`max_value`，禁止伪精确（与 financial_numeric 同原则）。
- `negation`：true / false / null（未标注）。
- `critical: true` 标记关键 claim（数字幻觉、speaker 归因错误的零容忍项）。

## 标注流程

1. 按 §73 覆盖类型抽样 50~100 个已入库视频，记录 `video_id`。
2. 人工校对 ASR 转写得到 `ground_truth_transcript`，标注 speakers。
3. 逐条抽取 golden claim：statement、evidence span（起止毫秒 + 原文）、
   condition、negation、support_label。
4. 标注 entities（名称 + 6 位 ticker）与 numbers（结构化数值，同 financial_numeric 口径）。
5. 用外部数据源（行情/财报）判定每条 claim 的 truth_status。
6. `python -c "from evaluation.video_accuracy.schema import load_dataset; load_dataset('<path>')"`
   校验通过后提交。

## 运行 benchmark

系统产物导出：`{"videos": [{"video_id": ..., "units": [<KnowledgeUnit dict>]}]}`，
unit 字段沿用 KnowledgeRepository 序列化格式（statement / entities / support_status /
support_score / truth_status / speaker_id …）。

```bash
python -m evaluation.video_accuracy.benchmark \
  --dataset evaluation/video_accuracy/sample_annotations.jsonl \
  --system evaluation/video_accuracy/fixtures/sample_system_export.json \
  --output artifacts/video_accuracy/report.json
```

指标：金融实体准确率、证券代码 exact match、关键数字 exact match
（financial_numeric 区间/单位语义比对）、否定表达准确率、claim precision、
SOURCE_SUPPORTED precision、unsupported claim rate、critical numeric hallucination、
critical speaker attribution error，以及 calibration（ECE / Brier / reliability bins）。

门禁（§86）：实体 ≥98%、代码 ≥99.5%、数字 ≥99%、否定 ≥99.5%、claim precision ≥98%、
SOURCE_SUPPORTED precision ≥99%、unsupported rate <1%、critical 类 =0、ECE ≤0.05
（ECE 仅在 (score,label) 对数 ≥ `--ece-min-pairs`，默认 20 时纳入门禁）。
任一不达标 exit 1；`--no-gate` 只出报告不卡退出码。

## CI 接入

`.github/workflows/test.yml` 的 `video-accuracy-gate` job 用合成样本跑冒烟评测并
启用门禁（合成数据与导出完全对齐，应恒为 PASS；FAIL 说明 benchmark/门禁逻辑被破坏）。
真实 50~100 视频标注集落地后，把 `--dataset` / `--system` 换成真实路径即可，
门禁阈值无需改动。

## Calibration（P2-3）

`calibration.py` 提供 `reliability_bins(pairs, n_bins)` / `expected_calibration_error(pairs)`
/ `brier_score(pairs)`，输入 `(score, label)` 对，纯标准库。

注意（§17/§75）：**未通过 ECE ≤ 0.05 校准前，`support_score` 不得解释为概率**，
只能作为 score/proxy 参与排序。
