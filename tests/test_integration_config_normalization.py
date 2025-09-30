#!/usr/bin/env python

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from filter_stub_application.filter import FilterStubApplication, FilterStubApplicationConfig, FilterStubApplicationOutputMode


class TestIntegrationConfigNormalization(unittest.TestCase):
    """Integration tests for FilterStubApplication configuration normalization."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.events_file = os.path.join(self.temp_dir.name, "events.json")
        self.template_file = os.path.join(self.temp_dir.name, "events_template.json")
        self.output_file = os.path.join(self.temp_dir.name, "output.json")

        # Create test files
        with open(self.events_file, "w") as f:
            json.dump([{"id": "test", "event": "test_event"}], f)
        
        with open(self.template_file, "w") as f:
            json.dump({"type": "object", "properties": {"event": {"type": "string"}}}, f)

    def tearDown(self):
        """Clean up after each test."""
        self.temp_dir.cleanup()

    def test_string_to_type_conversions(self):
        """Test that string values are properly converted to their expected types."""
        config_data = {
            'debug': 'true',
            'forward_upstream_data': 'false',
            'output_mode': 'echo',
            'output_json_path': self.output_file,
            'input_json_events_file_path': self.events_file
        }
        
        config = FilterStubApplication.normalize_config(config_data)
        
        # Verify string-to-boolean conversions
        self.assertTrue(config.debug)
        self.assertFalse(config.forward_upstream_data)
        
        # Verify other fields remain as expected
        self.assertEqual(config.output_mode, FilterStubApplicationOutputMode.ECHO)
        self.assertEqual(config.output_json_path, self.output_file)
        self.assertEqual(config.input_json_events_file_path, self.events_file)

    def test_boolean_string_edge_cases(self):
        """Test various string representations of boolean values."""
        test_cases = [
            ('true', True),
            ('True', True),
            ('TRUE', True),
            ('1', True),
            ('yes', True),
            ('Yes', True),
            ('false', False),
            ('False', False),
            ('FALSE', False),
            ('0', False),
            ('no', False),
            ('No', False)
        ]
        
        for string_value, expected_bool in test_cases:
            with self.subTest(string_value=string_value):
                config_data = {
                    'debug': string_value,
                    'forward_upstream_data': string_value,
                    'output_mode': 'echo',
                    'output_json_path': self.output_file,
                    'input_json_events_file_path': self.events_file
                }
                
                config = FilterStubApplication.normalize_config(config_data)
                self.assertEqual(config.debug, expected_bool)
                self.assertEqual(config.forward_upstream_data, expected_bool)

    def test_required_vs_optional_parameters(self):
        """Test that required parameters are validated and optional ones have defaults."""
        # Test with minimal required parameters for echo mode
        config_data = {
            'output_mode': 'echo',
            'input_json_events_file_path': self.events_file
        }
        
        config = FilterStubApplication.normalize_config(config_data)
        
        # Check required parameters
        self.assertEqual(config.output_mode, FilterStubApplicationOutputMode.ECHO)
        self.assertEqual(config.input_json_events_file_path, self.events_file)
        
        # Check default values
        self.assertFalse(config.debug)
        self.assertTrue(config.forward_upstream_data)
        self.assertEqual(config.output_json_path, "./output/output.json")
        self.assertEqual(config.input_json_template_file_path, "./input/events_template.json")

    def test_output_mode_validation(self):
        """Test validation of output_mode parameter."""
        # Test valid modes
        for mode in ['echo', 'random']:
            with self.subTest(mode=mode):
                config_data = {
                    'output_mode': mode,
                    'input_json_events_file_path': self.events_file if mode == 'echo' else None,
                    'input_json_template_file_path': self.template_file if mode == 'random' else None
                }
                
                config = FilterStubApplication.normalize_config(config_data)
                self.assertEqual(config.output_mode.value, mode)

        # Test invalid mode
        with self.assertRaises(ValueError) as context:
            config_data = {
                'output_mode': 'invalid_mode',
                'input_json_events_file_path': self.events_file
            }
            FilterStubApplication.normalize_config(config_data)
        
        self.assertIn("Invalid mode", str(context.exception))

    def test_boolean_validation(self):
        """Test validation of boolean parameters."""
        # Test invalid boolean values
        invalid_values = ['maybe', 'invalid', '2', 'maybe', 'unknown']
        
        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                config_data = {
                    'debug': invalid_value,
                    'output_mode': 'echo',
                    'input_json_events_file_path': self.events_file
                }
                
                with self.assertRaises(ValueError) as context:
                    FilterStubApplication.normalize_config(config_data)
                
                self.assertIn("Invalid debug", str(context.exception))

    def test_path_validation(self):
        """Test validation of file path parameters."""
        # Test invalid path types
        invalid_paths = [123, None, [], {}]
        
        for invalid_path in invalid_paths:
            with self.subTest(invalid_path=invalid_path):
                config_data = {
                    'output_mode': 'echo',
                    'input_json_events_file_path': invalid_path
                }
                
                with self.assertRaises(ValueError) as context:
                    FilterStubApplication.normalize_config(config_data)
                
                self.assertIn("Invalid input JSON events path", str(context.exception))

    def test_echo_mode_validation(self):
        """Test validation specific to echo mode."""
        config_data = {
            'output_mode': 'echo',
            'input_json_events_file_path': self.events_file,
            'output_json_path': self.output_file
        }
        
        config = FilterStubApplication.normalize_config(config_data)
        self.assertEqual(config.output_mode, FilterStubApplicationOutputMode.ECHO)
        self.assertEqual(config.input_json_events_file_path, self.events_file)

    def test_random_mode_validation(self):
        """Test validation specific to random mode."""
        config_data = {
            'output_mode': 'random',
            'input_json_template_file_path': self.template_file,
            'output_json_path': self.output_file
        }
        
        config = FilterStubApplication.normalize_config(config_data)
        self.assertEqual(config.output_mode, FilterStubApplicationOutputMode.RANDOM)
        self.assertEqual(config.input_json_template_file_path, self.template_file)

    def test_environment_variable_loading(self):
        """Test loading configuration from environment variables."""
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
            
            self.assertTrue(config.debug)
            self.assertFalse(config.forward_upstream_data)
            self.assertEqual(config.output_mode, FilterStubApplicationOutputMode.RANDOM)
            self.assertEqual(config.output_json_path, self.output_file)
            self.assertEqual(config.input_json_template_file_path, self.template_file)

    def test_edge_cases_and_error_handling(self):
        """Test edge cases and error handling in configuration normalization."""
        # Test with empty config
        config = FilterStubApplication.normalize_config({})
        self.assertFalse(config.debug)
        self.assertTrue(config.forward_upstream_data)
        self.assertEqual(config.output_mode, FilterStubApplicationOutputMode.ECHO)

        # Test with None values
        config_data = {
            'debug': None,
            'forward_upstream_data': None,
            'output_mode': 'echo',
            'input_json_events_file_path': self.events_file
        }
        
        with self.assertRaises(ValueError):
            FilterStubApplication.normalize_config(config_data)

    def test_unknown_config_key_validation(self):
        """Test that unknown configuration keys are handled gracefully."""
        config_data = {
            'unknown_key': 'unknown_value',
            'output_mode': 'echo',
            'input_json_events_file_path': self.events_file
        }
        
        # Should not raise an error for unknown keys
        config = FilterStubApplication.normalize_config(config_data)
        self.assertEqual(config.output_mode, FilterStubApplicationOutputMode.ECHO)

    def test_runtime_keys_ignored(self):
        """Test that runtime-specific keys are ignored during normalization."""
        config_data = {
            'id': 'test-filter-id',
            'output_mode': 'echo',
            'input_json_events_file_path': self.events_file
        }
        
        config = FilterStubApplication.normalize_config(config_data)
        self.assertEqual(config.output_mode, FilterStubApplicationOutputMode.ECHO)
        # Runtime keys may still be present in the dict but the config should be valid
        # The base class normalize_config doesn't filter these out

    def test_comprehensive_configuration(self):
        """Test a comprehensive configuration with all parameters."""
        config_data = {
            'debug': 'true',
            'forward_upstream_data': 'false',
            'output_mode': 'echo',
            'output_json_path': self.output_file,
            'input_json_events_file_path': self.events_file,
            'input_json_template_file_path': self.template_file
        }
        
        config = FilterStubApplication.normalize_config(config_data)
        
        # Verify all parameters
        self.assertTrue(config.debug)
        self.assertFalse(config.forward_upstream_data)
        self.assertEqual(config.output_mode, FilterStubApplicationOutputMode.ECHO)
        self.assertEqual(config.output_json_path, self.output_file)
        self.assertEqual(config.input_json_events_file_path, self.events_file)
        self.assertEqual(config.input_json_template_file_path, self.template_file)

    def test_forward_upstream_data_validation(self):
        """Test validation of forward_upstream_data parameter."""
        # Test valid values
        for value in [True, False, 'true', 'false', '1', '0']:
            with self.subTest(value=value):
                config_data = {
                    'forward_upstream_data': value,
                    'output_mode': 'echo',
                    'input_json_events_file_path': self.events_file
                }
                
                config = FilterStubApplication.normalize_config(config_data)
                expected = value in [True, 'true', '1'] if isinstance(value, str) else value
                self.assertEqual(config.forward_upstream_data, expected)

        # Test invalid value
        with self.assertRaises(ValueError) as context:
            config_data = {
                'forward_upstream_data': 'maybe',
                'output_mode': 'echo',
                'input_json_events_file_path': self.events_file
            }
            FilterStubApplication.normalize_config(config_data)
        
        self.assertIn("Invalid forward_upstream_data", str(context.exception))


if __name__ == '__main__':
    unittest.main()
