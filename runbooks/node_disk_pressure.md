# Runbook: Kubernetes Node DiskPressure
## Symptom
The node reports `DiskPressure`, pods may be evicted, and the root filesystem is running out of space. Image garbage collection can begin when image usage reaches the configured `imageGCHighThresholdPercent` (often 85%), but pod eviction is controlled separately by kubelet eviction thresholds such as `nodefs.available`. Do not assume 85% used is a universal eviction threshold; check the node's effective kubelet configuration and available bytes.
## Diagnostic Commands
1. `df -h /` - Check root filesystem capacity and available space on the affected node.
2. `du -ahx / | sort -rh | head -n 10` - Find the largest files and directories on the root filesystem; investigate permission errors and mount boundaries.
3. `sudo lsof +L1` - Find large deleted files that are still open by a process and therefore still consuming disk space.
4. `sudo crictl images` - Review container images present in the CRI image store before pruning.
5. Inspect kubelet configuration for `imageGCHighThresholdPercent`, `imageGCLowThresholdPercent`, and `evictionHard.nodefs.available`.
## Remediation
1. If an oversized log file is still open, identify its owner and confirm it is safe to truncate before running `sudo truncate -s 0 /path/to/large-open.log`. Truncating keeps the file and its open file descriptor but releases its allocated contents immediately.
2. Do not use `rm` as a way to reclaim space from a file that a process still has open. `rm` removes the directory entry, but the process keeps the file descriptor and disk blocks until it closes the file; restart or reload the owning process during a safe window if that is needed to release the blocks.
3. After confirming the images are unused, run `sudo crictl rmi --prune` on the affected node to remove unused container images.
4. Set kubelet image-GC high and low thresholds deliberately (for example, high 85% and low 80%) and review `evictionHard.nodefs.available` against the node's disk size and workload needs. Keep enough free space for kubelet, logs, and image operations.
5. Recheck `df -h /`, node conditions, and pod events after cleanup; prevent recurrence with log rotation, image retention, and disk-capacity alerts.
