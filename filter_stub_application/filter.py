import json
import logging
import math
import os
from enum import Enum

from hypothesis_jsonschema import from_schema
from openfilter.filter_runtime.filter import FilterConfig, Filter, Frame

from filter_stub_application.process import ProcessEngine, ProfileError
from filter_stub_application.realistic import GenerationError, JSON_TYPES, RealisticGenerator, SchemaContractError

__all__ = ["FilterStubApplicationConfig", "FilterStubApplication"]

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_BOOL_FIELDS = (
    "debug", "forward_upstream_data", "forward_images", "append_output",
)
_INT_FIELDS = (
    "realistic_seed", "realistic_max_attempts", "realistic_max_depth",
    "realistic_max_nodes", "realistic_max_event_bytes", "realistic_preflight_samples",
    "realistic_array_max_when_unbounded", "realistic_number_bound_when_unbounded", "emit_every_n_frames",
)
_FLOAT_FIELDS = (
    "realistic_optional_probability", "realistic_null_probability",
    "realistic_example_probability", "process_tick_seconds",
)
_PROBABILITY_FIELDS = (
    "realistic_optional_probability", "realistic_null_probability",
    "realistic_example_probability",
)


class FilterStubApplicationConfig(FilterConfig):
    debug: bool = False
    forward_upstream_data: bool = True
    forward_images: bool = False

    # echo replays a file, random is the legacy fuzzer, realistic is the draft-07 generator.
    output_mode: str = "random"
    output_json_path: str = "./output/events.json"
    input_json_events_file_path: str = "./input/events.json"
    input_json_template_file_path: str = "./input/events_template.json"
    process_profile_path: str = ""

    realistic_seed: int = 0
    realistic_optional_probability: float = 0.5
    realistic_null_probability: float = 0.1
    realistic_example_probability: float = 0.3
    realistic_max_attempts: int = 20
    realistic_max_depth: int = 32
    realistic_max_nodes: int = 10000
    realistic_max_event_bytes: int = 1048576
    realistic_preflight_samples: int = 10
    realistic_array_max_when_unbounded: int = 3
    realistic_number_bound_when_unbounded: int = 1000
    type_weights: dict = None
    process_tick_seconds: float = 1.0
    trigger_mode: str = "image"
    emit_every_n_frames: int = 1
    frame_event_key: str = "event"
    frame_event_collision: str = "error"
    event_topic: str = "main"
    append_output: bool = False
    failure_policy: str = "drop"
    io_failure_policy: str = "stop"


class FilterStubApplicationOutputMode(Enum):
    ECHO = "echo"
    RANDOM = "random"
    REALISTIC = "realistic"

    @classmethod
    def from_str(cls, value: str) -> "FilterStubApplicationOutputMode":
        try:
            return cls(value.strip().lower())
        except ValueError:
            raise ValueError(
                f"Invalid mode: {value!r}. Expected one of: {[s.value for s in cls]}"
            )


