# Enterprise SRE Agent

**Runbook-grounded incident triage with Kubernetes and Prometheus observability.**

This project combines a small FastAPI service, semantic runbook retrieval, and
metrics for both retrieval and diagnosis. It is designed to demonstrate applied
AI/RAG, MLOps, SRE, and customer-facing platform engineering.

> **Scope:** The agent recommends diagnostic and remediation steps; it does not
> execute commands or change a cluster. Prometheus rules are included, but
> Alertmanager-to-agent delivery is not wired yet. Alerts are submitted to
> `/triage` manually or by an external caller.

## At a glance

| Layer | What it does |
| --- | --- |
| Detection | Prometheus rules identify high CFS CPU throttling and container memory saturation. |
| Triage API | FastAPI accepts an alert at `POST /triage` and returns a structured diagnosis. |
| Retrieval | ChromaDB finds the closest of five operational runbooks using local text embeddings. |
| Diagnosis | Groq, local/remote Ollama, or a deterministic offline formatter produces the response. |
| Observability | `/metrics` exposes triage counts and separate retrieval/inference latency histograms. |

## Architecture

The diagram shows the intended signal path. **Today, the alert-to-agent hop is
manual**; an Alertmanager receiver or other incident sender still needs to be
configured to call `/triage`.

```text
 Kubernetes workloads
 (CPU throttling, memory pressure, pod failures)
          |
          | kubelet / cAdvisor metrics
          v
    Prometheus evaluates
    PrometheusRule alerts
    - CFS throttling > 25%
    - memory working set > 85% of limit (OOM risk)
          |
          | Alertmanager / incident sender
          | (integration point; not configured here)
          v
    FastAPI POST /triage <----- curl or another caller (current path)
          |
          | embed alert with all-MiniLM-L6-v2
          v
    ChromaDB (./chroma_db)
    - cosine vector search over runbooks
    - returns closest runbook, source, and distance
          |
          | alert + retrieved runbook
          v
    Grounded diagnosis
    - Groq: llama-3.3-70b-versatile
    - Ollama: OpenAI-compatible endpoint
    - Offline: deterministic runbook extraction
          |
          | structured JSON RCA
          v
    Caller receives recommendations (no automated remediation)

 Prometheus --ServiceMonitor scrapes /metrics every 30s--> FastAPI
    - sre_agent_rag_retrieval_seconds
    - sre_agent_llm_inference_seconds
    - sre_agent_triage_requests_total{runbook,backend}
```

The Prometheus Operator must be installed for the `ServiceMonitor` and
`PrometheusRule` custom resources. Prometheus must also be configured to scrape
the Kubernetes resource metrics used by the CPU and memory rules.

## How a triage request works

1. **Index runbooks.** On startup, the app reads `runbooks/*.md`, embeds each
   document with the local `all-MiniLM-L6-v2` model, and stores the document,
   vector, and source filename in ChromaDB's `sre_runbooks_minilm_v1`
   collection.
2. **Find a runbook.** The alert is embedded and compared with stored vectors.
   ChromaDB returns the nearest runbook using cosine distance. A lower distance
   means a closer vector match; it is **not** a confidence percentage. The
   current implementation returns the nearest runbook without a similarity
   cutoff.
3. **Create a diagnosis.** The alert and retrieved runbook go to the selected
   backend. The response is checked for `root_cause`,
   `immediate_triage_commands`, and `permanent_remediation`.
4. **Return the result.** The caller receives the matching runbook, distance,
   backend, structured diagnosis, and stage-by-stage latency.

The Markdown files are the source of truth. ChromaDB stores database records,
not additional visible `.md` files. Startup adds runbooks that are missing
from the collection without deleting it. **Edits to an already-indexed
runbook are not currently re-embedded automatically.** Incoming alerts and
diagnoses are not persisted by this service.

## API

| Method and path | Purpose |
| --- | --- |
| `GET /health` | Confirms the ChromaDB runbook collection is readable and non-empty. |
| `POST /triage` | Accepts `{"error_log":"..."}` and returns retrieval plus diagnosis. |
| `GET /metrics` | Exposes Prometheus counters and histograms. |
| `GET /docs` | Opens the interactive FastAPI API documentation. |

