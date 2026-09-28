import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest

from openfilter.filter_runtime.filter import Frame

from filter_stub_application.distributions import (
    DistributionError,
    _segment_at,
    integrate_intensity,
    next_nhpp_time,
    poisson,
    sample_distribution,
    truncated_normal,
    validate_segments,
)
from filter_stub_application.filter import FilterStubApplication, FilterStubApplicationConfig
from filter_stub_application.process import ProcessEngine, ProfileError
from filter_stub_application.realistic import (
    DRAFT7,
    GenerationError,
    RealisticGenerator,
    SchemaContractError,
    _bounds,
    _emit_regex,
    _merge_schemas,
    stream,
)


SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "count"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 8},
        "count": {"type": "integer", "minimum": 0, "maximum": 5},
    },
}


class RealisticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.schema_path = os.path.join(self.tmp.name, "schema.json")
        self.output_path = os.path.join(self.tmp.name, "out.json")
        with open(self.schema_path, "w", encoding="utf-8") as handle:
            json.dump(SCHEMA, handle)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_repeats_sequence_and_ticks_differ(self):
        first = self._sequence(7)
        second = self._sequence(7)
        self.assertEqual(first, second)
        self.assertGreater(len(set(first)), 1)
        self.assertNotEqual(first, self._sequence(8))

    def test_draft04_boolean_exclusive_is_rejected(self):
        path = os.path.join(self.tmp.name, "bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"type": "number", "minimum": 0, "exclusiveMinimum": True}, handle)
        config = self._config(schema=path)
        app = FilterStubApplication(config)
        with self.assertRaises(SchemaContractError):
            app.setup(app.config)

    def test_profile_without_initial_fails(self):
        path = os.path.join(self.tmp.name, "profile.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"states": {"a": {"transitions": []}}}, handle)
        config = self._config(profile=path)
        app = FilterStubApplication(config)
        with self.assertRaises(Exception):
            app.setup(app.config)

    def test_arrival_order_and_dwell_minimum(self):
        profile = {
            "initial": "a",
            "instances": 2,
            "initial_clock": "2026-01-01T00:00:00Z",
            "arrival": {"dist": "poisson", "rate": 1},
            "states": {
                "a": {
                    "dwell": {"dist": "constant", "seconds": 0.5},
                    "transitions": [{"to": "b", "p": 1}],
                    "on_enter": [{"op": "set", "pointer": "/phase", "value": "a"}],
                    "on_event": [{"op": "set", "pointer": "/phase", "value": "holding"}],
                },
                "b": {"transitions": []},
            },
        }
        engine = ProcessEngine(profile, {"realistic_seed": 3, "process_tick_seconds": 1})
        times = []
        for _ in range(12):
            pending = engine.next_pending()
            self.assertIsNotNone(pending)
            times.append((pending.time, pending.instance_id))
            engine.commit()
        self.assertEqual(times, sorted(times))
        self.assertTrue(set(item[1] for item in times).issubset({0, 1}))

    def test_truncated_normal_stays_inside_support(self):
        rng = random.Random(1)
        values = [truncated_normal(rng, 0, 1, -0.2, 0.2) for _ in range(30)]
        self.assertTrue(all(-0.2 <= value <= 0.2 for value in values))

    def test_poisson_mean_is_near_rate(self):
        rng = random.Random(2)
        draws = [poisson(rng, 4) for _ in range(400)]
        mean = sum(draws) / len(draws)
        self.assertLess(abs(mean - 4), 0.6)

    def test_nhpp_integral_crosses_segments(self):
        segments = [
            {"start": 0, "end": 1, "rate": 1},
            {"start": 1, "end": 3, "rate": 2},
        ]
        self.assertAlmostEqual(integrate_intensity(segments, 0.5, 2.5, None), 0.5 * 1 + 1.5 * 2)

    def test_independent_streams_do_not_share_draws(self):
        left = stream(1, 0, "arrival")
        right = stream(1, 0, "payload")
        self.assertNotEqual(left.random(), right.random())

    def test_inclusive_minimum_survives_a_looser_exclusive_bound(self):
        self.assertEqual(_bounds({"minimum": 10, "exclusiveMinimum": 0, "maximum": 12}), (10, 12, False, False))
        document = self._generate({"type": "integer", "minimum": 10, "exclusiveMinimum": 0, "maximum": 12})
        self.assertGreaterEqual(document, 10)
        self.assertLessEqual(document, 12)

    def test_poisson_exclusive_bound_excludes_the_endpoint(self):
        rng = random.Random(0)
        values = [
            sample_distribution(
                rng, {"dist": "poisson", "rate": 2},
                low=0, high=5, low_exclusive=True, high_exclusive=False, integer=True,
            )
            for _ in range(40)
        ]
        self.assertNotIn(0, values)

    def test_integer_normal_rejects_values_outside_the_support(self):
        rng = random.Random(1)
        values = [
            sample_distribution(
                rng, {"dist": "normal", "mean": 0.4, "std": 0.05},
                low=0, high=0.6, low_exclusive=False, high_exclusive=True, integer=True,
            )
            for _ in range(30)
        ]
        self.assertTrue(all(0 <= value < 0.6 for value in values))

    def test_pool_const_is_shared(self):
        document = self._generate({
            "type": "object",
            "additionalProperties": False,
            "required": ["a", "b"],
            "properties": {
                "a": {"type": "string", "minLength": 1, "maxLength": 8, "x-pool": "p"},
                "b": {"const": "fixed", "x-pool": "p"},
            },
        })
        self.assertEqual(document, {"a": "fixed", "b": "fixed"})

    def test_oneof_ref_resolves_against_the_document_root(self):
        document = self._generate({
            "oneOf": [{"$ref": "#/definitions/s"}, {"type": "number", "minimum": 0, "maximum": 1}],
            "definitions": {"s": {"type": "string", "minLength": 1, "maxLength": 3}},
        })
        self.assertTrue(isinstance(document, (str, int, float)))

    def test_ref_chain_does_not_consume_instance_depth(self):
        document = self._generate(
            {"$ref": "#/definitions/a", "definitions": {
                "a": {"$ref": "#/definitions/b"},
                "b": {"type": "string", "const": "ok"},
            }},
            realistic_max_depth=1,
        )
        self.assertEqual(document, "ok")

    def test_empty_string_is_allowed_when_max_length_is_zero(self):
        self.assertEqual(self._generate({"type": "string", "maxLength": 0}), "")

    def test_multiple_of_can_reach_the_top_of_a_wide_span(self):
        seen = set()
        for seed in range(30):
            seen.add(self._generate(
                {"type": "integer", "minimum": 0, "maximum": 100000, "multipleOf": 1},
                realistic_seed=seed,
            ))
        self.assertGreater(max(seen), 10000)

    def test_bad_state_arrival_fails_at_setup(self):
        with self.assertRaises(ProfileError):
            ProcessEngine(
                {"initial": "a", "states": {"a": {"arrival": {"dist": "nope"}, "transitions": []}}},
                {"realistic_seed": 1, "process_tick_seconds": 1},
            )

    def test_missing_pointer_is_a_profile_error(self):
        engine = ProcessEngine(
            {"initial": "a", "states": {"a": {"transitions": [], "on_event": [
                {"op": "copy", "from": "/missing", "to": "/x"},
            ]}}},
            {"realistic_seed": 1, "process_tick_seconds": 1},
        )
        with self.assertRaises(ProfileError):
            engine.next_pending()

    def test_sample_does_not_alias_the_profile(self):
        profile = {"initial": "a", "states": {"a": {"transitions": [], "on_event": [
            {"op": "sample", "pointer": "/item", "choices": [{"value": {"k": 1}, "p": 1}]},
        ]}}}
        engine = ProcessEngine(profile, {"realistic_seed": 1, "process_tick_seconds": 1})
        pending = engine.next_pending()
        pending.overlay["item"]["k"] = 99
        self.assertEqual(profile["states"]["a"]["on_event"][0]["choices"][0]["value"], {"k": 1})

    def test_transition_weight_defaults_to_one(self):
        engine = ProcessEngine(
            {
                "initial": "a",
                "states": {
                    "a": {"transitions": [{"to": "b"}]},
                    "b": {"transitions": []},
                },
            },
            {"realistic_seed": 1, "process_tick_seconds": 1},
        )
        pending = engine.next_pending()
        self.assertEqual(pending.state, "b")
        self.assertTrue(pending.entering)

    def test_sample_choice_requires_value(self):
        with self.assertRaises(ProfileError):
            ProcessEngine(
                {"initial": "a", "states": {"a": {"transitions": [], "on_event": [
                    {"op": "sample", "pointer": "/item", "choices": [{"p": 1}]},
                ]}}},
                {"realistic_seed": 1, "process_tick_seconds": 1},
            )
        with self.assertRaises(ProfileError):
            ProcessEngine(
                {"initial": "a", "states": {"a": {"transitions": [], "on_enter": [
                    {"op": "append_sample", "pointer": "/items", "choices": [{"p": 1}]},
                ]}}},
                {"realistic_seed": 1, "process_tick_seconds": 1},
            )

    def test_array_pointer_assigns_an_index(self):
        engine = ProcessEngine(
            {"initial": "a", "states": {"a": {"transitions": [], "on_event": [
                {"op": "set", "pointer": "/items/0", "value": "x"},
            ]}}},
            {"realistic_seed": 1, "process_tick_seconds": 1},
        )
        self.assertEqual(engine.next_pending().overlay["items"], ["x"])

    def test_tick_dwell_of_one_instance_does_not_age_the_other(self):
        engine = ProcessEngine(
            {
                "initial": "a",
                "instances": 2,
                "states": {"a": {
                    "dwell": {"dist": "constant", "seconds": 5},
                    "transitions": [{"to": "b", "p": 1}],
                    "on_event": [{"op": "set", "pointer": "/phase", "value": "holding"}],
                }, "b": {"transitions": []}},
            },
            {"realistic_seed": 1, "process_tick_seconds": 1},
        )
        first = engine.next_pending()
        engine.commit()
        second = engine.next_pending()
        self.assertNotEqual(first.instance_id, second.instance_id)
        self.assertEqual(first.time, 1)
        self.assertEqual(second.time, 1)
        self.assertFalse(first.entering)
        self.assertFalse(second.entering)

    def test_event_is_published_without_mutating_the_input_frame(self):
        config = self._config()
        app = FilterStubApplication(config)
        app.setup(app.config)
        incoming = Frame(data={"upstream": 1})
        output = app.process({"main": incoming})
        self.assertEqual(incoming.data, {"upstream": 1})
        self.assertIn("event", output["main"].data)
        self.assertIn("event_id", output["main"].data)
        self.assertFalse(output["main"].has_image)
        app.shutdown()

    def test_collision_is_rejected_before_the_event_is_consumed(self):
        config = self._config()
        app = FilterStubApplication(config)
        app.setup(app.config)
        incoming = Frame(data={"event": {"already": True}})
        with self.assertRaises(ValueError):
            app.process({"main": incoming})
        with open(self.output_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "")
        self.assertIsNone(app.process_engine)
        app.shutdown()

    def test_boolean_ref_is_allowed(self):
        document = self._generate({"$ref": "#/defs/ok", "defs": {"ok": True}})
        json.dumps(document, allow_nan=False)

    def test_hidden_ref_target_is_checked(self):
        with self.assertRaises(SchemaContractError):
            RealisticGenerator(
                {"$ref": "#/hidden", "hidden": {"not": True, "type": "string"}},
                self._gen_config(),
            )

    def test_multiple_of_keeps_decimal_points(self):
        seen = {self._generate({"type": "number", "minimum": 0, "maximum": 3, "multipleOf": 0.3}, realistic_seed=seed)
                for seed in range(40)}
        self.assertIn(2.1, seen)

    def test_contradictory_contains_fails_at_setup(self):
        with self.assertRaises(SchemaContractError):
            RealisticGenerator(
                {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": [{"type": "integer", "const": 0}],
                    "contains": {"type": "string", "const": "x"},
                },
                self._gen_config(),
            )

    def test_type_only_contains_fails_at_setup(self):
        with self.assertRaises(SchemaContractError):
            RealisticGenerator(
                {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "integer"},
                    "contains": {"type": "string"},
                },
                self._gen_config(),
            )

    def test_absolute_id_ref(self):
        self.assertEqual(
            self._generate({
                "$id": "http://example.com/s",
                "$ref": "http://example.com/s#foo",
                "definitions": {"named": {"$id": "#foo", "const": "ok"}},
            }),
            "ok",
        )
        self.assertEqual(
            self._generate({
                "$id": "http://example.com/s",
                "$ref": "http://example.com/s#/definitions/named",
                "definitions": {"named": {"const": "ok"}},
            }),
            "ok",
        )
        self.assertEqual(
            self._generate({
                "$ref": "http://example.com/s#foo",
                "definitions": {"named": {"$id": "http://example.com/s#foo", "const": "ok"}},
            }),
            "ok",
        )

    def test_tuple_unique_items_fails_at_setup(self):
        with self.assertRaises(SchemaContractError):
            RealisticGenerator(
                {
                    "type": "array",
                    "uniqueItems": True,
                    "minItems": 3,
                    "additionalItems": False,
                    "items": [{"type": "boolean"}, {"const": True}],
                },
                self._gen_config(),
            )

    def test_unique_items_distinguishes_one_and_true(self):
        document = self._generate({
            "type": "array",
            "uniqueItems": True,
            "minItems": 2,
            "maxItems": 2,
            "additionalItems": False,
            "items": [{"const": 1}, {"const": True}],
        })
        self.assertEqual({json.dumps(item) for item in document}, {"1", "true"})

    def test_allof_enum_intersection_is_used(self):
        values = {
            self._generate({"allOf": [{"enum": [1, 2, 3]}, {"enum": [2, 3, 4]}]}, realistic_seed=seed)
            for seed in range(16)
        }
        self.assertTrue(values <= {2, 3})
        self.assertTrue(values)

    def test_allof_enum_and_type_order_is_sorted(self):
        merged = _merge_schemas([
            {"enum": ["zeta", "alpha", "mu"], "type": ["object", "string", "integer"]},
            {"enum": ["mu", "alpha", "zeta"], "type": ["integer", "string"]},
        ])
        self.assertEqual(merged["enum"], ["alpha", "mu", "zeta"])
        self.assertEqual(merged["type"], ["integer", "string"])

    def test_allof_enum_is_stable_across_pythonhashseed(self):
        snippet = r"""
import json
from filter_stub_application.realistic import RealisticGenerator
schema = {"allOf": [{"enum": ["zeta", "alpha", "mu"]}, {"enum": ["mu", "alpha", "zeta"]}]}
config = {
    "realistic_seed": 7,
    "realistic_max_attempts": 20,
    "realistic_max_depth": 8,
    "realistic_max_nodes": 1000,
    "realistic_max_event_bytes": 100000,
    "realistic_preflight_samples": 0,
    "realistic_optional_probability": 1,
    "realistic_null_probability": 0,
    "realistic_example_probability": 0,
    "realistic_array_max_when_unbounded": 3,
    "process_tick_seconds": 1,
}
gen = RealisticGenerator(schema, config)
print(json.dumps([gen.generate_document() for _ in range(8)]))
"""
        sequences = []
        for hash_seed in ("0", "1"):
            env = os.environ.copy()
            env["PYTHONHASHSEED"] = hash_seed
            output = subprocess.check_output([sys.executable, "-c", snippet], env=env, text=True)
            sequences.append(json.loads(output))
        self.assertEqual(sequences[0], sequences[1])
        self.assertGreater(len(set(sequences[0])), 1)

    def test_allof_items_are_intersected(self):
        values = [
            self._generate(
                {
                    "allOf": [
                        {"type": "array", "minItems": 1, "maxItems": 1,
                         "items": {"type": "integer", "minimum": 7, "maximum": 9}},
                        {"items": {"type": "integer", "minimum": 8, "maximum": 12}},
                    ]
                },
                realistic_seed=seed,
            )
            for seed in range(10)
        ]
        self.assertTrue(all(item[0] in (8, 9) for item in values))
        with self.assertRaises(GenerationError):
            self._generate({
                "allOf": [
                    {"type": "array", "minItems": 1, "items": {"type": "integer"}},
                    {"items": {"type": "string"}},
                ]
            })

    def test_unsatisfiable_array_bounds_fail_at_setup(self):
        with self.assertRaises(SchemaContractError):
            RealisticGenerator(
                {"type": "array", "minItems": 3, "additionalItems": False, "items": [{}, {}]},
                self._gen_config(),
            )
        with self.assertRaises(SchemaContractError):
            RealisticGenerator({"type": "array", "items": False, "minItems": 1}, self._gen_config())
        with self.assertRaises(SchemaContractError):
            RealisticGenerator({"type": "array", "minItems": 2, "maxItems": 1}, self._gen_config())

    def test_allof_later_bounds_are_crossed(self):
        values = [
            self._generate({"allOf": [{"type": "integer"}, {"minimum": 40, "maximum": 42}]}, realistic_seed=seed)
            for seed in range(12)
        ]
        self.assertTrue(all(40 <= value <= 42 for value in values))
        tighter = [
            self._generate(
                {"allOf": [
                    {"type": "number", "minimum": 0, "maximum": 100},
                    {"exclusiveMinimum": 50, "maximum": 55},
                ]},
                realistic_seed=seed,
            )
            for seed in range(12)
        ]
        self.assertTrue(all(50 < value <= 55 for value in tighter))

    def test_null_probability_zero_never_emits_null(self):
        values = [
            self._generate({"type": ["string", "null"], "minLength": 1, "maxLength": 2}, realistic_null_probability=0)
            for _ in range(20)
        ]
        self.assertNotIn(None, values)

    def test_bool_arrival_rate_is_rejected(self):
        with self.assertRaises(ProfileError):
            ProcessEngine(
                {"initial": "a", "states": {"a": {"transitions": [], "arrival": {"dist": "poisson", "rate": True}}}},
                {"realistic_seed": 1, "process_tick_seconds": 1},
            )

    def test_anchor_ref_without_slash(self):
        self.assertEqual(
            self._generate({"$ref": "#foo", "definitions": {"named": {"$id": "#foo", "const": "ok"}}}),
            "ok",
        )

    def test_missing_event_topic_does_not_write(self):
        config = self._config()
        config["event_topic"] = "missing"
        app = FilterStubApplication(config)
        app.setup(app.config)
        app.process({"other": Frame(data={"x": 1})})
        with open(self.output_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "")
        app.shutdown()

    def test_none_event_topic_stays_none(self):
        config = self._config()
        app = FilterStubApplication(config)
        app.setup(app.config)
        output = app.process({"main": None})
        self.assertIsNone(output["main"])
        with open(self.output_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "")
        app.shutdown()

    def test_state_without_arrival_stays_scheduled(self):
        engine = ProcessEngine(
            {
                "initial": "a",
                "arrival": {"dist": "poisson", "rate": 2},
                "states": {
                    "a": {
                        "dwell": {"dist": "constant", "seconds": 0},
                        "transitions": [{"to": "b", "p": 1}],
                    },
                    "b": {"transitions": [{"to": "a", "p": 1}]},
                },
            },
            {"realistic_seed": 1, "process_tick_seconds": 1},
        )
        times = []
        for _ in range(6):
            pending = engine.next_pending()
            self.assertIsNotNone(pending)
            times.append(pending.time)
            engine.commit()
        self.assertEqual(times, sorted(times))
        self.assertGreater(times[-1], times[0])

    def test_observation_is_per_instance(self):
        engine = ProcessEngine(
            {
                "initial": "a",
                "instances": 2,
                "arrival": {"dist": "poisson", "rate": 1},
                "states": {"a": {"transitions": []}},
            },
            {"realistic_seed": 4, "process_tick_seconds": 1},
        )
        first = engine.next_pending()
        engine.commit()
        second = engine.next_pending()
        self.assertNotEqual(first.instance_id, second.instance_id)
        self.assertEqual(second.observation[0], 0.0)
        self.assertGreater(second.observation[1], 0.0)

    def test_event_ids_are_unique(self):
        engine = ProcessEngine(
            {"initial": "a", "states": {"a": {"transitions": []}}},
            {"realistic_seed": 1, "process_tick_seconds": 0.0000001},
        )
        ids = []
        for _ in range(5):
            pending = engine.next_pending()
            ids.append(pending.event_id)
            engine.commit()
        self.assertEqual(len(ids), len(set(ids)))

    def test_finite_nhpp_requires_on_exhaustion(self):
        with self.assertRaises(ProfileError):
            ProcessEngine(
                {
                    "initial": "a",
                    "arrival": {
                        "dist": "nonhomogeneous_poisson",
                        "segments": [{"start": 0, "end": 1, "rate": 1}],
                    },
                    "states": {"a": {"transitions": []}},
                },
                {"realistic_seed": 1, "process_tick_seconds": 1},
            )

    def test_regex_class_escapes_and_quantifiers(self):
        rng = random.Random(0)
        self.assertRegex(_emit_regex(rng, r"\d{3}", 64), r"^\d{3}$")
        self.assertRegex(_emit_regex(rng, r"\d{3}-\d{4}", 64), r"^\d{3}-\d{4}$")
        self.assertRegex(_emit_regex(rng, r"\w+", 64), r"^\w+$")
        self.assertRegex(_emit_regex(rng, r"[A-Z]{2}\d{4}", 64), r"^[A-Z]{2}\d{4}$")
        self.assertRegex(_emit_regex(rng, r"[\d]{2}", 64), r"^\d{2}$")
        document = self._generate({
            "$schema": DRAFT7,
            "type": "object",
            "required": ["phone"],
            "additionalProperties": False,
            "properties": {"phone": {"type": "string", "pattern": r"^\d{3}-\d{4}$"}},
        })
        self.assertRegex(document["phone"], r"^\d{3}-\d{4}$")

    def test_nhpp_leading_gap_is_rate_zero(self):
        segs = [{"start": 3600, "rate": 1.0}]
        with self.assertRaises(DistributionError):
            validate_segments(segs, None, False, None)
        validate_segments(segs, None, True, None)
        self.assertEqual(_segment_at(segs, 0.0, None)[2], 0.0)
        nxt = next_nhpp_time(random.Random(1), segs, 0.0, None, "stop")
        self.assertGreaterEqual(nxt, 3600)
        self.assertEqual(integrate_intensity(segs, 0.0, 100.0, None), 0.0)
        bounded = [{"start": 10, "end": 20, "rate": 5.0}, {"start": 20, "end": 30, "rate": 2.0}]
        with self.assertRaises(DistributionError):
            validate_segments(bounded, None, False, "stop")
        validate_segments(bounded, None, True, "stop")
        self.assertEqual(_segment_at(bounded, 5.0, None)[2], 0.0)
        self.assertEqual(integrate_intensity(bounded, 0.0, 5.0, None), 0.0)

    def test_terminating_recursive_ref_is_allowed(self):
        schema = {
            "$schema": DRAFT7,
            "$ref": "#/definitions/node",
            "definitions": {
                "node": {
                    "type": "object",
                    "required": ["v"],
                    "properties": {
                        "v": {"type": "integer", "minimum": 0, "maximum": 3},
                        "next": {"$ref": "#/definitions/node"},
                    },
                }
            },
        }
        document = self._generate(schema, realistic_optional_probability=0)
        self.assertIn("v", document)
        self.assertNotIn("next", document)

    def test_required_recursive_ref_is_rejected(self):
        schema = {
            "$schema": DRAFT7,
            "$ref": "#/definitions/node",
            "definitions": {
                "node": {
                    "type": "object",
                    "required": ["next"],
                    "properties": {"next": {"$ref": "#/definitions/node"}},
                }
            },
        }
        with self.assertRaises(SchemaContractError):
            RealisticGenerator(schema, self._gen_config())

    def test_unsupported_format_fails_at_setup(self):
        with self.assertRaises(SchemaContractError) as caught:
            RealisticGenerator({"type": "string", "format": "ipv4"}, self._gen_config())
        self.assertIn("ipv4", str(caught.exception))
        with self.assertRaises(SchemaContractError):
            RealisticGenerator({"type": "string", "format": "uri-reference"}, self._gen_config())

    def test_property_names_constrain_additional_keys(self):
        document = self._generate({
            "$schema": DRAFT7,
            "type": "object",
            "minProperties": 2,
            "propertyNames": {"type": "string", "pattern": "^[A-Z]+$", "minLength": 2, "maxLength": 4},
            "additionalProperties": {"type": "integer", "minimum": 0, "maximum": 5},
        })
        self.assertGreaterEqual(len(document), 2)
        self.assertTrue(all(re.fullmatch(r"[A-Z]+", name) for name in document))

    def test_type_weights_are_honored(self):
        values = [
            self._generate(
                {"type": ["integer", "string"], "minimum": 0, "maximum": 5, "minLength": 1, "maxLength": 3},
                type_weights={"integer": 1, "string": 0},
            )
            for _ in range(20)
        ]
        self.assertTrue(all(isinstance(value, int) and not isinstance(value, bool) for value in values))

    def test_unbounded_number_uses_configured_span(self):
        values = [self._generate({"type": "integer"}, realistic_number_bound_when_unbounded=7) for _ in range(20)]
        self.assertTrue(all(-7 <= value <= 7 for value in values))

    def _gen_config(self, **overrides):
        config = {
            "realistic_seed": 1,
            "realistic_max_attempts": 20,
            "realistic_max_depth": 8,
            "realistic_max_nodes": 1000,
            "realistic_max_event_bytes": 100000,
            "realistic_preflight_samples": 0,
            "realistic_optional_probability": 1,
            "realistic_null_probability": 0,
            "realistic_example_probability": 0,
            "realistic_array_max_when_unbounded": 3,
            "process_tick_seconds": 1,
        }
        config.update(overrides)
        return config

    def _generate(self, schema, **overrides):
        return RealisticGenerator(schema, self._gen_config(**overrides)).generate_document()

    def _config(self, schema=None, profile=""):
        return FilterStubApplicationConfig(
            output_mode="realistic",
            input_json_template_file_path=schema or self.schema_path,
            output_json_path=self.output_path,
            process_profile_path=profile,
            trigger_mode="process",
            realistic_preflight_samples=1,
            realistic_seed=7,
        )

    def _sequence(self, seed: int) -> list[str]:
        output = os.path.join(self.tmp.name, f"out-{seed}-{len(os.listdir(self.tmp.name))}.json")
        config = self._config()
        config["output_json_path"] = output
        config["realistic_seed"] = seed
        app = FilterStubApplication(config)
        app.setup(app.config)
        for _ in range(3):
            app.process({})
        app.shutdown()
        with open(output, encoding="utf-8") as handle:
            return [line for line in handle.read().splitlines() if line]


if __name__ == "__main__":
    unittest.main()
