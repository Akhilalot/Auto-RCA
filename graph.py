# graph.py — AutoRCA Multi-Agent LangGraph Orchestrator

import os
import logging
from typing import Annotated
from typing_extensions import TypedDict, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from langgraph.prebuilt import create_react_agent

from langchain_community.tools.tavily_search import TavilySearchResults

from tools import (
    analyze_cpu_metrics,
    analyze_memory_metrics,
    analyze_latency_metrics,
    analyze_error_rates,
    analyze_active_sessions,
    get_failed_application_logs,
    get_error_log_timeline,
    get_cicd_failures,
    get_deployment_timeline,
    search_resolution_faqs,
    send_email_via_power_platform,
)

# ──────────────── LOGGING ────────────────
logger = logging.getLogger(__name__)

# ──────────────── ENV & LLM ────────────────
load_dotenv()

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.4")

llm = init_chat_model(
    model=LLM_MODEL,
    model_provider="openai",
)


# ──────────────── STATE ────────────────
class AgentState(TypedDict):
    """Shared state that flows between all agents in the graph."""
    issue: str
    time_stamp: str
    output: str
    metrics_report: str
    logs_report: str
    cicd_report: str
    resolution_report: str
    final_report: str
    agents_called: list[str]
    iteration_count: int
    next_agent: str
    commander_reasoning: str
    commander_directive: str


# ──────────────── AGENT DEFINITIONS ────────────────

# ━━━━━━━━━━ 1. COMMANDER AGENT (LLM-based Autonomous Orchestrator) ━━━━━━━━━━

MAX_COMMANDER_ITERATIONS = 10  # Safety: prevent infinite routing loops


class CommanderDecision(BaseModel):
    """Structured output schema for the Commander's routing decision."""
    reasoning: str = Field(
        description="Brief analysis of the current investigation state and why this agent should run next"
    )
    next_agent: Literal["metrics", "logs", "cicd", "resolver", "reporter", "__end__"] = Field(
        description="The next agent to invoke, or __end__ to finish the investigation"
    )
    directive: str = Field(
        description=(
            "Specific instruction for the next agent. On first invocation, use 'initial_analysis'. "
            "On re-invocation, provide a targeted follow-up question or area to investigate "
            "(e.g., 'Focus on the timeout errors between 14:00-14:30 that correlate with the deployment')."
        )
    )


commander_llm = llm.with_structured_output(CommanderDecision)

COMMANDER_SYSTEM_PROMPT = """You are the Commander agent orchestrating an IT incident investigation pipeline.

The initial data-gathering phase (metrics, logs, cicd) has already been executed IN PARALLEL.
You are now being invoked to analyze the results and decide what to do next.

Available agents:
- metrics : Analyzes CPU, memory, latency, error rates, active sessions from telemetry.
- logs    : Analyzes application error logs and correlates with other findings.
- cicd    : Investigates CI/CD pipeline failures, deployments, and rollbacks.
- resolver: Searches internal FAQs and the web for resolution steps.
- reporter: Generates the final HTML incident report and sends email.

Dependency constraints (MUST follow):
- resolver REQUIRES metrics_report AND logs_report AND cicd_report to exist.
- reporter REQUIRES resolution_report to exist.
- reporter should only be called once, as the final step before __end__.

Your autonomy:
- You MAY re-invoke an agent that has already run. This is your key capability.
  Use it when you spot gaps, inconsistencies, or leads in the existing reports that warrant deeper investigation.
  Examples of valid re-invocations:
  * Re-invoke metrics after seeing cicd_report mentions a deploy at 14:05, to check if metrics spiked at that exact time.
  * Re-invoke logs after resolver suggests a specific error class, to search for that pattern specifically.
  * Re-invoke resolver after a re-invoked agent provides new findings.
- You SHOULD NOT re-invoke just to repeat the same work. Only re-invoke with a specific, different directive.
- You MAY skip straight to resolver if the three analysis reports are already sufficient.
- Route to __end__ once reporter has generated the final report.

Directive field:
- On first invocation of resolver/reporter (which haven't run yet), set directive to 'initial_analysis'.
- On re-invocation of any agent, provide a SPECIFIC follow-up question or focus area.
  The agent will receive this directive and tailor its analysis accordingly.

Be decisive. If the reports are sufficient for resolution, move to resolver rather than re-investigating."""


def _truncate(text: str, max_len: int = 500) -> str:
    """Truncate report text for the commander's state summary."""
    if not text:
        return ""
    return text[:max_len] + "..." if len(text) > max_len else text


