# Filter-Stub Subject Data Demo Scripts

This directory contains scripts to demonstrate the Filter-Stub's ability to emit JSON data as subject data on frames.

## Overview

The demo pipeline consists of three components:

1. **VideoIn**: Reads sample video frames from `data/sample-video.mp4`
2. **FilterStub**: Emits JSON subject data from `data/sample-subject-data.json` and attaches it to each frame
3. **Webvis**: Displays the video frames along with the attached subject data

This demonstrates how Filter-Stub can inject structured JSON metadata into the OpenFilter pipeline, making it available to downstream filters for processing, visualization, or logging.

## Quick Start

### Start the Demo Pipeline

```bash
./scripts/run-demo-pipeline.sh
```

This will:
- Build the Filter-Stub Docker image
- Start the complete pipeline (VideoIn → FilterStub → Webvis)
- Make the webvis interface available at http://localhost:8020

### View the Demo

Open your browser to **http://localhost:8020** to see:
- Live video frames from the sample video
- JSON subject data attached to each frame
- Metadata visualization in the webvis interface

### Stop the Demo Pipeline

```bash
./scripts/stop-demo-pipeline.sh
```

## What's Being Demonstrated

### Subject Data Emission

The Filter-Stub filter has been enhanced with a new configuration option:

```yaml
FILTER_EMIT_SUBJECT_DATA: "true"
```

When enabled, Filter-Stub:
1. Reads JSON events from the input file (`data/sample-subject-data.json`)
2. For each frame received from upstream filters
3. Attaches the JSON data to the frame's `data` field
4. Forwards the frame with metadata to downstream filters

### Configuration

The demo uses these key settings in `docker-compose.demo.yaml`:

```yaml
FILTER_OUTPUT_MODE: echo                              # Replay JSON from file
FILTER_INPUT_JSON_EVENTS_FILE_PATH: /sample-subject-data.json
FILTER_EMIT_SUBJECT_DATA: "true"                      # Attach JSON to frames
FILTER_FORWARD_UPSTREAM_DATA: "true"                  # Forward frames from upstream
```

### Use Cases

This capability enables:
- **Metadata Injection**: Add classification results, timestamps, or custom data to frames
- **Testing Pipelines**: Simulate upstream filter outputs without running full pipelines
- **Data Enrichment**: Attach external data sources to video streams
- **Mock Filters**: Replace complex filters with stub filters during development

## Advanced Usage

### View Logs

To monitor what's happening in the pipeline:

```bash
docker compose -f docker-compose.demo.yaml logs -f
```

To view logs for a specific service:

```bash
docker compose -f docker-compose.demo.yaml logs -f stub_application_filter
```

### Modify Subject Data

1. Edit `data/sample-subject-data.json` with your custom JSON structure
2. Restart the pipeline:
   ```bash
   ./scripts/stop-demo-pipeline.sh
   ./scripts/run-demo-pipeline.sh
   ```

### Custom Configuration

Edit `docker-compose.demo.yaml` to:
- Change video input source
- Adjust logging levels (`LOG_LEVEL: DEBUG`)
- Add additional filters to the pipeline
- Change webvis port

## Troubleshooting

### Port Already in Use

If port 8020 is already in use, edit `docker-compose.demo.yaml` and change:

```yaml
ports:
  - 8020:8000  # Change 8020 to another port
```

### Docker Not Running

Ensure Docker Desktop is running:

```bash
docker info
```

### Pipeline Won't Start

Check if containers from a previous run are still active:

```bash
docker compose -f docker-compose.demo.yaml down -v
```

Then restart the demo.

## Files

- `run-demo-pipeline.sh`: Start the demo pipeline
- `stop-demo-pipeline.sh`: Stop the demo pipeline
- `README.md`: This documentation
- `../docker-compose.demo.yaml`: Pipeline definition
- `../data/sample-subject-data.json`: Sample JSON data to emit

## Learn More

For complete Filter-Stub documentation, see the main [README.md](../README.md).
