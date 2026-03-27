import asyncio
import json
import logging
import os

import aiofiles
import chromadb
import httpx
from chromadb.utils import embedding_functions
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# ──────────────── Paths & Config ────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TELEMETRY_PATH = os.path.join(BASE_DIR, "logs", "telemetry_logs.json")
APP_LOGS_PATH = os.path.join(BASE_DIR, "logs", "application_logs.json")
CICD_LOGS_PATH = os.path.join(BASE_DIR, "logs", "cicd_logs.json")
CHROMA_DB_PATH = os.path.join(BASE_DIR, "vector_database", "chroma_db")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "sre_runbooks")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
POWER_AUTOMATE_WEBHOOK_URL = os.getenv("POWER_AUTOMATE_WEBHOOK_URL", "")


# ──────────────── Helpers ────────────────
async def _load_json(path: str) -> list:
    """Load a JSON file asynchronously and return the parsed list."""
    try:
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        return json.loads(content)
    except FileNotFoundError:
        logger.error("File not found: %s", path)
        return []
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in %s: %s", path, exc)
        return []


async def _telemetry_by_metric(metric_name: str) -> list[dict]:
    """Filter telemetry logs for a specific metric."""
    data = await _load_json(TELEMETRY_PATH)
    return [entry for entry in data if entry.get("metric") == metric_name]


def _stats(values: list[float]) -> dict:
    """Compute basic statistics for a list of values."""
    if not values:
        return {"count": 0, "min": 0, "max": 0, "avg": 0}
    return {
        "count": len(values),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "avg": round(sum(values) / len(values), 2),
    }


# ═══════════════ METRICS TOOLS ═══════════════

@tool
async def analyze_cpu_metrics() -> str:
    """Analyze CPU utilization metrics from telemetry logs.
    Detects spikes above 80 percent and returns statistics with anomalous entries."""
    logger.info("Tool called: analyze_cpu_metrics")
    entries = await _telemetry_by_metric("cpu_utilization_percent")
    values = [e["value"] for e in entries]
    stats = _stats(values)

    spikes = [e for e in entries if e["value"] > 80]
    spike_summary = ""
    if spikes:
        spike_summary = f"\n\n[ALERT] {len(spikes)} CPU spike(s) detected (>80%):\n"
        for s in spikes[:20]:
            spike_summary += f"  - {s['timestamp']}: {s['value']}% on {s['service']}\n"
    else:
        spike_summary = "\n\n[OK] No CPU spikes detected (all readings below 80%)."

    logger.info("analyze_cpu_metrics: %d points, %d spikes", stats["count"], len(spikes))
    return (
        f"CPU Utilization Analysis ({stats['count']} data points)\n"
        f"  Min: {stats['min']}%  |  Max: {stats['max']}%  |  Avg: {stats['avg']}%"
        f"{spike_summary}"
    )


@tool
async def analyze_memory_metrics() -> str:
    """Analyze memory usage metrics from telemetry logs.
    Detects high usage above 1500MB and returns statistics with anomalous entries."""
    logger.info("Tool called: analyze_memory_metrics")
    entries = await _telemetry_by_metric("memory_usage_mb")
    values = [e["value"] for e in entries]
    stats = _stats(values)

    high_mem = [e for e in entries if e["value"] > 1500]
    alert = ""
    if high_mem:
        alert = f"\n\n[ALERT] {len(high_mem)} high memory reading(s) detected (>1500MB):\n"
        for h in high_mem[:20]:
            alert += f"  - {h['timestamp']}: {h['value']}MB on {h['service']}\n"
    else:
        alert = "\n\n[OK] Memory usage within normal range (all below 1500MB)."

    logger.info("analyze_memory_metrics: %d points, %d anomalies", stats["count"], len(high_mem))
    return (
        f"Memory Usage Analysis ({stats['count']} data points)\n"
        f"  Min: {stats['min']}MB  |  Max: {stats['max']}MB  |  Avg: {stats['avg']}MB"
        f"{alert}"
    )


