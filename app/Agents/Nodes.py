from Agents.Prompts import CLASSIFY_PROMPT
from Agents.LLM import llm
from Agents.State import AgentState
import re
from langchain_core.messages import SystemMessage,HumanMessage,AIMessage
from Agents.ToolManager import TOOLS
from langchain_core.agents import create_agent


def classify_node(state: AgentState):
    recent_messages = state["messages"][-4:]
    last_message = recent_messages[-1].content.strip().lower()

    greeting_pattern = (
        r"^(hi|hello|hey|assalamu alaikum|assalamualaikum|salam|"
        r"good morning|good afternoon|good evening)[!. ]*$"
    )

    if re.match(greeting_pattern, last_message):
        return {
            "intent": "greeting",
            "escalated": False,
            "progress": "classified:greeting",
        }

    classifier = llm.with_structured_output({
        "title": "IntentClassification",
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["search", "details", "book", "out_of_scope"],
            }
        },
        "required": ["intent"],
    })

    result = classifier.invoke([
        SystemMessage(content=CLASSIFY_PROMPT),
        *recent_messages,
    ])

    return {
        "intent": result["intent"],
        "escalated": result["intent"] == "out_of_scope",
        "progress": f"classified:{result['intent']}",
    }


def greeting_node(state: AgentState):
    return {
        "messages": [
            AIMessage(
                content=(
                    "Hello! I can help you search properties, show listing details, "
                    "or create a booking."
                )
            )
        ],
        "escalated": False,
        "progress": "completed:greeting",
    }

agent = create_agent(
    model=llm,
    tools=TOOLS,
    system_prompt="""
You are a StayEase guest booking assistant.

You can ONLY help with:

1. Search
- Search available properties by location, dates, and number of guests.

2. Details
- Give details about a specific listing.

3. Book
- Create a booking after the guest clearly confirms.

Rules:
- Do not answer questions outside search, details, or booking.
- If information is missing, ask the guest for the missing fields.
- Use tools when needed.
- Never create a booking unless the guest clearly confirms.
"""
)


def agent_node(state: AgentState):
    try:
        recent_messages = state["messages"][-10:]

        result = agent.invoke({
            "messages": recent_messages
        })

        return {
            "messages": result["messages"],
            "escalated": False,
            "progress": f"completed:{state['intent']}",
        }

    except Exception:
        return {
            "messages": [
                AIMessage(
                    content=(
                        "Sorry, something went wrong while handling your request. "
                        "I’m routing this to human support."
                    )
                )
            ],
            "intent": "out_of_scope",
            "escalated": True,
            "progress": "failed:agent_node",
        }


def escalation_node(state: AgentState):
    return {
        "messages": [
            AIMessage(
                content="This request is outside my booking assistant scope. I’m routing this to human support."
            )
        ],
        "escalated": True,
    }
