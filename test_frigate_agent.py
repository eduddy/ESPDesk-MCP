#!/usr/bin/env python3
"""
Test script for Frigate Diagnostic Agent

Verifies connectivity, runs a capture, and validates output.
Usage: python test_frigate_agent.py --frigate-url http://frigate.local:5000
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx

from frigate_diagnostic_agent import capture_snapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def test_connectivity(frigate_url: str, api_key: str | None = None) -> bool:
    """Test basic connectivity to Frigate API."""
    logger.info("Testing Frigate API connectivity...")

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
            resp = await client.get(f"{frigate_url}/api/version")
            resp.raise_for_status()
            version_data = resp.json()
            logger.info(f"✓ Connected to Frigate {version_data.get('version', 'unknown')}")
            return True
    except Exception as e:
        logger.error(f"✗ Connection failed: {e}")
        return False


async def test_api_scopes(frigate_url: str, api_key: str | None = None) -> bool:
    """Verify API has required read scopes."""
    logger.info("Checking API scopes...")

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    endpoints_to_test = [
        ("/api/stats", "stats:read"),
        ("/api/events?limit=1", "events:read"),
        ("/api/config", "config:read"),
    ]

    all_ok = True
    async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
        for endpoint, scope in endpoints_to_test:
            try:
                resp = await client.get(f"{frigate_url}{endpoint}")
                if resp.status_code == 401:
                    logger.error(f"✗ {scope}: Unauthorized (check API key)")
                    all_ok = False
                elif resp.status_code == 403:
                    logger.error(f"✗ {scope}: Forbidden (missing scope)")
                    all_ok = False
                elif resp.status_code == 200:
                    logger.info(f"✓ {scope}: OK")
                else:
                    logger.warning(f"? {scope}: HTTP {resp.status_code}")
            except Exception as e:
                logger.error(f"✗ {scope}: {e}")
                all_ok = False

    return all_ok


async def test_capture(
    frigate_url: str,
    api_key: str | None = None,
    output_dir: str | None = None,
) -> dict:
    """Run a test capture."""
    logger.info("Running diagnostic capture (this takes ~2 minutes)...")

    if not output_dir:
        output_dir = "/tmp/frigate-test"

    try:
        result = await capture_snapshot(
            frigate_url=frigate_url,
            frigate_api_key=api_key,
            output_dir=output_dir,
            event_window_hours=24,
            metric_sample_seconds=60,  # Shorter for test
            include_media=False,
        )

        logger.info(f"✓ Capture complete")
        logger.info(f"  Health: {result['health_status']['label']}")
        logger.info(f"  Cameras: {result['data_summary']['cameras']}")
        logger.info(f"  Events: {result['data_summary']['events_24h']}")
        logger.info(f"  Detectors: {result['data_summary']['detectors']}")
        logger.info(f"  Output: {result['output_dir']}")

        return result
    except Exception as e:
        logger.error(f"✗ Capture failed: {e}")
        return {"error": str(e)}


def validate_output(result: dict) -> bool:
    """Validate capture result structure."""
    logger.info("Validating output...")

    required_keys = {"health_status", "output_dir", "output_files", "data_summary"}
    missing = required_keys - set(result.keys())

    if missing:
        logger.error(f"✗ Missing keys: {missing}")
        return False

    logger.info(f"✓ Output structure valid")

    # Check output files exist
    output_dir = Path(result["output_dir"])
    for file_key, file_path_str in result["output_files"].items():
        file_path = Path(file_path_str)
        if file_path.exists():
            size_kb = file_path.stat().st_size / 1024
            logger.info(f"✓ {file_key}: {file_path.name} ({size_kb:.1f} KB)")
        else:
            logger.warning(f"⚠ {file_key}: file not found ({file_path})")

    return True


async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Test Frigate Diagnostic Agent connectivity and functionality"
    )
    parser.add_argument(
        "--frigate-url",
        required=True,
        help="Frigate API URL (e.g., http://frigate.local:5000)",
    )
    parser.add_argument(
        "--frigate-api-key",
        help="Optional API key for authentication",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for test captures (default: /tmp/frigate-test)",
    )

    args = parser.parse_args()

    print("\n" + "="*60)
    print("FRIGATE DIAGNOSTIC AGENT TEST")
    print("="*60 + "\n")

    # Step 1: Connectivity
    print("STEP 1: Connectivity Test")
    print("-" * 40)
    if not await test_connectivity(args.frigate_url, args.frigate_api_key):
        logger.error("\n✗ Cannot proceed: no connectivity to Frigate")
        return False
    print()

    # Step 2: API Scopes
    print("STEP 2: API Scope Verification")
    print("-" * 40)
    if not await test_api_scopes(args.frigate_url, args.frigate_api_key):
        logger.warning("\n⚠ Some scopes may be missing (try with a properly scoped API key)")
    print()

    # Step 3: Capture
    print("STEP 3: Diagnostic Capture")
    print("-" * 40)
    result = await test_capture(
        args.frigate_url,
        args.frigate_api_key,
        args.output_dir,
    )

    if "error" in result:
        logger.error("\n✗ Capture failed")
        return False
    print()

    # Step 4: Validation
    print("STEP 4: Output Validation")
    print("-" * 40)
    if not validate_output(result):
        logger.error("\n✗ Output validation failed")
        return False
    print()

    # Summary
    print("="*60)
    print("✓ ALL TESTS PASSED")
    print("="*60)
    print(f"\nSummary:")
    print(f"  Health Status: {result['health_status']['label']}")
    print(f"  Cameras Found: {result['data_summary']['cameras']}")
    print(f"  Events (24h): {result['data_summary']['events_24h']}")
    print(f"  Detectors: {result['data_summary']['detectors']}")
    print(f"\nOutput Location:")
    print(f"  {result['output_dir']}")
    print(f"\nNext Steps:")
    print(f"  1. Review {result['output_dir']}/*/summary.md")
    print(f"  2. If health is DEGRADED/BLOCKED, check the detailed JSON files")
    print(f"  3. Configure agent for production use (see FRIGATE_SETUP.md)")
    print()

    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
