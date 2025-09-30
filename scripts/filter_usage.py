#!/usr/bin/env python

import argparse
import json
import os
import sys
import logging
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from openfilter.filter_runtime import Filter
from openfilter.filter_runtime.filters.webvis import Webvis
from openfilter.filter_runtime.filters.video_in import VideoIn
from filter_stub_application.filter import FilterStubApplication, FilterStubApplicationConfig


def create_sample_events():
    """Create sample events file for testing."""
    # Ensure input directory exists
    os.makedirs("input", exist_ok=True)
    
    events = [
        {"id": "event_1", "type": "sensor_reading", "value": 25.5, "timestamp": "2024-01-01T10:00:00Z", "location": "zone_a"},
        {"id": "event_2", "type": "sensor_reading", "value": 26.0, "timestamp": "2024-01-01T10:01:00Z", "location": "zone_b"},
        {"id": "event_3", "type": "alert", "message": "Temperature threshold exceeded", "severity": "warning", "timestamp": "2024-01-01T10:02:00Z"},
        {"id": "event_4", "type": "sensor_reading", "value": 24.8, "timestamp": "2024-01-01T10:03:00Z", "location": "zone_a"},
        {"id": "event_5", "type": "maintenance", "action": "filter_replacement", "status": "completed", "timestamp": "2024-01-01T10:04:00Z"}
    ]
    
    with open("input/events.json", "w") as f:
        json.dump(events, f, indent=2)
    
    print("Created sample events file: input/events.json")


def create_sample_template():
    """Create sample JSON schema template for random mode."""
    # Ensure input directory exists
    os.makedirs("input", exist_ok=True)
    
    template = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "pattern": "^event_[0-9]+$"},
            "type": {"type": "string", "enum": ["sensor_reading", "alert", "maintenance", "status_update"]},
            "value": {"type": "number", "minimum": 0, "maximum": 100},
            "timestamp": {"type": "string", "format": "date-time"},
            "location": {"type": "string", "enum": ["zone_a", "zone_b", "zone_c", "zone_d"]},
            "message": {"type": "string", "minLength": 10, "maxLength": 200},
            "severity": {"type": "string", "enum": ["info", "warning", "error", "critical"]},
            "action": {"type": "string", "enum": ["filter_replacement", "calibration", "inspection", "cleaning"]},
            "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "failed"]}
        },
        "required": ["id", "type", "timestamp"],
        "additionalProperties": False
    }
    
    with open("input/events_template.json", "w") as f:
        json.dump(template, f, indent=2)
    
    print("Created sample template file: input/events_template.json")


def main():
    parser = argparse.ArgumentParser(description="Run the FilterStubApplication test pipeline.")
    parser.add_argument("--output_path", default="output/stub_events.json", help="Where the output events will be saved.")
    parser.add_argument("--mode", choices=["echo", "random"], default="echo", help="Output mode: echo or random.")
    args = parser.parse_args()

    # Create sample data files if they don't exist
    if not os.path.exists("input/events.json"):
        create_sample_events()
    
    if not os.path.exists("input/events_template.json"):
        create_sample_template()

    # Ensure output directory exists
    os.makedirs(os.path.dirname(args.output_path), exist_ok=True)

    # Load configuration from environment variables
    debug = os.getenv('FILTER_DEBUG', 'False').lower() in ('true', '1', 'yes')
    forward_upstream_data = os.getenv('FILTER_FORWARD_UPSTREAM_DATA', 'True').lower() in ('true', '1', 'yes')
    output_mode = os.getenv('FILTER_OUTPUT_MODE', args.mode)
    output_json_path = os.getenv('FILTER_OUTPUT_JSON_PATH', args.output_path)
    input_json_events_file_path = os.getenv('FILTER_INPUT_JSON_EVENTS_FILE_PATH', './input/events.json')
    input_json_template_file_path = os.getenv('FILTER_INPUT_JSON_TEMPLATE_FILE_PATH', './input/events_template.json')

    print("Starting FilterStubApplication pipeline...")
    print(f"Mode: {output_mode}")
    print(f"Output file: {output_json_path}")
    print(f"Debug: {debug}")
    print(f"Forward upstream data: {forward_upstream_data}")
    print(f"Web visualization: http://localhost:{os.getenv('WEBVIS_PORT', '8000')}")
    print("Press Ctrl+C to stop")

    # Configure the pipeline
    Filter.run_multi(
        [
            (
                VideoIn,
                dict(
                    id="video_in",
                    sources=f"file://{str(os.getenv('VIDEO_INPUT', '../data/sample-video.mp4'))}!resize=960x540lin!loop",
                    outputs="tcp://*:6000",
                )
            ),
            # Stub application filter
            (
                FilterStubApplication,
                dict(
                    id="stub_app",
                    sources="tcp://127.0.0.1:6000",
                    outputs="tcp://*:6002",
                    debug=debug,
                    forward_upstream_data=forward_upstream_data,
                    output_mode=output_mode,
                    output_json_path=output_json_path,
                    input_json_events_file_path=input_json_events_file_path,
                    input_json_template_file_path=input_json_template_file_path
                )
            ),
            # Web visualization
            (
                Webvis,
                dict(
                    id="webvis",
                    sources="tcp://127.0.0.1:6002",
                    port=int(os.getenv('WEBVIS_PORT', '8000'))
                )
            )
        ]
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    main()
