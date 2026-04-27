# OCR noise correction (regex)
from typing_extensions import TypedDict
from typing import List
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    conversation_id: str
    messages: List[BaseMessage]
    classification: str  
    current_intent: str
    property_results: List[dict]
    booking_result: dict
    escalated: bool
    user_info: dict