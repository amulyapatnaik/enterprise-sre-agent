# Runbook: HTTP 502 Bad Gateway at Ingress
## Symptom
Users receive HTTP 502 Bad Gateway from ALB or Istio Ingress Gateway. Upstream TCP connection was refused.
## Diagnostic Commands
1. `kubectl get endpoints <service-name>` - Check if service endpoints list is empty due to label selector mismatch.
2. `ss -tulnp` or `curl -Iv http://<pod-ip>:<port>` - Verify container process is listening on `0.0.0.0` and not `127.0.0.1`.
## Remediation
1. Fix label selector mismatch between Service and Deployment template.
2. Ensure container `targetPort` matches application listening port and readiness probe passes.
