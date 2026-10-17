# EagleHA Integration: Frigate Diagnostic Agent

Integration guide for adding automated Frigate diagnostics to your EagleHA briefing pipeline.

---

## Architecture Overview

```
EagleHA Briefing Pipeline
├── System Health (Frigate diagnostics) ← YOU ARE HERE
├── Home Assistant Status
├── Security System Status
├── Energy Usage
└── Alerts & Issues
    └── [Optional] Triggered troubleshooting agents
```

---

## 1. Add Frigate Diagnostics to Briefing Request

### In your briefing generation code:

```python
# briefing.py or similar
import asyncio
from frigate_diagnostic_agent import capture_snapshot

async def generate_eagleha_briefing():
    """Generate daily briefing with system health checks."""
    
    # ... existing briefing code ...
    
    # Collect Frigate diagnostics
    print("Gathering Frigate system health...")
    frigate_health = await capture_snapshot(
        frigate_url="http://frigate.local:5000",
        frigate_api_key=os.getenv("FRIGATE_API_KEY"),
        event_window_hours=24,
        metric_sample_seconds=120,
        include_media=False
    )
    
    # Add to briefing
    briefing_sections = {
        "frigate_health": frigate_health["health_status"],
        "frigate_summary": {
            "cameras": frigate_health["data_summary"]["cameras"],
            "events": frigate_health["data_summary"]["events_24h"],
            "detectors": frigate_health["data_summary"]["detectors"],
            "warnings": frigate_health["data_summary"]["warnings_errors"],
        }
    }
    
    return briefing_sections

# Usage in briefing pipeline:
asyncio.run(generate_eagleha_briefing())
```

---

## 2. Format for Claude Integration

### Option A: Direct MCP Tool Call

If Claude is running the briefing agent, expose the tool:

```json
{
  "mcpServers": {
    "frigate": {
      "command": "python",
      "args": [
        "/app/frigate_mcp_server.py",
        "--frigate-url", "http://frigate.local:5000",
        "--frigate-api-key", "${FRIGATE_API_KEY}",
        "--output-dir", "/eagleha/evidence/frigate"
      ]
    }
  }
}
```

**Claude will then see:**
```
Tool: frigate/capture_snapshot
Args:
  - event_window_hours (int, optional, default=24)
  - metric_sample_seconds (int, optional, default=120)
  - include_media (bool, optional, default=false)

Returns: {
  health_status: { label, summary, issues, timestamp },
  output_dir: string,
  output_files: { summary, config, stats, events, logs },
  data_summary: { cameras, events_24h, detectors, warnings_errors }
}
```

Claude can call it during briefing generation:

```
Claude: "I'll check Frigate's system health to include in the briefing."
[Calling: frigate/capture_snapshot(event_window_hours=24)]
Result: Health status PASS, 412 events, 4 cameras, 0 warnings
```

### Option B: Home Assistant Automation

Trigger via Home Assistant automation:

```yaml
# automations.yaml
- alias: "EagleHA Daily Briefing - Gather Frigate Evidence"
  trigger:
    time: "03:00"  # 3 AM daily
  action:
    - service: shell_command.frigate_snapshot
    - delay:
        minutes: 3
    - service: notify.briefing_complete
```

```yaml
# configuration.yaml
shell_command:
  frigate_snapshot: >
    docker exec frigate-diagnostics python frigate_mcp_server.py
    --frigate-url http://frigate:5000
    --frigate-api-key !secret frigate_api_key
```

---

## 3. Display Results in Briefing

### Markdown template:

```markdown
## Frigate System Health

**Status: {{ frigate_health.label }}**

{{ frigate_health.summary }}

### Quick Stats
- **Cameras**: {{ frigate_summary.cameras }}
- **Events (24h)**: {{ frigate_summary.events }}
- **Detectors**: {{ frigate_summary.detectors }}
- **Warnings/Errors**: {{ frigate_summary.warnings }}

### Details
{{ frigate_health.issues | join("\n- ") }}

**Evidence**: `/eagleha/evidence/frigate/frigate-snapshot-{{ date }}/`
- `summary.md` — Full health report
- `stats-samples.json` — CPU, FPS metrics
- `events-24h.json` — Recent events
- `config-redacted.yml` — System configuration
- `warnings-errors.txt` — Log entries
```

