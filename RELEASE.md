# Changelog
JSONSim release notes

## [Unreleased]

## v0.2.12 - 2025-09-27
### Changed
- Updated documentation

## v0.2.10 - 2025-09-24
### Added

### Added
- **Testing Suite**
  - Integration tests for configuration normalization and validation
  - Smoke tests for basic filter functionality and end-to-end testing
  - 44 total tests covering all functionality and edge cases

- **Enhanced Configuration System**
  - Added `forward_upstream_data` parameter to control forwarding of non-image frames
  - Robust string-to-boolean conversion for `debug` and `forward_upstream_data` parameters
  - Improved enum validation for `output_mode` parameter

- **Improved Usage Script**
  - Complete rewrite of `filter_usage.py` using proper OpenFilter `Filter.run_multi()` pattern
  - Environment variable support for all configuration parameters

- **Filter Improvements**
  - Process all incoming topics (not just main topic)
  - Forward non-image frames from upstream filters when enabled
  - Ensure proper topic ordering in output

### Changed
- **Configuration Validation**: More validation with better error messages
- **Test Structure**: Separated integration and smoke tests for better organization
- **Documentation**: Complete rewrite with focus on usability and examples

## v0.2.9 - 2025-07-14
### Added
- Migrate from filter_runtime to openfilter

### Added
- Internal improvements
## v0.2.8 - 2025-05-20
- Added Multi Event Json support can now echo json events corresponding to frames.

## v0.2.7 - 2024-03-27

### Added
- Internal improvements

## v0.2.4 - 2024-02-25

### Added
- Initial Release: new synthetic filter for emitting synthetic events from files or templates

- **Two Output Modes**
  - `echo`: Reads and emits pre-recorded events from `input_json_events_file_path`
  - `random`: Dynamically generates random events using a JSON schema at `input_json_template_file_path`

- **Configurable Output Path**
  - Emits events to a newline-delimited JSON file defined by `output_json_path`

- **Debug Mode**
  - Optional `debug` flag for verbose logging and traceability

- **Schema-Based Event Generation**
  - Uses JSON Schema to generate structured synthetic data for testing pipelines

- **Graceful Resource Handling**
  - Handles file I/O safely with proper setup and shutdown lifecycle hooks
