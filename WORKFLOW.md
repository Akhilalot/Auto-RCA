# AutoRCA — Agent Workflow

## Overview

AutoRCA is a multi-agent AI system for IT Operations that autonomously detects anomalies, investigates incidents across logs and metrics, correlates events, and recommends resolutions. It is built with **LangGraph**, **LangChain**, and **FastAPI**, and uses a **ChromaDB** vector store for FAQ-based resolution lookups.

---

## Architecture Diagram

```
                         ┌──────────────┐
                         │   Client     │
                         │  (POST /run  │
                         │   -agent)    │
                         └──────┬───────┘
                                │
                                ▼
                         ┌──────────────┐
                         │   FastAPI    │
                         │   main.py    │
                         └──────┬───────┘
                                │
                                ▼
                    ┌───────────────────────┐
                    │    LangGraph Engine    │
                    │      (graph.py)       │
                    └───────────┬───────────┘
                                │
                                ▼
              ┌─────────────────────────────────────┐
              │          COMMANDER AGENT             │
              │  (LLM-based Autonomous Orchestrator) │
              │                                     │
              │  Uses structured LLM output to       │
              │  decide next agent based on state.   │
              │  Can reorder, skip, or prioritize.   │
              └──────────────┬──────────────────────┘
                             │
           ┌─────────────────┼─────────────────┐
           │                 │                 │
           ▼                 ▼                 ▼
   ┌──────────────┐  ┌─────────────┐  ┌──────────────┐
   │   METRICS    │  │    LOGS     │  │    CI/CD     │
   │    AGENT     │  │    AGENT    │  │    AGENT     │
   │  (ReAct)     │  │  (ReAct)   │  │  (ReAct)     │
   └──────┬───────┘  └─────┬──────┘  └──────┬───────┘
          │                 │                 │
          │    ◄── Each returns to Commander ──►
          │                 │                 │
          └────────┬────────┘─────────┬───────┘
                   │                  │
                   ▼                  ▼
          ┌──────────────┐   ┌───────────────┐
          │   RESOLVER   │   │   REPORTER    │
          │    AGENT     │   │    AGENT      │
          │  (ReAct)     │   │  (ReAct)      │
          └──────────────┘   └───────┬───────┘
                                     │
                                     ▼
                             ┌───────────────┐
                             │  Power Auto-  │
                             │  mate Email   │
                             └───────────────┘
```

---

## Pipeline Sequence

The Commander agent operates in two phases:

1. **Phase 1 — Parallel Data Gathering:** On first invocation, the Commander deterministically fans out to **metrics, logs, and cicd agents in parallel** (no LLM call). All three run concurrently, each writing to its own state key. When all three complete, their updates merge and the Commander is invoked again.

2. **Phase 2 — Autonomous Orchestration:** The Commander uses an **LLM with structured output** to analyze the collected reports and decide the next step — including **re-invoking agents with targeted follow-up directives** when it spots gaps or leads.

### Standard Flow (no re-invocations needed)
```
START → Commander ──┬── Metrics ──┐
                    ├── Logs    ──┤ (parallel)
                    └── CI/CD   ──┘
                          │
                    Commander (LLM) → Resolver → Commander
                    → Reporter → Commander → END
```

### Re-invocation Flow (commander spots a lead)
```
START → Commander ──┬── Metrics ──┐
                    ├── Logs    ──┤ (parallel)
                    └── CI/CD   ──┘
                          │
                    Commander (LLM)
                    → Metrics (re-invoked: "Check if CPU spike at 14:05 matches deploy")
                    → Commander → Resolver → Commander
                    → Reporter → Commander → END
```

### Minimal Flow (no deployment relevance, no re-invocations)
```
START → Commander ──┬── Metrics ──┐
                    ├── Logs    ──┤ (parallel)
                    └── CI/CD   ──┘
                          │
                    Commander (LLM) → Resolver → Commander
                    → Reporter → Commander → END
```

| Agent            | Purpose                                       | Returns to   |
|------------------|-----------------------------------------------|--------------|
| **Commander**    | Parallel fan-out (1st) then LLM-based routing (2nd+) | Next agent(s) |
| **Metrics**      | Analyzes telemetry data for anomalies          | Commander    |
| **Logs**         | Analyzes application error/warning logs        | Commander    |
| **CI/CD**        | Investigates pipeline failures & deployments   | Commander    |
| **Resolver**     | Searches FAQs + web for mitigation steps       | Commander    |
| **Reporter**     | Generates final HTML incident report & emails  | Commander    |

### Commander Autonomy & Constraints

