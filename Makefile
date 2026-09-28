# ---------------------------------
# Repo-specific variables
# ---------------------------------

IMAGE ?= plainsightai/openfilter-stub-application

# Define these for consistency in the repo
REPO_NAME ?= filter-stub-application
REPO_NAME_SNAKECASE ?= filter_stub_application
REPO_NAME_PASCALCASE ?= FilterStubApplication

# Unique pipeline configuration for this repo
PIPELINE := \
	- VideoIn \
		--sources file://data/sample-video.mp4!loop \
	- $(REPO_NAME_SNAKECASE).filter.$(REPO_NAME_PASCALCASE) \
		--mq_log pretty \
		--output_mode random \
		--input_json_template_file_path './inputs/events_template_example.json' \
	- Webvis

# ---------------------------------
# Repo-specific targets
# ---------------------------------

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install:  ## Install package with dev dependencies
	pip install -e .[dev] \
		--index-url https://python.openfilter.io/simple \
		--extra-index-url https://pypi.org/simple

.PHONY: run
run:  ## Run locally with supporting Filters in other processes
	openfilter run ${PIPELINE}

.PHONY: test
test:  ## Run unit tests
	pytest -vv -s tests/ --junitxml=results/pytest-results.xml

.PHONY: test-coverage
test-coverage:  ## Run unit tests and generate coverage report
	@mkdir -p Reports
	@pytest -vv --cov=tests --junitxml=Reports/coverage.xml --cov-report=json:Reports/coverage.json -s tests/
	@jq -r '["File Name", "Statements", "Missing", "Coverage%"], (.files | to_entries[] | [.key, .value.summary.num_statements, .value.summary.missing_lines, .value.summary.percent_covered_display]) | @csv'  Reports/coverage.json >  Reports/coverage_report.csv
	@jq -r '["TOTAL", (.totals.num_statements // 0), (.totals.missing_lines // 0), (.totals.percent_covered_display // "0")] | @csv'  Reports/coverage.json >>  Reports/coverage_report.csv

.PHONY: build-wheel
build-wheel:  ## Build python wheel
	python -m pip install setuptools build wheel twine setuptools-scm --index-url https://pypi.org/simple
	python -m build --wheel

.PHONY: sample-video
sample-video:  ## Create data/sample-video.mp4 if it is missing
	@mkdir -p data
	@if [ -f data/sample-video.mp4 ]; then exit 0; fi; \
	if command -v ffmpeg >/dev/null 2>&1; then \
	  ffmpeg -y -f lavfi -i testsrc=size=640x360:rate=5 -t 8 -pix_fmt yuv420p data/sample-video.mp4; \
	else \
	  docker run --rm -v "$(CURDIR)/data:/data" mwader/static-ffmpeg:7.1 \
	    -y -f lavfi -i testsrc=size=640x360:rate=5 -t 8 -pix_fmt yuv420p /data/sample-video.mp4; \
	fi

.PHONY: run-realistic
run-realistic: sample-video  ## Build the local filter and run the realistic compose pipeline
	@mkdir -p output && chmod a+rwX output
	docker compose -f docker-compose.realistic.yaml up --build

.PHONY: run-realistic-detached
run-realistic-detached: sample-video  ## Same pipeline, detached
	@mkdir -p output && chmod a+rwX output
	docker compose -f docker-compose.realistic.yaml up --build -d
	@echo "Webvis: http://localhost:$${WEBVIS_PORT:-8001}"
	@echo "Events: $(CURDIR)/output/events.json"

.PHONY: down-realistic
down-realistic:  ## Stop the realistic compose pipeline
	docker compose -f docker-compose.realistic.yaml down

.PHONY: events-realistic
events-realistic:  ## Tail generated realistic events
	@test -f output/events.json || { echo "output/events.json is missing; is the pipeline running?"; exit 1; }
	tail -n 20 output/events.json

.PHONY: clean
clean:  ## Delete all generated files and directories
	sudo rm -rf build/ cache/ dist/ $(REPO_NAME_SNAKECASE).egg-info/ telemetry/
	find . -name __pycache__ -type d -exec rm -rf {} +