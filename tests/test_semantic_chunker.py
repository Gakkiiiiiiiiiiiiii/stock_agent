from engines.content.semantic_chunker import SemanticChunker


def test_infer_topic_requires_whole_word_ai():
    # SAIF 中的 "AI" 是单词内部子串，不应命中主题；半导体出现两次应胜出
    topic = SemanticChunker._infer_topic("今天讲半导体设备，SAIF 数据显示半导体继续走强。", [])

    assert topic == "半导体"


def test_infer_topic_picks_dominant_keyword_by_frequency():
    transcript = "上证指数今天跌了。上证指数跌破关键位。半导体有反弹。上证指数尾盘回升。"
    topic = SemanticChunker._infer_topic(transcript, [])

    assert topic == "上证指数"


def test_infer_topic_falls_back_to_generic_label_instead_of_long_snippet():
    topic = SemanticChunker._infer_topic("大家周末好，欢迎收看今天的节目，我们先来聊聊天。", [])

    assert topic == "综合盘面"


def test_dedup_ocr_lines_removes_repeated_ui_lines_across_frames():
    texts = [
        "系统 功能 深度 报价\n上证指数 3764.15",
        "系统 功能 深度 报价\n创业板指 3428.63",
    ]

    deduped = SemanticChunker._dedup_ocr_lines(texts)

    assert deduped[0] == "系统 功能 深度 报价\n上证指数 3764.15"
    assert deduped[1] == "创业板指 3428.63"
