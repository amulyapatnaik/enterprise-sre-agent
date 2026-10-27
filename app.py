import time
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from rag_engine import RunbookRAG

app = FastAPI(title="Enterprise SRE Triage Agent", version="1.0")

# Initialize our RAG engine from Phase 1
rag = RunbookRAG(runbook_dir="runbooks")

# Prometheus Telemetry Metrics
TRIAGE_REQUESTS = Counter(
    "sre_agent_triage_requests_total",
    "Total number of SRE agent triage requests",
    ["source"]
)
TRIAGE_LATENCY = Histogram(
    "sre_agent_triage_duration_seconds",
    "Time taken to retrieve runbook and process triage",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0]
)

class AlertPayload(BaseModel):
    error_log: str

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "sre-triage-agent"}

@app.post("/triage")
def triage_incident(payload: AlertPayload):
    start_time = time.time()
    try:
        # Step 1: Use RAG to fetch the correct standard operating procedure
        match_result = rag.retrieve_runbook(payload.error_log)
        
        duration = time.time() - start_time
        
        # Step 2: Record Prometheus metrics
        TRIAGE_REQUESTS.labels(source=match_result["source"]).inc()
        TRIAGE_LATENCY.observe(duration)
        
        return {
            "status": "success",
            "matched_runbook": match_result["source"],
            "recommended_sops": match_result["content"],
            "processing_latency_sec": duration
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics")
def metrics():
    """Exposes Prometheus scraping endpoint for Kubernetes / monitoring"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
