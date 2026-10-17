#!/usr/bin/env python3
"""
Frigate Diagnostic Evidence Collector

Automated agent for gathering Frigate system health and event data.
Produces structured diagnostic output for troubleshooting.

Security model:
- Read-only access to Frigate API and logs
- No modification of config, events, or camera control
- No access to credentials, MQTT, or RTSP streams
- Constrained single-tool interface
"""

import asyncio
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import yaml

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticConfig:
    """Configuration for diagnostic capture."""

    frigate_url: str
    frigate_api_key: str | None = None
    output_dir: Path = Path("/eagleha/evidence/frigate")
    event_window_hours: int = 24
    metric_sample_seconds: int = 120
    include_media: bool = False


@dataclass
class HealthStatus:
    """Health assessment result."""

    label: str  # PASS, DEGRADED, or BLOCKED
    summary: str
    issues: list[str]
    timestamp: str


class FrigateDiagnosticCollector:
    """Collects diagnostic data from Frigate API."""

    def __init__(self, config: DiagnosticConfig):
        self.config = config
        self.frigate_url = config.frigate_url.rstrip("/")
        self.output_dir = config.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session: httpx.AsyncClient | None = None
        self.data: dict[str, Any] = {}
        self.issues: list[str] = []

    async def __aenter__(self):
        headers = {}
        if self.config.frigate_api_key:
            headers["Authorization"] = f"Bearer {self.config.frigate_api_key}"

        self.session = httpx.AsyncClient(
            base_url=self.frigate_url,
            headers=headers,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.aclose()

    async def _get(self, endpoint: str) -> dict[str, Any] | None:
        """Safe GET request with error handling."""
        try:
            resp = await self.session.get(endpoint)
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except httpx.RequestError as e:
            self.issues.append(f"API request failed: {endpoint}: {e}")
            logger.error(f"Request failed: {endpoint}: {e}")
            return None
        except Exception as e:
            self.issues.append(f"Unexpected error: {endpoint}: {e}")
            logger.error(f"Unexpected error: {endpoint}: {e}")
            return None

    async def capture_version(self) -> bool:
        """Record Frigate version and capture time."""
        logger.info("Capturing version...")
        version_data = await self._get("/api/version")
        if not version_data:
            self.issues.append("Unable to fetch version")
            return False

        self.data["version"] = version_data
        self.data["capture_time"] = datetime.utcnow().isoformat()
        logger.info(f"Frigate version: {version_data}")
        return True

    async def capture_stats_samples(self) -> bool:
        """Sample /api/stats every 15 seconds for specified duration."""
        logger.info(f"Capturing stats samples (duration={self.config.metric_sample_seconds}s)...")
        samples = []
        sample_interval = 15  # seconds
        num_samples = max(1, self.config.metric_sample_seconds // sample_interval)

        for i in range(num_samples):
            stats = await self._get("/api/stats")
            if stats:
                stats["sample_timestamp"] = datetime.utcnow().isoformat()
                samples.append(stats)
                logger.debug(f"Sample {i+1}/{num_samples} collected")

            if i < num_samples - 1:
                await asyncio.sleep(sample_interval)

        if not samples:
            self.issues.append("Unable to collect stats samples")
            return False

        self.data["stats_samples"] = samples
        logger.info(f"Collected {len(samples)} stats samples")
        return True

    def _calculate_camera_health(self) -> dict[str, Any]:
        """Analyze camera health from stats samples."""
        if "stats_samples" not in self.data:
            return {"error": "No stats samples available"}

        samples = self.data["stats_samples"]
        camera_health = {}

        for sample in samples:
            if "cameras" not in sample:
                continue

            for cam_name, cam_data in sample["cameras"].items():
                if cam_name not in camera_health:
                    camera_health[cam_name] = {
                        "fps": [],
                        "skipped_frames": [],
                        "process_available": [],
                    }

                camera_health[cam_name]["fps"].append(cam_data.get("fps", 0))
                camera_health[cam_name]["skipped_frames"].append(cam_data.get("skipped_frames", 0))
                camera_health[cam_name]["process_available"].append(
                    cam_data.get("process_fps", 0) > 0
                )

        # Calculate averages
        for cam_name in camera_health:
            data = camera_health[cam_name]
            data["avg_fps"] = sum(data["fps"]) / len(data["fps"]) if data["fps"] else 0
            data["avg_skipped"] = (
                sum(data["skipped_frames"]) / len(data["skipped_frames"])
                if data["skipped_frames"]
                else 0
            )
            data["process_uptime_pct"] = (
                sum(data["process_available"]) / len(data["process_available"]) * 100
                if data["process_available"]
                else 0
            )
            del data["fps"]
            del data["skipped_frames"]
            del data["process_available"]

        self.data["camera_health"] = camera_health
        logger.info(f"Calculated health for {len(camera_health)} camera(s)")
        return camera_health

    def _extract_detector_info(self) -> dict[str, Any]:
        """Extract detector type, model, and latency from stats."""
        if "stats_samples" not in self.data or not self.data["stats_samples"]:
            return {"error": "No stats samples available"}

        sample = self.data["stats_samples"][0]
        detection_data = {}

        if "detectors" in sample:
            for detector_name, detector_info in sample["detectors"].items():
                detection_data[detector_name] = {
                    "type": detector_info.get("type", "unknown"),
                    "model": detector_info.get("model", "unknown"),
                    "avg_inference_ms": detector_info.get("inference_speed", 0),
                }

        self.data["detection_info"] = detection_data
        logger.info(f"Detected {len(detection_data)} detector(s)")
        return detection_data

    def _calculate_cpu_load(self) -> dict[str, Any]:
        """Calculate CPU load from stats samples."""
        if "stats_samples" not in self.data:
            return {"error": "No stats samples available"}

        samples = self.data["stats_samples"]
        cpu_data = {
            "total_host": [],
            "process_per_core": {},
        }

        for sample in samples:
            if "cpu_percent" in sample:
                cpu_data["total_host"].append(sample["cpu_percent"])

            if "cpu_percent_processes" in sample:
                for process_name, per_core_list in sample["cpu_percent_processes"].items():
                    if process_name not in cpu_data["process_per_core"]:
                        cpu_data["process_per_core"][process_name] = []
                    cpu_data["process_per_core"][process_name].extend(per_core_list)

        # Calculate averages
        result = {
            "avg_total_host": (
                sum(cpu_data["total_host"]) / len(cpu_data["total_host"])
                if cpu_data["total_host"]
                else 0
            ),
            "process_avg_per_core": {},
        }

        for proc_name, values in cpu_data["process_per_core"].items():
            result["process_avg_per_core"][proc_name] = sum(values) / len(values) if values else 0

        self.data["cpu_analysis"] = result
        logger.info("CPU analysis complete")
        return result

    async def capture_events(self) -> bool:
        """Retrieve events from the past N hours without thumbnails."""
        logger.info(f"Retrieving events (last {self.config.event_window_hours} hours)...")
        before = int(datetime.utcnow().timestamp())
        after = int(
            (datetime.utcnow() - timedelta(hours=self.config.event_window_hours)).timestamp()
        )

        events = await self._get(f"/api/events?after={after}&before={before}&limit=1000")
        if events is None:
            self.issues.append("Unable to fetch events")
            return False

        if isinstance(events, list):
            event_list = events
        elif isinstance(events, dict) and "events" in events:
            event_list = events["events"]
        else:
            event_list = []

        # Remove thumbnail data to reduce payload
        for event in event_list:
            event.pop("thumbnail", None)
            event.pop("media", None) if not self.config.include_media else None

        self.data["events"] = event_list
        logger.info(f"Retrieved {len(event_list)} events")

        # Validate presence of expected event data
        if event_list:
            sample_event = event_list[0]
            required_keys = {"camera", "label", "start_time", "end_time"}
            missing = required_keys - set(sample_event.keys())
            if missing:
                self.issues.append(f"Event data incomplete: missing {missing}")
                logger.warning(f"Event structure issue: {missing}")

        return True

    async def capture_config(self) -> bool:
        """Fetch and sanitize configuration."""
        logger.info("Retrieving configuration...")
        config_data = await self._get("/api/config")
        if not config_data:
            self.issues.append("Unable to fetch configuration")
            return False

        sanitized = self._sanitize_config(config_data)
        self.data["config"] = sanitized
        logger.info("Configuration retrieved and sanitized")
        return True

    def _sanitize_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive data from configuration."""
        import copy

        sanitized = copy.deepcopy(config)

        # Patterns for sensitive data
        sensitive_patterns = [
            r"password",
            r"secret",
            r"token",
            r"key",
            r"api_key",
            r"auth",
        ]
        pattern = re.compile("|".join(sensitive_patterns), re.IGNORECASE)

        def sanitize_recursive(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {
                    k: (
                        "***REDACTED***"
                        if pattern.search(k)
                        else sanitize_recursive(v)
                    )
                    for k, v in obj.items()
                }
            elif isinstance(obj, list):
                return [sanitize_recursive(item) for item in obj]
            else:
                return obj

        return sanitize_recursive(sanitized)

    async def capture_logs(self) -> bool:
        """Capture recent warnings and errors from logs."""
        logger.info("Retrieving logs...")
        try:
            logs = await self._get("/api/logs")
            if logs is None:
                logger.warning("Unable to fetch logs via API")
                return True  # Non-fatal

            # Filter warnings and errors
            filtered = []
            if isinstance(logs, list):
                for entry in logs:
                    if isinstance(entry, dict):
                        level = entry.get("level", "").upper()
                        if level in ("WARNING", "ERROR", "FATAL"):
                            filtered.append(entry)

            self.data["warnings_errors"] = filtered[:100]  # Last 100
            logger.info(f"Retrieved {len(filtered)} warning/error entries")
            return True
        except Exception as e:
            logger.warning(f"Log retrieval failed: {e}")
            return True  # Non-fatal

    def _calculate_health_status(self) -> HealthStatus:
        """Determine overall health status."""
        issues_count = len(self.issues)

        if issues_count == 0:
            # Check for degradation signals
            camera_health = self.data.get("camera_health", {})
            degraded = []

            for cam_name, health in camera_health.items():
                if health.get("process_uptime_pct", 100) < 95:
                    degraded.append(f"{cam_name}: process uptime {health['process_uptime_pct']:.1f}%")
                if health.get("avg_skipped", 0) > 10:
                    degraded.append(f"{cam_name}: skipped {health['avg_skipped']:.1f} frames/sample")

            if degraded:
                return HealthStatus(
                    label="DEGRADED",
                    summary="System operational but with issues",
                    issues=degraded,
                    timestamp=datetime.utcnow().isoformat(),
                )

            return HealthStatus(
                label="PASS",
                summary="System healthy",
                issues=[],
                timestamp=datetime.utcnow().isoformat(),
            )
        else:
            return HealthStatus(
                label="BLOCKED",
                summary="Unable to collect complete diagnostic data",
                issues=self.issues,
                timestamp=datetime.utcnow().isoformat(),
            )

    async def run_capture(self) -> dict[str, Any]:
        """Execute full diagnostic capture workflow."""
        logger.info("Starting Frigate diagnostic capture...")

        steps = [
            ("Version", self.capture_version),
            ("Stats", self.capture_stats_samples),
            ("Events", self.capture_events),
            ("Configuration", self.capture_config),
            ("Logs", self.capture_logs),
        ]

        for step_name, step_func in steps:
            try:
                success = await step_func()
                if not success:
                    logger.warning(f"{step_name} capture failed")
            except Exception as e:
                logger.error(f"{step_name} capture error: {e}")
                self.issues.append(f"{step_name} capture exception: {e}")

        # Compute analysis
        self._calculate_camera_health()
        self._extract_detector_info()
        self._calculate_cpu_load()

        health = self._calculate_health_status()
        self.data["health_status"] = asdict(health)

        logger.info(f"Capture complete. Health status: {health.label}")
        return self.data

    async def write_output(self, data: dict[str, Any]) -> dict[str, Path]:
        """Write diagnostic data to output directory."""
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        output_subdir = self.output_dir / f"frigate-snapshot-{date_str}"
        output_subdir.mkdir(parents=True, exist_ok=True)

        files = {}

        # Summary
        summary_path = output_subdir / "summary.md"
        summary_md = self._generate_summary_md(data)
        summary_path.write_text(summary_md)
        files["summary"] = summary_path
        logger.info(f"Wrote summary to {summary_path}")

        # Config (YAML)
        if "config" in data:
            config_path = output_subdir / "config-redacted.yml"
            config_path.write_text(yaml.dump(data["config"], default_flow_style=False))
            files["config"] = config_path
            logger.info(f"Wrote config to {config_path}")

        # Stats samples (JSON)
        if "stats_samples" in data:
            stats_path = output_subdir / "stats-samples.json"
            stats_path.write_text(json.dumps(data["stats_samples"], indent=2))
            files["stats"] = stats_path
            logger.info(f"Wrote stats to {stats_path}")

        # Events (JSON)
        if "events" in data:
            events_path = output_subdir / "events-24h.json"
            events_path.write_text(json.dumps(data["events"], indent=2))
            files["events"] = events_path
            logger.info(f"Wrote events to {events_path}")

        # Warnings/errors
        if "warnings_errors" in data:
            logs_path = output_subdir / "warnings-errors.txt"
            logs_content = "\n".join(
                [f"[{e.get('timestamp', 'N/A')}] {e.get('level', 'UNKNOWN')}: {e.get('message', '')}"
                 for e in data["warnings_errors"]]
            )
            logs_path.write_text(logs_content)
            files["logs"] = logs_path
            logger.info(f"Wrote logs to {logs_path}")

        return files

    def _generate_summary_md(self, data: dict[str, Any]) -> str:
        """Generate markdown summary of diagnostic data."""
        health = data.get("health_status", {})
        label = health.get("label", "UNKNOWN")
        timestamp = data.get("capture_time", "N/A")

        lines = [
            f"# Frigate Diagnostic Report",
            f"\n**Status**: `{label}`",
            f"\n**Captured**: {timestamp}",
            f"\n## Health Summary",
            f"\n{health.get('summary', 'N/A')}",
        ]

        if health.get("issues"):
            lines.append("\n### Issues\n")
            for issue in health["issues"]:
                lines.append(f"- {issue}")

        # Camera health
        camera_health = data.get("camera_health", {})
        if camera_health:
            lines.append("\n## Camera Health\n")
            for cam_name, health_data in camera_health.items():
                lines.append(f"\n### {cam_name}")
                lines.append(f"- Avg FPS: {health_data.get('avg_fps', 0):.1f}")
                lines.append(f"- Avg Skipped Frames: {health_data.get('avg_skipped', 0):.1f}")
                lines.append(f"- Process Uptime: {health_data.get('process_uptime_pct', 0):.1f}%")

        # Detection info
        detection_info = data.get("detection_info", {})
        if detection_info:
            lines.append("\n## Detection Configuration\n")
            for detector_name, info in detection_info.items():
                lines.append(f"\n### {detector_name}")
                lines.append(f"- Type: {info.get('type', 'N/A')}")
                lines.append(f"- Model: {info.get('model', 'N/A')}")
                lines.append(f"- Avg Inference: {info.get('avg_inference_ms', 0):.1f}ms")

        # CPU analysis
        cpu_analysis = data.get("cpu_analysis", {})
        if cpu_analysis:
            lines.append("\n## CPU Load\n")
            lines.append(f"- Total Host Avg: {cpu_analysis.get('avg_total_host', 0):.1f}%")
            if cpu_analysis.get("process_avg_per_core"):
                lines.append("\n### Per-Core Process Load")
                for proc_name, avg_load in cpu_analysis["process_avg_per_core"].items():
                    lines.append(f"- {proc_name}: {avg_load:.1f}%")

        # Events summary
        events = data.get("events", [])
        if events:
            lines.append(f"\n## Events (24h)")
            lines.append(f"\nTotal: {len(events)} events")

            # Group by camera
            by_camera = {}
            for event in events:
                cam = event.get("camera", "unknown")
                by_camera.setdefault(cam, []).append(event)

            for cam_name, cam_events in by_camera.items():
                labels = {}
                for event in cam_events:
                    label = event.get("label", "unknown")
                    labels[label] = labels.get(label, 0) + 1

                lines.append(f"\n### {cam_name}: {len(cam_events)} events")
                for label, count in sorted(labels.items(), key=lambda x: -x[1]):
                    lines.append(f"- {label}: {count}")

        lines.append("\n---\nGenerated by Frigate Diagnostic Agent")
        return "\n".join(lines)


async def capture_snapshot(
    frigate_url: str,
    frigate_api_key: str | None = None,
    output_dir: str | None = None,
    event_window_hours: int = 24,
    metric_sample_seconds: int = 120,
    include_media: bool = False,
) -> dict[str, Any]:
    """
    Capture Frigate diagnostic snapshot.

    Collects system metrics, configuration, events, and logs for troubleshooting.

    Args:
        frigate_url: Base URL of Frigate API (e.g., http://frigate.local:5000)
        frigate_api_key: Optional API key for authentication
        output_dir: Directory to write diagnostic files (default: /eagleha/evidence/frigate)
        event_window_hours: Hours of event history to capture (default: 24)
        metric_sample_seconds: Duration for metric sampling in seconds (default: 120)
        include_media: Whether to include event thumbnails/clips (default: false)

    Returns:
        Dictionary with diagnostic data and output file paths
    """
    config = DiagnosticConfig(
        frigate_url=frigate_url,
        frigate_api_key=frigate_api_key,
        output_dir=Path(output_dir) if output_dir else Path("/eagleha/evidence/frigate"),
        event_window_hours=event_window_hours,
        metric_sample_seconds=metric_sample_seconds,
        include_media=include_media,
    )

    async with FrigateDiagnosticCollector(config) as collector:
        data = await collector.run_capture()
        output_files = await collector.write_output(data)

    return {
        "health_status": data.get("health_status", {}),
        "output_dir": str(config.output_dir),
        "output_files": {k: str(v) for k, v in output_files.items()},
        "data_summary": {
            "cameras": len(data.get("camera_health", {})),
            "events_24h": len(data.get("events", [])),
            "detectors": len(data.get("detection_info", {})),
            "warnings_errors": len(data.get("warnings_errors", [])),
        },
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Example usage
    frigate_url = "http://frigate.local:5000"
    result = asyncio.run(
        capture_snapshot(
            frigate_url=frigate_url,
            frigate_api_key=None,  # Set if needed
            event_window_hours=24,
            metric_sample_seconds=120,
        )
    )

    print(json.dumps(result, indent=2))
