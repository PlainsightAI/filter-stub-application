# JSONSim

[![PyPI version](https://img.shields.io/pypi/v/filter-stub-application.svg?style=flat-square)](https://pypi.org/project/filter-stub-application/)
[![Docker Version](https://img.shields.io/docker/v/plainsightai/openfilter-stub-application?sort=semver)](https://hub.docker.com/r/plainsightai/openfilter-stub-application)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/PlainsightAI/filter-stub-application/blob/main/LICENSE)

JSONSim is a synthetic filter that outputs structured JSON events without analyzing image frames. Perfect for testing and debugging pipelines that expect event streams.

## Features

- **Three Output Modes**:
  - **Echo Mode**: Replays events from a static JSON file
  - **Random Mode**: Generates synthetic events using JSON Schema templates (`hypothesis-jsonschema`)
  - **Realistic Mode**: Draft-07 generator with optional process profile (`FILTER_OUTPUT_MODE=realistic`)
- **Upstream Data Forwarding**: Optionally forwards non-image frames from upstream filters
- **Environment Variable Configuration**: Easy setup using environment variables
- **Debug Logging**: Comprehensive logging for troubleshooting
- **Sample Data Generation**: Automatically creates sample files for quick testing

## Quick Start

### Using the Usage Script

The easiest way to run the filter is using the provided `filter_usage.py` script:

```bash
# Install dependencies
make install

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

### Using Make Commands

```bash
# Run with default settings
make run

# Run tests
make test
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FILTER_DEBUG` | Enable debug logging | `false` |
| `FILTER_OUTPUT_MODE` | Output mode (`echo` / `random` / `realistic`) | `random` |
| `FILTER_FORWARD_UPSTREAM_DATA` | Forward upstream data | `true` |
| `FILTER_FORWARD_IMAGES` | Share the image when attaching an event | `false` |
| `FILTER_OUTPUT_JSON_PATH` | Output file path | `./output/events.json` |
| `FILTER_INPUT_JSON_EVENTS_FILE_PATH` | Input events file (echo) | `./input/events.json` |
| `FILTER_INPUT_JSON_TEMPLATE_FILE_PATH` | Schema / template (random, realistic). Realistic `format` on strings supports `date-time`, `date`, `time`, `email`, `uuid`, `uri`, `hostname`; other string formats fail at setup. `format` on non-string types is ignored. | `./input/events_template.json` |
| `FILTER_PROCESS_PROFILE_PATH` | Optional semi-Markov profile (realistic) | empty |
| `FILTER_TRIGGER_MODE` | Emit on `image` frames or every `process` call (realistic) | `image` |
| `FILTER_EMIT_EVERY_N_FRAMES` | Emit every N trigger opportunities | `1` |
| `FILTER_EVENT_TOPIC` | Topic that receives the generated event | `main` |
| `FILTER_FRAME_EVENT_KEY` | Frame data key for the document | `event` |
| `FILTER_FRAME_EVENT_COLLISION` | `error` / `overwrite` / `skip` | `error` |
| `FILTER_APPEND_OUTPUT` | Append to an existing NDJSON file | `false` |
| `FILTER_FAILURE_POLICY` | `drop` or `stop` after payload retries | `drop` |
| `FILTER_IO_FAILURE_POLICY` | `stop` or `drop` on write failure | `stop` |
| `FILTER_REALISTIC_SEED` | Master seed for realistic streams | `0` |
| `FILTER_REALISTIC_OPTIONAL_PROBABILITY` | Chance of emitting an optional property | `0.5` |
| `FILTER_REALISTIC_NULL_PROBABILITY` | Chance of choosing `null` in a type union | `0.1` |
| `FILTER_REALISTIC_EXAMPLE_PROBABILITY` | Chance of using a valid `examples` value | `0.3` |
| `FILTER_REALISTIC_MAX_ATTEMPTS` | Payload retries per event | `20` |
| `FILTER_REALISTIC_MAX_DEPTH` | Generated instance depth cap | `32` |
| `FILTER_REALISTIC_MAX_NODES` | Generated node budget | `10000` |
| `FILTER_REALISTIC_MAX_EVENT_BYTES` | Serialized event size cap | `1048576` |
| `FILTER_REALISTIC_PREFLIGHT_SAMPLES` | Documents generated at setup | `10` |
| `FILTER_REALISTIC_ARRAY_MAX_WHEN_UNBOUNDED` | Extra items when `maxItems` is absent | `3` |
| `FILTER_REALISTIC_NUMBER_BOUND_WHEN_UNBOUNDED` | Symmetric numeric span when min/max are absent | `1000` |
| `FILTER_TYPE_WEIGHTS` | JSON object of type → weight for unions | `{}` (uniform) |
| `FILTER_PROCESS_TICK_SECONDS` | Clock step when the profile has no arrival | `1.0` |
| `VIDEO_INPUT` | Video source | `../data/sample-video.mp4` |
| `WEBVIS_PORT` | Web visualization port | `8000` |

### Input File Formats

**Echo Mode** - JSON Array or JSON Lines:
```json
[
  {"id": "event_1", "type": "sensor", "value": 25.5},
  {"id": "event_2", "type": "alert", "message": "Warning"}
]
```

**Random Mode** - JSON Schema:
```json
{
  "type": "object",
  "properties": {
    "id": {"type": "string"},
    "type": {"type": "string", "enum": ["sensor", "alert"]},
    "value": {"type": "number", "minimum": 0, "maximum": 100}
  },
  "required": ["id", "type"]
}
```

## Requirements

To follow these instructions there are a few prerequisites. You must:

- Be authenticated to GAR:
```
gcloud auth login
gcloud auth application-default login
```

- Set your gcloud project to plainsightai-prod and configure docker to use gcloud:
```
gcloud config set project plainsightai-prod
gcloud auth configure-docker us-west1-docker.pkg.dev
```

It is assumed you will be running this on a GPU. If not then you will have to comment out the `deploy:` section in the `docker-compose.yaml` file and the unit test will fail since it compares against GPU numbers.

## Install

In order to run the filter locally or build/publish the Python wheel we need to install properly:

    virtualenv venv
    source venv/bin/activate
    make install

## Advanced Usage

### Custom Event Files

Create your own event files for echo mode:

```bash
# Create custom events file
cat > input/my_events.json << EOF
[
  {"id": "custom_1", "type": "sensor", "value": 42.0, "location": "zone_a"},
  {"id": "custom_2", "type": "alert", "message": "Custom alert", "severity": "high"}
]
EOF

# Run with custom events
export FILTER_INPUT_JSON_EVENTS_FILE_PATH=./input/my_events.json
python scripts/filter_usage.py
```

### Custom Schema Templates

Create custom JSON schemas for random mode:

```bash
# Create custom schema
cat > input/my_schema.json << EOF
{
  "type": "object",
  "properties": {
    "id": {"type": "string", "pattern": "^custom_[0-9]+$"},
    "type": {"type": "string", "enum": ["sensor", "alert", "status"]},
    "value": {"type": "number", "minimum": 0, "maximum": 1000},
    "timestamp": {"type": "string", "format": "date-time"}
  },
  "required": ["id", "type", "timestamp"]
}
EOF

# Run with custom schema
export FILTER_INPUT_JSON_TEMPLATE_FILE_PATH=./input/my_schema.json
export FILTER_OUTPUT_MODE=random
python scripts/filter_usage.py
```

### Debug Mode

Enable debug logging for detailed information:

```bash
export FILTER_DEBUG=true
python scripts/filter_usage.py
```

## Docker Usage

### Environment Variables
- Docker-compose automatically reads `.env` files in the same directory as the compose files. The provided `.env.example` file can serve as a template to create a `.env` file.

**IMPORTANT!** If your filter uses the GPU and `make compose` doesn't automatically add it to the `docker-compose.yaml` then make sure to add the following to your filter's section in the compose file:

    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]

First, build the filter docker image:

    make build-image

If you changed the PIPELINE in the Makefile (if not then skip this step), then rebuild the docker-compose.yaml (you may have to tweak the generated docker-compose.yaml):

    make compose

Now run it:

    make run-image

Again, navigating to `http://localhost:8000` will show you the video.

## Testing

Run the comprehensive test suite:

```bash
# Run all tests
make test

# Run specific test categories
pytest tests/test_smoke_simple.py -v
pytest tests/test_integration_config_normalization.py -v
```

The test suite includes:
- **Smoke Tests**: Basic functionality and end-to-end testing
- **Integration Tests**: Configuration validation and normalization
- **Unit Tests**: Individual component testing

## Development

### VS Code Debugging

Use the provided VS Code launch configuration:

1. Open VS Code in the project directory
2. Go to Run and Debug (Ctrl+Shift+D)
3. Select "JSONSim - Usage Script"
4. Set breakpoints and start debugging

### Make Commands

```bash
make install    # Install dependencies
make test       # Run tests
make debug      # Run in debug mode
make run        # Run with default settings
make build-image # Build Docker image
make compose    # Generate docker-compose.yaml
```

## Publishing

- Ensure the `VERSION` file at root has a production semver tag (i.e. `v1.2.3`)
    - If you intend to release a non-production version such as a development, release candidate or an internal release then add a build number and a classification to your version tag (i.e. `v1.2.3.4-dev`, `v1.2.3.0-rc` or `v1.2.3.47-int`)
- Ensure the version tag of newest entry in `RELEASE.md` matches the tag in `VERSION`
    - Important: Our releases are documentation driven. Not updating `RELEASE.md` will not trigger a release. Filters cannot be merged to main unless `RELEASE.md` is updated. The `RELEASE.md` file is validated by our CI and requires version entries to be in the correct descending order.
- Simple merge to main. When a new version is detected in `RELEASE.md` the CI will:
  - Build and publish the docker image to the GAR OCI registry
  - Build and publish the python wheel to the GAR python registry
  - Push the docs to both production and development documentation sites