Example:

```bash
curl -sS -X POST http://127.0.0.1:8000/triage \
  -H 'Content-Type: application/json' \
  -d '{"error_log":"Pod crashed with OOMKilled and Exit Code 137"}'
```

Response shape:

```json
{
  "status": "success",
  "matched_runbook": "oom_killed.md",
  "retrieval_distance": 0.25,
  "retrieval_distance_metric": "cosine",
  "llm_backend": "offline",
  "diagnosis": {
    "root_cause": "The runbook's symptom and cause...",
    "immediate_triage_commands": ["Check the pod's last state..."],
    "permanent_remediation": ["Review memory limits and application usage..."]
  },
  "latency_seconds": {
    "rag_retrieval": 0.02,
    "llm_inference": 0.001,
    "total": 0.03
  }
}
```

Values above illustrate the response format; distances and timings vary per
request.

## Supported incident runbooks

The source files are in [`runbooks/`](runbooks/).

| Runbook | Signals and engineering mechanics |
| --- | --- |
| [`oom_killed.md`](runbooks/oom_killed.md) | Exit Code 137 commonly means SIGKILL. A container may exceed its cgroup memory limit; confirm the pod's last state and node evidence before changing limits. |
| [`bad_gateway_502.md`](runbooks/bad_gateway_502.md) | Check ingress connectivity, Service endpoints, selectors, ports, and readiness. Binding only to `127.0.0.1` prevents access through the pod network; bind to `0.0.0.0` where appropriate. |
| [`cpu_throttling.md`](runbooks/cpu_throttling.md) | Linux CFS commonly meters CPU quota in 100 ms periods. Exhausting the quota can pause a container for the remainder of a period and increase tail latency. |
| [`node_disk_pressure.md`](runbooks/node_disk_pressure.md) | Separates kubelet eviction thresholds from image-GC thresholds. `truncate -s 0` frees blocks in a large open log; `rm` only unlinks it, and its blocks remain allocated until the process closes the file. Also covers `crictl rmi --prune`. |
| [`kafka_consumer_lag.md`](runbooks/kafka_consumer_lag.md) | Uses `kafka_consumergroup_lag` and group/partition state. Slow processing beyond `max.poll.interval.ms` can trigger rebalances and `CommitFailedException`; consumers beyond the partition count do not add parallelism. |

These are investigation guides, not universal fixes. Verify the incident and
choose safe, context-appropriate actions before changing a workload.

## LLM backends and data handling

The backend is selected in this order:

1. `GROQ_API_KEY` — Groq's OpenAI-compatible API; defaults to
   `llama-3.3-70b-versatile`. Set `GROQ_MODEL` to override it.
2. `OLLAMA_URL` — a local or remote OpenAI-compatible Ollama endpoint.
   `OLLAMA_MODEL` defaults to `llama3.2`.
3. Neither configured — deterministic offline extraction from the retrieved
   runbook. This fallback does **not** call an LLM or generate new reasoning.

If a configured backend fails or returns invalid JSON, the API reports an
error instead of silently switching backends. Groq receives the alert and
retrieved runbook text; choose Ollama or offline mode when incident data must
remain local.

## Observability

`GET /metrics` exposes:

| Metric | Meaning |
| --- | --- |
| `sre_agent_rag_retrieval_seconds` | Alert embedding and ChromaDB lookup time. |
| `sre_agent_llm_inference_seconds` | LLM response time, or offline formatting time. |
| `sre_agent_triage_requests_total{runbook,backend}` | Successful triage requests by matched runbook and backend. |

The Prometheus rules alert on CFS throttling above 25% for 10 minutes, container
working set above 85% of a non-zero memory limit for 5 minutes, and agent
diagnosis p95 above 2.5 seconds for 10 minutes.

Example PromQL:

```promql
# Fraction of CPU scheduling periods throttled
sum by (namespace, pod, container) (
  rate(container_cpu_cfs_throttled_periods_total[5m])
)
/
sum by (namespace, pod, container) (
  rate(container_cpu_cfs_periods_total[5m])
)

# Container memory working set as a fraction of its limit
container_memory_working_set_bytes
/
container_spec_memory_limit_bytes

# Agent diagnosis p95 over five minutes
histogram_quantile(
  0.95,
  sum by (le) (rate(sre_agent_llm_inference_seconds_bucket[5m]))
)
```