async def commander_agent(state: AgentState) -> Command[Literal[
    "metrics", "logs", "cicd", "resolver", "reporter", "__end__"
]]:
    """
    The Commander uses an LLM to autonomously decide routing.

    First invocation: fans out to metrics, logs, and cicd in PARALLEL
    (no LLM call needed — always gather all raw data first).

    Subsequent invocations: reads the current reports (truncated) and
    agent history, then decides the next step — including potential
    re-invocations with targeted follow-up directives.
    """
    called = state.get("agents_called", [])
    iteration = state.get("iteration_count", 0)
    logger.info("Commander invoked | iteration=%d | agents_called=%s", iteration, called)

    # Safety: prevent infinite loops
    if iteration >= MAX_COMMANDER_ITERATIONS:
        logger.warning("Commander hit max iterations (%d), forcing __end__", MAX_COMMANDER_ITERATIONS)
        return Command(update={"next_agent": "__end__"}, goto="__end__")

    # ── First invocation: fan out to all three analysis agents in parallel ──
    if iteration == 0:
        logger.info("Commander: first invocation — fanning out to metrics, logs, cicd in parallel")
        return Command(
            update={
                "next_agent": "parallel:metrics,logs,cicd",
                "agents_called": ["metrics", "logs", "cicd"],
                "iteration_count": 1,
                "commander_reasoning": "Initial invocation — launching all three analysis agents in parallel for comprehensive data gathering.",
                "commander_directive": "initial_analysis",
            },
            goto=["metrics", "logs", "cicd"],
        )

    # ── Subsequent invocations: LLM decides next step ──
    # Build state summary with truncated report contents so the LLM can spot gaps
    metrics_report = state.get("metrics_report", "")
    logs_report = state.get("logs_report", "")
    cicd_report = state.get("cicd_report", "")
    resolution_report = state.get("resolution_report", "")
    final_report = state.get("final_report", "")

    state_summary = (
        f"Issue under investigation: {state.get('issue', 'N/A')}\n"
        f"Timestamp: {state.get('time_stamp', 'N/A')}\n\n"
        f"Iteration: {iteration} / {MAX_COMMANDER_ITERATIONS}\n"
        f"Agents called (in order): {called if called else 'None yet'}\n\n"
        f"--- Report Summaries ---\n\n"
        f"metrics_report: {_truncate(metrics_report) if metrics_report else 'NOT YET COLLECTED'}\n\n"
        f"logs_report: {_truncate(logs_report) if logs_report else 'NOT YET COLLECTED'}\n\n"
        f"cicd_report: {_truncate(cicd_report) if cicd_report else 'NOT YET COLLECTED'}\n\n"
        f"resolution_report: {_truncate(resolution_report) if resolution_report else 'NOT YET COLLECTED'}\n\n"
        f"final_report: {'GENERATED — ready to end' if final_report else 'NOT YET GENERATED'}\n"
    )

    decision: CommanderDecision = await commander_llm.ainvoke([  # type: ignore[assignment]
        SystemMessage(content=COMMANDER_SYSTEM_PROMPT),
        HumanMessage(content=state_summary),
    ])

    next_agent = decision.next_agent
    logger.info(
        "Commander decided: %s | directive: %s | reasoning: %s",
        next_agent, decision.directive, decision.reasoning,
    )

    return Command(
        update={
            "next_agent": next_agent,
            "agents_called": called + [next_agent] if next_agent != "__end__" else called,
            "iteration_count": iteration + 1,
            "commander_reasoning": decision.reasoning,
            "commander_directive": decision.directive,
        },
        goto=next_agent,
    )

# ━━━━━━━━━━ 2. METRICS AGENT (ReAct) ━━━━━━━━━━
metrics_tools = [
    analyze_cpu_metrics,
    analyze_memory_metrics,
    analyze_latency_metrics,
    analyze_error_rates,
    analyze_active_sessions,
]
metrics_agent_app = create_react_agent(
    model=llm,
    tools=metrics_tools,
    prompt=(
        "You are a Senior SRE specializing in infrastructure telemetry and metrics analysis.\n\n"
        "Your task:\n"
        "- Analyze system telemetry to detect anomalies, performance degradation, and incident indicators.\n"
        "- Call ALL available metric tools to ensure comprehensive coverage.\n\n"
        "Rules:\n"
        "1. Invoke every tool before drawing conclusions — partial analysis is unacceptable.\n"
        "2. Identify anomaly time windows with precise timestamps.\n"
        "3. Quantify deviations: include baseline vs. peak values.\n"
        "4. Correlate across metrics (e.g., CPU spike + latency spike at the same time).\n\n"
        "Output format:\n"
        "- Start with a severity assessment (Critical / High / Medium / Low).\n"
        "- List each anomaly with: metric name, time range, baseline value, peak value.\n"
        "- End with a correlation summary connecting the anomalies."
    ),
)


