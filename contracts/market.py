from pydantic import BaseModel
from contracts.common import ServiceEnvelope
class MarketFeatureRequest(ServiceEnvelope): pass
class SectorStrengthRequest(ServiceEnvelope): top_k: int = 20
