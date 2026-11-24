# Logs through Grafana Loki (free, open source)

BlackBox can read application logs from a Grafana Loki server instead of the local JSONL files.
The model still uses the same `search_logs` tool; only the source behind it changes.

```
demo services ──JSONL──► shipper ──push──► Loki :3100 ◄──LogQL query── BlackBox (blackbox/connectors/loki.py) ──► qwen3:4b
```

## 1. Install and start Loki (Windows, no Docker)
1. Download `loki-windows-amd64.exe.zip` from https://github.com/grafana/loki/releases (tested config: v3.x)
   and unzip it into `tools/observability/bin/`.
2. From the repo root:
   ```
   tools\observability\bin\loki-windows-amd64.exe -config.file=tools/observability/loki-config.yaml
   ```
3. Check: `curl http://127.0.0.1:3100/ready` prints `ready`.

(Docker alternative: `docker run -p 3100:3100 grafana/loki:3.7.10`.)

## 2. Ship logs into Loki
- Live demo (tail the files the running services write):
  `python scripts/push_logs_to_loki.py telemetry --follow`
- A frozen incident snapshot, with its original timestamps and evidence ids:
  `python scripts/push_logs_to_loki.py snapshot --incident INC-9001`

Labels: `job="blackbox-demo"`, `service`, `level` (+ `incident` for snapshots). Grafana Alloy or Promtail
can replace the shipper with the same labels.

## 3. Investigate with logs from Loki
- CLI: `python -m blackbox.cli investigate --incident INC-9001 --system blackbox --live --loki-url http://127.0.0.1:3100 --loki-incident INC-9001 --out runs/_manual/loki_9001`
- API/UI: start the API with `BLACKBOX_LOKI_URL=http://127.0.0.1:3100`; live jobs then read logs from Loki and
  the Connections page shows "logs (Grafana Loki)" with its health.
- Smoke test: `python -m blackbox.connectors.loki smoke --incident INC-9001`

Live mode is for demonstrations; the formal evaluation stays on frozen snapshots.
Optional: point Grafana (free) at Loki to browse the same logs.
