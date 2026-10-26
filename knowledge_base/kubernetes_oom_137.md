# Runbook: Kubernetes Exit Code 137 (OOMKilled)
## Symptoms
Pods enter CrashLoopBackOff with Exit Code 137. 
Memory metrics show a sudden vertical spike (poison pill payload) or a steady sawtooth climb (memory leak).

## Immediate Mitigation
1. Check recent deployment rollouts: `kubectl rollout undo deployment/<service-name>`.
2. Check pre-crash container logs: `kubectl logs <pod-name> --previous --tail=100`.
3. If node RAM permits, scale pod replicas to buy headroom: `kubectl scale deployment/<service-name> --replicas=8`.
4. Apply rate limiting (HTTP 429) at the Ingress boundary to shed excess traffic.

## Root Cause Analysis & Prevention
- Exit Code 137 indicates the Linux kernel Out-Of-Memory (OOM) Killer terminated the container for exceeding `resources.limits.memory`.
- Ensure application runtime garbage collection limits are set strictly below container cgroup limits (e.g., Node.js `--max-old-space-size=1536` inside a 2Gi container).