| The Commander CAN                                          | The Commander CANNOT                                |
|------------------------------------------------------------|-----------------------------------------------------|
| Re-invoke an agent with a targeted follow-up directive     | Call resolver before metrics + logs reports exist    |
| Skip `cicd` if evidence rules out deployment causes        | Call reporter before resolver has run                |
| Spot gaps/leads in reports and send agents back to dig deeper | Exceed max iterations (safety cap = 10)           |
| Route to `__end__` once final report is generated          | Re-invoke without a specific, different directive    |

---

## Shared State

All agents read from and write to a shared `AgentState` dictionary:

| Field              | Type         | Written By      | Description                                |
|--------------------|--------------|-----------------|--------------------------------------------|
| `issue`            | `str`        | Client request  | User's original issue description          |
| `time_stamp`       | `str`        | Client request  | Timestamp from the request body            |
| `output`           | `str`        | Reporter        | Final output sent back to the client       |
| `metrics_report`   | `str`        | Metrics Agent   | Telemetry analysis findings                |
| `logs_report`      | `str`        | Logs Agent      | Application log analysis findings          |
| `cicd_report`      | `str`        | CI/CD Agent     | Pipeline & deployment analysis findings    |
| `resolution_report`| `str`        | Resolver Agent  | Recommended mitigation steps               |
| `final_report`     | `str`        | Reporter Agent  | Final formatted HTML report                |
| `agents_called`    | `list[str]`  | Commander       | Tracks agent invocation history (may contain duplicates on re-invoke) |
| `iteration_count`  | `int`        | Commander       | Number of commander iterations (for safety cap)    |
| `next_agent`       | `str`        | Commander       | The next agent to be invoked               |
| `commander_reasoning` | `str`     | Commander       | LLM's reasoning for its latest routing decision |
| `commander_directive` | `str`     | Commander       | Targeted instruction for the next agent ("initial_analysis" or follow-up) |

---

## Agent Details

### 1. Commander Agent

- **Type:** Hybrid — deterministic parallel fan-out (1st invocation) + LLM-based autonomous orchestrator (subsequent)
- **Phase 1 (iteration 0):** Deterministically fans out to `metrics`, `logs`, and `cicd` in **parallel** using `Command(goto=["metrics", "logs", "cicd"])`. No LLM call. All three agents run concurrently, each writing to its own state key. When all complete, their updates merge and the commander is invoked again.
- **Phase 2 (iteration 1+):** Uses an LLM call with `with_structured_output(CommanderDecision)` to analyze the collected reports and decide the next agent + directive.
- **Input (Phase 2):** A state summary including: issue, agent call history, and **truncated report contents** (500 chars each) so the commander can spot gaps and leads.
- **Output Schema:** `CommanderDecision { reasoning: str, next_agent: Literal[...], directive: str }`
- **Key capability — Re-invocation:** The commander MAY call an agent that has already run, providing a specific `directive` that tells the agent what to focus on differently. On re-invocation, the directive is a targeted follow-up (e.g., `"Check for NullPointerException between 14:00-14:30 on payment-service"`).
- **Dependency constraints (enforced via system prompt):**
  - `resolver` requires `metrics_report`, `logs_report`, and `cicd_report` to exist
  - `reporter` requires `resolution_report` to exist
  - `reporter` should only be called once, as the final step
- **Safety:** Hard cap of 10 commander iterations to prevent infinite loops
- **State routing:** Uses `Command(goto=...)` — no `messages` field needed; routes entirely on structured state variables. Supports both single goto (string) and parallel fan-out (list).

### 2. Metrics Agent (ReAct)

- **LLM Prompt Role:** Expert SRE specializing in telemetry and metrics analysis
- **Tools:**
  | Tool                       | Description                                              |
  |----------------------------|----------------------------------------------------------|
  | `analyze_cpu_metrics`      | Analyzes CPU utilization; detects spikes > 80%           |
  | `analyze_memory_metrics`   | Analyzes memory usage; detects readings > 1500 MB        |
  | `analyze_latency_metrics`  | Analyzes payment gateway (> 1000 ms) & DB (> 200 ms) latency |
  | `analyze_error_rates`      | Analyzes checkout error rates; flags > 5%                |
  | `analyze_active_sessions`  | Analyzes concurrent sessions; flags > 1000               |
- **Data Source:** `logs/telemetry_logs.json`
- **Output:** `metrics_report` — a structured anomaly summary with statistics

### 3. Logs Agent (ReAct)

- **LLM Prompt Role:** DevOps engineer analyzing application logs
- **Input Context:** Receives `metrics_report` for cross-correlation
- **Tools:**
  | Tool                        | Description                                        |
  |-----------------------------|----------------------------------------------------|
  | `get_failed_application_logs` | Retrieves ERROR/WARN logs grouped by method       |
  | `get_error_log_timeline`     | Hourly error distribution to identify burst windows |
