---
title: Overview
id: jsonsim
sidebar_position: 1
---

import Admonition from '@theme/Admonition';

# JSONSim

JSONSim is a simple synthetic filter that outputs structured events without analyzing image frames. It is ideal for testing and debugging pipelines that expect a stream of JSON events.

## Features

- **Two Output Modes**  
  - `echo`: Emits real events from a static JSON file
  - `random`: Emits synthetic events generated from a user-defined JSON schema  
  Select the mode using the `output_mode` parameter.

- **Schema-Based Random Generation**  
  The `random` mode uses [JSON Schema](https://json-schema.org/) to generate realistic test events.
  
- **Configurable Output**  
  Events are streamed to the location defined by `output_json_path` in newline-delimited JSON format.

- **Upstream Data Forwarding**  
  Optionally forwards non-image frames from upstream filters using `forward_upstream_data`.

- **Debug Logging**  
  Enable `debug: true` to activate verbose logs for insight into behavior and issues.

- **Graceful Setup and Shutdown**  
  The filter ensures proper file handling by opening and closing resources as needed.

## Quick Start

### Using the Usage Script

The easiest way to run the filter is using the provided `filter_usage.py` script:

```bash
# Run with default settings (echo mode)
python scripts/filter_usage.py

# Run in random mode
python scripts/filter_usage.py --mode random

# Specify custom output path
python scripts/filter_usage.py --output_path ./my_events.json
```

### Using Environment Variables

Configure the filter using environment variables:

```bash
export FILTER_DEBUG=true
export FILTER_OUTPUT_MODE=random
export FILTER_FORWARD_UPSTREAM_DATA=true
export FILTER_OUTPUT_JSON_PATH=./output/events.json
export FILTER_INPUT_JSON_EVENTS_FILE_PATH=./input/events.json
export FILTER_INPUT_JSON_TEMPLATE_FILE_PATH=./input/events_template.json
export VIDEO_INPUT=./data/sample-video.mp4
export WEBVIS_PORT=8000

python scripts/filter_usage.py
```

## Example Output

Each event is emitted to the output file as a single JSON line, e.g.:

```json
{
  "id": "event_1",
  "type": "sensor_reading",
  "value": 25.5,
  "timestamp": "2024-01-01T10:00:00Z",
  "location": "zone_a"
}
```

## When to Use

Use this filter when:

- You want to simulate event streams for integration or performance testing
- You want to test downstream processing logic without requiring real input data
- You want to verify pipeline stability with synthetic or controlled data
- You need to test event processing pipelines with consistent, repeatable data

## Configuration Reference

| Key                          | Type       | Default                             | Description |
|-----------------------------|------------|-------------------------------------|-------------|
| `output_mode`               | `string`   | `"echo"`                            | Mode of operation: `"echo"` or `"random"` |
| `debug`                     | `boolean`  | `false`                             | Enable debug logging |
| `forward_upstream_data`     | `boolean`  | `true`                              | Forward non-image frames from upstream filters |
| `output_json_path`          | `string`   | `"./output/output.json"`            | Path to save emitted JSON events |
| `input_json_events_file_path` | `string` | `"./input/events.json"`            | Path to input file for echo mode |
| `input_json_template_file_path` | `string` | `"./input/events_template.json"`  | Path to JSON schema template for random mode |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FILTER_DEBUG` | Enable debug logging | `false` |
| `FILTER_OUTPUT_MODE` | Output mode (echo/random) | `echo` |
| `FILTER_FORWARD_UPSTREAM_DATA` | Forward upstream data | `true` |
| `FILTER_OUTPUT_JSON_PATH` | Output file path | `./output/output.json` |
| `FILTER_INPUT_JSON_EVENTS_FILE_PATH` | Input events file | `./input/events.json` |
| `FILTER_INPUT_JSON_TEMPLATE_FILE_PATH` | Input template file | `./input/events_template.json` |
| `VIDEO_INPUT` | Video source | `../data/sample-video.mp4` |
| `WEBVIS_PORT` | Web visualization port | `8000` |

<Admonition type="tip" title="Tip">
Echo mode is great for replaying fixed data during development. Use random mode to test edge cases with synthetic variety.
</Admonition>

<Admonition type="info" title="Sample Files">
The usage script automatically creates sample `events.json` and `events_template.json` files if they don't exist, making it easy to get started quickly.
</Admonition>
