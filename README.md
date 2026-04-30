# AgenticOps

**Autonomous AI agents that investigate production incidents so you don't have to.**

---

## What is this?

AgenticOps is a multi-agent system that acts like a tireless on-call engineer. You give it an incident description (e.g. "checkout latency spiked at 2pm"), and it autonomously:

1. **Analyzes telemetry** — CPU, memory, latency, error rates, active sessions
2. **Digs through application logs** — finds error bursts, stack traces, and patterns
3. **Checks CI/CD pipelines** — looks for bad deploys or failed stages that correlate with the incident
4. **Finds a resolution** — searches an internal FAQ knowledge base (backed by ChromaDB vector search) and the web
5. **Generates a full incident report** — formatted HTML sent via Power Automate email

All of this happens without human intervention. A "Commander" agent orchestrates the whole thing — it can re-invoke agents with follow-up questions if it spots gaps or leads in their initial reports.

---

## How it works

Built with **LangGraph** + **LangChain** + **FastAPI**. The Commander uses structured LLM output to decide which agent goes next, making the pipeline genuinely autonomous rather than a fixed sequence.

See [WORKFLOW.md](WORKFLOW.md) for the full architecture diagram, agent details, and state schema.

---

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Set your env vars (.env file)
# OPENAI_API_KEY, TAVILY_API_KEY, POWER_AUTOMATE_URL, etc.

# Run the server
python main.py
```

Then POST to `/run-agent` with an issue description and timestamp.

---

## About the mock data

**All data in this repo is synthetically generated to simulate a realistic system failure.** Nothing here comes from a real production environment.

The generators in `mock_data_generators/` create:

- **Application logs** (~1000 entries) — with an injected 45-minute "incident window" of elevated errors and exceptions
- **Telemetry logs** (~800 entries) — CPU spikes, memory pressure, latency surges, and error rate increases during that same window
- **CI/CD logs** (~50 entries) — including a failed production deployment that lines up with the incident timeline
- **Resolution FAQs** — a curated knowledge base with a "golden" FAQ that matches the simulated incident perfectly

The goal is to give the agents a realistic scenario to investigate end-to-end: a bad deploy causes payment gateway timeouts, and the system figures that out on its own.

---

## Project structure

```
graph.py                  — LangGraph agent orchestration (Commander + ReAct agents)
tools.py                  — All agent tools (metrics analysis, log parsing, FAQ search, etc.)
main.py                   — FastAPI server exposing /run-agent endpoint
mock_data_generators/     — Scripts that produce the synthetic incident data
vector_database/          — ChromaDB ingestion pipeline for FAQ embeddings
FAQs/                     — Generated resolution knowledge base
logs/                     — Generated application, telemetry, and CI/CD logs
```

---

## Tech stack

- LangGraph & LangChain (agent orchestration)
- OpenAI GPT (LLM backbone)
- ChromaDB (vector search for FAQ resolution)
- Tavily (web search fallback)
- FastAPI (API layer)
- Power Automate (email notifications)