## Local quickstart

Python 3.11 is recommended. The first run downloads the embedding model if it
isn't already cached, then indexes the runbooks.

```bash
python3.11 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
unset GROQ_API_KEY OLLAMA_URL
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
curl -sS -X POST http://127.0.0.1:8000/triage \
  -H 'Content-Type: application/json' \
  -d '{"error_log":"ALERT: Pod payment-service crashed with OOMKilled and exit code 137"}'
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/metrics
```

### Use Groq

Keep the API key in your shell or a secrets manager; never commit it:

```bash
export GROQ_API_KEY="your-groq-api-key"
export GROQ_MODEL="llama-3.3-70b-versatile"
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

### Use local Ollama

Ensure Ollama is running, then download the model and start the API:

```bash
ollama pull llama3.2
OLLAMA_URL=http://localhost:11434/v1 \
OLLAMA_MODEL=llama3.2 \
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Groq takes precedence if both `GROQ_API_KEY` and `OLLAMA_URL` are set.

## Docker

The multi-stage image installs a CPU-only PyTorch build for Linux, preloads the
embedding model, and runs as non-root UID `10001`.

```bash
docker build -t enterprise-sre-agent:latest .
docker run --rm \
  -p 8000:8000 \
  -v sre-agent-chroma:/app/chroma_db \
  enterprise-sre-agent:latest
```

To use Groq, set `GROQ_API_KEY` in your shell and add `--env GROQ_API_KEY` to
`docker run`. The named volume preserves ChromaDB across container replacement.

## Kubernetes on OrbStack

Build the image and make sure it is available to the local cluster. Check the
active context before applying:

```bash
kubectl config current-context
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/prometheus-rules.yaml
kubectl rollout status deployment/enterprise-sre-agent
kubectl port-forward service/enterprise-sre-agent 8000:8000
```

`k8s/deployment.yaml` contains only the core Deployment and Service resources,
so it can be applied without Prometheus Operator CRDs. Apply
`k8s/prometheus-rules.yaml` only when the Prometheus Operator is installed; that
file contains the `PrometheusRule` and `ServiceMonitor`.
The Prometheus Operator's selectors must include the
`app.kubernetes.io/name: enterprise-sre-agent` label used by both resources.

The Deployment uses `/health` for startup, readiness, and liveness probes, with
CPU requests/limits of `100m`/`500m` and memory requests/limits of
`256Mi`/`512Mi`. ChromaDB is local to the pod; a replacement pod rebuilds its
index from the runbooks. Configure a persistent volume if the index must
survive pod replacement.

No Alertmanager webhook is included, and the agent does not perform automatic
remediation. Until alert delivery is configured, send alerts manually or from
an external incident sender.

## Tests and CI

GitHub Actions runs on pushes and pull requests targeting `main`. It installs
the pinned dependencies, runs the standard-library `unittest` suite, and parses
all YAML files under `k8s/`.

Run the checks locally:

```bash
python -m unittest test_agent -v
python - <<'PY'
from pathlib import Path
import yaml

files = sorted(
    path for path in Path("k8s").rglob("*")
    if path.suffix in {".yaml", ".yml"}
)
for path in files:
    list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    print(f"Validated {path}")
PY
```

## Incident troubleshooting approach

1. **Validate the signal.** Confirm namespace, pod, container, time window, and
   source metrics. Check for missing or zero-valued series.
2. **Follow the evidence.** Submit the alert to `/triage`; inspect the selected
   runbook, cosine distance, and backend. A nearest match is not proof of cause.
3. **Find the latency source.** Compare RAG retrieval and inference histograms
   to see which stage is slow.
4. **Act safely.** Verify the diagnosis, assess blast radius, and choose a
   reversible remediation. The agent provides recommendations; a human or
   separately approved automation executes changes.
5. **Confirm recovery.** Check that workload signals recover and alerts resolve.
   Capture what was learned and update the source runbook when needed.
