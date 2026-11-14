# AI SRE Incident Triage & Workload Observability

A portfolio project combining Kubernetes workload signals with a grounded,
runbook-based incident triage API. It demonstrates applied AI/RAG, Python API
engineering, and the SRE practices needed to observe the service itself.

The agent **recommends** investigation and remediation steps; it does not run
commands against a cluster or automatically change workloads.

## Architecture

```text
  Kubernetes workloads
  (CPU throttling, memory pressure, pod failures)
             |
             | container / kubelet metrics
             v
       Prometheus evaluates
       PrometheusRule alerts
       - CFS throttling > 25%
       - working set > 85% of memory limit
             |
             | Alertmanager / incident sender
             | (delivery to /triage is an integration point;
             |  no Alertmanager receiver is configured in this repository yet)
             v
       FastAPI POST /triage <---------------- manual curl or caller
             |
             | embed the alert with all-MiniLM-L6-v2
             v
       ChromaDB PersistentClient (./chroma_db)
       - cosine vector search over Markdown runbooks
       - returns the closest runbook, source, and distance
             |
             | alert + retrieved runbook
             v
       Grounded diagnosis
       - Groq: llama-3.3-70b-versatile
       - Ollama: local OpenAI-compatible endpoint
       - Offline: deterministic runbook section extraction
             |
             | structured JSON RCA
             v
       Caller receives recommended diagnosis
       (no automatic remediation)

  Prometheus --scrapes ServiceMonitor /metrics every 30s--> FastAPI
       - sre_agent_rag_retrieval_seconds
       - sre_agent_llm_inference_seconds
       - sre_agent_triage_requests_total{runbook, backend}
```

The rules describe detection conditions, and the ServiceMonitor exposes this
agent's own metrics. Alertmanager-to-`/triage` delivery is not configured yet:
today, a person or another caller submits the alert to the API. The diagram
shows where that integration belongs, not an already-automated webhook.

## Incident runbooks

The source of truth is the Markdown in [`runbooks/`](runbooks/). At startup,
the agent checks ChromaDB's stored source metadata and upserts runbooks that are
missing without deleting the local database. New alerts are queried, not saved
as runbook records.

| Runbook | Signals and engineering mechanics |
| --- | --- |
| [`oom_killed.md`](runbooks/oom_killed.md) | Exit Code 137 commonly indicates SIGKILL. A container can be killed when its cgroup memory use exceeds its configured limit. Confirm the pod's last state and node/kernel evidence before changing limits. |
| [`bad_gateway_502.md`](runbooks/bad_gateway_502.md) | A 502 often means the ingress could not connect to a healthy upstream. Check Service endpoints, selectors, target ports, and readiness. A process bound only to `127.0.0.1` is not reachable through the pod network; bind to `0.0.0.0` where appropriate. |
| [`cpu_throttling.md`](runbooks/cpu_throttling.md) | Linux CFS commonly accounts CPU quota over a 100 ms period. A container that exhausts its quota can be paused for the remainder of that period, increasing tail latency even when average CPU looks moderate. Verify throttled periods before adjusting limits. |
| [`node_disk_pressure.md`](runbooks/node_disk_pressure.md) | Distinguishes image garbage-collection thresholds from kubelet eviction thresholds. `truncate -s 0` releases space in a large open log while preserving its file; `rm` only unlinks an open file, whose blocks remain allocated until the process closes it. Includes `crictl rmi --prune` for confirmed unused images. |
| [`kafka_consumer_lag.md`](runbooks/kafka_consumer_lag.md) | Uses `kafka_consumergroup_lag` and group/partition state to diagnose lag and rebalance storms. Slow work beyond `max.poll.interval.ms` can lead to rebalances and `CommitFailedException`; adding consumers beyond the partition count does not increase parallelism. |

These are investigation guides, not universal fixes. Validate the affected
workload and change safely in its operating context.

