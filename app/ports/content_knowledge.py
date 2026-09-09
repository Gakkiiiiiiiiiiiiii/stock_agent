"""Locked, content-bundle-only boundary for knowledge conclusions."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

CONTENT_KNOWLEDGE_CONTRACT = "content-knowledge-bundle.v1"
CONTENT_KNOWLEDGE_SCHEMA_VERSION = "1.0.0"
CONTENT_KNOWLEDGE_C14N_VERSION = "content-bundle-c14n-v1"
CONTENT_KNOWLEDGE_SCHEMA_CHECKSUM = "sha256:EBFD13B78622C3846890438A4FB3CB858278F571FDAB247CDD72EF18CA211621"
CONTENT_KNOWLEDGE_V2_CONTRACT = "content-knowledge-bundle.v2"
CONTENT_KNOWLEDGE_V2_SCHEMA_VERSION = "2.0.0"
CONTENT_KNOWLEDGE_V2_C14N_VERSION = "content-bundle-c14n-v2"
# This lock is the SHA-256 of stock_content/contracts/content-knowledge-bundle.v2.json.
# v1 remains the default wire contract and its replay identity is untouched.
CONTENT_KNOWLEDGE_V2_SCHEMA_CHECKSUM = "sha256:23C1D9C6BE131CBA8F270F01F7F45EB5D3148EE219EDF43D689F1C5707115800"
PUBLIC_STRICT = "PUBLIC_STRICT"
# These bindings belong to the Content bundle contract, not to the local
# conclusion policy.  Keeping them explicit prevents a caller from silently
# broadening the producer query.
MINIMUM_SUPPORT_STATUS = "SOURCE_SUPPORTED"
KNOWLEDGE_POLICY_VERSION = "content-bundle-policy.v1"


@dataclass(frozen=True)
class KnowledgeBundleRequest:
    content_snapshot_id: str
    query: str
    symbol: str
    business_as_of: datetime
    knowledge_as_of: datetime
    availability_as_of: datetime
    minimum_support_status: str = MINIMUM_SUPPORT_STATUS
    max_items: int = 20
    policy: str = PUBLIC_STRICT
    policy_version: str = KNOWLEDGE_POLICY_VERSION
    contract_version: str = CONTENT_KNOWLEDGE_CONTRACT


@dataclass(frozen=True)
class ContentKnowledgeBundle:
    """Validated, immutable producer material; callers cannot supply raw JSON."""

    bundle_id: str
    bundle_hash: str
    request_hash: str
    content_snapshot_id: str
    producer_sha: str
    contract_checksum: str
    payload: Mapping[str, Any]


class TraceContext(Protocol):
    trace_id: str


class ContentKnowledgeBundlePort(Protocol):
    def create_bundle(self, request: KnowledgeBundleRequest, *, trace: TraceContext) -> ContentKnowledgeBundle: ...

    def get_bundle(self, bundle_id: str, *, trace: TraceContext) -> ContentKnowledgeBundle: ...
