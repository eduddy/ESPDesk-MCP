# Frigate Diagnostic Agent Setup

Automated diagnostic collection for Frigate video processing systems. Gathers system health, configuration, events, and logs without modifying state.

## Architecture

```
┌─────────────────────┐
│  Frigate Instance   │
│  (read-only access) │
└──────────┬──────────┘
           │ HTTP API (GET only)
           │
┌──────────▼──────────┐
│ Frigate Diagnostic  │
│ Agent (MCP Server)  │ ← Constrained tool interface
│ on Foundry Pi       │
└──────────┬──────────┘
           │ capture_snapshot()
           │
┌──────────▼──────────────┐
│ EagleHA Briefing        │
│ Pipeline                │
└─────────────────────────┘
```

**Security model:**
- Agent has **read-only** access to Frigate API
- No permission to modify config, events, or camera control
- No access to passwords, MQTT, RTSP streams
- Output is automatically sanitized (secrets redacted)
- Single constrained tool: `frigate.capture_snapshot()`

---

## Prerequisites

### 1. Frigate API Key (Read-Only)

Create a read-only API token in Frigate dashboard:

1. Open `http://frigate.local:5000` (or your Frigate URL)
2. Navigate: **Settings → API Tokens**
3. Click **+ New Token**
4. Name: `eagle-diagnostics` (or similar)
5. Copy the token — you'll need this below

**Scopes to verify:**
- `events:read` — retrieve event history
- `config:read` — read configuration (sanitized)
- `stats:read` — system metrics
- *(no write/delete scopes)*

### 2. Network Access

Ensure the Foundry Pi can reach Frigate's API:

```bash
# From Foundry Pi, test connectivity:
curl -s http://frigate.local:5000/api/version | jq .
# Should return version info, not auth errors

# If behind a reverse proxy, use the proxied URL:
curl -s http://home.example.com/frigate/api/version | jq .
```

### 3. Evidence Directory

Create output directory on the Foundry Pi:

```bash
# On the Foundry Pi (or NAS mounted as /eagleha)
mkdir -p /eagleha/evidence/frigate
chmod 755 /eagleha/evidence/frigate
```

---

## Installation

### Option A: Standalone Python

1. **Install dependencies:**
   ```bash
   pip install httpx pyyaml fastmcp
   ```

2. **Copy agent files:**
   ```bash
   cp frigate_diagnostic_agent.py /path/to/agent/
   cp frigate_mcp_server.py /path/to/agent/
   ```

3. **Run MCP server:**
   ```bash
   python frigate_mcp_server.py \
     --frigate-url http://frigate.local:5000 \
     --frigate-api-key sk_xxxx... \
     --output-dir /eagleha/evidence/frigate
   ```

### Option B: Docker (Recommended)

1. **Create Dockerfile:**
   ```dockerfile
   FROM python:3.13-slim
   
   WORKDIR /app
   
   RUN pip install httpx pyyaml fastmcp
   
   COPY frigate_diagnostic_agent.py .
   COPY frigate_mcp_server.py .
   
   ENTRYPOINT ["python", "frigate_mcp_server.py"]
   ```

2. **Build image:**
   ```bash
   docker build -t eagle-frigate-diagnostics .
   ```

3. **Run in docker-compose:**
   ```yaml
   services:
     frigate-diagnostics:
       image: eagle-frigate-diagnostics:latest
       container_name: frigate-diagnostics
       restart: unless-stopped
       environment:
         FRIGATE_URL: http://frigate:5000
         FRIGATE_API_KEY: sk_xxxx...
       volumes:
         - /eagleha/evidence/frigate:/eagleha/evidence/frigate
       # MCP listens on stdio (no exposed ports)
   ```

---

## Client Configuration

### For Claude (Claude Code or API)

Add to `claude_desktop_config.json` or Cowork settings:

```json
{
  "mcpServers": {
    "frigate-diagnostics": {
      "command": "python",
      "args": ["/path/to/frigate_mcp_server.py",
               "--frigate-url", "http://frigate.local:5000",
               "--frigate-api-key", "sk_xxxx..."]
    }
  }
}
```

### For Agentic Loop (Home Assistant / Automation)

Call the tool directly via subprocess or HTTP:

```python
import subprocess
import json

result = subprocess.run(
    ["python", "frigate_mcp_server.py",
     "--frigate-url", "http://frigate.local:5000",
     "--frigate-api-key", "sk_xxxx..."],
    input=json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "capture_snapshot",
            "arguments": {
                "event_window_hours": 24,
                "metric_sample_seconds": 120
            }
        }
    }),
    capture_output=True,
    text=True
)
```

---

## Output Structure

After running `capture_snapshot()`, you'll get:

```
/eagleha/evidence/frigate/frigate-snapshot-2026-10-17/
├── summary.md              # Human-readable report
├── config-redacted.yml     # Configuration (secrets redacted)
├── stats-samples.json      # CPU, memory, FPS metrics
├── events-24h.json         # Recent events (no media)
└── warnings-errors.txt     # Log entries
```

### summary.md example:

