from app.Agents.Nodes import *
from typing import Literal, Any
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
import logging
logger = logging.getLogger(__name__)



def route_after_classification(
    state: AgentState,
) -> Literal["greeting_node", "agent_node", "escalation_node"]:
    if state["intent"] == "greeting":
        return "greeting_node"

    if state["intent"] in ["search", "details", "book"]:
        return "agent_node"

    return "escalation_node"


def route_after_agent(
    state: AgentState,
) -> Literal["escalation_node", "__end__"]:
    if state["escalated"]:
        return "escalation_node"

    return "__end__"

graph: Any | None = None
_checkpointer_cm: Any | None = None
_checkpointer: AsyncPostgresSaver | None = None

def build_graph(checkpointer=None):
    builder = StateGraph(AgentState)

    builder.add_node("classify_node", classify_node)
    builder.add_node("greeting_node", greeting_node)
    builder.add_node("agent_node", agent_node)
    builder.add_node("escalation_node", escalation_node)

    builder.add_edge(START, "classify_node")

    builder.add_conditional_edges(
        "classify_node",
        route_after_classification,
        {
            "greeting_node": "greeting_node",
            "agent_node": "agent_node",
            "escalation_node": "escalation_node",
        },
    )

    builder.add_edge("greeting_node", END)

    builder.add_conditional_edges(
        "agent_node",
        route_after_agent,
        {
            "escalation_node": "escalation_node",
            "__end__": END,
        },
    )

    builder.add_edge("escalation_node", END)

    return builder.compile(checkpointer=checkpointer)


async def init_graph(checkpoint_dsn: str) -> None:
    global graph, _checkpointer_cm, _checkpointer

    if graph is not None:
        return

    _checkpointer_cm = AsyncPostgresSaver.from_conn_string(checkpoint_dsn)
    _checkpointer = await _checkpointer_cm.__aenter__()
    await _checkpointer.setup()
    graph = build_graph(checkpointer=_checkpointer)
    logger.info("LangGraph checkpointer ready.")


async def close_graph() -> None:
    global graph, _checkpointer_cm, _checkpointer

    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)

    graph = None
    _checkpointer = None
    _checkpointer_cm = None


def get_graph():
    if graph is None:
        raise RuntimeError("LangGraph is not initialised. Call init_graph() at startup.")
    return graph