### HTML for web briefing:

```html
<section class="frigate-health">
  <h2>Frigate Health</h2>
  <div class="status {{ frigate_health.label.lower() }}">
    <span class="badge">{{ frigate_health.label }}</span>
    <p>{{ frigate_health.summary }}</p>
  </div>
  
  <div class="metrics">
    <div class="metric">
      <label>Cameras</label>
      <value>{{ frigate_summary.cameras }}</value>
    </div>
    <div class="metric">
      <label>Events (24h)</label>
      <value>{{ frigate_summary.events }}</value>
    </div>
    <div class="metric">
      <label>Detectors</label>
      <value>{{ frigate_summary.detectors }}</value>
    </div>
    <div class="metric">
      <label>Warnings</label>
      <value>{{ frigate_summary.warnings }}</value>
    </div>
  </div>
  
  {% if frigate_health.issues %}
  <div class="issues">
    <h3>Issues Detected</h3>
    <ul>
      {% for issue in frigate_health.issues %}
      <li>{{ issue }}</li>
      {% endfor %}
    </ul>
  </div>
  {% endif %}
</section>
```

---

## 4. Conditional Troubleshooting

If Frigate health is DEGRADED or BLOCKED, trigger additional diagnostics:

```python
async def conditional_troubleshooting(frigate_result):
    """If problems detected, gather more evidence."""
    
    status = frigate_result["health_status"]["label"]
    
    if status == "DEGRADED":
        print("🔍 System degraded. Gathering extended diagnostics...")
        
        # Capture longer window for pattern analysis
        extended = await capture_snapshot(
            frigate_url="http://frigate.local:5000",
            frigate_api_key=os.getenv("FRIGATE_API_KEY"),
            event_window_hours=72,  # 3 days instead of 1
            metric_sample_seconds=240,  # 4-minute sample
        )
        
        # Analyze for patterns
        issues = extended["health_status"]["issues"]
        
        # Could trigger additional actions:
        if "process uptime" in str(issues):
            # Frigate processes crashing - check logs, memory
            await gather_process_diagnostics()
        
        if "skipped frames" in str(issues):
            # Performance issue - check CPU, network
            await gather_performance_diagnostics()
    
    elif status == "BLOCKED":
        print("❌ Cannot collect diagnostics. Checking API access...")
        # Manual intervention needed - alert admin
        await send_alert("Frigate diagnostics unavailable")
```

---

## 5. Evidence Persistence & Archival

The agent writes timestamped snapshots:

```
/eagleha/evidence/frigate/
├── frigate-snapshot-2026-10-17/
│   ├── summary.md
│   ├── config-redacted.yml
│   ├── stats-samples.json
│   ├── events-24h.json
│   └── warnings-errors.txt
├── frigate-snapshot-2026-10-16/
│   └── ...
└── frigate-snapshot-2026-10-15/
    └── ...
```

**For archival/analysis:**

```bash
# Keep last 30 days (daily snapshots)
find /eagleha/evidence/frigate -name "frigate-snapshot-*" \
  -mtime +30 -exec rm -rf {} \;

# Export monthly summaries
for snapshot in /eagleha/evidence/frigate/frigate-snapshot-*/; do
  cat "$snapshot/summary.md" >> /eagleha/reports/frigate-monthly.md
done
```

---

## 6. Security & Permissions

### Container user:
The MCP server runs as non-root (`agent:agent`):

```bash
# In Dockerfile.frigate
USER agent
```

### Evidence directory:
```bash
# Set up on Foundry Pi
mkdir -p /eagleha/evidence/frigate
chmod 755 /eagleha/evidence/frigate
```