async def metrics_agent(state: AgentState) -> Command[Literal["commander"]]:
    """Metrics Agent: analyzes telemetry data for anomalies and performance issues."""
    logger.info("Metrics agent started")
    directive = state.get("commander_directive", "initial_analysis")
    try:
        if directive == "initial_analysis":
            input_message = (
                f"Incident under investigation: {state.get('issue', 'Analyze system health')}\n"
                f"Fetch and analyze ALL available telemetry metrics."
            )
        else:
            input_message = (
                f"Incident under investigation: {state.get('issue', 'Analyze system health')}\n\n"
                f"Previous metrics analysis is available. The Commander has re-invoked you with a specific directive:\n"
                f">>> {directive}\n\n"
                f"Previous metrics report:\n{state.get('metrics_report', 'N/A')}\n\n"
                f"Logs Report (for correlation):\n{state.get('logs_report', 'N/A')}\n\n"
                f"CI/CD Report (for correlation):\n{state.get('cicd_report', 'N/A')}\n\n"
                f"Focus your analysis on the directive above. Use your tools to investigate further."
            )
        response = await metrics_agent_app.ainvoke({"messages": [("user", input_message)]})
        final_output = response["messages"][-1].content
        logger.info("Metrics agent completed | output_length=%d", len(final_output))
    except Exception as exc:
        logger.error("Metrics agent failed: %s", exc)
        final_output = f"[ERROR] Metrics analysis failed: {type(exc).__name__}"

    return Command(update={"metrics_report": final_output}, goto="commander")


# ━━━━━━━━━━ 3. LOGS AGENT (ReAct) ━━━━━━━━━━
logs_tools = [get_failed_application_logs, get_error_log_timeline]
logs_agent_app = create_react_agent(
    model=llm,
    tools=logs_tools,
    prompt=(
        "You are a DevOps Engineer specializing in application log analysis and incident correlation.\n\n"
        "Your task:\n"
        "- Analyze application logs to identify error patterns, failure modes, and incident timelines.\n"
        "- Correlate log evidence with the metrics report provided.\n\n"
        "Rules:\n"
        "1. Invoke all available log analysis tools before concluding.\n"
        "2. Identify exact error types, affected methods/endpoints, and time windows.\n"
        "3. Cross-reference error spikes with the metrics anomalies to strengthen the root cause hypothesis.\n"
        "4. Distinguish between incident-related errors and background noise.\n\n"
        "Output format:\n"
        "- Error summary: types, counts, affected endpoints.\n"
        "- Timeline: when errors started, peaked, and subsided.\n"
        "- Correlation: how log evidence maps to the metrics anomalies."
    ),
)


async def logs_agent(state: AgentState) -> Command[Literal["commander"]]:
    """Logs Agent: analyzes application logs and correlates with metrics findings."""
    logger.info("Logs agent started")
    directive = state.get("commander_directive", "initial_analysis")
    try:
        if directive == "initial_analysis":
            input_message = (
                f"Incident under investigation: {state.get('issue', 'Analyze application logs')}\n\n"
                f"Analyze the application logs using your tools. Identify error patterns, failure modes, and timelines."
            )
        else:
            input_message = (
                f"Incident under investigation: {state.get('issue', 'Analyze application logs')}\n\n"
                f"The Commander has re-invoked you with a specific directive:\n"
                f">>> {directive}\n\n"
                f"Previous logs report:\n{state.get('logs_report', 'N/A')}\n\n"
                f"Metrics Analysis (for correlation):\n{state.get('metrics_report', 'N/A')}\n\n"
                f"CI/CD Report (for correlation):\n{state.get('cicd_report', 'N/A')}\n\n"
                f"Focus your analysis on the directive above. Use your tools to investigate further."
            )
        response = await logs_agent_app.ainvoke({"messages": [("user", input_message)]})
        final_output = response["messages"][-1].content
        logger.info("Logs agent completed | output_length=%d", len(final_output))
    except Exception as exc:
        logger.error("Logs agent failed: %s", exc)
        final_output = f"[ERROR] Log analysis failed: {type(exc).__name__}"

    return Command(update={"logs_report": final_output}, goto="commander")


