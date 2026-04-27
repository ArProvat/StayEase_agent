from typing import Any

from pydantic import BaseModel


class ChatMessageCreate(BaseModel):
    message: str


class ConversationMessage(BaseModel):
    role: str
    content: str
    timestamp: str | None = None
    metadata: dict[str, Any] | None = None


class ConversationHistoryResponse(BaseModel):
    conversation_id: str
    status: str
    escalated: bool
    messages: list[ConversationMessage]
