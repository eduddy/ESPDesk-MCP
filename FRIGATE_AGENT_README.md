# Frigate Diagnostic Agent

Automated, read-only diagnostic collection for Frigate video processing systems. Produces structured evidence for system health assessment and troubleshooting.

**Status**: ✅ Complete implementation, ready for deployment

---

## What It Does

Collects diagnostic evidence from Frigate every 24 hours (or on-demand):

```
📊 System Metrics          📝 Configuration         🎬 Events & Logs
├─ CPU per-core load      ├─ Detector config      ├─ 24h event history
├─ Memory usage           ├─ Camera settings      ├─ Event counts by label
├─ FPS per camera         └─ (secrets redacted)   ├─ Process uptime
└─ Skipped frames                                 └─ Warnings & errors

Output: /eagleha/evidence/frigate/frigate-snapshot-YYYY-MM-DD/
├─ summary.md             (human-readable report)
├─ config-redacted.yml    (sanitized config)
├─ stats-samples.json     (metrics)
├─ events-24h.json        (events)
└─ warnings-errors.txt    (logs)
```

**Health Status Labels:**
- ✅ **PASS** — System healthy
- ⚠️ **DEGRADED** — Issues detected (check summary)
- ❌ **BLOCKED** — Cannot collect data (API access issue)

---

## Files Provided

| File | Purpose |
|------|---------|
| **frigate_diagnostic_agent.py** | Core diagnostic collector (2 models: async for real use, synchronous helpers) |
| **frigate_mcp_server.py** | MCP server wrapper (exposes single constrained tool) |
| **test_frigate_agent.py** | Test & verification script |
| **FRIGATE_SETUP.md** | ⭐ Complete setup guide (read this first) |
| **EAGLEHA_INTEGRATION.md** | How to integrate into briefing pipeline |
| **requirements.frigate.txt** | Python dependencies |
| **Dockerfile.frigate** | Container image definition |
| **compose.frigate.yaml** | Docker Compose config for deployment |

---

## Quick Start (5 minutes)

### 1. Get Frigate API Key

1. Open `http://frigate.local:5000`
2. Settings → API Tokens → New Token
3. Name: `eagle-diagnostics` (read-only)
4. Scopes: `events:read`, `config:read`, `stats:read`

### 2. Test Connectivity

```bash
pip install -r requirements.frigate.txt

python test_frigate_agent.py \
  --frigate-url http://frigate.local:5000 \
  --frigate-api-key sk_xxxx...
```

**Expected output:**
```
✓ Connected to Frigate 2026.6.2
✓ stats:read: OK
✓ events:read: OK
✓ config:read: OK
✓ Capture complete
✓ ALL TESTS PASSED
```

### 3. Deploy MCP Server

**Option A: Docker (recommended)**
```bash
docker compose -f compose.frigate.yaml up -d
```

**Option B: Standalone**
```bash
python frigate_mcp_server.py \
  --frigate-url http://frigate.local:5000 \
  --frigate-api-key sk_xxxx... \
  --output-dir /eagleha/evidence/frigate
```

### 4. Test MCP Tool

For Claude Code or Claude API:

```json
// In MCP config
{
  "frigate": {
    "command": "python",
    "args": [
      "frigate_mcp_server.py",
      "--frigate-url", "http://frigate.local:5000",
      "--frigate-api-key", "sk_xxxx..."
    ]
  }
}
```

Claude can now call:
```
Tool: frigate/capture_snapshot(event_window_hours=24)
Returns: { health_status, output_files, data_summary }
```

### 5. Check Output

```bash
ls /eagleha/evidence/frigate/frigate-snapshot-*/
cat /eagleha/evidence/frigate/frigate-snapshot-*/summary.md
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ Frigate                                             │
│ (video processing, object detection, events)        │
└────────────────────┬────────────────────────────────┘
                     │ HTTP API (GET-only)
                     │
┌────────────────────▼────────────────────────────────┐
│ Frigate Diagnostic Agent                            │
│ (Python async collector)                            │
│                                                     │
│ 1. Sample stats every 15s for 2 min                │
│ 2. Fetch events (24h, no media)                    │
│ 3. Read & sanitize config                          │
│ 4. Collect warnings/errors                         │
│ 5. Calculate health (camera, detector, CPU)        │
│ 6. Generate reports                                │
└────────────────────┬────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
        ▼            ▼            ▼
    ┌────────┐  ┌────────┐  ┌──────────────┐
    │ Claude │  │ Claude │  │ Home         │
    │ Code   │  │ API    │  │ Assistant    │
    │ (MCP)  │  │ (MCP)  │  │ Automation   │
    └────────┘  └────────┘  └──────────────┘
        │            │            │
        └────────────┼────────────┘
                     │
        ┌────────────▼──────────┐
        │ EagleHA Briefing      │
        │ Pipeline              │
        │                       │
        │ Daily Report with:    │
        │ - System Health       │
        │ - Alerts              │
        │ - Evidence Links      │
        └───────────────────────┘
```

