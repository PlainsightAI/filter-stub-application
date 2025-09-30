import json
import logging
from enum import Enum
import os

from hypothesis_jsonschema import from_schema
from openfilter.filter_runtime.filter import FilterConfig, Filter, Frame

__all__ = ["FilterStubApplicationConfig", "FilterStubApplication"]

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class FilterStubApplicationConfig(FilterConfig):
    debug: bool = False  # Debug mode
    forward_upstream_data: bool = True  # Forward data from upstream filters

    # Supported output_modes:
    #   echo - output events from `input_json_events_file_path`
    #   random - random events based on `input_json_template_file_path`
    output_mode: str = "random"
    output_json_path: str = "./output/events.json"  # Path to the output json file
    # The path to the file containing JSON events.
    #   This file will be echoed if echo mode is enabled
    input_json_events_file_path: str = "./input/events.json"
    # The path to the file containing JSON events template.
    #   This file will be utilized to generate random events if random mode is enabled
    input_json_template_file_path: str = "./input/events_template.json"


class FilterStubApplicationOutputMode(Enum):
    ECHO = "echo" # echo events from a file
    RANDOM = "random" # randomly generaing events

    @classmethod
    def from_str(cls, value: str) -> "FilterStubApplicationOutputMode":
        try:
            return cls(value.strip().lower())
        except ValueError:
            raise ValueError(
                f"Invalid mode: {value!r}. Expected one of: {[s.value for s in cls]}"
            )


class FilterStubApplication(Filter):
    """Simple stub application filter. Equipped with Echo mode and Random mode
    Modes:
        - Echo: outputs events provided in the input_json_events_file_path
        - Random: outputs randomly generated events, based on a JSON template
    """

    @classmethod
    def normalize_config(cls, config: FilterStubApplicationConfig):
        config = FilterStubApplicationConfig(super().normalize_config(config))

        # Convert string booleans to actual booleans
        for key in ['debug', 'forward_upstream_data']:
            if isinstance(config.get(key), str):
                value = config[key].lower().strip()
                if value in ('true', '1', 'yes'):
                    config[key] = True
                elif value in ('false', '0', 'no'):
                    config[key] = False
                else:
                    raise ValueError(f"Invalid {key}: {config[key]}. Must be true/false, 1/0, or yes/no.")

        # Validate debug mode
        if not isinstance(config.debug, bool):
            raise ValueError(
                f"Invalid debug mode: {config.debug}. It should be True or False."
            )

        # Validate forward_upstream_data mode
        if not isinstance(config.forward_upstream_data, bool):
            raise ValueError(
                f"Invalid forward_upstream_data: {config.forward_upstream_data}. It should be True or False."
            )

        # Validate mode
        if isinstance(config.output_mode, str):
            config.output_mode = FilterStubApplicationOutputMode.from_str(
                config.output_mode
            )  # throws value error for invalid modes
        elif not isinstance(config.output_mode, FilterStubApplicationOutputMode):
            raise ValueError(
                f"Invalid output mode: {config.output_mode}. Must be one of {[name.lower() for name in FilterStubApplicationOutputMode._member_names_]}"
            )

        # Validate input JSON events for echo mode
        if (
            config.output_mode == FilterStubApplicationOutputMode.ECHO
            and not isinstance(config.input_json_events_file_path, str)
        ):
            raise ValueError(
                f"Invalid input JSON events path: {config.input_json_events_file_path}"
            )

        # Validate input JSON schema for random mode
        if (
            config.output_mode == FilterStubApplicationOutputMode.RANDOM
            and not isinstance(config.input_json_template_file_path, str)
        ):
            raise ValueError(
                f"Invalid input JSON template path: {config.input_json_template_file_path}"
            )

        if not isinstance(config.output_json_path, str):
            raise ValueError(f"Invalid output json path: {config.output_json_path}")

        return config

    def setup(self, config: FilterStubApplicationConfig):
        logger.info("===========================================")
        logger.info(f"FilterStubApplication setup: {config}")
        logger.info("===========================================")

        self.cfg = config
        self.debug = config.debug
        self.forward_upstream_data = config.forward_upstream_data
        self.output_mode = config.output_mode
        self.input_json_events_file_path = config.input_json_events_file_path
        self.input_json_template_file_path = config.input_json_template_file_path
        self.output_json_path = config.output_json_path
        self.events = []  # List to store all events
        self.current_event_index = 0  # Index to track current event
        self.all_events_processed = False  # Flag to track if we've processed all events

        output_dir = os.path.dirname(self.output_json_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        # Clear the output file
        with open(self.output_json_path, "w") as f:
            pass  # Just create/clear the file

        if self.output_mode == FilterStubApplicationOutputMode.ECHO:
            # Load all events from file at startup
            try:
                # Try loading as a JSON array first
                try:
                    with open(self.input_json_events_file_path, "r") as f:
                        self.events = json.load(f)
                    logger.info(f"Loaded {len(self.events)} events from input file as JSON array.")
                except json.JSONDecodeError:
                    # If that fails, try loading as JSON Lines format (one JSON object per line)
                    self.events = []
                    with open(self.input_json_events_file_path, "r") as f:
                        for line in f:
                            line = line.strip()
                            if line:  # Skip empty lines
                                try:
                                    event = json.loads(line)
                                    self.events.append(event)
                                except json.JSONDecodeError:
                                    logger.error(f"Failed to parse JSON: {line}")
                    
                    logger.info(f"Loaded {len(self.events)} events from input file as JSON Lines format.")
                
                if not self.events:
                    logger.warning("No events found in the input file.")
            except Exception as e:
                logger.error(f"Failed to open or parse input JSON events file: {e}")
                raise
        elif self.output_mode == FilterStubApplicationOutputMode.RANDOM:
            # Generate random events based on template
            try:
                with open(self.input_json_template_file_path, "r") as schema_file:
                    self.schema = json.load(schema_file)
                    logger.info("Loaded input JSON template.")
            except Exception as e:
                logger.error(f"Failed to load JSON schema: {e}")
                raise

    def shutdown(self):
        logger.info("FilterStubApplication shutdown")

    def process(self, frames: dict[str, Frame]):
        # Initialize output frames dictionary
        output_frames = {}
        
        # Forward non-image frames if upstream forwarding is enabled
        for topic, frame in frames.items():
            if frame is None or not frame.has_image:
                if self.forward_upstream_data:
                    output_frames[topic] = frame
                continue

        # we do not process the frames, only output events
        if self.output_mode == FilterStubApplicationOutputMode.ECHO:
            if not self.all_events_processed and self.events and self.current_event_index < len(self.events):
                # Get the current event
                event = self.events[self.current_event_index]
                
                # Write the event to the output file
                with open(self.output_json_path, "a") as file:
                    file.write(json.dumps(event) + "\n")
                
                logger.info(f"Echoed event: {event['id']}")
                
                # Move to the next event
                self.current_event_index += 1
                
                # If we've reached the end, mark all events as processed
                if self.current_event_index >= len(self.events):
                    logger.info("All events processed. No more events to echo.")
                    self.all_events_processed = True
            else:
                logger.warning("No more events to echo.")
        elif self.output_mode == FilterStubApplicationOutputMode.RANDOM:
            try:
                random_event = from_schema(self.schema).example()
                with open(self.output_json_path, "a") as file:
                    file.write(json.dumps(random_event) + "\n")
                logger.info(f"Generated random event: {random_event}")
            except Exception as e:
                logger.error(f"Error generating random event: {e}")
        
        return output_frames


if __name__ == "__main__":
    FilterStubApplication.run()