```markdown
# Frigate Diagnostic Report

**Status**: `PASS`

**Captured**: 2026-10-17T14:23:45.123456

## Health Summary

System healthy

## Camera Health

### front-door
- Avg FPS: 29.8
- Avg Skipped Frames: 0.0
- Process Uptime: 100.0%

### driveway
- Avg FPS: 14.9
- Avg Skipped Frames: 2.3
- Process Uptime: 99.5%

## Detection Configuration

### coral
- Type: edgetpu
- Model: coco
- Avg Inference: 12.3ms

## CPU Load

- Total Host Avg: 45.2%

### Per-Core Process Load
- frigate.detector: 28.5%
- frigate.record: 8.2%

## Events (24h)

Total: 234 events

### front-door: 156 events
- person: 87
- car: 45
- dog: 24

### driveway: 78 events
- car: 62
- person: 16
```

---

## Health Status Labels

**PASS** — System is healthy
- All cameras running at expected FPS
- No process availability gaps
- Recent events detected
- Configuration valid

**DEGRADED** — System operational but attention needed
- One or more cameras: <95% process uptime
- Skipped frames detected
- High CPU load (>80%)
- Events present but infrequent

**BLOCKED** — Diagnostic data unavailable
- Cannot connect to Frigate API
- Missing required scopes
- Configuration fetch failed
- No events in window

---

## Integration with EagleHA Briefing

### 1. Add to Briefing Request

```python
# In your briefing generation code
from frigate_diagnostic_agent import capture_snapshot

async def gather_system_diagnostics():
    """Collect evidence for briefing."""
    result = await capture_snapshot(
        frigate_url="http://frigate.local:5000",
        frigate_api_key=os.getenv("FRIGATE_API_KEY"),
        event_window_hours=24,
        metric_sample_seconds=120,
        include_media=False
    )
    return result
```

### 2. Scheduled Daily Capture

In Home Assistant or cron:

```bash
# /etc/cron.d/frigate-diagnostics
# Daily at 3 AM
0 3 * * * /usr/local/bin/python /path/to/frigate_mcp_server.py \
    --frigate-url http://frigate.local:5000 \
    --frigate-api-key sk_xxxx... \
    --output-dir /eagleha/evidence/frigate \
    >> /var/log/frigate-diagnostic.log 2>&1
```

### 3. On-Demand via Claude

Claude can call the tool directly during briefings:

```
Claude: "I'll gather the latest Frigate diagnostics to check system health."
[calls: frigate-diagnostics.capture_snapshot(event_window_hours=24)]
Result: Health status PASS, 412 events in 24h, no warnings
```

---

## Troubleshooting

### "Unable to fetch version" / API Connection Failed

**Check:**
1. Frigate is running: `curl http://frigate.local:5000`
2. Network path: `ping frigate.local` (or docker network if containerized)
3. DNS: If using hostname, add to `/etc/hosts` on Foundry Pi

**Fix:**
- Use IP instead of hostname: `--frigate-url http://192.168.1.x:5000`
- Check Frigate port (default 5000, may differ in reverse proxy)

### "401 Unauthorized" on API calls

**Check:**
1. API key is correct: Copy fresh token from Frigate UI
2. Key has required scopes: `events:read`, `config:read`, `stats:read`

**Fix:**
```bash
python frigate_mcp_server.py \
  --frigate-url http://frigate.local:5000 \
  --frigate-api-key sk_newtoken_xyz \
  --log-level DEBUG
```

### "No events found" or "BLOCKED" status

**Causes:**
- No activity in monitored zones during window
- Events expired (check Frigate retention policy)

**Fix:**
- Trigger event manually (walk through monitored zone)
- Increase `event_window_hours` to 72 or more
- Verify cameras are online: check Frigate dashboard

### Output files empty or missing

**Check permissions:**
```bash
ls -la /eagleha/evidence/frigate
chmod 755 /eagleha/evidence/frigate
```

**Check disk space:**
```bash
df -h /eagleha
# Need ~100MB per capture (with media: ~500MB)
```

---

## Security Checklist

- [ ] Read-only API key created
- [ ] Key has only `*:read` scopes
- [ ] Frigate internal port 5000 **not** exposed to LAN (only via reverse proxy)
- [ ] Output directory owned by process user, not root
- [ ] No hardcoded passwords in configs (use env vars)
- [ ] Log output sanitized (secrets redacted automatically)

---

## Performance Expectations

| Operation | Duration |
|-----------|----------|
| Version fetch | <1s |
| Stats sampling (120s) | ~2min |
| Event retrieval (24h) | <5s |
| Config fetch & sanitization | <2s |
| Log collection | <2s |
| **Total capture** | **~2-3 min** |
| **Output size** | **10-50 MB** (without media) |

---

## Next Steps

1. **Verify connectivity:**
   ```bash
   python frigate_mcp_server.py \
     --frigate-url http://frigate.local:5000 \
     --log-level DEBUG
   ```

2. **Test capture:**
   ```python
   # In Python REPL
   import asyncio
   from frigate_diagnostic_agent import capture_snapshot
   
   result = asyncio.run(capture_snapshot(
       frigate_url="http://frigate.local:5000",
       event_window_hours=24
   ))
   print(result["health_status"])
   ```

3. **Integrate into EagleHA briefing pipeline**

4. **Schedule daily captures (optional)**

---

## Questions or Issues?

Refer to:
- Frigate docs: https://docs.frigate.video/
- This agent source: `frigate_diagnostic_agent.py` (well-commented)
- System logs: `/var/log/frigate-diagnostic.log` (if cron-scheduled)