@tool
async def analyze_latency_metrics() -> str:
    """Analyze payment gateway and DB query latency from telemetry logs.
    Detects payment latency spikes (>1000ms) and DB latency spikes (>200ms)."""
    logger.info("Tool called: analyze_latency_metrics")
    # Payment gateway latency
    pg_entries = await _telemetry_by_metric("payment_gateway_latency_ms")
    pg_values = [e["value"] for e in pg_entries]
    pg_stats = _stats(pg_values)

    pg_spikes = [e for e in pg_entries if e["value"] > 1000]
    pg_alert = ""
    if pg_spikes:
        pg_alert = f"\n  [ALERT] {len(pg_spikes)} payment latency spike(s) (>1000ms):\n"
        for s in pg_spikes[:15]:
            pg_alert += f"    - {s['timestamp']}: {s['value']}ms\n"
    else:
        pg_alert = "\n  [OK] Payment gateway latency normal."

    # DB query latency
    db_entries = await _telemetry_by_metric("db_query_latency_ms")
    db_values = [e["value"] for e in db_entries]
    db_stats = _stats(db_values)

    db_spikes = [e for e in db_entries if e["value"] > 200]
    db_alert = ""
    if db_spikes:
        db_alert = f"\n  [ALERT] {len(db_spikes)} DB latency spike(s) (>200ms):\n"
        for s in db_spikes[:15]:
            db_alert += f"    - {s['timestamp']}: {s['value']}ms\n"
    else:
        db_alert = "\n  [OK] DB query latency normal."

    logger.info(
        "analyze_latency_metrics: pg=%d spikes, db=%d spikes",
        len(pg_spikes), len(db_spikes),
    )
    return (
        f"Latency Analysis\n"
        f"Payment Gateway ({pg_stats['count']} points): "
        f"Min={pg_stats['min']}ms | Max={pg_stats['max']}ms | Avg={pg_stats['avg']}ms"
        f"{pg_alert}\n"
        f"DB Query ({db_stats['count']} points): "
        f"Min={db_stats['min']}ms | Max={db_stats['max']}ms | Avg={db_stats['avg']}ms"
        f"{db_alert}"
    )


@tool
async def analyze_error_rates() -> str:
    """Analyze checkout error rate metrics from telemetry logs.
    Detects elevated error rates above 5 percent."""
    logger.info("Tool called: analyze_error_rates")
    entries = await _telemetry_by_metric("checkout_error_rate_percent")
    values = [e["value"] for e in entries]
    stats = _stats(values)

    elevated = [e for e in entries if e["value"] > 5]
    alert = ""
    if elevated:
        alert = f"\n\n[ALERT] {len(elevated)} elevated error rate reading(s) (>5%):\n"
        for e in elevated[:20]:
            alert += f"  - {e['timestamp']}: {e['value']}% on {e['service']}\n"
    else:
        alert = "\n\n[OK] Error rates within normal range (all below 5%)."

    logger.info("analyze_error_rates: %d points, %d elevated", stats["count"], len(elevated))
    return (
        f"Error Rate Analysis ({stats['count']} data points)\n"
        f"  Min: {stats['min']}%  |  Max: {stats['max']}%  |  Avg: {stats['avg']}%"
        f"{alert}"
    )


@tool
async def analyze_active_sessions() -> str:
    """Analyze active checkout sessions from telemetry logs.
    Detects session count spikes above 1000."""
    logger.info("Tool called: analyze_active_sessions")
    entries = await _telemetry_by_metric("active_checkout_sessions")
    values = [e["value"] for e in entries]
    stats = _stats(values)

    spikes = [e for e in entries if e["value"] > 1000]
    alert = ""
    if spikes:
        alert = f"\n\n[ALERT] {len(spikes)} session spike(s) (>1000 concurrent):\n"
        for s in spikes[:20]:
            alert += f"  - {s['timestamp']}: {s['value']} sessions on {s['service']}\n"
    else:
        alert = "\n\n[OK] Active sessions within normal range (all below 1000)."

    logger.info("analyze_active_sessions: %d points, %d spikes", stats["count"], len(spikes))
    return (
        f"Active Sessions Analysis ({stats['count']} data points)\n"
        f"  Min: {stats['min']}  |  Max: {stats['max']}  |  Avg: {stats['avg']}"
        f"{alert}"
    )