---

## Deployment Options

### Docker (Recommended)

```bash
# Build
docker build -f Dockerfile.frigate -t frigate-diagnostics .

# Run
docker compose -f compose.frigate.yaml up -d

# Monitor
docker logs -f frigate-diagnostics
```

### Standalone Python (Pi, VM)

```bash
pip install -r requirements.frigate.txt

python frigate_mcp_server.py \
  --frigate-url http://frigate.local:5000 \
  --frigate-api-key sk_xxxx... \
  --output-dir /eagleha/evidence/frigate &
```

### Home Assistant Automation

```yaml
automation:
  - alias: "Daily Frigate Snapshot"
    trigger:
      time: "03:00"
    action:
      service: shell_command.frigate_snapshot

shell_command:
  frigate_snapshot: >
    python /path/to/frigate_mcp_server.py
    --frigate-url http://frigate:5000
    --frigate-api-key !secret frigate_api_key
```

---

## Integration into EagleHA Briefing

See **EAGLEHA_INTEGRATION.md** for complete examples.

Quick example:

```python
from frigate_diagnostic_agent import capture_snapshot

# During briefing generation:
frigate_health = await capture_snapshot(
    frigate_url="http://frigate.local:5000",
    frigate_api_key=os.getenv("FRIGATE_API_KEY"),
    event_window_hours=24,
    metric_sample_seconds=120,
)

# Add to briefing:
briefing["frigate"] = {
    "health": frigate_health["health_status"],
    "summary": frigate_health["data_summary"],
    "evidence": frigate_health["output_dir"],
}
```

---

## Security Model

✅ **What the agent can do (read-only):**
- GET `/api/stats` — system metrics
- GET `/api/events` — event history
- GET `/api/config` — configuration
- GET `/api/logs` — log entries
- GET `/api/version` — version info

❌ **What the agent cannot do:**
- Modify configuration
- Delete or retain events
- Control cameras or PTZ
- Access MQTT or RTSP credentials
- Restart services
- Call POST/PUT/DELETE endpoints

**Output sanitization:**
- Passwords/tokens automatically redacted
- API keys removed from config
- RTSP URLs stripped of credentials

---

## Performance

| Operation | Time |
|-----------|------|
| API connectivity test | <1s |
| Stats sampling (120s) | ~2min |
| Event retrieval (24h) | <5s |
| Config fetch + sanitize | <2s |
| Log collection | <2s |
| **Total capture** | **~2-3 min** |
| **Output size** | **10-50 MB** (without media) |

---

## Troubleshooting

### "Connection refused"
Check: Network path, Frigate port (default 5000), firewall rules
```bash
curl http://frigate.local:5000/api/version
```

### "401 Unauthorized"
Check: API key is correct, has required scopes (`*:read`)
```bash
# Test with explicit key
python test_frigate_agent.py \
  --frigate-url http://frigate.local:5000 \
  --frigate-api-key sk_actual_key_here
```

### "BLOCKED" status
Check: Frigate is running, API is responding, logs are accessible
```bash
docker compose logs frigate  # if containerized
tail -f /var/log/frigate.log # if on host
```

### Output files empty
Check: Disk space, directory permissions
```bash
df -h /eagleha
ls -la /eagleha/evidence/frigate/
chmod 755 /eagleha/evidence/frigate
```

See **FRIGATE_SETUP.md** for complete troubleshooting.

---

## Next Steps

1. **Read FRIGATE_SETUP.md** — Detailed deployment guide
2. **Run test_frigate_agent.py** — Verify connectivity
3. **Deploy MCP server** — Docker or standalone
4. **Integrate with EagleHA** — See EAGLEHA_INTEGRATION.md
5. **Schedule daily captures** — Cron or Home Assistant automation
6. **Review first evidence snapshot** — Check /eagleha/evidence/frigate/

---

## Example Output: summary.md

```markdown
# Frigate Diagnostic Report

**Status**: `PASS`

**Captured**: 2026-10-17T14:23:45

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

## Events (24h)

Total: 412 events

### front-door: 256 events
- person: 142
- car: 87
- dog: 27

### driveway: 156 events
- car: 124
- person: 32
```

---

## Support

- **Setup issues**: See FRIGATE_SETUP.md
- **Integration questions**: See EAGLEHA_INTEGRATION.md
- **Agent code**: Review frigate_diagnostic_agent.py (well-commented)
- **MCP server**: Review frigate_mcp_server.py
- **Testing**: Run test_frigate_agent.py with --frigate-url

---

## License

MIT (same as ESPDesk-MCP project)

---

## Changelog

**v1.0** (2026-10-17)
- Initial release
- Core diagnostic collection
- MCP server wrapper
- Docker support
- Complete documentation

