# Pilot review outcomes and recovery

This software-pilot update fixes valid runtime, deployment and console findings. It is not a production-readiness claim. [ADR 0012](../adr/0012-pilot-recovery-and-transport-bounds.md) amends v0.3 and ADR 0011.

## Review disposition

| Finding | Resolution |
| --- | --- |
| Handshake rejection terminates Node; command bursts exceed capacity | Transport retry, advertised credits and `busy` responses |
| Unknown commands permanently stop dispatch | Durable receipts, tombstone reconciliation and audited manager release |
| Cross-machine time rejects valid evidence | Raw timestamps retained, approximate offset for freshness, causal sequence checks |
| Historical receipt scans and tick-based leases | Indexed fence flags, in-process notification, two-second fallback and independent heartbeat |
| Opposing locks, inconsistent inference deadlines, last-manager revocation | Aligned lock order and deadlines; last-manager guard |
| Outbox flooding and temporary-error quarantine | One-record acknowledgement window and retryable rejection handling |
| Unused signed turn, missing usage, offline key consumption, redundant cancel | Turn-bound keys, opt-in usage, offline check before admission, cancel incomplete streams only |
| Reader blamed for expiry | Audit actor is `system:deadline` |
| Global connection limit, slow uploads, unsafe redirects | Separate admission budgets, body deadline and exact API paths |
| Backup failure traps upgrades | Database starts first; pending starts at migration; restore previously running apps on earlier failure |
| Compose collision and stale base images | Development project is `agenticiot-dev`; image builds use `--pull` |
| Token overwritten, incorrect action permissions, ambiguous 4xx retry | Node-labelled secret, guarded rotation, effective permissions and definitive rejection handling |
| UUID failure outside secure context; misleading LOCAL label | HTTPS guidance inside try/finally; PILOT label |

Three suggestions need qualification. Missing acknowledgement cannot establish nonexecution. Retrying every programming/configuration error indefinitely conceals defects, so expected transport errors are retried and a supervisor example is provided. Deleting receipts or Node journals would invalidate deduplication; only ordinary observation history is pruned automatically.

Autocomplete hints do not control password managers or extensions. The console does not persist credentials in browser storage, but cannot guarantee an uncompromised browser. Documentation endpoints remain available in the pilot; restrict them at the reverse proxy if required. CSP/HSTS policy, dedicated metrics and load qualification remain deployment work, not implicit guarantees of this patch.

## Upgrade and recovery

Back up first; upgrade platform to `0011_runtime_recovery` before upgrading Nodes. Stop each old Node, preserve its data directory, and restart the new runtime with the same identity and journal. Never delete the journal to clear uncertainty or run two runtimes against the same devices. Old v1 Nodes can connect during transition but cannot implement the new reconciliation protocol.

`upgrade` starts PostgreSQL before backup, including after `stop`. Backup failure does not create `pending`; old applications restart only if the API was running before maintenance. Fix the backup failure and retry. Once migration starts, retain `pending` and follow the [Linux recovery procedure](../deployment/linux.md#回退和失败处理). INT, TERM and HUP use the failure handler. Power loss and forced kill cannot run that handler; inspect markers, logs and database before recovery.

Preflight checks identity configuration before downtime; it does not establish identity-provider availability or verify that a database grant remains enabled. Readiness is a database/schema check. Exact API paths are required: `/v1/chat/completions/` returns 404 rather than redirecting. The Caddy example bounds header/body reads without a total streaming-response timeout; see [Caddy timeouts](https://caddyserver.com/docs/caddyfile/options#timeouts).

## Command reconciliation

Command metadata includes `received_at`, `blocked`, `barrier_at` and `resolution`. A missing receipt does not mean nothing happened. On recovery, the Node replays saved results or writes a tombstone before reporting nonexecution/uncertainty. Active execution tasks prevent a barrier; an existing intent prevents a nonexecution claim.

For an uncertain command with a barrier, inspect the device and refresh its state. A domain manager can select **Reconcile and release dispatch** in the console, or call `POST /v1/commands/{id}/reconcile`:

```json
{"observation_id":"LATEST_POST_BARRIER_OBSERVATION_ID","reason":"Inspected current state and approved subsequent commands"}
```

The state endpoint returns `observation_id`. It must identify the latest observation, newer than the barrier and no more than ten seconds old. Release records the decision and leaves the old result unknown. It permits later commands; it never retries the old action. Lost journals and potentially still-running physical machinery need separate incident handling, not automated reconciliation.

## Node supervision

Prepare `/opt/agenticiot-node` and its Python environment and create a dedicated `agenticiot-node` system user/group. Copy the [example unit](../../deploy/agenticiot-node.service.example) to `/etc/systemd/system/agenticiot-node.service` and adapt its hostname/paths. Put the machine credential in `/etc/agenticiot-node.env` as `AGENTICIOT_NODE_TOKEN=...`, owned by root with mode 600. Systemd reads this file. The service user must be able to read the local-service JSON; omit `--services` if no model service is configured. `StateDirectory` provides persistent journal storage.

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now agenticiot-node
sudo systemctl status agenticiot-node
sudo journalctl -u agenticiot-node --since '10 minutes ago'
```

Transport failures retry in process; other process failures restart with a delay. Investigate repeated configuration failures. Logs report clock estimates, reconnect classes, dispatch counts and quarantined sequence/error codes without prompt/output bodies or credentials. The Node console shows reported pending/quarantined upload counts (unknown while offline). Inspect `rejected_outbox` only in a protected journal copy; do not edit sequences or delete evidence. Dedicated pool-wait metrics remain pending.

## Inference compatibility

Set `"include_usage": true` in a Node local-service configuration only after testing upstream support for `stream_options.include_usage`. Missing usage remains unknown. HTTP errors cover admission failures. Once SSE headers are sent, even a pre-token upstream failure uses HTTP 200 with an in-stream error and no successful `[DONE]`.

The 60-second budget begins at admission. Five seconds of metadata expiry grace permits bookkeeping, not longer generation. No automatic retry or fallback is added. Known-offline admission does not consume the key; other post-admission failures may conservatively retain it.

## PostgreSQL patch maintenance

Application upgrades do not recreate PostgreSQL. Schedule patch updates separately with a tested backup/restore plan. Retain the current image digest. Keep the major version at 18 and the existing volume mount. Set `RELEASE_SHA` and `RELEASE_DIR` from the successful `current` release as described in the Linux guide, then run each step only after the previous one succeeds:

```sh
sudo --preserve-env=RELEASE_SHA,RELEASE_DIR docker compose --project-name agenticiot --env-file /opt/agenticiot/config.env -f "$RELEASE_DIR/deploy/compose.linux.yaml" pull postgres
sudo bash scripts/deploy.sh stop
sudo --preserve-env=RELEASE_SHA,RELEASE_DIR docker compose --project-name agenticiot --env-file /opt/agenticiot/config.env -f "$RELEASE_DIR/deploy/compose.linux.yaml" up -d --no-recreate --wait postgres
sudo bash scripts/deploy.sh backup
sudo --preserve-env=RELEASE_SHA,RELEASE_DIR docker compose --project-name agenticiot --env-file /opt/agenticiot/config.env -f "$RELEASE_DIR/deploy/compose.linux.yaml" up -d --no-deps --force-recreate --wait postgres
sudo bash scripts/deploy.sh start
```

The final backup occurs with applications stopped. Retain the old image, configuration and backup until verification completes. On failure, stop and diagnose; never remove the data volume. Major-version upgrades require a separate migration plan.
