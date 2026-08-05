import logging
from typing import Literal

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import BaseModel

from app.agents.knowledge_agent.agent import knowledge_agent_node
from app.agents.main_agent.state import AgentState
from app.agents.sql_agent.agents import sql_agent_node
from app.agents.visualizer.agent import visualizer_node
from app.core.llm import get_llm
from app.core.logging import log_duration
from app.prompts.main_agent import RESPOND_PROMPT, SYSTEM_PROMPT

MAX_ATTEMPTS = 3

ROUTES = Literal["sql_agent", "knowledge_agent", "visualizer", "respond"]


class Decision(BaseModel):
    resolved: Literal["yes", "no"]
    next: ROUTES
    refined_query: str
    reasoning: str


def _decision_context(state: AgentState) -> list[AnyMessage]:
    context = [SystemMessage(content=SYSTEM_PROMPT), *state["chat_history"], HumanMessage(content=state["question"])]
    if state.get("agent_output") is not None:
        context.append(AIMessage(content=f"[{state['next']} responded]: {state['agent_output']}"))
    return context


def supervisor_node(
    state: AgentState,
) -> Command[Literal["sql_agent", "knowledge_agent", "visualizer", "responder", "finalize"]]:
    attempted = state.get("agent_output") is not None

    if attempted and state.get("attempts", 0) >= MAX_ATTEMPTS:
        return Command(
            goto="finalize", update={"final_answer": state["agent_output"], "final_sql": state.get("agent_sql")}
        )

    with log_duration("Routing decision"):
        decision = get_llm("main_agent").with_structured_output(Decision).invoke(_decision_context(state))

    if attempted and decision.resolved == "yes":
        return Command(
            goto="finalize", update={"final_answer": state["agent_output"], "final_sql": state.get("agent_sql")}
        )

    goto = "responder" if decision.next == "respond" else decision.next
    return Command(
        goto=goto,
        update={
            "next": decision.next,
            "refined_query": decision.refined_query,
            "attempts": state.get("attempts", 0) + 1,
        },
    )


def responder_node(state: AgentState) -> dict:
    llm = get_llm("main_agent")
    with log_duration("Direct response"):
        response = llm.invoke([SystemMessage(content=RESPOND_PROMPT), *state["chat_history"], HumanMessage(content=state["question"])])
    return {"final_answer": response.content}


def finalize_node(state: AgentState) -> dict:
    logging.info("Final answer: %s", state["final_answer"])
    return {"chat_history": [HumanMessage(content=state["question"]), AIMessage(content=state["final_answer"])]}


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("sql_agent", sql_agent_node)
    graph.add_node("knowledge_agent", knowledge_agent_node)
    graph.add_node("visualizer", visualizer_node)
    graph.add_node("responder", responder_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "supervisor")
    graph.add_edge("sql_agent", "supervisor")
    graph.add_edge("knowledge_agent", "supervisor")
    graph.add_edge("visualizer", "supervisor")
    graph.add_edge("responder", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


graph = build_graph()