## RAG and diagnosis mechanics

1. **Index:** `RunbookRAG` reads `runbooks/*.md`, uses the local
   `all-MiniLM-L6-v2` sentence embedding model, and stores each document,
   vector, and source filename in the `sre_runbooks_minilm_v1` ChromaDB
   collection at `./chroma_db`.
2. **Retrieve:** The alert is embedded and ChromaDB returns the nearest runbook
   using cosine distance. Lower distance means closer in the embedding space;
   it is not a probability or confidence score. The current implementation
   returns the nearest document without a minimum-similarity cutoff.
3. **Diagnose:** The alert and retrieved runbook are sent to the configured LLM
   backend. The system prompt requires JSON fields `root_cause`,
   `immediate_triage_commands`, and `permanent_remediation`, grounded in the
   supplied runbook.
4. **Fallback:** If no LLM backend is configured, the offline path deterministically
   extracts the runbook's symptom, diagnostic commands, and remediation steps.
   This is not LLM inference or generated reasoning.

Runbooks are stored in the local Chroma database as records, not as separate
`.md` files inside `chroma_db/`. The Markdown files remain the source of truth.
The database is ignored by Git because it is generated and can be rebuilt.
On the first local startup, `sentence-transformers` downloads the embedding
model if it is not already cached; the first startup also embeds and indexes
the runbooks. Later startups reuse the persistent index and add only missing
runbooks.

### LLM backend selection

The backend is selected in this order:

1. `GROQ_API_KEY` — uses Groq's OpenAI-compatible API and
   `llama-3.3-70b-versatile` by default (`GROQ_MODEL` can override the model).
2. `OLLAMA_URL` — uses the configured local or remote OpenAI-compatible Ollama
   endpoint (`OLLAMA_MODEL` defaults to `llama3.2`).
3. Neither set — use the deterministic offline runbook formatter.

If a configured backend is unavailable or returns invalid JSON, the API reports
an error instead of silently substituting an offline result. When Groq is used,
the alert text and retrieved runbook are sent to Groq. Use Ollama or offline
mode when incident data must remain local.

## Observability

`GET /metrics` exposes:

- `sre_agent_rag_retrieval_seconds` — alert embedding and Chroma vector lookup.
- `sre_agent_llm_inference_seconds` — LLM response time or offline diagnosis
  formatting time.
- `sre_agent_triage_requests_total{runbook,backend}` — successful triage count.

The `ServiceMonitor` scrapes `/metrics`. The `PrometheusRule` defines:

- `ContainerCPUThrottlingHigh`: fraction of CFS periods throttled is above 25%
  for 10 minutes.
- `ContainerMemorySaturationHigh`: working set is above 85% of a non-zero
  container memory limit for 5 minutes.
- `SREAgentLLMInferenceSlow`: five-minute p95 inference histogram is above
  2.5 seconds for 10 minutes.

Example PromQL:

```promql
# Fraction of CPU scheduling periods that were throttled
sum by (namespace, pod, container) (
  rate(container_cpu_cfs_throttled_periods_total[5m])
)
/
sum by (namespace, pod, container) (
  rate(container_cpu_cfs_periods_total[5m])
)

# Container working set as a fraction of its memory limit
container_memory_working_set_bytes
/
container_spec_memory_limit_bytes

# Agent diagnosis p95 over five minutes
histogram_quantile(
  0.95,
  sum by (le) (rate(sre_agent_llm_inference_seconds_bucket[5m]))
)
```

These Kubernetes resource metrics must be available to Prometheus (for example,
through kubelet/cAdvisor scraping). The Prometheus Operator must be installed
for `ServiceMonitor` and `PrometheusRule`; its selectors must include the
`app.kubernetes.io/name: enterprise-sre-agent` label used here.

## Local quickstart

Python 3.11 is recommended:

