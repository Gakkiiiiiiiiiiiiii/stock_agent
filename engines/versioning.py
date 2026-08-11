"""集中式版本读取：从 config/versions.yaml 解析各计算/策略版本号。"""
from __future__ import annotations

from typing import Any

from financial_agent.config import load_yaml_config

VERSIONS_FILE = "versions.yaml"


def all_versions() -> dict[str, Any]:
    """返回 versions.yaml 中 versions 段的完整映射；文件缺失时返回空 dict。"""
    try:
        data = load_yaml_config(VERSIONS_FILE)
    except FileNotFoundError:
        return {}
    return dict(data.get("versions") or {})


def get_version(name: str, default: Any = None) -> Any:
    """读取单个版本号；缺失时返回 default。"""
    return all_versions().get(name, default)
