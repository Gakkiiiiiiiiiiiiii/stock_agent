from contracts.common import ServiceEnvelope
from engines.execution.models import TradeIntent
class TradeIntentRequest(ServiceEnvelope):
    intent: TradeIntent
    context: dict = {}
    quantity: int = 0
