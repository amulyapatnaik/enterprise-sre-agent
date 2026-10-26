# Runbook: Ingress HTTP 502 Bad Gateway
## Symptoms
Clients receive HTTP 502 Bad Gateway errors while pods show Running (0 restarts) with normal CPU and memory.

## Immediate Triage
1. Check Kubernetes service endpoints: `kubectl get endpoints <service-name> -n <namespace>`.
   - If endpoints list is `<none>`, verify the Service label selector exactly matches active Pod labels.
2. Check local socket binding:
   - Run `curl -Iv http://<pod-ip>:<port>/healthz`.
   - If connection is refused, verify the application process binds to `0.0.0.0` (all interfaces), not `127.0.0.1` (localhost).
3. Check worker node Security Groups to ensure inbound traffic from the Ingress ALB security group is permitted.