# ═══════════════ LOGS TOOLS ═══════════════

@tool
async def get_failed_application_logs() -> str:
    """Retrieve ERROR and WARN level application logs.
    Returns a summary of failures grouped by method and error type."""
    logger.info("Tool called: get_failed_application_logs")
    data = await _load_json(APP_LOGS_PATH)

    errors = [e for e in data if e.get("level") == "ERROR"]
    warnings = [e for e in data if e.get("level") == "WARN"]

    error_by_method: dict[str, list] = {}
    for e in errors:
        method = e.get("method", "unknown")
        error_by_method.setdefault(method, []).append(e)

    result = "Application Log Analysis\n"
    result += f"  Total ERROR logs: {len(errors)}\n"
    result += f"  Total WARN logs: {len(warnings)}\n\n"

    if errors:
        result += "ERROR breakdown by method:\n"
        for method, errs in error_by_method.items():
            result += f"\n  [FAIL] {method} ({len(errs)} errors):\n"
            unique_msgs = set(e["message"] for e in errs)
            for msg in unique_msgs:
                count = sum(1 for e in errs if e["message"] == msg)
                result += f"    - [{count}x] {msg}\n"
            times = [e["timestamp"] for e in errs]
            result += f"    Time range: {min(times)} -> {max(times)}\n"

    if warnings:
        result += f"\nWARNING logs ({len(warnings)} total):\n"
        warn_methods = set(w["method"] for w in warnings)
        for method in warn_methods:
            count = sum(1 for w in warnings if w["method"] == method)
            result += f"  [WARN] {method}: {count} warning(s)\n"

    if not errors and not warnings:
        result += "[OK] No errors or warnings found in application logs."

    logger.info("get_failed_application_logs: %d errors, %d warnings", len(errors), len(warnings))
    return result


@tool
async def get_error_log_timeline() -> str:
    """Get a timeline of ERROR logs to identify incident windows.
    Groups errors into time windows to detect bursts."""
    logger.info("Tool called: get_error_log_timeline")
    data = await _load_json(APP_LOGS_PATH)
    errors = [e for e in data if e.get("level") == "ERROR"]

    if not errors:
        return "[OK] No ERROR logs found."

    hourly: dict[str, int] = {}
    for e in errors:
        hour = e["timestamp"][:13]
        hourly[hour] = hourly.get(hour, 0) + 1

    result = "Error Timeline (hourly distribution):\n"
    for hour in sorted(hourly.keys()):
        bar = "█" * hourly[hour]
        result += f"  {hour}:00Z  | {hourly[hour]:3d} errors | {bar}\n"

    peak_hour = max(hourly, key=lambda h: hourly[h])
    result += f"\n[ALERT] Peak error hour: {peak_hour}:00Z with {hourly[peak_hour]} errors"

    logger.info("get_error_log_timeline: %d total errors, peak=%s", len(errors), peak_hour)
    return result


# ═══════════════ CI/CD TOOLS ═══════════════

@tool
async def get_cicd_failures() -> str:
    """Retrieve failed CI/CD pipeline runs.
    Returns details of failed builds, tests, deployments, and security scans."""
    logger.info("Tool called: get_cicd_failures")
    data = await _load_json(CICD_LOGS_PATH)

    failures = [e for e in data if e.get("status") == "FAILED"]
    all_runs = len(data)

    result = f"CI/CD Pipeline Analysis ({all_runs} total runs)\n"
    if all_runs > 0:
        result += f"  Failed: {len(failures)}  |  Success rate: {round((all_runs - len(failures)) / all_runs * 100, 1)}%\n\n"
    else:
        result += "  No pipeline runs found.\n\n"

    if failures:
        result += "Failed pipelines:\n"
        for f in failures:
            result += (
                f"  [FAIL] Pipeline {f['pipeline_id']} | Stage: {f['stage']} | "
                f"Commit: {f['commit_hash']} | Duration: {f['duration_sec']}s | "
                f"Time: {f['timestamp']}\n"
            )
    else:
        result += "[OK] All pipelines succeeded."

    rollbacks = [e for e in data if e.get("stage") == "rollback_production"]
    if rollbacks:
        result += f"\n\n[WARN] Production rollbacks detected ({len(rollbacks)}):\n"
        for r in rollbacks:
            result += f"  [ROLLBACK] {r['timestamp']} | Commit: {r['commit_hash']} | Status: {r['status']}\n"

    logger.info("get_cicd_failures: %d/%d failed, %d rollbacks", len(failures), all_runs, len(rollbacks))
    return result


