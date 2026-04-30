# main.py — AutoRCA FastAPI Entry Point

import logging
import os
import time
import uuid

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from graph import graph

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AutoRCA API",
    description="Multi-agent AI for IT Operations — Incident Detection & Resolution",
    version="1.0.0",
)


class RequestModel(BaseModel):
    time_stamp: str = Field(..., min_length=1, max_length=100)
    issue: str = Field(..., min_length=1, max_length=5000)


@app.get("/")
async def home() -> dict:
    return {
        "message": "AutoRCA API is running",
        "endpoints": {
            "POST /run-agent": "Run the multi-agent analysis pipeline",
            "GET /health": "Health check",
        },
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "agents": ["commander", "metrics", "logs", "cicd", "resolver", "reporter"]}


@app.post("/run-agent")
async def run_agent(request: RequestModel):
    """
    Run the full AutoRCA multi-agent pipeline.

    The pipeline flow:
      commander -> metrics -> logs -> cicd -> resolver -> reporter -> END

    Each agent analyzes a different aspect of the system and contributes
    to the final incident report.
    """
    start_time = time.monotonic()
    logger.info(
        "POST /run-agent | issue=%s | time_stamp=%s",
        request.issue[:120],
        request.time_stamp,
    )
    try:
        result = await graph.ainvoke({
            "issue": request.issue,
            "time_stamp": request.time_stamp,
            "agents_called": [],
            "iteration_count": 0,
        })
        elapsed = round(time.monotonic() - start_time, 2)
        logger.info(
            "Pipeline completed | agents_called=%s | elapsed=%ss",
            result.get("agents_called", []),
            elapsed,
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "issue": request.issue,
                "time_stamp": request.time_stamp,
                "response": result.get("output", "No output generated"),
                "agents_called": result.get("agents_called", []),
                "reports": {
                    "metrics": result.get("metrics_report", ""),
                    "logs": result.get("logs_report", ""),
                    "cicd": result.get("cicd_report", ""),
                    "resolution": result.get("resolution_report", ""),
                    "final": result.get("final_report", ""),
                },
            },
        )
    except Exception as exc:
        elapsed = round(time.monotonic() - start_time, 2)
        logger.exception(
            "Pipeline failed | elapsed=%ss | error=%s",
            elapsed,
            exc,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Internal pipeline error. Check server logs for details.",
            },
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)