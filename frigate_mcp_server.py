#!/usr/bin/env python3
"""
MCP Server for Frigate Diagnostics

Exposes frigate.capture_snapshot as a single constrained tool for safe
automated diagnostics collection.

Usage:
    python frigate_mcp_server.py --frigate-url http://frigate.local:5000
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

from fastmcp import FastMCP

from frigate_diagnostic_agent import capture_snapshot

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="Frigate Diagnostics",
    instructions="""\
This MCP server provides automated diagnostics collection for Frigate video processing systems.

## Tool: capture_snapshot

Collects system health, configuration, events and logs from Frigate.

**Output structure:**
- `health_status`: Overall system health (PASS/DEGRADED/BLOCKED)
- `output_dir`: Directory containing diagnostic files
- `output_files`: Paths to generated reports:
  - `summary`: Markdown report of all findings
  - `config`: Sanitized YAML configuration
  - `stats`: JSON metrics samples
  - `events`: Recent event data
  - `logs`: Warning/error log entries
- `data_summary`: Quick counts (cameras, events, detectors, issues)

**Security model:**
- Read-only access to Frigate API
- No configuration modifications
- No event deletion or retention control
- Sanitized output (passwords/tokens redacted)
- Optional media exclusion (thumbnails/clips)

**Typical usage:**
1. Call with default parameters to gather 24h of history
2. Check health_status for overall system health
3. Review summary.md for human-readable findings
4. Inspect detailed JSON files for programmatic analysis
""",
)


@mcp.tool()
async def capture_snapshot(
    event_window_hours: int = 24,
    metric_sample_seconds: int = 120,
    include_media: bool = False,
) -> dict:
    """
    Capture Frigate diagnostic snapshot.

    Collects system metrics, configuration, events and logs for troubleshooting.
    All data is sanitized (passwords/tokens redacted).

    Args:
        event_window_hours: Hours of event history to capture (1-168, default 24)
        metric_sample_seconds: Duration for metric collection (30-300, default 120)
        include_media: Whether to include event thumbnails/clips (default false)

    Returns:
        Diagnostic data bundle with health status, output paths and summary counts
    """
    # Validate inputs
    event_window_hours = max(1, min(168, event_window_hours))
    metric_sample_seconds = max(30, min(300, metric_sample_seconds))

    logger.info(
        f"Capture requested: window={event_window_hours}h, "
        f"sample={metric_sample_seconds}s, media={include_media}"
    )

    frigate_url = global_config["frigate_url"]
    frigate_api_key = global_config.get("frigate_api_key")
    output_dir = global_config.get("output_dir", "/eagleha/evidence/frigate")

    try:
        result = await capture_snapshot(
            frigate_url=frigate_url,
            frigate_api_key=frigate_api_key,
            output_dir=output_dir,
            event_window_hours=event_window_hours,
            metric_sample_seconds=metric_sample_seconds,
            include_media=include_media,
        )

        logger.info(f"Capture complete: {result['health_status']['label']}")
        return result
    except Exception as e:
        logger.error(f"Capture failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "health_status": {
                "label": "BLOCKED",
                "summary": "Diagnostic capture failed",
                "issues": [str(e)],
            },
        }


# Global configuration (set at startup)
global_config = {}


def main():
    """Run MCP server."""
    import argparse

    parser = argparse.ArgumentParser(description="Frigate Diagnostics MCP Server")
    parser.add_argument(
        "--frigate-url",
        required=True,
        help="Frigate API base URL (e.g., http://frigate.local:5000)",
    )
    parser.add_argument(
        "--frigate-api-key",
        help="Optional API key for Frigate authentication",
    )
    parser.add_argument(
        "--output-dir",
        default="/eagleha/evidence/frigate",
        help="Output directory for diagnostic files",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Store config globally
    global_config["frigate_url"] = args.frigate_url
    if args.frigate_api_key:
        global_config["frigate_api_key"] = args.frigate_api_key
    global_config["output_dir"] = args.output_dir

    logger.info(f"Starting Frigate Diagnostics MCP Server")
    logger.info(f"Frigate URL: {args.frigate_url}")
    logger.info(f"Output directory: {args.output_dir}")

    # Run MCP server (uses stdio by default)
    mcp.run()


if __name__ == "__main__":
    main()