# ━━━━━━━━━━ 4. CI/CD AGENT (ReAct) ━━━━━━━━━━
cicd_tools = [get_cicd_failures, get_deployment_timeline]
cicd_agent_app = create_react_agent(
    model=llm,
    tools=cicd_tools,
    prompt=(
        "You are a CI/CD Pipeline Analyst investigating whether a code change or deployment caused the incident.\n\n"
        "Your task:\n"
        "- Examine pipeline runs, deployments, and rollbacks to determine if a bad deployment correlates with the incident.\n\n"
        "Rules:\n"
        "1. Invoke all CI/CD tools before drawing conclusions.\n"
        "2. Focus on the timeline: identify deployments that occurred just before the incident window.\n"
        "3. Check for rollbacks — a rollback after the incident confirms a deployment-related root cause.\n"
        "4. Report the exact commit hash and pipeline ID of any suspicious deployment.\n\n"
        "Output format:\n"
        "- Deployment timeline with key events highlighted.\n"
        "- Verdict: whether a deployment is the likely root cause (and which one).\n"
        "- Evidence: timestamps showing deploy -> incident -> rollback correlation."
    ),
)


async def cicd_agent(state: AgentState) -> Command[Literal["commander"]]:
    """CI/CD Agent: investigates pipeline failures and deployment correlation."""
    logger.info("CI/CD agent started")
    directive = state.get("commander_directive", "initial_analysis")
    try:
        if directive == "initial_analysis":
            input_message = (
                f"Incident under investigation: {state.get('issue', 'Analyze CI/CD pipelines')}\n\n"
                f"Analyze the CI/CD pipelines using your tools. Check for failed deployments, rollbacks, and suspicious commits."
            )
        else:
            input_message = (
                f"Incident under investigation: {state.get('issue', 'Analyze CI/CD pipelines')}\n\n"
                f"The Commander has re-invoked you with a specific directive:\n"
                f">>> {directive}\n\n"
                f"Previous CI/CD report:\n{state.get('cicd_report', 'N/A')}\n\n"
                f"Metrics Report (for correlation):\n{state.get('metrics_report', 'N/A')}\n\n"
                f"Logs Report (for correlation):\n{state.get('logs_report', 'N/A')}\n\n"
                f"Focus your analysis on the directive above. Use your tools to investigate further."
            )
        response = await cicd_agent_app.ainvoke({"messages": [("user", input_message)]})
        final_output = response["messages"][-1].content
        logger.info("CI/CD agent completed | output_length=%d", len(final_output))
    except Exception as exc:
        logger.error("CI/CD agent failed: %s", exc)
        final_output = f"[ERROR] CI/CD analysis failed: {type(exc).__name__}"

    return Command(update={"cicd_report": final_output}, goto="commander")


# ━━━━━━━━━━ 5. RESOLVER AGENT (ReAct) ━━━━━━━━━━
tavily_tool = TavilySearchResults(max_results=8)
resolver_tools = [search_resolution_faqs, tavily_tool]
resolver_agent_app = create_react_agent(
    model=llm,
    tools=resolver_tools,
    prompt=(
        "You are an Incident Response Specialist with access to an internal SRE runbook knowledge base "
        "and the Tavily web search tool.\n\n"
        "Your task:\n"
        "- Synthesize findings from metrics, logs, and CI/CD analyses into an actionable mitigation plan.\n"
        "- Search both internal FAQs and the web for resolution guidance.\n\n"
        "Rules:\n"
        "1. You MUST call both search_resolution_faqs and TavilySearchResults before finalizing.\n"
        "2. For FAQ search, use specific keywords from the investigation (e.g., error names, service names).\n"
        "3. Clearly label each recommendation's source: [Internal FAQ] or [Web Research].\n"
        "4. Prioritize immediate mitigation over long-term fixes.\n\n"
        "Output format:\n"
        "- Immediate actions (stop the bleeding): numbered steps.\n"
        "- Root cause fix: the code/config change that resolves the underlying issue.\n"
        "- Prevention: how to avoid recurrence (monitoring, guardrails, etc.)."
    ),
)


