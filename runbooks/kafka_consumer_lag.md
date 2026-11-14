# Runbook: Kafka Consumer Lag and Rebalance Storm
## Symptom
The `kafka_consumergroup_lag` metric is rising or remains high, message processing falls behind, and consumer group members repeatedly join and leave the group.
## Diagnostic Commands
1. `kafka-consumer-groups.sh --bootstrap-server <broker:port> --describe --group <group-name>` - Compare each partition's current offset, log end offset, and lag.
2. Review consumer logs for repeated rebalances, `CommitFailedException`, and processing that exceeds `max.poll.interval.ms`.
3. Compare the number of active consumers with the number of topic partitions; consumers beyond the partition count cannot increase parallelism for that topic.
4. Check whether slow downstream calls, long batches, or CPU saturation prevent a consumer from polling again before `max.poll.interval.ms`.
## Remediation
1. Reduce per-poll work (for example, lower `max.poll.records`) or improve processing capacity so each batch completes within `max.poll.interval.ms`.
2. Increase `max.poll.interval.ms` only when longer processing is expected and safe; also verify heartbeats, session settings, and offset-commit behavior to avoid masking a stalled consumer.
3. Handle `CommitFailedException` as a sign that the consumer may have lost its group assignment; avoid committing offsets from a revoked assignment and allow the group to rebalance cleanly.
4. Scale consumers up only as far as the topic's partition count and available broker/downstream capacity permit. Adding more consumers than partitions does not increase throughput and may add coordination overhead.
5. Confirm that `kafka_consumergroup_lag` trends down and that group membership stabilizes after the change.
