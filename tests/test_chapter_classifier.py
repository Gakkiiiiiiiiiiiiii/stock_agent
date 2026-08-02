from engines.content.chapter_classifier import ChapterClassifier


def test_subscribe_prompt_does_not_turn_a_market_chapter_into_an_advertisement():
    result = ChapterClassifier().classify(
        "上证指数下跌，市场成交额缩量，科技股回调后出现反弹。"
        "请点赞、订阅、转发支持节目。"
    )

    assert result["chapter_type"] == "MARKET_REVIEW"
    assert result["primary_domain"] == "MARKET"


def test_standalone_subscribe_prompt_is_an_advertisement():
    result = ChapterClassifier().classify("请点赞、订阅、转发，关注频道获取更多内容。")

    assert result["chapter_type"] == "ADVERTISEMENT"
