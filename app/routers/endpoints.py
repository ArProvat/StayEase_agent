

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.Agents.Graph import get_graph
from app.Agents.db import append_messages, get_conversation, get_or_create_conversation
from app.models import ChatMessageCreate, ConversationHistoryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=True)}\n\n"


def _is_rate_limit_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True

    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 429:
        return True

    message = str(exc).lower()
    return "rate limit" in message or "429" in message


def _build_fallback_assistant_message(conversation_id: str, exc: Exception) -> dict[str, Any]:
    if _is_rate_limit_error(exc):
        content = (
            "Sorry, I’m temporarily unavailable because the AI provider rate limit has been reached. "
            "Please try again in a little while."
        )
        escalated = False
        progress = "failed:rate_limit"
    else:
        content = (
            "Sorry, something went wrong while handling your request. "
            "Please try again."
        )
        escalated = True
        progress = "failed:chat_message"

    return {
        "role": "assistant",
        "content": content,
        "timestamp": _utc_now_iso(),
        "metadata": {
            "escalated": escalated,
            "progress": progress,
            "intent": None,
            "error": str(exc),
            "conversation_id": conversation_id,
        },
    }


def _extract_final_ai_message(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str):
                return content
            return str(content)
    raise ValueError("No AI message was produced by the agent")


def _normalize_history_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        normalized.append(
            {
                "role": message.get("role", ""),
                "content": message.get("content", ""),
                "timestamp": message.get("timestamp"),
                "metadata": message.get("metadata"),
            }
        )
    return normalized


@router.post("/{conversation_id}/message")
async def send_guest_message(conversation_id: str, payload: ChatMessageCreate) -> StreamingResponse:
    conversation = get_or_create_conversation(conversation_id)
    user_message = {
        "role": "user",
        "content": payload.message,
        "timestamp": _utc_now_iso(),
    }
    append_messages(conversation_id, [user_message], escalated=False)

    async def event_stream():
        yield _sse_event(
            "message_saved",
            {
                "conversation_id": conversation_id,
                "message": user_message,
            },
        )

        try:
            result = get_graph().invoke(
                {
                    "conversation_id": conversation_id,
                    "messages": [HumanMessage(content=payload.message)],
                    "escalated": conversation.get("escalated", False),
                }
            )
            assistant_text = _extract_final_ai_message(result["messages"])
            assistant_message = {
                "role": "assistant",
                "content": assistant_text,
                "timestamp": _utc_now_iso(),
                "metadata": {
                    "escalated": bool(result.get("escalated", False)),
                    "progress": result.get("progress"),
                    "intent": result.get("intent"),
                },
            }
            append_messages(
                conversation_id,
                [assistant_message],
                escalated=bool(result.get("escalated", False)),
            )
            yield _sse_event(
                "final_message",
                {
                    "conversation_id": conversation_id,
                    "message": assistant_message,
                },
            )
        except Exception as exc:
            logger.exception("Failed to process conversation_id=%s", conversation_id)
            assistant_message = _build_fallback_assistant_message(conversation_id, exc)
            append_messages(
                conversation_id,
                [assistant_message],
                escalated=bool(assistant_message["metadata"]["escalated"]),
            )
            yield _sse_event(
                "final_message",
                {
                    "conversation_id": conversation_id,
                    "message": assistant_message,
                },
            )
        finally:
            yield _sse_event("done", {"conversation_id": conversation_id})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{conversation_id}/history", response_model=ConversationHistoryResponse)
async def get_conversation_history(conversation_id: str) -> ConversationHistoryResponse:
    conversation = get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationHistoryResponse(
        conversation_id=conversation["id"],
        status=conversation.get("status", "active"),
        escalated=bool(conversation.get("escalated", False)),
        messages=_normalize_history_messages(conversation.get("messages", [])),
    )