```bash
python3.11 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Start in offline mode (the default when neither backend variable is set):

```bash
unset GROQ_API_KEY OLLAMA_URL
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Submit an alert and inspect health and metrics:

```bash
curl -sS -X POST http://127.0.0.1:8000/triage \
  -H 'Content-Type: application/json' \
  -d '{"error_log":"ALERT: Pod payment-service crashed with OOMKilled and exit code 137"}'

curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/metrics
```

### Use Groq

Set the key in your shell; do not commit it to the repository:

```bash
export GROQ_API_KEY="your-groq-api-key"
export GROQ_MODEL="llama-3.3-70b-versatile"
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

### Use local Ollama

In another terminal, ensure the Ollama server is running and the model is
available, then start the app:

```bash
ollama pull llama3.2
OLLAMA_URL=http://localhost:11434/v1 \
OLLAMA_MODEL=llama3.2 \
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Groq takes precedence if both `GROQ_API_KEY` and `OLLAMA_URL` are set.

## Docker

The multi-stage image installs dependencies in a builder, caches the embedding
model in the image, uses CPU-only PyTorch for Linux, and runs the application as
non-root UID `10001`:

```bash
docker build -t enterprise-sre-agent:latest .
docker run --rm \
  -p 8000:8000 \
  -v sre-agent-chroma:/app/chroma_db \
  enterprise-sre-agent:latest
```

To pass a Groq key to the container, set it in your shell and add
`--env GROQ_API_KEY` to `docker run`. The named volume preserves the local
Chroma database across container replacement.

## Kubernetes on OrbStack

The manifests assume the image has been built and is available to the local
cluster. For OrbStack Kubernetes, check that the active context is the intended
cluster before applying:

```bash
kubectl config current-context
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/prometheus-rules.yaml
kubectl rollout status deployment/enterprise-sre-agent
kubectl port-forward service/enterprise-sre-agent 8000:8000
```

The deployment uses `/health` for startup, readiness, and liveness probes, with
CPU requests/limits of `100m`/`500m` and memory requests/limits of
`256Mi`/`512Mi`. The Chroma database in this deployment is local to the pod;
if it is replaced, the runbooks are re-indexed from the image. Use a persistent
volume if retaining the index across pod replacement is required.

The manifests do not include an Alertmanager receiver or webhook. Until one is
configured to call `/triage`, submit alerts manually or from an external
incident sender. They also do not perform automated remediation.

## CI

GitHub Actions runs on pushes and pull requests targeting `main`. It installs
the pinned dependencies, runs the standard-library `unittest` suite, and parses
every YAML document under `k8s/` with PyYAML. Run the same checks locally:

```bash
python -m unittest test_agent -v
python - <<'PY'
from pathlib import Path
import yaml

for path in sorted(
    path for path in Path("k8s").rglob("*")
    if path.suffix in {".yaml", ".yml"}
):
    list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    print(f"Validated {path}")
PY
```

## Interview troubleshooting walkthrough

1. **Validate the signal:** Check the alert's namespace, pod, container, time
   window, and the underlying Prometheus series. Confirm a throttling ratio or
   memory-to-limit ratio is not caused by missing or zero-valued metrics.
2. **Follow the evidence:** Submit the error log to `/triage`; inspect the
   matched runbook, cosine distance, and whether the backend was Groq, Ollama,
   or offline. A nearest match is not a confidence guarantee.
3. **Separate latency stages:** Compare
   `sre_agent_rag_retrieval_seconds` with
   `sre_agent_llm_inference_seconds` to locate time spent in retrieval versus
   diagnosis.
4. **Choose a safe action:** Use the runbook to verify the cause, assess blast
   radius, and apply a reversible remediation. The agent only recommends steps;
   a human or approved automation remains responsible for executing them.
5. **Close the loop:** Confirm the workload signal recovers, the Prometheus
   alert resolves, and the agent's metrics return to baseline. Capture new
   evidence and update the source runbook when the incident reveals a gap.
