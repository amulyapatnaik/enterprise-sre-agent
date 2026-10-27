# Runbook: Kubernetes Pod OOMKilled (Exit Code 137)
## Symptom
Pod crashes repeatedly with `OOMKilled` and `Exit Code 137`. Linux kernel SIGKILL (-9) terminated the container because `container_memory_working_set_bytes` exceeded `resources.limits.memory`.
## Diagnostic Commands
1. `kubectl describe pod <pod-name>` - Check Last State for Exit Code 137.
2. `dmesg -T | grep -i oom` - Verify kernel OOM killer invocation on the node.
## Remediation
1. Immediate: Bump `resources.limits.memory` in deployment YAML and scale replicas via HPA.
2. Long-term: Capture a heap dump before crash and tune application garbage collection ceiling below container limit.