async def resolver_agent(state: AgentState) -> Command[Literal["commander"]]:
    """Resolver Agent: searches FAQs and web for resolution steps."""
    logger.info("Resolver agent started")
    directive = state.get("commander_directive", "initial_analysis")
    try:
        if directive == "initial_analysis":
            input_message = (
                f"Investigation Summary:\n\n"
                f"METRICS:\n{state.get('metrics_report', 'N/A')}\n\n"
                f"LOGS:\n{state.get('logs_report', 'N/A')}\n\n"
                f"CI/CD:\n{state.get('cicd_report', 'N/A')}\n\n"
                f"Search the FAQs and web based on these findings and recommend specific resolution steps."
            )
        else:
            input_message = (
                f"The Commander has re-invoked you with a specific directive:\n"
                f">>> {directive}\n\n"
                f"Previous resolution report:\n{state.get('resolution_report', 'N/A')}\n\n"
                f"METRICS:\n{state.get('metrics_report', 'N/A')}\n\n"
                f"LOGS:\n{state.get('logs_report', 'N/A')}\n\n"
                f"CI/CD:\n{state.get('cicd_report', 'N/A')}\n\n"
                f"Refine your resolution recommendations based on the directive above."
            )
        response = await resolver_agent_app.ainvoke({"messages": [("user", input_message)]})
        final_output = response["messages"][-1].content
        logger.info("Resolver agent completed | output_length=%d", len(final_output))
    except Exception as exc:
        logger.error("Resolver agent failed: %s", exc)
        final_output = f"[ERROR] Resolution search failed: {type(exc).__name__}"

    return Command(update={"resolution_report": final_output}, goto="commander")


# ━━━━━━━━━━ 6. REPORTER AGENT (ReAct) ━━━━━━━━━━
reporter_agent_app = create_react_agent(
    model=llm,
    tools=[],
    prompt=(
        "You are a Lead SRE writing the final incident report for stakeholder distribution.\n\n"
        "Your task:\n"
        "- Compile all investigation findings into a professional, well-structured HTML email.\n\n"
        "Rules:\n"
        "1. Output ONLY raw HTML — no markdown fences, no preamble, no commentary outside the HTML.\n"
        "2. Use inline CSS for styling (professional color scheme, proper padding, borders).\n"
        "3. Keep language concise and authoritative — this is read by executives and engineers alike.\n"
        "4. Every claim must be backed by specific data points from the investigation.\n\n"
        "Required sections:\n"
        "- Executive Summary (2-3 sentences: what happened, impact, resolution status)\n"
        "- Root Cause Analysis (the specific deployment/change that caused the issue)\n"
        "- Key Findings (subsections: Metrics, Logs, CI/CD — with specific numbers)\n"
        "- Resolution Steps (numbered, actionable items from the resolver)\n"
        "- Impact Assessment (duration, affected services, user impact)"
    ),
)


async def reporter_agent(state: AgentState) -> Command[Literal["commander", "__end__"]]:
    """Reporter Agent: generates the final HTML incident report and dispatches email."""
    logger.info("Reporter agent started")
    try:
        input_message = (
            f"Original Incident: {state.get('issue', 'System health analysis')}\n"
            f"Timestamp: {state.get('time_stamp', '')}\n\n"
            f"=== METRICS REPORT ===\n{state.get('metrics_report', 'N/A')}\n\n"
            f"=== LOGS REPORT ===\n{state.get('logs_report', 'N/A')}\n\n"
            f"=== CI/CD REPORT ===\n{state.get('cicd_report', 'N/A')}\n\n"
            f"=== RESOLUTION REPORT ===\n{state.get('resolution_report', 'N/A')}"
        )
        response = await reporter_agent_app.ainvoke({"messages": [("user", input_message)]})
        final_output = response["messages"][-1].content
        logger.info("Reporter agent completed | report_length=%d", len(final_output))
    except Exception as exc:
        logger.error("Reporter agent failed: %s", exc)
        final_output = f"<html><body><h2>Report Generation Failed</h2><p>{type(exc).__name__}</p></body></html>"

    # Dispatch email via Power Automate
    try:
        timestamp = state.get("time_stamp", "")
        email_result = await send_email_via_power_platform.ainvoke(
            {"email_html": final_output, "timestamp": timestamp}
        )
        logger.info("Email dispatch result: %s", email_result)
    except Exception as exc:
        logger.error("Email dispatch failed (non-blocking): %s", exc)

    return Command(
        update={"final_report": final_output, "output": final_output},
        goto="__end__",
    )


# ──────────────── BUILD GRAPH ────────────────

builder = StateGraph(AgentState)

# Add all nodes
builder.add_node("commander", commander_agent)
builder.add_node("metrics", metrics_agent)
builder.add_node("logs", logs_agent)
builder.add_node("cicd", cicd_agent)
builder.add_node("resolver", resolver_agent)
builder.add_node("reporter", reporter_agent)

# Entry point: always start at commander
builder.add_edge(START, "commander")

# Compile the graph
graph = builder.compile()
logger.info("LangGraph compiled successfully")