- **Data Source:** `logs/application_logs.json`
- **Output:** `logs_report` — error patterns, stack traces, and correlation with metrics

### 4. CI/CD Agent (ReAct)

- **LLM Prompt Role:** CI/CD pipeline analyst
- **Input Context:** Receives `metrics_report` and `logs_report`
- **Tools:**
  | Tool                      | Description                                            |
  |---------------------------|--------------------------------------------------------|
  | `get_cicd_failures`       | Retrieves failed pipeline runs, rollbacks, success rate |
  | `get_deployment_timeline` | Timeline of production/staging deploys and rollbacks    |
- **Data Source:** `logs/cicd_logs.json`
- **Output:** `cicd_report` — deployment IDs/commits that may have caused the incident

### 5. Resolver Agent (ReAct)

- **LLM Prompt Role:** Incident response specialist
- **Input Context:** Receives `metrics_report`, `logs_report`, and `cicd_report`
- **Tools:**
  | Tool                    | Description                                              |
  |-------------------------|----------------------------------------------------------|
  | `search_resolution_faqs`| Semantic search over ChromaDB SRE runbook vector store   |
  | `TavilySearchResults`   | Web search for additional context and best practices     |
- **Data Sources:** ChromaDB vector database (`vector_database/chroma_db`), Tavily web search
- **Output:** `resolution_report` — step-by-step mitigation plan combining internal FAQs and web research

### 6. Reporter Agent (ReAct)

- **LLM Prompt Role:** Lead SRE formatting a final incident report
- **Input Context:** All prior reports (`metrics`, `logs`, `cicd`, `resolution`) plus original issue and timestamp
- **Tools:** None (generation-only)
- **Output:** `final_report` — well-structured HTML email with sections:
  - Executive Summary
  - Root Cause Analysis
  - Key Findings (Metrics, Logs, CI/CD)
  - Actionable Resolution Steps
  - Impact
- **Side Effect:** Triggers `send_email_via_power_platform` to dispatch the HTML report as an email via Power Automate

---

## Data Flow

```
telemetry_logs.json ──► Metrics Agent ──► metrics_report
                                              │
application_logs.json ──► Logs Agent ◄────────┘
                              │
                              ├──► logs_report
                              │         │
cicd_logs.json ──► CI/CD Agent ◄────────┘
                       │
                       ├──► cicd_report
                       │         │
ChromaDB ──► Resolver Agent ◄───┘
Tavily Web       │
                 ├──► resolution_report
                 │         │
           Reporter Agent ◄┘
                 │
                 ├──► final_report (HTML)
                 │
                 └──► Power Automate (Email)
```

---

## API Endpoints

| Method | Path          | Description                              |
|--------|---------------|------------------------------------------|
| GET    | `/`           | Service info and available endpoints      |
| GET    | `/health`     | Health check; lists all agent names       |
| POST   | `/run-agent`  | Triggers the full multi-agent pipeline    |

### POST `/run-agent` Request Body

```json
{
  "time_stamp": "2026-03-25T10:30:00Z",
  "issue": "High API latency detected on the payment service..."
}
```

### POST `/run-agent` Response

```json
{
  "status": "success",
  "issue": "...",
  "time_stamp": "...",
  "response": "<html>...final report...</html>",
  "agents_called": ["metrics", "logs", "cicd", "resolver", "reporter"],
  "reports": {
    "metrics": "...",
    "logs": "...",
    "cicd": "...",
    "resolution": "...",
    "final": "..."
  }
}
```

---

## Vector Database (ChromaDB)

The Resolver Agent queries a pre-built ChromaDB collection of SRE runbook FAQs.

- **Collection:** `sre_runbooks`
- **Embedding Model:** `text-embedding-3-large` (OpenAI)
- **Similarity Metric:** Cosine
- **Source Data:** `FAQs/resolution_faqs.json`
- **Ingestion Script:** `vector_database/ingestion_pipeline.py`

Each FAQ document is indexed as:
```
Category: {category}
Question: {question}
Resolution: {answer}
```

---

## Tech Stack

| Component        | Technology                              |
|------------------|-----------------------------------------|
| Orchestration    | LangGraph (StateGraph)                  |
| Agent Framework  | LangChain ReAct agents                  |
| LLM              | OpenAI GPT-5.4                          |
| API Server       | FastAPI + Uvicorn                       |
| Vector Database  | ChromaDB (persistent, cosine similarity)|
| Embeddings       | OpenAI `text-embedding-3-large`         |
| Web Search       | Tavily Search API                       |
| Email Dispatch   | Microsoft Power Automate                |
| Configuration    | python-dotenv                           |
