#!/usr/bin/env python

import json
import logging
import multiprocessing
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from filter_stub_application.filter import FilterStubApplication, FilterStubApplicationConfig, FilterStubApplicationOutputMode
from openfilter.filter_runtime.frame import Frame

logger = logging.getLogger(__name__)

logger.setLevel(int(getattr(logging, (os.getenv('LOG_LEVEL') or 'INFO').upper())))

VERBOSE   = '-v' in sys.argv or '--verbose' in sys.argv
LOG_LEVEL = logger.getEffectiveLevel()


class TestSmokeSimple(unittest.TestCase):
    """Smoke tests for FilterStubApplication basic functionality."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.events_file = os.path.join(self.temp_dir.name, "events.json")
        self.template_file = os.path.join(self.temp_dir.name, "events_template.json")
        self.output_file = os.path.join(self.temp_dir.name, "output.json")

        # Create test files
        with open(self.events_file, "w") as f:
            json.dump([
                {"id": "test1", "event": "test_event_1", "value": 100},
                {"id": "test2", "event": "test_event_2", "value": 200}
            ], f)
        
        with open(self.template_file, "w") as f:
            json.dump({
                "type": "object", 
                "properties": {
                    "event": {"type": "string"},
                    "value": {"type": "number", "minimum": 1, "maximum": 1000}
                },
                "required": ["event", "value"]
            }, f)

    def tearDown(self):
        """Clean up after each test."""
        self.temp_dir.cleanup()

    def test_filter_initialization(self):
        """Test basic filter initialization."""
        config = FilterStubApplicationConfig(
            output_mode="echo",
            input_json_events_file_path=self.events_file,
            output_json_path=self.output_file
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(config)
        self.assertIsNotNone(filter_instance)
        self.assertEqual(filter_instance.debug, False)
        self.assertEqual(filter_instance.forward_upstream_data, True)

    def test_setup_and_shutdown(self):
        """Test filter setup and shutdown lifecycle."""
        config = FilterStubApplicationConfig(
            output_mode="echo",
            input_json_events_file_path=self.events_file,
            output_json_path=self.output_file
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        # Verify setup
        self.assertEqual(len(filter_instance.events), 2)
        self.assertEqual(filter_instance.current_event_index, 0)
        self.assertFalse(filter_instance.all_events_processed)
        
        # Test shutdown
        filter_instance.shutdown()
        # Shutdown should complete without errors

    def test_config_validation(self):
        """Test configuration validation."""
        # Test valid configuration
        config_data = {
            'debug': True,
            'forward_upstream_data': False,
            'output_mode': 'echo',
            'output_json_path': self.output_file,
            'input_json_events_file_path': self.events_file
        }
        
        config = FilterStubApplication.normalize_config(config_data)
        self.assertTrue(config.debug)
        self.assertFalse(config.forward_upstream_data)
        self.assertEqual(config.output_mode, FilterStubApplicationOutputMode.ECHO)

    def test_echo_mode_processing(self):
        """Test echo mode event processing."""
        config = FilterStubApplicationConfig(
            output_mode="echo",
            input_json_events_file_path=self.events_file,
            output_json_path=self.output_file
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        # Process frames
        frames = {"main": Frame(image=None, data={}, format=None)}
        result = filter_instance.process(frames)
        
        # Verify output file contains the first event
        with open(self.output_file, "r") as f:
            output_content = f.read().strip()
        
        self.assertIn("test1", output_content)
        self.assertIn("test_event_1", output_content)
        self.assertIn("100", output_content)

    def test_random_mode_processing(self):
        """Test random mode event processing."""
        config = FilterStubApplicationConfig(
            output_mode="random",
            input_json_template_file_path=self.template_file,
            output_json_path=self.output_file
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        # Process frames
        frames = {"main": Frame(image=None, data={}, format=None)}
        result = filter_instance.process(frames)
        
        # Verify output file contains a random event
        with open(self.output_file, "r") as f:
            output_content = f.read().strip()
        
        # Should be valid JSON
        event = json.loads(output_content)
        self.assertIn("event", event)
        self.assertIn("value", event)
        self.assertIsInstance(event["value"], (int, float))

    def test_forward_upstream_data_enabled(self):
        """Test upstream data forwarding when enabled."""
        config = FilterStubApplicationConfig(
            output_mode="echo",
            input_json_events_file_path=self.events_file,
            output_json_path=self.output_file,
            forward_upstream_data=True
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        # Create frames with non-image data
        frames = {
            "main": Frame(image=None, data={"test": "data"}, format=None),
            "metadata": Frame(image=None, data={"meta": "info"}, format=None)
        }
        
        result = filter_instance.process(frames)
        
        # Should forward non-image frames
        self.assertIn("main", result)
        self.assertIn("metadata", result)
        self.assertEqual(result["main"].data["test"], "data")
        self.assertEqual(result["metadata"].data["meta"], "info")

    def test_forward_upstream_data_disabled(self):
        """Test that upstream data is not forwarded when disabled."""
        config = FilterStubApplicationConfig(
            output_mode="echo",
            input_json_events_file_path=self.events_file,
            output_json_path=self.output_file,
            forward_upstream_data=False
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        # Create frames with non-image data
        frames = {
            "main": Frame(image=None, data={"test": "data"}, format=None),
            "metadata": Frame(image=None, data={"meta": "info"}, format=None)
        }
        
        result = filter_instance.process(frames)
        
        # Should not forward non-image frames
        self.assertEqual(len(result), 0)

    def test_empty_frame_processing(self):
        """Test processing with empty frame dictionary."""
        config = FilterStubApplicationConfig(
            output_mode="echo",
            input_json_events_file_path=self.events_file,
            output_json_path=self.output_file
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        # Process empty frames
        frames = {}
        result = filter_instance.process(frames)
        
        # Should return empty result
        self.assertEqual(result, {})

    def test_echo_mode_multiple_events(self):
        """Test echo mode with multiple events."""
        config = FilterStubApplicationConfig(
            output_mode="echo",
            input_json_events_file_path=self.events_file,
            output_json_path=self.output_file
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        # Process first event
        frames = {"main": Frame(image=None, data={}, format=None)}
        result = filter_instance.process(frames)
        
        # Process second event
        result = filter_instance.process(frames)
        
        # Verify both events are in output file
        with open(self.output_file, "r") as f:
            lines = f.readlines()
        
        self.assertEqual(len(lines), 2)
        
        # Verify first event
        first_event = json.loads(lines[0].strip())
        self.assertEqual(first_event["id"], "test1")
        
        # Verify second event
        second_event = json.loads(lines[1].strip())
        self.assertEqual(second_event["id"], "test2")

    def test_echo_mode_end_of_events(self):
        """Test echo mode behavior when all events are processed."""
        config = FilterStubApplicationConfig(
            output_mode="echo",
            input_json_events_file_path=self.events_file,
            output_json_path=self.output_file
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        # Process all events
        frames = {"main": Frame(image=None, data={}, format=None)}
        for _ in range(3):  # Process more times than we have events
            result = filter_instance.process(frames)
        
        # Verify only 2 events were written (we only have 2 events)
        with open(self.output_file, "r") as f:
            lines = f.readlines()
        
        self.assertEqual(len(lines), 2)
        self.assertTrue(filter_instance.all_events_processed)

    def test_debug_mode_processing(self):
        """Test processing with debug mode enabled."""
        config = FilterStubApplicationConfig(
            output_mode="echo",
            input_json_events_file_path=self.events_file,
            output_json_path=self.output_file,
            debug=True
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        # Process frames
        frames = {"main": Frame(image=None, data={}, format=None)}
        result = filter_instance.process(frames)
        
        # Should complete without errors
        self.assertIsNotNone(result)

    def test_string_config_conversion(self):
        """Test string configuration values are properly converted."""
        config_data = {
            'debug': 'true',
            'forward_upstream_data': 'false',
            'output_mode': 'echo',
            'output_json_path': self.output_file,
            'input_json_events_file_path': self.events_file
        }
        
        config = FilterStubApplication.normalize_config(config_data)
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        self.assertTrue(filter_instance.debug)
        self.assertFalse(filter_instance.forward_upstream_data)

    def test_error_handling_invalid_config(self):
        """Test error handling with invalid configuration."""
        with self.assertRaises(ValueError):
            config_data = {
                'debug': 'maybe',  # Invalid boolean
                'output_mode': 'echo',
                'input_json_events_file_path': self.events_file
            }
            FilterStubApplication.normalize_config(config_data)

    def test_environment_variable_loading(self):
        """Test loading configuration from environment variables."""
        # Create required files for the test
        os.makedirs(os.path.dirname(self.template_file), exist_ok=True)
        with open(self.template_file, 'w') as f:
            json.dump({"type": "object", "properties": {"test": {"type": "string"}}}, f)
        
        env_vars = {
            'FILTER_DEBUG': 'true',
            'FILTER_FORWARD_UPSTREAM_DATA': 'false',
            'FILTER_OUTPUT_MODE': 'random',
            'FILTER_OUTPUT_JSON_PATH': self.output_file,
            'FILTER_INPUT_JSON_TEMPLATE_FILE_PATH': self.template_file
        }
        
        with patch.dict(os.environ, env_vars):
            # Create a config with environment variables loaded
            config_data = {
                'debug': os.getenv('FILTER_DEBUG', 'false'),
                'forward_upstream_data': os.getenv('FILTER_FORWARD_UPSTREAM_DATA', 'true'),
                'output_mode': os.getenv('FILTER_OUTPUT_MODE', 'echo'),
                'output_json_path': os.getenv('FILTER_OUTPUT_JSON_PATH', './output/output.json'),
                'input_json_template_file_path': os.getenv('FILTER_INPUT_JSON_TEMPLATE_FILE_PATH', './input/events_template.json')
            }
            config = FilterStubApplication.normalize_config(config_data)
            filter_instance = FilterStubApplication(config)
            filter_instance.setup(config)
            
            self.assertTrue(filter_instance.debug)
            self.assertFalse(filter_instance.forward_upstream_data)
            self.assertEqual(filter_instance.output_mode, FilterStubApplicationOutputMode.RANDOM)

    def test_comprehensive_echo_mode(self):
        """Test comprehensive echo mode functionality."""
        config = FilterStubApplicationConfig(
            output_mode="echo",
            input_json_events_file_path=self.events_file,
            output_json_path=self.output_file,
            debug=True,
            forward_upstream_data=True
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        # Process with mixed frame types
        import numpy as np
        test_image = np.zeros((10, 10, 3), dtype=np.uint8)
        frames = {
            "main": Frame(image=None, data={"main_data": "value"}, format=None),
            "image": Frame(image=test_image, data={"image_data": "value"}, format="RGB"),
            "metadata": Frame(image=None, data={"meta": "info"}, format=None)
        }
        
        result = filter_instance.process(frames)
        
        # Should forward non-image frames
        self.assertIn("main", result)
        self.assertIn("metadata", result)
        self.assertNotIn("image", result)  # Image frames should not be forwarded
        
        # Verify event was written
        with open(self.output_file, "r") as f:
            output_content = f.read().strip()
        
        self.assertIn("test1", output_content)

    def test_comprehensive_random_mode(self):
        """Test comprehensive random mode functionality."""
        config = FilterStubApplicationConfig(
            output_mode="random",
            input_json_template_file_path=self.template_file,
            output_json_path=self.output_file,
            debug=True,
            forward_upstream_data=False
        )
        
        filter_instance = FilterStubApplication(config)
        filter_instance.setup(filter_instance.config)
        
        # Process multiple times
        frames = {"main": Frame(image=None, data={}, format=None)}
        for _ in range(3):
            result = filter_instance.process(frames)
        
        # Verify multiple random events were generated
        with open(self.output_file, "r") as f:
            lines = f.readlines()
        
        self.assertEqual(len(lines), 3)
        
        # Verify each line is valid JSON with required fields
        for line in lines:
            event = json.loads(line.strip())
            self.assertIn("event", event)
            self.assertIn("value", event)
            self.assertIsInstance(event["value"], (int, float))
            self.assertGreaterEqual(event["value"], 1)
            self.assertLessEqual(event["value"], 1000)


try:
    multiprocessing.set_start_method('spawn')  # CUDA doesn't like fork()
except Exception:
    pass

if __name__ == '__main__':
    unittest.main()
