"""
LangGraph QA pipeline graph.
Nodes: change_analyzer → test_runner → browser_inspector → classifier
       → [auto_fixer | ticket_creator] → reporter
"""
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from app.agents.state import QAState
from app.agents.nodes import (
    change_analyzer_node,
    test_runner_node,
    browser_inspector_node,
    classifier_node,
    auto_fixer_node,
    ticket_creator_node,
    reporter_node,
)


def _route_after_classifier(state: QAState) -> str:
    classification = state.get("classification", "env")
    if classification == "drift":
        return "auto_fixer"
    return "ticket_creator"


def build_graph() -> StateGraph:
    builder = StateGraph(QAState)

    builder.add_node("change_analyzer",   change_analyzer_node)
    builder.add_node("test_runner",        test_runner_node)
    builder.add_node("browser_inspector",  browser_inspector_node)
    builder.add_node("classifier",         classifier_node)
    builder.add_node("auto_fixer",         auto_fixer_node)
    builder.add_node("ticket_creator",     ticket_creator_node)
    builder.add_node("reporter",           reporter_node)

    builder.set_entry_point("change_analyzer")
    builder.add_edge("change_analyzer",  "test_runner")
    builder.add_edge("test_runner",      "browser_inspector")
    builder.add_edge("browser_inspector","classifier")
    builder.add_conditional_edges(
        "classifier",
        _route_after_classifier,
        {"auto_fixer": "auto_fixer", "ticket_creator": "ticket_creator"},
    )
    builder.add_edge("auto_fixer",    "reporter")
    builder.add_edge("ticket_creator","reporter")
    builder.add_edge("reporter",       END)

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


# Singleton graph instance
_graph = build_graph()


def get_graph():
    return _graph