### API Key management:
Store securely, never in config files:

```bash
# Option 1: Docker secret
echo "sk_xxxx..." | docker secret create frigate_api_key -

# Option 2: Environment file
echo FRIGATE_API_KEY=sk_xxxx > /run/secrets/frigate_env
chmod 600 /run/secrets/frigate_env

# Option 3: Home Assistant secrets
# In secrets.yaml:
frigate_api_key: sk_xxxx...
```

---

## 7. Integration Checklist

- [ ] Frigate API key created (read-only scopes)
- [ ] Network path verified (Foundry Pi → Frigate)
- [ ] Evidence directory set up (`/eagleha/evidence/frigate`)
- [ ] MCP server running (Docker or standalone)
- [ ] Claude MCP config updated with tool definition
- [ ] Briefing code modified to call `capture_snapshot()`
- [ ] Results formatted for display
- [ ] Conditional troubleshooting rules defined
- [ ] Archival/cleanup scheduled (optional)
- [ ] Manual test run successful

---

## 8. Example: Full Briefing Integration

```python
# eagleha_briefing.py - Complete integration example

import asyncio
import os
from datetime import datetime
from pathlib import Path

from frigate_diagnostic_agent import capture_snapshot


async def generate_full_briefing():
    """Generate complete EagleHA daily briefing."""
    
    briefing = {
        "generated_at": datetime.utcnow().isoformat(),
        "sections": {}
    }
    
    # 1. Frigate Health (NEW)
    print("[1/5] Gathering Frigate diagnostics...")
    try:
        frigate_result = await capture_snapshot(
            frigate_url=os.getenv("FRIGATE_URL", "http://frigate.local:5000"),
            frigate_api_key=os.getenv("FRIGATE_API_KEY"),
            event_window_hours=24,
            metric_sample_seconds=120,
        )
        briefing["sections"]["frigate"] = {
            "health": frigate_result["health_status"],
            "summary": frigate_result["data_summary"],
            "evidence_path": frigate_result["output_dir"],
        }
    except Exception as e:
        briefing["sections"]["frigate"] = {
            "error": str(e),
            "health": {"label": "BLOCKED", "summary": "Collection failed"}
        }
    
    # 2. Home Assistant Status
    print("[2/5] Home Assistant status...")
    # ... existing code ...
    
    # 3. Security System Status
    print("[3/5] Security system status...")
    # ... existing code ...
    
    # 4. Energy Usage
    print("[4/5] Energy usage...")
    # ... existing code ...
    
    # 5. Alerts & Conditional Actions
    print("[5/5] Checking for alerts...")
    if briefing["sections"]["frigate"]["health"]["label"] != "PASS":
        print(f"  ⚠️  Frigate health: {briefing['sections']['frigate']['health']['label']}")
        # Could trigger additional diagnostics here
    
    return briefing


async def main():
    briefing = await generate_full_briefing()
    
    # Output to file
    output_path = Path("/eagleha/briefings") / f"briefing-{datetime.utcnow().strftime('%Y-%m-%d')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    import json
    output_path.write_text(json.dumps(briefing, indent=2))
    
    print(f"\n✓ Briefing generated: {output_path}")
    return briefing


if __name__ == "__main__":
    briefing = asyncio.run(main())
```

---

## Next Steps

1. **Set up Frigate API key** (see FRIGATE_SETUP.md)
2. **Deploy MCP server** (Docker or standalone)
3. **Test connectivity** (`python test_frigate_agent.py`)
4. **Integrate into briefing pipeline** (code samples above)
5. **Run first briefing** and verify output
6. **Schedule daily runs** (cron or Home Assistant automation)

---

## Questions?

Refer to:
- `FRIGATE_SETUP.md` — Deployment and configuration
- `frigate_diagnostic_agent.py` — Agent source code
- `frigate_mcp_server.py` — MCP server implementation
- `test_frigate_agent.py` — Testing and verification
