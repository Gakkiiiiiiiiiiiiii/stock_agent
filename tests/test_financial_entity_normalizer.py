from engines.content.financial_entity_normalizer import FinancialEntityNormalizer


def test_extract_entities_captures_chinese_company_names():
    normalizer = FinancialEntityNormalizer()

    entities = normalizer.extract_entities("锐捷网络今天走得很强，另外中际旭创也有表现。")
    tickers = {item["ticker"] for item in entities}

    assert "锐捷网络" in tickers


def test_extract_company_names_supports_chunk_topic_inference():
    names = FinancialEntityNormalizer.extract_company_names("这页主要在讲锐捷网络和中际旭创的分化。")

    assert "锐捷网络" in names


def test_extract_entities_captures_code_name_pairs_from_ocr():
    normalizer = FinancialEntityNormalizer()

    entities = normalizer.extract_entities(
        "KR688222成都先导 688222 成都先导(日线.前复权) 现价36.58"
    )

    assert {"name": "成都先导", "ticker": "688222", "entity_type": "EQUITY"} in entities
