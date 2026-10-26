# Runbook: NodeDiskPressure and Root Volume Exhaustion
## Symptoms
Worker nodes report `NodeDiskPressure` condition when root filesystem (`/`) reaches or exceeds 85% capacity, causing the Kubelet to evict non-critical pods.

## Immediate Mitigation
1. Locate largest log consumers: `du -ahx /var/log/pods | sort -rh | head -n 10`.
2. Safely clear unrotated container logs:
   - Run `truncate -s 0 /var/log/pods/*/*/*.log`.
   - DO NOT USE `rm`! If an active process holds the file handle open, deleting the file leaves the inode allocated in memory and disk space remains 100% full.
3. Prune dangling images via container runtime: `crictl rmi --prune`.

## Long-term Prevention
- Configure Kubelet image garbage collection: `imageGCHighThresholdPercent: 80`, `imageGCLowThresholdPercent: 70`.
- Mount dedicated EBS persistent disks for `/var/log` and `/var/lib/containerd`.