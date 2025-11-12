#!/bin/bash
set -e

echo "=================================="
echo "Stopping Filter-Stub Demo Pipeline"
echo "=================================="
echo ""

# Navigate to project root
cd "$(dirname "$0")/.."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running."
    exit 1
fi

echo "Stopping demo pipeline..."
docker compose -f docker-compose.demo.yaml down

echo ""
echo "✓ Demo pipeline stopped successfully!"
echo ""
echo "To restart the pipeline:"
echo "  ./scripts/run-demo-pipeline.sh"
echo ""
echo "To remove all containers and volumes:"
echo "  docker compose -f docker-compose.demo.yaml down -v"
echo ""
