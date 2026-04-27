from Agents.Nodes import *
from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from ..config import settings


checkpointer = PostgresSaver.from_conn_string(settings.DATABASE_URL)
checkpointer.setup()

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

graph = builder.compile(checkpointer=checkpointer)