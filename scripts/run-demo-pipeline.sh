#!/bin/bash
set -e

echo "=================================="
echo "Filter-Stub Subject Data Demo"
echo "=================================="
echo ""

# Navigate to project root
cd "$(dirname "$0")/.."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker and try again."
    exit 1
fi

# Create output directory
mkdir -p output

echo "Building filter-stub-application Docker image..."
docker compose -f docker-compose.demo.yaml build stub_application_filter

echo ""
echo "Starting demo pipeline..."
echo "  - VideoIn: Reading from data/sample-video.mp4"
echo "  - FilterStub: Emitting JSON subject data from data/sample-subject-data.json"
echo "  - Webvis: Available at http://localhost:8020"
echo ""

docker compose -f docker-compose.demo.yaml up -d

echo ""
echo "✓ Demo pipeline started successfully!"
echo ""
echo "To view the demo:"
echo "  1. Open browser: http://localhost:8020"
echo "  2. View subject data in webvis interface"
echo ""
echo "To view logs:"
echo "  docker compose -f docker-compose.demo.yaml logs -f"
echo ""
echo "To stop the pipeline:"
echo "  ./scripts/stop-demo-pipeline.sh"
echo ""