class FilterStubApplication(Filter):
    """Stub filter with echo, random, and realistic output modes."""

    @classmethod
    def normalize_config(cls, config: FilterStubApplicationConfig):
        config = FilterStubApplicationConfig(super().normalize_config(config))
        defaults = FilterStubApplicationConfig()
        for key in (
            *_BOOL_FIELDS, *_INT_FIELDS, *_FLOAT_FIELDS,
            "trigger_mode", "frame_event_collision", "failure_policy", "io_failure_policy",
            "frame_event_key", "event_topic", "process_profile_path", "type_weights",
        ):
            if key not in config:
                config[key] = getattr(defaults, key)
        for key in _BOOL_FIELDS:
            if isinstance(config.get(key), str):
                value = config[key].lower().strip()
                if value in ("true", "1", "yes"):
                    config[key] = True
                elif value in ("false", "0", "no"):
                    config[key] = False
                else:
                    raise ValueError(f"Invalid {key}: {config[key]}. Must be true/false, 1/0, or yes/no.")
        for key in ("debug", "forward_upstream_data"):
            if not isinstance(config.get(key), bool):
                raise ValueError(f"Invalid {key}: {config.get(key)}. It should be True or False.")
        for key in _INT_FIELDS:
            if isinstance(config.get(key), str):
                config[key] = int(config[key])
            if key in config and (not isinstance(config[key], int) or isinstance(config[key], bool)):
                raise ValueError(f"Invalid {key}: {config[key]}")
        for key in _FLOAT_FIELDS:
            value = config.get(key)
            if isinstance(value, str):
                try:
                    value = float(value)
                except ValueError as exc:
                    raise ValueError(f"Invalid {key}: {config.get(key)}") from exc
                config[key] = value
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"Invalid {key}: {value}")
        if config.realistic_seed < 0:
            raise ValueError("realistic_seed must be >= 0")
        for key in ("realistic_max_attempts", "realistic_max_depth", "realistic_max_nodes",
                    "realistic_max_event_bytes", "emit_every_n_frames", "realistic_array_max_when_unbounded",
                    "realistic_number_bound_when_unbounded"):
            if config[key] < 1:
                raise ValueError(f"{key} must be >= 1")
        weights = config.get("type_weights")
        if weights is None or weights == "":
            config["type_weights"] = {}
        else:
            if isinstance(weights, str):
                try:
                    weights = json.loads(weights)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid type_weights: {config.get('type_weights')}") from exc
            if not isinstance(weights, dict):
                raise ValueError("type_weights must be an object mapping JSON types to weights")
            cleaned = {}
            for key, value in weights.items():
                if key not in JSON_TYPES:
                    raise ValueError(f"Invalid type_weights key: {key}")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
                    raise ValueError(f"Invalid type_weights[{key}]: {value}")
                cleaned[key] = float(value)
            if cleaned and sum(cleaned.values()) <= 0:
                raise ValueError("type_weights must sum to more than zero")
            config["type_weights"] = cleaned
        if config.realistic_preflight_samples < 0:
            raise ValueError("realistic_preflight_samples must be >= 0")
        for key in _PROBABILITY_FIELDS:
            if not 0 <= float(config[key]) <= 1:
                raise ValueError(f"{key} must be in [0, 1]")
        if float(config.process_tick_seconds) <= 0:
            raise ValueError("process_tick_seconds must be > 0")
        if config.trigger_mode not in ("image", "process"):
            raise ValueError("trigger_mode must be image or process")
        if config.frame_event_collision not in ("error", "overwrite", "skip"):
            raise ValueError("frame_event_collision must be error, overwrite, or skip")
        if config.failure_policy not in ("drop", "stop"):
            raise ValueError("failure_policy must be drop or stop")
        if config.io_failure_policy not in ("drop", "stop"):
            raise ValueError("io_failure_policy must be drop or stop")

        if isinstance(config.output_mode, str):
            config.output_mode = FilterStubApplicationOutputMode.from_str(config.output_mode)
        elif not isinstance(config.output_mode, FilterStubApplicationOutputMode):
            raise ValueError(
                f"Invalid output mode: {config.output_mode}. Must be one of {[name.lower() for name in FilterStubApplicationOutputMode._member_names_]}"
            )
        if config.output_mode == FilterStubApplicationOutputMode.ECHO and not isinstance(config.input_json_events_file_path, str):
            raise ValueError(f"Invalid input JSON events path: {config.input_json_events_file_path}")
        if config.output_mode in (FilterStubApplicationOutputMode.RANDOM, FilterStubApplicationOutputMode.REALISTIC):
            if not isinstance(config.input_json_template_file_path, str):
                raise ValueError(f"Invalid input JSON template path: {config.input_json_template_file_path}")
        if not isinstance(config.output_json_path, str):
            raise ValueError(f"Invalid output json path: {config.output_json_path}")
        if config.process_profile_path and config.output_mode != FilterStubApplicationOutputMode.REALISTIC:
            logger.warning("process_profile_path is ignored unless output_mode is realistic")
        return config

    def setup(self, config: FilterStubApplicationConfig):
        logger.info("===========================================")
        logger.info(f"FilterStubApplication setup: {config}")
        logger.info("===========================================")
        if config.debug:
            logger.setLevel(logging.DEBUG)

        if getattr(self, "_output", None) is not None and not getattr(self, "_closed", True):
            self._output.close()
            self._output = None
        self.cfg = config
        self.debug = config.debug
        self.forward_upstream_data = config.forward_upstream_data
        self.forward_images = bool(getattr(config, "forward_images", False))
        mode = config.output_mode
        if isinstance(mode, str):
            mode = FilterStubApplicationOutputMode.from_str(mode)
        self.output_mode = mode
        self.output_json_path = config.output_json_path
        self.events = []
        self.current_event_index = 0
        self.all_events_processed = False
        self.schema = None
        self.generator = None
        self.process_engine = None
        self._calls = 0
        self._output = None
        self._closed = False
        self.generation_counts = {
            "generated": 0, "dropped": 0, "validation_failed": 0,
            "serialization_failed": 0, "write_failed": 0,
        }
        self._log_occurrences = {}

        inputs = []
        if self.output_mode == FilterStubApplicationOutputMode.ECHO:
            inputs.append(config.input_json_events_file_path)
            self._load_echo(config.input_json_events_file_path)
        elif self.output_mode == FilterStubApplicationOutputMode.RANDOM:
            inputs.append(config.input_json_template_file_path)
            self._load_random_schema(config.input_json_template_file_path)
        else:
            inputs.append(config.input_json_template_file_path)
            schema = self._load_random_schema(config.input_json_template_file_path)
            profile = None
            if config.process_profile_path:
                inputs.append(config.process_profile_path)
                with open(config.process_profile_path, "r", encoding="utf-8") as handle:
                    profile = json.load(handle)
                self.process_engine = ProcessEngine(profile, self._runtime_config(config))
            self.generator = RealisticGenerator(schema, self._runtime_config(config), defer_preflight=True)
            overlay = self.process_engine.preview_overlay() if self.process_engine else None
            self.generator.run_preflight(overlay)

        self._reject_aliased_output(inputs)
        output_dir = os.path.dirname(self.output_json_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        self._open_output(config.append_output)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        if self._output is not None:
            self._output.flush()
            self._output.close()
            self._output = None
        logger.info("FilterStubApplication shutdown")

    def process(self, frames: dict[str, Frame]):
        if self.output_mode in (FilterStubApplicationOutputMode.ECHO, FilterStubApplicationOutputMode.RANDOM):
            output_frames = {}
            for topic, frame in frames.items():
                if frame is None or not frame.has_image:
                    if self.forward_upstream_data:
                        output_frames[topic] = frame
            self._emit_legacy()
            return output_frames
        return self._process_realistic(frames)

    def _process_realistic(self, frames: dict[str, Frame]):
        output_frames = {}
        image_topics = []
        for topic, frame in frames.items():
            if frame is None:
                if self.forward_upstream_data:
                    output_frames[topic] = None
                continue
            if not frame.has_image:
                if self.forward_upstream_data:
                    output_frames[topic] = Frame(data=dict(frame.data))
                continue
            image_topics.append(topic)
            if self.forward_images:
                output_frames[topic] = Frame(frame, data=dict(frame.data))

        if self.cfg.trigger_mode == "image" and not image_topics:
            return output_frames
        self._calls += 1
        if self._calls % self.cfg.emit_every_n_frames != 0:
            return output_frames
        topic = self.cfg.event_topic
        if frames and topic not in frames:
            logger.warning("event_topic %r is not in this batch; skipping emit", topic)
            return output_frames
        if frames and frames.get(topic) is None:
            return output_frames
        # Reject a collision before the event is written or the process advances.
        prepared = self._prepare_event_frame(frames, output_frames)
        event = self._emit_realistic()
        if event is not None and prepared is not None:
            self._attach_event(output_frames, prepared, event)
        return output_frames

    def _prepare_event_frame(self, frames, output_frames):
        topic = self.cfg.event_topic
        if topic not in frames:
            return None
        source = frames[topic]
        if source is None:
            return None
        if source.has_image and self.forward_images and topic in output_frames:
            carrier = output_frames[topic]
        else:
            carrier = Frame(data=dict(source.data))
        key = self.cfg.frame_event_key
        if key in carrier.data and self.cfg.frame_event_collision == "error":
            raise ValueError(f"frame data already contains {key!r}")
        output_frames[topic] = carrier
        return topic

    def _emit_legacy(self) -> None:
        if self.output_mode == FilterStubApplicationOutputMode.ECHO:
            if not self.all_events_processed and self.events and self.current_event_index < len(self.events):
                event = self.events[self.current_event_index]
                self._write_line(event)
                logger.info("Echoed event index %s", self.current_event_index)
                self.current_event_index += 1
                if self.current_event_index >= len(self.events):
                    logger.info("All events processed. No more events to echo.")
                    self.all_events_processed = True
            else:
                logger.warning("No more events to echo.")
        else:
            try:
                random_event = from_schema(self.schema).example()
                self._write_line(random_event)
                logger.info("Generated random event: %s", random_event)
            except Exception as exc:
                logger.error("Error generating random event: %s", exc)

    def _emit_realistic(self):
        try:
            pending = self.process_engine.next_pending() if self.process_engine else None
        except ProfileError as exc:
            return self._fail("validation_failed", exc, self.cfg.failure_policy, None, commit=False)
        if self.process_engine and pending is None:
            return None
        if pending is not None:
            self.generator.observation = pending.observation
        instance_id = pending.instance_id if pending is not None else 0
        try:
            document = self.generator.generate_document(
                pending.overlay if pending else None, instance_id,
            )
        except (GenerationError, SchemaContractError, ProfileError) as exc:
            return self._fail("validation_failed", exc, self.cfg.failure_policy, pending, commit=True)
        try:
            self._write_line(document)
        except (TypeError, ValueError) as exc:
            return self._fail("serialization_failed", exc, self.cfg.io_failure_policy, pending, commit=True)
        except OSError as exc:
            return self._fail("write_failed", exc, self.cfg.io_failure_policy, pending, commit=True)
        self.generator.commit_sequences()
        if pending is not None:
            self.process_engine.commit()
        self.generation_counts["generated"] += 1
        self._publish_counts()
        return {"document": document, "event_id": pending.event_id if pending else str(self.generation_counts["generated"])}

    def _fail(self, reason: str, exc: Exception, policy: str, pending, commit: bool):
        self.generation_counts[reason] += 1
        self._log_limited(reason, f"realistic {reason.replace('_', ' ')}: {exc}")
        if policy == "stop":
            raise exc
        self._sync_dropped()
        self._publish_counts()
        if commit and pending is not None and self.process_engine is not None:
            self.process_engine.commit()
        return None

    def _sync_dropped(self) -> None:
        counts = self.generation_counts
        # dropped is the total of lost observations. The reason counters are its breakdown.
        counts["dropped"] = counts["validation_failed"] + counts["serialization_failed"] + counts["write_failed"]

    def _publish_counts(self) -> None:
        metrics = getattr(getattr(self, "mq", None), "metrics", None)
        if isinstance(metrics, dict):
            metrics.update({f"realistic_{key}": value for key, value in self.generation_counts.items()})

    def _log_limited(self, key: str, message: str) -> None:
        seen = self._log_occurrences.get(key, 0) + 1
        self._log_occurrences[key] = seen
        if seen <= 3 or seen % 100 == 0:
            logger.error("%s (occurrence %s)", message, seen)

    def _attach_event(self, output_frames, topic, event) -> None:
        if topic not in output_frames:
            return
        frame = output_frames[topic]
        key = self.cfg.frame_event_key
        if key in frame.data and self.cfg.frame_event_collision == "skip":
            return
        frame.data[key] = event["document"]
        frame.data["event_id"] = event["event_id"]

    def _write_line(self, event) -> None:
        if self.output_mode == FilterStubApplicationOutputMode.REALISTIC:
            line = json.dumps(event, allow_nan=False, ensure_ascii=False)
            if len(line.encode("utf-8")) > self.cfg.realistic_max_event_bytes:
                raise ValueError("event exceeds realistic_max_event_bytes")
        else:
            line = json.dumps(event)
        self._output.write(line + "\n")
        self._output.flush()

    def _load_echo(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                self.events = json.load(handle)
            logger.info("Loaded %s events from input file as JSON array.", len(self.events))
        except json.JSONDecodeError:
            self.events = []
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self.events.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.error("Failed to parse JSON: %s", line)
            logger.info("Loaded %s events from input file as JSON Lines format.", len(self.events))
        if not self.events:
            logger.warning("No events found in the input file.")

    def _load_random_schema(self, path: str):
        with open(path, "r", encoding="utf-8") as handle:
            self.schema = json.load(handle)
        logger.info("Loaded input JSON template.")
        return self.schema

    def _reject_aliased_output(self, inputs: list[str]) -> None:
        output = os.path.abspath(self.output_json_path)
        for path in inputs:
            if not path:
                continue
            if os.path.realpath(path) == os.path.realpath(output):
                raise ValueError("output path aliases an input path")
            if os.path.exists(path) and os.path.exists(output) and os.path.samefile(path, output):
                raise ValueError("output path aliases an input path")

    def _open_output(self, append: bool) -> None:
        mode = "a" if append else "w"
        self._output = open(self.output_json_path, mode, encoding="utf-8")
        if append and os.path.getsize(self.output_json_path) > 0:
            with open(self.output_json_path, "rb") as existing:
                existing.seek(-1, os.SEEK_END)
                if existing.read(1) != b"\n":
                    self._output.write("\n")

    def _runtime_config(self, config) -> dict:
        return {
            "realistic_seed": config.realistic_seed,
            "realistic_optional_probability": config.realistic_optional_probability,
            "realistic_null_probability": config.realistic_null_probability,
            "realistic_example_probability": config.realistic_example_probability,
            "realistic_max_attempts": config.realistic_max_attempts,
            "realistic_max_depth": config.realistic_max_depth,
            "realistic_max_nodes": config.realistic_max_nodes,
            "realistic_max_event_bytes": config.realistic_max_event_bytes,
            "realistic_preflight_samples": config.realistic_preflight_samples,
            "realistic_array_max_when_unbounded": config.realistic_array_max_when_unbounded,
            "realistic_number_bound_when_unbounded": config.realistic_number_bound_when_unbounded,
            "type_weights": config.type_weights or {},
            "process_tick_seconds": config.process_tick_seconds,
        }


if __name__ == "__main__":
    FilterStubApplication.run()