@tool
async def get_deployment_timeline() -> str:
    """Get a timeline of production deployments and rollbacks
    to identify which deployments might have caused incidents."""
    logger.info("Tool called: get_deployment_timeline")
    data = await _load_json(CICD_LOGS_PATH)

    deploys = [
        e for e in data
        if e.get("stage") in ("deploy_production", "deploy_staging", "rollback_production")
    ]

    if not deploys:
        return "No deployment events found."

    result = "Deployment Timeline:\n"
    for d in sorted(deploys, key=lambda x: x["timestamp"]):
        icon = "[DEPLOY]" if "deploy" in d["stage"] else "[ROLLBACK]"
        status_icon = "[OK]" if d["status"] == "SUCCESS" else "[FAIL]"
        result += (
            f"  {icon} {d['timestamp']} | {d['stage']} | "
            f"Commit: {d['commit_hash']} | {status_icon} {d['status']}\n"
        )

    logger.info("get_deployment_timeline: %d deployment events", len(deploys))
    return result


# ═══════════════ RESOLUTION TOOLS ═══════════════

@tool
async def search_resolution_faqs(query: str, n_results: int = 3) -> str:
    """Search the FAQ knowledge base for resolution steps matching the query.
    Uses semantic similarity search over the ChromaDB vector store of SRE runbooks.
    Returns a JSON list of matching documents with metadata and similarity scores.

    Args:
        query: A description of the incident or problem to find resolutions for.
        n_results: Number of top matching FAQs to return (default 3).
    """
    logger.info("Tool called: search_resolution_faqs | query=%s", query[:100])

    def _sync_search() -> str:
        try:
            client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
            openai_ef = embedding_functions.OpenAIEmbeddingFunction(
                api_key=os.environ.get("OPENAI_API_KEY", ""),
                model_name=EMBEDDING_MODEL,
            )
            collection = client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=openai_ef,
                metadata={"hnsw:space": "cosine"},
            )

            results = collection.query(query_texts=[query], n_results=n_results)

            if not results["documents"][0]:
                return json.dumps([])

            documents = []
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                documents.append({
                    "faq_id": meta.get("faq_id"),
                    "category": meta.get("category"),
                    "content": doc,
                    "similarity_score": round(1 - dist, 4),
                })

            return json.dumps(documents, indent=2)
        except Exception as exc:
            logger.error("FAQ search failed: %s", exc)
            return json.dumps({"error": str(exc)})

    result = await asyncio.to_thread(_sync_search)
    logger.info("search_resolution_faqs completed | results_length=%d", len(result))
    return result


# ═══════════════ EMAIL TOOL ═══════════════

@tool
async def send_email_via_power_platform(email_html: str, timestamp: str) -> str:
    """Send an incident report email by triggering a Power Automate flow.

    Args:
        email_html: The full HTML content of the email body.
        timestamp: The incident timestamp to include in the payload.
    """
    logger.info("Tool called: send_email_via_power_platform | timestamp=%s", timestamp)

    if not POWER_AUTOMATE_WEBHOOK_URL:
        logger.warning("POWER_AUTOMATE_WEBHOOK_URL not configured; skipping email dispatch")
        return "Email dispatch skipped: webhook URL not configured."

    payload = {
        "timestamp": timestamp,
        "email_html": email_html,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                POWER_AUTOMATE_WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
        if resp.is_success:
            logger.info("Email sent successfully (HTTP %d)", resp.status_code)
            return f"Email sent successfully (HTTP {resp.status_code})."
        logger.error("Email dispatch failed: HTTP %d", resp.status_code)
        return f"Failed to send email: HTTP {resp.status_code}."
    except httpx.HTTPError as exc:
        logger.error("Email dispatch error: %s", exc)
        return f"Email dispatch error: {type(exc).__name__}"

