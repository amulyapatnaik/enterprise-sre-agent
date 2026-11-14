import time
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import requests

# Import the runbook-search class from rag_engine.py. This is the connection
# between the web API in this file and the code that finds matching runbooks.
from rag_engine import RunbookRAG

# FastAPI creates the web application. The decorators below attach URL paths
# (such as /triage) to Python functions in this file.
app = FastAPI(title="Enterprise SRE Triage Agent", version="1.0")

# Create one runbook searcher when the app starts. RunbookRAG loads the Markdown
# files from the "runbooks" folder, so each request can search those files.
rag = RunbookRAG(runbook_dir="runbooks")

# Count successful triage requests, grouped by the retrieved runbook and the
# backend that produced the diagnosis (Groq, Ollama, or offline).
TRIAGE_REQUESTS = Counter(
    "sre_agent_triage_requests_total",
    "Total number of SRE agent triage requests",
    ["runbook", "backend"]
)
# Measure vector lookup separately from the slower LLM response. Both are in
# seconds, so dashboards can show which stage is responsible for delays.
RAG_RETRIEVAL_LATENCY = Histogram(
    "sre_agent_rag_retrieval_seconds",
    "Time spent embedding an alert and retrieving its closest runbook",
)
LLM_INFERENCE_LATENCY = Histogram(
    "sre_agent_llm_inference_seconds",
    "Time spent waiting for or creating the structured incident diagnosis",
)

class AlertPayload(BaseModel):
    """Describe the JSON shape expected when someone sends an alert to the API.

    For example: {"error_log": "Pod was OOMKilled with exit code 137"}.
    FastAPI uses this model to read and validate the incoming request body.
    """
    error_log: str

@app.get("/health")
def health_check():
    """Check that the API can read at least one runbook from ChromaDB.

    Kubernetes can call GET /health as a liveness or readiness probe. This
    verifies the local runbook collection is readable; it does not check the
    health of the Kubernetes workloads described by the runbooks.
    """
    return {"service": "sre-triage-agent", **rag.health_check()}

@app.post("/triage")
def triage_incident(payload: AlertPayload):
    """Retrieve a relevant runbook, ask for a diagnosis, and return both.

    The alert arrives in an HTTP POST request's JSON body. FastAPI converts
    that JSON into an AlertPayload object called "payload". The first pipeline
    stage asks rag_engine.py to find a semantically similar runbook in ChromaDB.
    The second stage gives the alert and that runbook to Groq, Ollama, or the
    deterministic offline formatter and gets a structured diagnosis.
    """
    request_start = time.perf_counter()

    retrieval_start = time.perf_counter()
    retrieval_latency = 0.0
    try:
        match_result = rag.retrieve_runbook(payload.error_log)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        retrieval_latency = time.perf_counter() - retrieval_start
        RAG_RETRIEVAL_LATENCY.observe(retrieval_latency)

    inference_start = time.perf_counter()
    inference_latency = 0.0
    try:
        diagnosis_result = rag.generate_ai_diagnosis(
            payload.error_log,
            match_result["content"],
        )
    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"The configured LLM backend could not be reached: {error}",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=502,
            detail=f"The configured LLM backend returned an invalid diagnosis: {error}",
        ) from error
    finally:
        inference_latency = time.perf_counter() - inference_start
        LLM_INFERENCE_LATENCY.observe(inference_latency)

    backend = diagnosis_result["backend"]
    TRIAGE_REQUESTS.labels(
        runbook=match_result["source"],
        backend=backend,
    ).inc()

    return {
        "status": "success",
        "matched_runbook": match_result["source"],
        "retrieval_distance": match_result["distance"],
        "retrieval_distance_metric": match_result["distance_metric"],
        "llm_backend": backend,
        "diagnosis": diagnosis_result["diagnosis"],
        "latency_seconds": {
            "rag_retrieval": retrieval_latency,
            "llm_inference": inference_latency,
            "total": time.perf_counter() - request_start,
        },
    }

@app.get("/metrics")
def metrics():
    """Expose the recorded Prometheus metrics to monitoring tools.

    A Prometheus server can call GET /metrics and read the request counts and
    latency measurements in the format it understands.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
