"""External Fact Verification Provider 包（P0-7 / §25-26）。"""

from engines.content.external_verification.base import AuthoritativeVerificationProvider, make_result
from engines.content.external_verification.factory import CompositeProvider, build_default_provider

__all__ = [
    "AuthoritativeVerificationProvider",
    "CompositeProvider",
    "build_default_provider",
    "make_result",
]
