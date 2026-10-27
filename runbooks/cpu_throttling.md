# Runbook: High p99 Latency / HTTP 504 Timeout (CFS CPU Throttling)
## Symptom
Service returns HTTP 504 Gateway Timeouts and p99 latency spikes above 1500ms, even though average CPU usage looks normal (~50%).
## Diagnostic Commands
1. PromQL: `rate(container_cpu_cfs_throttled_periods_total[5m])` - Check if container is exhausting its CPU quota inside the 100ms Linux CFS period.
## Remediation
1. Remove hard `resources.limits.cpu` (shift to requests-only CPU model) or increase CPU limit ceiling so bursts are not paused for 80ms per cycle.
