"""Constraint-driven draft-07 generator.

The schema stays symbolic. References are resolved while generating, and a
document is accepted only after the compiled Draft7Validator succeeds.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import random
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import unquote, urldefrag, urljoin

from jsonschema import FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError, _WrappedReferencingError
from jsonschema.validators import Draft7Validator, extend
from referencing import Registry
from referencing.jsonschema import DRAFT7 as DRAFT7_SPEC

from filter_stub_application.distributions import (
    DistributionError,
    integrate_intensity,
    integer_window,
    poisson,
    sample_distribution,
    sample_lattice,
    validate_distribution_spec,
)
from filter_stub_application.rng import stream

_WALL_CLOCK_PROVIDERS = {
    "date", "date_object", "date_of_birth", "date_this_century", "date_this_decade",
    "date_this_year", "date_this_month", "date_time", "date_time_ad",
    "date_time_between", "date_time_between_dates", "date_time_this_century",
    "date_time_this_decade", "date_time_this_year", "date_time_this_month",
    "future_date", "future_datetime", "past_date", "past_datetime",
    "iso8601", "unix_time", "time", "time_object", "times", "time_series",
}

logger = logging.getLogger(__name__)

DRAFT7 = "http://json-schema.org/draft-07/schema#"
SUPPORTED_FORMATS = {"date-time", "date", "time", "email", "uuid", "uri", "hostname"}
JSON_TYPES = ("null", "boolean", "object", "array", "number", "integer", "string")
UNSUPPORTED = {
    "if", "then", "else", "not", "patternProperties", "dependencies",
    "dependentRequired", "dependentSchemas", "prefixItems",
    "unevaluatedProperties", "unevaluatedItems", "$dynamicRef", "$recursiveRef",
}


class _Missing:
    pass


_MISSING = _Missing()


class SchemaContractError(ValueError):
    """The schema or an extension cannot be generated under the draft-07 contract."""


class GenerationError(RuntimeError):
    """One attempt failed. The caller may retry the payload without touching process state."""


_RETRYABLE = (GenerationError, ValidationError, DistributionError, ValueError, _WrappedReferencingError)


class RealisticGenerator:
    def __init__(self, schema: dict, config: dict, instance_id: int = 0, *, defer_preflight: bool = False):
        self.root = schema
        self.config = config
        self.instance_id = instance_id
        self.validator = compile_schema(schema)
        self._payload_by_instance: dict[int, random.Random] = {
            instance_id: stream(config["realistic_seed"], instance_id, "payload"),
        }
        self.payload_rng = self._payload_by_instance[instance_id]
        self._faker_by_instance: dict[Any, Any] = {}
        self._preflight_active = False
        self.sequences: dict[str, int] = {}
        self.sequence_steps: dict[str, int] = {}
        self.pending_sequences: dict[str, int] = {}
        self.pool: dict[str, Any] = {}
        self._pool_sites: dict[str, list] = defaultdict(list)
        self._anchors: dict[str, Any] = {}
        self._ids: dict[str, Any] = {}
        self._base = ""
        self._sequence_specs: dict[str, tuple[int, int]] = {}
        self.nodes = 0
        self._ref_hops = 0
        self.observation = (0.0, config.get("process_tick_seconds", 1.0))
        self._index_anchors(schema)
        self._check(schema, 0)
        self._intersect_pools()
        if not defer_preflight and config.get("realistic_preflight_samples", 0):
            self.run_preflight()

    def generate_document(self, fixed: Optional[dict] = None, instance_id: int = 0) -> dict:
        if not self._preflight_active:
            self._activate(instance_id)
        attempts = self.config["realistic_max_attempts"]
        last = None
        for _ in range(attempts):
            self.nodes = 0
            self._ref_hops = 0
            self.pool = {}
            self.pending_sequences = {}
            try:
                document = self._gen(self.root, 0)
                if fixed:
                    document = _overlay(document, fixed)
                self._reject_non_finite(document)
                encoded = json.dumps(document, allow_nan=False, ensure_ascii=False)
                if len(encoded.encode("utf-8")) > self.config["realistic_max_event_bytes"]:
                    raise GenerationError("event exceeds realistic_max_event_bytes")
                self.validator.validate(document)
                return document
            except SchemaContractError:
                raise
            except _RETRYABLE as exc:
                last = exc
        raise GenerationError(f"payload attempts exhausted: {last}")

    def commit_sequences(self) -> None:
        for name, value in self.pending_sequences.items():
            step = self.sequence_steps.get(name, 1)
            self.sequences[name] = value + step
        self.pending_sequences = {}

    def _activate(self, instance_id: int) -> None:
        self.instance_id = instance_id
        if instance_id not in self._payload_by_instance:
            self._payload_by_instance[instance_id] = stream(
                self.config["realistic_seed"], instance_id, "payload",
            )
        self.payload_rng = self._payload_by_instance[instance_id]

    def run_preflight(self, overlay: Optional[dict] = None) -> None:
        samples = self.config.get("realistic_preflight_samples", 0)
        if not samples:
            return
        saved_rng = self.payload_rng
        saved_fakers = self._faker_by_instance
        self._preflight_active = True
        self.payload_rng = stream(self.config["realistic_seed"], self.instance_id, "preflight")
        self._faker_by_instance = {}
        try:
            for _ in range(samples):
                self.generate_document(overlay)
        finally:
            self._preflight_active = False
            self.payload_rng = saved_rng
            self._faker_by_instance = saved_fakers
            self.pending_sequences = {}
            self.pool = {}

    def _index_anchors(self, node, depth: int = 0, base: str = "") -> None:
        if depth > 64:
            return
        if isinstance(node, list):
            for item in node:
                self._index_anchors(item, depth + 1, base)
            return
        if not isinstance(node, dict):
            return
        identifier = node.get("$id")
        next_base = base
        if isinstance(identifier, str) and identifier:
            absolute = urljoin(base, identifier)
            resource, fragment = urldefrag(absolute)
            if resource and not fragment:
                self._ids[resource] = node
                next_base = resource
                if not self._base:
                    self._base = resource
            elif fragment:
                self._anchors[fragment] = node
                self._ids[absolute] = node
                if resource:
                    next_base = resource
                    if not self._base:
                        self._base = resource
            if identifier not in self._ids:
                self._ids[identifier] = node
        for value in node.values():
            self._index_anchors(value, depth + 1, next_base)

    def _check(
        self, schema, depth: int, stack: Optional[set] = None, *, required_path: bool = True,
        inferred_types=None, catalog: bool = False,
    ) -> None:
        if depth > 64:
            raise SchemaContractError("schema is too deep to check")
        if schema is True or schema is False:
            return
        if not isinstance(schema, dict):
            raise SchemaContractError("schema node must be an object or boolean")
        unknown = UNSUPPORTED.intersection(schema)
        if unknown:
            raise SchemaContractError(f"unsupported keywords: {sorted(unknown)}")
        if schema.get("$schema") not in (None, DRAFT7):
            raise SchemaContractError(f"only draft-07 is supported, got {schema.get('$schema')!r}")
        for key in ("exclusiveMinimum", "exclusiveMaximum"):
            if isinstance(schema.get(key), bool):
                raise SchemaContractError(f"draft-04 boolean {key} is rejected")
        stack = set(stack or ())
        ref = schema.get("$ref")
        if isinstance(ref, str):
            if ref in stack:
                if required_path:
                    raise SchemaContractError(f"non-terminating $ref cycle: {ref}")
            else:
                self._check(
                    self._resolve(ref), depth + 1, stack | {ref},
                    required_path=required_path, inferred_types=inferred_types,
                )
        pool_name = _pool_name(schema)
        if pool_name:
            self._pool_sites[pool_name].append(schema)
        if "x-sequence" in schema:
            _check_sequence(schema)
            self._remember_sequence_spec(schema["x-sequence"])
        if "x-faker" in schema:
            _check_faker(schema["x-faker"])
        if "x-distribution" in schema:
            types = schema.get("type")
            integer = types == "integer" or (isinstance(types, list) and "integer" in types and "number" not in types)
            try:
                validate_distribution_spec(schema["x-distribution"], integer=integer or types == "integer")
            except DistributionError as exc:
                raise SchemaContractError(str(exc)) from exc
        if "pattern" in schema:
            _compile_pattern(schema["pattern"])
        if self._may_be_string(schema, inferred_types) and "format" in schema and schema["format"] not in SUPPORTED_FORMATS:
            raise SchemaContractError(f"unsupported format {schema['format']!r}")
        if not catalog:
            self._check_type_weight_mass(schema)
        required = set(schema.get("required") or [])
        for name, child in (schema.get("properties") or {}).items():
            self._check(
                child, depth + 1, stack,
                required_path=required_path and name in required, catalog=catalog,
            )
        for key in ("definitions", "$defs"):
            for child in (schema.get(key) or {}).values():
                self._check(child, depth + 1, stack, required_path=True, catalog=True)
        min_items = schema.get("minItems", 0)
        items = schema.get("items")
        if isinstance(items, list):
            for index, child in enumerate(items):
                self._check(
                    child, depth + 1, stack,
                    required_path=required_path and index < min_items, catalog=catalog,
                )
        elif isinstance(items, dict):
            self._check(
                items, depth + 1, stack,
                required_path=required_path and min_items > 0, catalog=catalog,
            )
        additional_items = schema.get("additionalItems")
        if isinstance(additional_items, dict):
            extra_required = required_path and min_items > (len(items) if isinstance(items, list) else 0)
            self._check(additional_items, depth + 1, stack, required_path=extra_required, catalog=catalog)
        context_types = self._instance_types(schema, inferred_types)
        for key in ("allOf",):
            for child in schema.get(key) or []:
                self._check(
                    child, depth + 1, stack,
                    required_path=required_path, inferred_types=context_types, catalog=catalog,
                )
        for key in ("oneOf", "anyOf"):
            self._check_choice_branches(
                schema.get(key) or [], depth, stack, required_path, key, catalog=catalog,
            )
        contains = schema.get("contains")
        if isinstance(contains, dict):
            self._check(contains, depth + 1, stack, required_path=True, catalog=catalog)
        named = len(schema.get("properties") or {})
        must_add = schema.get("minProperties", 0) > named
        additional_props = schema.get("additionalProperties")
        if isinstance(additional_props, dict):
            self._check(
                additional_props, depth + 1, stack,
                required_path=required_path and must_add, catalog=catalog,
            )
        names = schema.get("propertyNames")
        if isinstance(names, dict):
            self._check(names, depth + 1, stack, required_path=False, catalog=catalog)
        if schema.get("additionalProperties") is False:
            named_keys = set(schema.get("properties") or []) | set(schema.get("required") or [])
            if schema.get("minProperties", 0) > len(named_keys):
                raise SchemaContractError("minProperties is unsatisfiable when additionalProperties is false")
        for value in list(schema.get("enum") or []) + list(schema.get("examples") or []):
            _measure(value, self.config["realistic_max_event_bytes"])
        if "const" in schema:
            _measure(schema["const"], self.config["realistic_max_event_bytes"])
        self._check_array_bounds(schema)
        self._check_unique_domain(schema)
        self._check_contains_feasible(schema)

    def _check_choice_branches(
        self, branches: list, depth: int, stack: set, required_path: bool, kind: str, *, catalog: bool = False,
    ) -> None:
        """oneOf/anyOf may terminate via any branch. Reject only when every branch is a required cycle."""
        if not branches:
            return
        doomed = 0
        last_cycle = None
        for child in branches:
            saved_pools = {name: list(nodes) for name, nodes in self._pool_sites.items()}
            saved_seq = dict(self._sequence_specs)
            try:
                self._check(child, depth + 1, stack, required_path=required_path, catalog=catalog)
            except SchemaContractError as exc:
                self._pool_sites.clear()
                for name, nodes in saved_pools.items():
                    self._pool_sites[name] = nodes
                self._sequence_specs = saved_seq
                if "non-terminating $ref cycle" in str(exc):
                    doomed += 1
                    last_cycle = exc
                    continue
                raise
        if doomed == len(branches):
            raise SchemaContractError(
                str(last_cycle) if last_cycle else f"non-terminating $ref cycle through {kind}"
            )

    def _check_type_weight_mass(self, schema: dict) -> None:
        # _gen returns before _typed for these keywords. allOf is included on purpose:
        # _all_of merges the siblings back in and can still reach _typed when no branch
        # narrows type to a scalar, but this node cannot tell that case from one that does.
        # Skipping is the safer half: default preflight still fails setup, and with preflight
        # off the _typed backstop is a non-retryable SchemaContractError through failure_policy.
        # $ref is ignored as a sibling by draft-07; generation follows the target alone.
        if any(
            key in schema
            for key in (
                "const", "enum", "x-sequence", "x-distribution", "x-faker",
                "oneOf", "anyOf", "allOf", "$ref",
            )
        ):
            return
        types = schema.get("type")
        if not isinstance(types, list):
            return
        non_null = [item for item in types if item != "null"]
        if not non_null:
            return
        weights = self.config.get("type_weights") or {}
        mass = sum(float(weights.get(item, 1)) for item in non_null)
        if mass <= 0:
            raise SchemaContractError(f"type_weights give no mass to type union {non_null}")

    def _may_be_string(self, schema: dict, inferred_types=None) -> bool:
        pinned = self._instance_types(schema, inferred_types)
        if pinned is not None:
            return "string" in pinned
        return True

    def _instance_types(self, schema, inferred=None, depth: int = 0) -> Optional[set]:
        if depth > 64 or schema is True or schema is False or not isinstance(schema, dict):
            return inferred
        if "$ref" in schema:
            try:
                return self._instance_types(self._resolve(schema["$ref"]), inferred, depth + 1)
            except SchemaContractError:
                return inferred
        pinned = _declared_types(schema)
        if "allOf" in schema:
            known = [self._instance_types(part, None, depth + 1) for part in schema["allOf"]]
            known = [item for item in known if item is not None]
            if pinned is not None:
                known.append(pinned)
            pinned = set.intersection(*known) if known else pinned
        if inferred is not None:
            pinned = inferred if pinned is None else pinned & inferred
        return pinned

    def _check_array_bounds(self, schema: dict) -> None:
        minimum = schema.get("minItems", 0)
        maximum = schema.get("maxItems")
        items = schema.get("items")
        if maximum is not None and minimum > maximum:
            raise SchemaContractError("minItems is greater than maxItems")
        if items is False and minimum > 0:
            raise SchemaContractError("items: false cannot satisfy minItems")
        if isinstance(items, list) and schema.get("additionalItems") is False:
            cap = len(items) if maximum is None else min(len(items), maximum)
            if minimum > cap:
                raise SchemaContractError("minItems exceeds tuple length when additionalItems is false")

    def _remember_sequence_spec(self, spec: dict) -> None:
        name = spec["name"]
        pair = (spec.get("start", 0), spec.get("step", 1))
        previous = self._sequence_specs.get(name)
        if previous is not None and previous != pair:
            raise SchemaContractError(f"x-sequence {name!r} has conflicting start or step")
        self._sequence_specs[name] = pair

    def _check_unique_domain(self, schema: dict) -> None:
        if not schema.get("uniqueItems"):
            return
        minimum = schema.get("minItems", 0)
        values = _unique_value_union(schema)
        if values is not None and minimum > len(values):
            raise SchemaContractError("uniqueItems domain is smaller than minItems")

    def _check_contains_feasible(self, schema: dict) -> None:
        contains = schema.get("contains")
        if contains is None:
            return
        if schema.get("maxItems") == 0:
            raise SchemaContractError("contains is unsatisfiable when maxItems is 0")
        candidates = _contains_candidates(schema)
        if isinstance(contains, dict) and "const" in contains:
            if not any(self._node_accepts(contains["const"], candidate) for candidate in candidates):
                raise SchemaContractError("contains const is not allowed by items/additionalItems")
        if isinstance(contains, dict) and "enum" in contains:
            if not any(
                self._node_accepts(value, candidate)
                for value in contains["enum"]
                for candidate in candidates
            ):
                raise SchemaContractError("contains enum is not allowed by items/additionalItems")
        if not any(_types_overlap(contains, candidate) for candidate in candidates):
            raise SchemaContractError("contains type is disjoint from items/additionalItems")

    def _resolve(self, ref: str):
        if not isinstance(ref, str):
            raise SchemaContractError(f"external $ref is rejected: {ref!r}")
        if ref in self._ids:
            return self._require_schema(self._ids[ref], ref)
        if ref.startswith("#"):
            return self._resolve_in(self.root, ref)
        absolute = urljoin(self._base, ref)
        if absolute in self._ids:
            return self._require_schema(self._ids[absolute], ref)
        resource, fragment = urldefrag(absolute)
        base_node = self._ids.get(resource)
        if base_node is None and resource and resource == self._base:
            base_node = self.root
        if base_node is None:
            raise SchemaContractError(f"external $ref is rejected: {ref!r}")
        if not fragment:
            return self._require_schema(base_node, ref)
        return self._resolve_in(base_node, "#" + fragment)

    def _resolve_in(self, root, ref: str):
        if ref == "#":
            return self._require_schema(root, ref)
        if ref.startswith("#/"):
            node: Any = root
            for part in ref[2:].split("/"):
                part = unquote(part).replace("~1", "/").replace("~0", "~")
                if isinstance(node, dict) and part in node:
                    node = node[part]
                elif isinstance(node, list) and part.isdigit():
                    node = node[int(part)]
                else:
                    raise SchemaContractError(f"unresolvable $ref {ref!r}")
            return self._require_schema(node, ref)
        anchor = unquote(ref[1:])
        if anchor in self._anchors:
            return self._require_schema(self._anchors[anchor], ref)
        raise SchemaContractError(f"unresolvable $ref {ref!r}")

    @staticmethod
    def _require_schema(node, ref: str):
        if not isinstance(node, (dict, bool)):
            raise SchemaContractError(f"$ref {ref!r} does not point at a schema")
        return node

    def _gen(self, schema, depth: int, fixed: Any = _MISSING) -> Any:
        self.nodes += 1
        if self.nodes > self.config["realistic_max_nodes"]:
            raise GenerationError("node budget exceeded")
        if depth > self.config["realistic_max_depth"]:
            raise GenerationError("depth budget exceeded")
        if schema is False:
            raise GenerationError("schema is false")
        if schema is True or schema == {}:
            return self._any() if fixed is _MISSING else fixed
        if not isinstance(schema, dict):
            raise SchemaContractError("schema node must be an object or boolean")
        if "$ref" in schema:
            self._ref_hops += 1
            if self._ref_hops > 64:
                raise GenerationError("reference cycle exceeded")
            try:
                # Depth limits the instance, not the reference chain.
                return self._gen(self._resolve(schema["$ref"]), depth, fixed)
            finally:
                self._ref_hops -= 1
        if fixed is not _MISSING:
            return copy.deepcopy(fixed)
        pooled = self._pool_hit(schema)
        if pooled is not _MISSING:
            return copy.deepcopy(pooled)
        forced = self._forced_pool_value(schema)
        if forced is not _MISSING:
            if not self._node_accepts(forced, schema):
                raise GenerationError("pool const does not satisfy this occurrence")
            self._remember_pool(schema, forced)
            return copy.deepcopy(forced)
        if "const" in schema:
            value = copy.deepcopy(schema["const"])
            self._remember_pool(schema, value)
            return value
        if "enum" in schema:
            choices = list(schema["enum"])
            name = _pool_name(schema)
            if name:
                choices = [
                    item for item in choices
                    if all(self._node_accepts(item, node) for node in self._pool_sites[name])
                ]
                if not choices:
                    raise GenerationError(f"pool {name!r} enum intersection is empty")
            value = copy.deepcopy(self.payload_rng.choice(choices))
            self._remember_pool(schema, value)
            return value
        if "x-sequence" in schema:
            value = self._sequence(schema["x-sequence"])
            self._remember_pool(schema, value)
            return value
        if "x-distribution" in schema and schema["x-distribution"]["dist"] != "nonhomogeneous_poisson":
            value = self._distribution(schema)
            self._remember_pool(schema, value)
            return value
        if "examples" in schema and self.payload_rng.random() < self.config["realistic_example_probability"]:
            valid = [item for item in schema["examples"] if self._node_accepts(item, schema)]
            if valid:
                value = copy.deepcopy(self.payload_rng.choice(valid))
                self._remember_pool(schema, value)
                return value
        if "x-faker" in schema:
            value = self._faker(schema["x-faker"])
            self._remember_pool(schema, value)
            return value
        if "oneOf" in schema:
            return self._one_of(schema, depth)
        if "anyOf" in schema:
            branch = self.payload_rng.choice(schema["anyOf"])
            value = self._gen(branch, depth)
            if not self._node_accepts(value, schema):
                raise GenerationError("anyOf branch did not satisfy the schema")
            self._remember_pool(schema, value)
            return value
        if "allOf" in schema:
            value = self._all_of(schema, depth)
            self._remember_pool(schema, value)
            return value
        if "x-distribution" in schema and schema["x-distribution"]["dist"] == "nonhomogeneous_poisson":
            value = self._nhpp_count(schema["x-distribution"])
            self._remember_pool(schema, value)
            return value
        value = self._typed(schema, depth)
        self._remember_pool(schema, value)
        return value

    def _typed(self, schema: dict, depth: int) -> Any:
        types = schema.get("type")
        if types is None:
            if "properties" in schema or "required" in schema:
                types = "object"
            elif "items" in schema:
                types = "array"
            else:
                types = "string"
        if isinstance(types, list):
            if "null" in types:
                if self.payload_rng.random() < float(self.config.get("realistic_null_probability", 0)):
                    return None
                types = [item for item in types if item != "null"]
                if not types:
                    return None
            weights = self.config.get("type_weights") or {}
            population = [(item, float(weights.get(item, 1))) for item in types]
            if sum(weight for _, weight in population) <= 0:
                raise SchemaContractError("type_weights give no mass to this union")
            types = _weighted(self.payload_rng, population)
        if types == "object":
            return self._object(schema, depth)
        if types == "array":
            return self._array(schema, depth)
        if types == "string":
            return self._string(schema)
        if types == "number":
            return self._number(schema, integer=False)
        if types == "integer":
            return self._number(schema, integer=True)
        if types == "boolean":
            return bool(self.payload_rng.getrandbits(1))
        if types == "null":
            return None
        raise GenerationError(f"cannot generate type {types!r}")

    def _object(self, schema: dict, depth: int) -> dict:
        properties = schema.get("properties") or {}
        required = list(schema.get("required") or [])
        minimum = schema.get("minProperties", 0)
        maximum = schema.get("maxProperties")
        result = {}
        for name in required:
            result[name] = self._gen(properties.get(name, {}), depth + 1)
        optional = [name for name in properties if name not in result]
        self.payload_rng.shuffle(optional)
        for name in optional:
            if maximum is not None and len(result) >= maximum:
                break
            need = len(result) < minimum
            if need or self.payload_rng.random() < self.config["realistic_optional_probability"]:
                result[name] = self._gen(properties[name], depth + 1)
        additional = schema.get("additionalProperties", True)
        names = schema.get("propertyNames", True)
        while len(result) < minimum:
            if additional is False:
                raise GenerationError("property count is outside minProperties/maxProperties")
            key = self._fresh_key(result, names, depth)
            child = additional if isinstance(additional, dict) else {}
            result[key] = self._gen(child, depth + 1)
        if maximum is not None and len(result) > maximum:
            raise GenerationError("property count is outside minProperties/maxProperties")
        return result

    def _array(self, schema: dict, depth: int) -> list:
        items = schema.get("items", {})
        minimum = schema.get("minItems", 0)
        maximum = schema.get("maxItems")
        if isinstance(items, list):
            additional = schema.get("additionalItems", True)
            hard_max = len(items) if additional is False else minimum + self.config["realistic_array_max_when_unbounded"]
            if maximum is not None:
                hard_max = min(hard_max, maximum)
            if hard_max < minimum:
                raise GenerationError("array bounds are unsatisfiable")
            length = self.payload_rng.randint(minimum, hard_max)
            values = []
            for index in range(length):
                if index < len(items):
                    values.append(self._gen(items[index], depth + 1))
                elif additional is True:
                    values.append(self._any())
                else:
                    values.append(self._gen(additional, depth + 1))
        else:
            hard_max = minimum + self.config["realistic_array_max_when_unbounded"] if maximum is None else maximum
            if hard_max < minimum:
                raise GenerationError("array bounds are unsatisfiable")
            length = self.payload_rng.randint(minimum, hard_max)
            values = [self._gen(items, depth + 1) for _ in range(length)]
        if schema.get("uniqueItems"):
            values = _dedupe_json(values)
            if len(values) < minimum:
                raise GenerationError("uniqueItems domain is smaller than minItems")
        for contains in _contains_schemas(schema):
            values = self._place_contains(values, contains, schema, depth, maximum)
        if schema.get("uniqueItems") and len(_dedupe_json(values)) != len(values):
            raise GenerationError("uniqueItems rejected the contains item")
        return values

    def _place_contains(self, values: list, contains, schema: dict, depth: int, maximum) -> list:
        if any(self._node_accepts(item, contains) for item in values):
            return values
        extra = self._gen(contains, depth + 1)
        if schema.get("uniqueItems") and _json_contains(values, extra):
            raise GenerationError("contains item collides with uniqueItems")
        for index, existing in enumerate(values):
            slot = _slot_schema(schema, index)
            if self._node_accepts(extra, slot):
                values[index] = extra
                return values
            _ = existing
        additional = schema.get("additionalItems", True)
        can_append = additional is not False and (maximum is None or len(values) < maximum)
        if not can_append:
            raise GenerationError("contains cannot be placed without violating items")
        values.append(extra)
        return values

    def _string(self, schema: dict) -> str:
        minimum = schema.get("minLength", 0)
        default_max = 64 if "format" in schema else 12
        maximum = schema.get("maxLength", max(minimum, default_max))
        if maximum < minimum:
            raise GenerationError("string bounds are unsatisfiable")
        patterns = _schema_patterns(schema)
        if "format" in schema:
            for _ in range(50):
                value = _format_string(self.payload_rng, schema["format"])
                if minimum <= len(value) <= maximum and _matches_patterns(value, patterns):
                    return value
            raise GenerationError("format string does not satisfy length or pattern")
        if patterns:
            for _ in range(100):
                value = _pattern_string(self.payload_rng, patterns[0], minimum, maximum)
                if _matches_patterns(value, patterns[1:]):
                    return value
            raise GenerationError(f"could not satisfy pattern {patterns!r}")
        length = self.payload_rng.randint(minimum, maximum)
        alphabet = "abcdefghijklmnopqrstuvwxyz"
        return "".join(self.payload_rng.choice(alphabet) for _ in range(length))

    def _number(self, schema: dict, integer: bool):
        low, high, low_exclusive, high_exclusive = _bounds(schema)
        multiple = None if schema.get("multipleOf") is None else Decimal(str(schema["multipleOf"]))
        if "x-distribution" in schema:
            return sample_distribution(
                self.payload_rng, schema["x-distribution"],
                low=low, high=high, low_exclusive=low_exclusive, high_exclusive=high_exclusive,
                integer=integer, multiple=multiple, unbound=self._unbound_number(),
            )
        if multiple is not None:
            return sample_lattice(
                self.payload_rng, None, low, high, low_exclusive, high_exclusive, multiple, integer=integer,
                unbound=self._unbound_number(),
            )
        span = self._unbound_number()
        if low is None and high is None:
            low, high = -span, span
        elif low is None:
            low = high - span
        elif high is None:
            high = low + span
        if integer:
            try:
                start, stop = integer_window(low, high, low_exclusive, high_exclusive)
            except DistributionError as exc:
                raise GenerationError(str(exc)) from exc
            return self.payload_rng.randint(start, stop)
        for _ in range(1000):
            value = self.payload_rng.uniform(low, high)
            if _contains(value, low, high, low_exclusive, high_exclusive):
                return value
        raise GenerationError("number bounds are empty")

    def _unbound_number(self) -> float:
        return float(self.config.get("realistic_number_bound_when_unbounded", 1000))

    def _fresh_key(self, existing: dict, names, depth: int) -> str:
        for _ in range(100):
            if names is True or names is None:
                key = "k" + "".join(self.payload_rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(6))
            else:
                key = self._gen(names if names is not False else {"type": "string"}, depth + 1)
                if not isinstance(key, str):
                    raise GenerationError("propertyNames must generate a string")
            if key not in existing:
                return key
        raise GenerationError("could not allocate an additional property name")

    def _one_of(self, schema: dict, depth: int):
        branches = list(enumerate(schema["oneOf"]))
        self.payload_rng.shuffle(branches)
        for _, branch in branches:
            state = self.payload_rng.getstate()
            pool = self.pool.copy()
            pending = self.pending_sequences.copy()
            hops = self._ref_hops
            try:
                value = self._gen(branch, depth)
            except GenerationError:
                self._restore_attempt(state, pool, pending, hops)
                continue
            matches = sum(self._node_accepts(value, item) for item in schema["oneOf"])
            if matches == 1:
                self._remember_pool(schema, value)
                return value
            self._restore_attempt(state, pool, pending, hops)
        raise GenerationError("oneOf produced zero or multiple matches")

    def _restore_attempt(self, state, pool, pending, hops) -> None:
        self.payload_rng.setstate(state)
        self.pool = pool
        self.pending_sequences = pending
        self._ref_hops = hops

    def _all_of(self, schema: dict, depth: int):
        siblings = {key: value for key, value in schema.items() if key != "allOf"}
        parts = [self._materialize(branch) for branch in schema["allOf"]]
        if siblings:
            parts.append(siblings)
        merged = _merge_schemas(parts)
        value = self._gen(merged, depth)
        if not all(self._node_accepts(value, branch) for branch in schema["allOf"]):
            raise GenerationError("allOf intersection rejected the candidate")
        return value

    def _materialize(self, schema):
        hops = 0
        while isinstance(schema, dict) and "$ref" in schema:
            hops += 1
            if hops > 64:
                raise GenerationError("reference cycle exceeded")
            schema = self._resolve(schema["$ref"])
        return schema

    def _distribution(self, schema: dict):
        integer = schema.get("type") == "integer" or (
            isinstance(schema.get("type"), list) and "integer" in schema["type"] and "number" not in schema["type"]
        )
        low, high, low_exclusive, high_exclusive = _bounds(schema)
        multiple = None if schema.get("multipleOf") is None else Decimal(str(schema["multipleOf"]))
        return sample_distribution(
            self.payload_rng, schema["x-distribution"],
            low=low, high=high, low_exclusive=low_exclusive, high_exclusive=high_exclusive,
            integer=integer, multiple=multiple,
        )

    def _nhpp_count(self, spec: dict) -> int:
        if "segments" not in spec:
            raise GenerationError("nonhomogeneous_poisson requires segments")
        start, end = self.observation
        intensity = integrate_intensity(spec["segments"], start, end, spec.get("period"))
        return poisson(self.payload_rng, intensity)

    def _sequence(self, spec: dict):
        name = spec["name"]
        if name not in self.pending_sequences:
            self.pending_sequences[name] = self.sequences.get(name, spec.get("start", 0))
            self.sequence_steps[name] = spec.get("step", 1)
        return self.pending_sequences[name]

    def _forced_pool_value(self, schema):
        name = _pool_name(schema)
        if not name:
            return _MISSING
        for node in self._pool_sites.get(name, []):
            if isinstance(node, dict) and "const" in node:
                return node["const"]
        return _MISSING

    def _pool_hit(self, schema):
        pool = _pool_name(schema)
        if pool and pool in self.pool:
            value = self.pool[pool]
            if not self._node_accepts(value, schema):
                raise GenerationError(f"pool {pool!r} does not satisfy this occurrence")
            return value
        return _MISSING

    def _remember_pool(self, schema, value) -> None:
        pool = _pool_name(schema)
        if pool and pool not in self.pool:
            self.pool[pool] = copy.deepcopy(value)

    def _faker(self, spec: dict):
        _check_faker(spec)
        provider = spec["provider"] if isinstance(spec, dict) else spec
        locale = spec.get("locale") if isinstance(spec, dict) else None
        faker = self._faker_instance(locale or "en_US")
        method = getattr(faker, provider)
        value = method(**(spec.get("args") or {} if isinstance(spec, dict) else {}))
        return _jsonify(value)

    def _faker_instance(self, locale: str = "en_US"):
        key = ("preflight" if self._preflight_active else self.instance_id, locale)
        if key not in self._faker_by_instance:
            from faker import Faker
            faker = Faker(locale)
            purpose = "preflight-faker" if self._preflight_active else f"faker:{locale}"
            seed_id = 0 if self._preflight_active else self.instance_id
            seed_rng = stream(self.config["realistic_seed"], seed_id, purpose)
            faker.seed_instance(seed_rng.randint(0, 2**31 - 1))
            self._faker_by_instance[key] = faker
        return self._faker_by_instance[key]

    def _node_accepts(self, value, schema) -> bool:
        """Validate against a subschema using the document root as the $ref base."""
        try:
            return not any(self.validator.descend(value, schema))
        except (ValidationError, _WrappedReferencingError):
            return False

    def _intersect_pools(self) -> None:
        for name, nodes in self._pool_sites.items():
            consts = [node["const"] for node in nodes if isinstance(node, dict) and "const" in node]
            if consts:
                signature = json.dumps(consts[0], sort_keys=True, default=str)
                if any(json.dumps(item, sort_keys=True, default=str) != signature for item in consts[1:]):
                    raise SchemaContractError(f"x-pool {name!r} has conflicting const values")
                for node in nodes:
                    if not self._node_accepts(consts[0], node):
                        raise SchemaContractError(f"x-pool {name!r} const does not satisfy every occurrence")
            enums = []
            for node in nodes:
                if isinstance(node, dict) and "enum" in node:
                    enums.append({json.dumps(item, sort_keys=True, default=str) for item in node["enum"]})
            if enums and not set.intersection(*enums):
                raise SchemaContractError(f"x-pool {name!r} has an empty enum intersection")
            types = []
            for node in nodes:
                declared = node.get("type") if isinstance(node, dict) else None
                if isinstance(declared, str):
                    types.append({declared})
                elif isinstance(declared, list):
                    types.append(set(declared))
            if types and not set.intersection(*types):
                raise SchemaContractError(f"x-pool {name!r} has incompatible types")

    def _any(self):
        return _format_string(self.payload_rng, "uuid")

    @staticmethod
    def _reject_non_finite(value) -> None:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise GenerationError("non-finite number")
        if isinstance(value, dict):
            for item in value.values():
                RealisticGenerator._reject_non_finite(item)
        elif isinstance(value, list):
            for item in value:
                RealisticGenerator._reject_non_finite(item)


def _decimal_multiple_of(validator, multiple, instance, schema):
    if not validator.is_type(instance, "number"):
        return
    try:
        remainder = Decimal(str(instance)) % Decimal(str(multiple))
    except Exception:
        yield ValidationError(f"{instance!r} is not a multiple of {multiple}")
        return
    if remainder != 0:
        yield ValidationError(f"{instance!r} is not a multiple of {multiple}")


RealisticDraft7 = extend(Draft7Validator, {"multipleOf": _decimal_multiple_of})


def compile_schema(schema: dict) -> Draft7Validator:
    if not isinstance(schema, dict):
        raise SchemaContractError("schema must be a JSON object")
    declared = schema.get("$schema")
    if declared not in (None, DRAFT7):
        raise SchemaContractError(f"only draft-07 is supported, got {declared!r}")
    if declared is None:
        logger.info("schema has no $schema; assuming draft-07")
    try:
        Draft7Validator.check_schema(schema)
    except SchemaError as exc:
        raise SchemaContractError(str(exc)) from exc
    registry = _schema_registry(schema)
    if registry is None:
        return RealisticDraft7(schema, format_checker=FormatChecker())
    return RealisticDraft7(schema, format_checker=FormatChecker(), registry=registry)


def _schema_registry(schema: dict):
    """Expose in-document $id resources so absolute $ref URIs validate."""
    resources = _resource_nodes(schema)
    if not resources:
        return None
    registry = Registry()
    prepared_root = _prepare_registry_schema(schema)
    for uri, node in resources.items():
        contents = prepared_root if node is schema else _prepare_registry_schema(node)
        registry = registry.with_resource(uri, DRAFT7_SPEC.create_resource(contents))
    return registry


def _resource_nodes(schema: dict) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def walk(node, base: str) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, base)
            return
        if not isinstance(node, dict):
            return
        identifier = node.get("$id")
        next_base = base
        if isinstance(identifier, str) and identifier:
            absolute = urljoin(base, identifier)
            resource, fragment = urldefrag(absolute)
            if resource and not fragment:
                found[resource] = node
                next_base = resource
            elif resource:
                found.setdefault(resource, schema)
                next_base = resource
        for value in node.values():
            walk(value, next_base)

    walk(schema, "")
    return found


def _prepare_registry_schema(schema):
    """Rewrite absolute $id fragments to #anchors so draft-07 can crawl them."""
    prepared = copy.deepcopy(schema)

    def walk(node, base: str) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, base)
            return
        if not isinstance(node, dict):
            return
        identifier = node.get("$id")
        next_base = base
        if isinstance(identifier, str) and identifier:
            absolute = urljoin(base, identifier)
            resource, fragment = urldefrag(absolute)
            if fragment:
                node["$id"] = "#" + fragment
            if resource:
                next_base = resource
        for value in node.values():
            walk(value, next_base)

    walk(prepared, "")
    return prepared


def _format_string(rng: random.Random, fmt: str) -> str:
    if fmt not in SUPPORTED_FORMATS:
        raise SchemaContractError(f"unsupported format {fmt!r}")
    if fmt == "date-time":
        base = datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=rng.randrange(0, 10**8))
        return base.strftime("%Y-%m-%dT%H:%M:%SZ")
    if fmt == "date":
        return f"2020-01-{(rng.randrange(28) + 1):02d}"
    if fmt == "time":
        return f"{rng.randrange(24):02d}:{rng.randrange(60):02d}:00Z"
    if fmt == "email":
        return f"user{rng.randrange(10000)}@example.com"
    if fmt == "uuid":
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))
    if fmt == "uri":
        return f"https://example.com/{rng.randrange(10000)}"
    if fmt == "hostname":
        return f"host{rng.randrange(10000)}.example.com"
    raise SchemaContractError(f"unsupported format {fmt!r}")


def _bounds(schema: dict):
    """Honor inclusive and exclusive bounds together. The tighter side wins; a tie is exclusive."""
    lows = []
    highs = []
    if "minimum" in schema and not isinstance(schema["minimum"], bool):
        lows.append((schema["minimum"], False))
    if "exclusiveMinimum" in schema and not isinstance(schema["exclusiveMinimum"], bool):
        lows.append((schema["exclusiveMinimum"], True))
    if "maximum" in schema and not isinstance(schema["maximum"], bool):
        highs.append((schema["maximum"], False))
    if "exclusiveMaximum" in schema and not isinstance(schema["exclusiveMaximum"], bool):
        highs.append((schema["exclusiveMaximum"], True))
    low, low_exclusive = _tighter_low(lows)
    high, high_exclusive = _tighter_high(highs)
    return low, high, low_exclusive, high_exclusive


def _tighter_low(bounds: list) -> tuple:
    best = None
    for value, exclusive in bounds:
        if best is None or value > best[0] or (value == best[0] and exclusive and not best[1]):
            best = (value, exclusive)
    if best is None:
        return None, False
    return best


def _tighter_high(bounds: list) -> tuple:
    best = None
    for value, exclusive in bounds:
        if best is None or value < best[0] or (value == best[0] and exclusive and not best[1]):
            best = (value, exclusive)
    if best is None:
        return None, False
    return best


def _contains(value, low, high, low_exclusive, high_exclusive) -> bool:
    if low is not None and (value < low or (low_exclusive and value <= low)):
        return False
    if high is not None and (value > high or (high_exclusive and value >= high)):
        return False
    return True


def _weighted(rng: random.Random, pairs: list[tuple[Any, float]]):
    total = sum(weight for _, weight in pairs)
    draw = rng.random() * total
    cursor = 0.0
    for item, weight in pairs:
        cursor += weight
        if draw <= cursor:
            return item
    return pairs[-1][0]


def _compile_pattern(pattern: str) -> None:
    if re.search(r"\(\?(?!:)", pattern) or re.search(r"\\[1-9]", pattern):
        raise SchemaContractError(f"unsafe pattern {pattern!r}")
    if re.search(r"([+*]|\{\d*,?\d*\})\s*([+*?]|\{\d)", pattern):
        raise SchemaContractError(f"unsafe pattern {pattern!r}")
    if re.search(r"\([^()]*[+*][^()]*\)\s*[+*{]", pattern):
        raise SchemaContractError(f"unsafe pattern {pattern!r}")
    re.compile(pattern)


def _pattern_string(rng: random.Random, pattern: str, minimum: int, maximum: int) -> str:
    body = pattern[1:] if pattern.startswith("^") else pattern
    body, _anchored = _strip_end_anchor(body)
    for _ in range(100):
        value = _emit_regex(rng, body, maximum)
        if minimum <= len(value) <= maximum and re.fullmatch(pattern, value):
            return value
    raise GenerationError(f"could not satisfy pattern {pattern!r}")


def _strip_end_anchor(body: str) -> tuple[str, bool]:
    if not body.endswith("$"):
        return body, False
    slashes = 0
    index = len(body) - 2
    while index >= 0 and body[index] == "\\":
        slashes += 1
        index -= 1
    if slashes % 2 == 0:
        return body[:-1], True
    return body, False


def _emit_regex(rng: random.Random, pattern: str, limit: int) -> str:
    parts = _split_alt(pattern)
    if len(parts) > 1:
        return _emit_regex(rng, rng.choice(parts), limit)
    out = []
    index = 0
    while index < len(pattern) and len(out) < limit:
        char = pattern[index]
        if char == "\\":
            if index + 1 >= len(pattern):
                break
            alphabet = _escape_alphabet(pattern[index + 1])
            repeat, index = _quantifier(pattern, index + 2, rng)
            out.extend(rng.choice(alphabet) for _ in range(min(repeat, limit - len(out))))
            continue
        if char == "(":
            end = _matching_paren(pattern, index)
            inner = pattern[index + 1:end]
            if inner.startswith("?:"):
                inner = inner[2:]
            repeat, index = _quantifier(pattern, end + 1, rng)
            for _ in range(min(repeat, limit - len(out))):
                out.extend(_emit_regex(rng, inner, limit - len(out)))
            continue
        if char == "[":
            end = pattern.index("]", index)
            alphabet = _class(pattern[index + 1:end])
            repeat, index = _quantifier(pattern, end + 1, rng)
            out.extend(rng.choice(alphabet) for _ in range(min(repeat, limit - len(out))))
            continue
        if char == ".":
            repeat, index = _quantifier(pattern, index + 1, rng)
            out.extend(rng.choice("abcd") for _ in range(min(repeat, limit - len(out))))
            continue
        repeat, index = _quantifier(pattern, index + 1, rng)
        out.extend(char for _ in range(min(repeat, limit - len(out))))
    return "".join(out)


def _split_alt(pattern: str) -> list[str]:
    parts = []
    depth = 0
    start = 0
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            index = pattern.index("]", index) + 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append(pattern[start:index])
            start = index + 1
        index += 1
    parts.append(pattern[start:])
    return parts


def _matching_paren(pattern: str, start: int) -> int:
    depth = 0
    index = start
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            index = pattern.index("]", index) + 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise SchemaContractError(f"unbalanced pattern {pattern!r}")


def _class(body: str) -> str:
    negated = body.startswith("^")
    if negated:
        body = body[1:]
    chars = []
    index = 0
    while index < len(body):
        if body[index] == "\\" and index + 1 < len(body):
            chars.extend(_escape_alphabet(body[index + 1]))
            index += 2
            continue
        if index + 2 < len(body) and body[index + 1] == "-":
            chars.extend(chr(code) for code in range(ord(body[index]), ord(body[index + 2]) + 1))
            index += 3
        else:
            chars.append(body[index])
            index += 1
    chosen = "".join(chars) or "a"
    if not negated:
        return chosen
    excluded = set(chosen)
    alphabet = [chr(code) for code in range(32, 127) if chr(code) not in excluded]
    return "".join(alphabet) or "a"


_PRINTABLE = "".join(chr(code) for code in range(32, 127))
_DIGITS = "0123456789"
_WORD = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
_SPACE = " "


def _escape_alphabet(char: str) -> str:
    if char == "d":
        return _DIGITS
    if char == "D":
        return "".join(item for item in _PRINTABLE if item not in _DIGITS)
    if char == "w":
        return _WORD
    if char == "W":
        return "".join(item for item in _PRINTABLE if item not in _WORD)
    if char == "s":
        return _SPACE
    if char == "S":
        return "".join(item for item in _PRINTABLE if item not in " \t\n\r")
    if char == "n":
        return "\n"
    if char == "t":
        return "\t"
    if char == "r":
        return "\r"
    return char


def _quantifier(pattern: str, index: int, rng: random.Random):
    if index >= len(pattern):
        return 1, index
    char = pattern[index]
    if char == "?":
        return int(rng.random() < 0.5), index + 1
    if char == "+":
        return rng.randint(1, 3), index + 1
    if char == "*":
        return rng.randint(0, 3), index + 1
    if char == "{":
        end = pattern.index("}", index)
        piece = pattern[index + 1:end]
        if "," in piece:
            left, right = piece.split(",")
            low = int(left) if left else 0
            high = int(right) if right else low + 3
        else:
            low = high = int(piece)
        return rng.randint(low, min(high, low + 8)), end + 1
    return 1, index


def _jsonify(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise GenerationError("Faker returned a non-finite number")
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonify(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    raise SchemaContractError(f"Faker returned a non-JSON value of type {type(value).__name__}")


def _measure(value, limit: int) -> None:
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SchemaContractError("const, enum, or example is not finite JSON") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise SchemaContractError("const, enum, or example exceeds realistic_max_event_bytes")


def _pool_name(schema) -> Optional[str]:
    if not isinstance(schema, dict):
        return None
    pool = schema.get("x-pool")
    if isinstance(pool, dict):
        pool = pool.get("name")
    if isinstance(pool, str) and pool:
        return pool
    return None


def _check_sequence(schema: dict) -> None:
    spec = schema["x-sequence"]
    if not isinstance(spec, dict) or not isinstance(spec.get("name"), str) or not spec["name"]:
        raise SchemaContractError("x-sequence requires a name")
    start = spec.get("start", 0)
    step = spec.get("step", 1)
    if isinstance(start, bool) or not isinstance(start, int) or isinstance(step, bool) or not isinstance(step, int) or step == 0:
        raise SchemaContractError("x-sequence start and step must be integers and step must not be 0")
    if "const" in schema and schema["const"] != start:
        raise SchemaContractError("x-sequence conflicts with const")
    if "enum" in schema and start not in schema["enum"]:
        raise SchemaContractError("x-sequence start is outside enum")


def _check_faker(spec) -> None:
    provider = spec["provider"] if isinstance(spec, dict) else spec
    if not isinstance(provider, str) or not provider:
        raise SchemaContractError("x-faker requires a provider")
    lowered = provider.lower()
    if (
        lowered in _WALL_CLOCK_PROVIDERS
        or lowered.startswith(("future_", "past_", "date_time", "date_this"))
    ):
        raise SchemaContractError(f"wall-clock Faker provider {provider!r} is rejected")
    probe = getattr(_check_faker, "_probe", None)
    if probe is None:
        from faker import Faker
        probe = Faker("en_US")
        setattr(_check_faker, "_probe", probe)
    if not hasattr(probe, provider):
        raise SchemaContractError(f"unknown Faker provider {provider!r}")


def _json_key(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _json_contains(values: list, item) -> bool:
    key = _json_key(item)
    return any(_json_key(existing) == key for existing in values)


def _dedupe_json(values: list) -> list:
    seen: set[str] = set()
    unique = []
    for item in values:
        key = _json_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _schema_patterns(schema: dict) -> list:
    patterns = []
    if "pattern" in schema:
        patterns.append(schema["pattern"])
    for item in schema.get("_patterns") or []:
        if item not in patterns:
            patterns.append(item)
    return patterns


def _matches_patterns(value: str, patterns: list) -> bool:
    return all(re.fullmatch(pattern, value) for pattern in patterns)


def _contains_schemas(schema: dict) -> list:
    extras = schema.get("_contains")
    if extras:
        return list(extras)
    contains = schema.get("contains")
    return [] if contains is None else [contains]


def _finite_value_set(schema) -> Optional[set]:
    if schema is False:
        return set()
    if schema is True or schema == {} or not isinstance(schema, dict):
        return None
    if "const" in schema:
        return {_json_key(schema["const"])}
    if "enum" in schema:
        return {_json_key(item) for item in schema["enum"]}
    types = schema.get("type")
    if types == "null" or types == ["null"]:
        return {"null"}
    if types == "boolean" or types == ["boolean"]:
        return {"true", "false"}
    return None


def _contains_candidates(schema: dict) -> list:
    items = schema.get("items", {})
    additional = schema.get("additionalItems", True)
    maximum = schema.get("maxItems")
    if isinstance(items, list):
        usable = items if maximum is None else items[:maximum]
        candidates = list(usable)
        if additional is not False and (maximum is None or maximum > len(items)):
            candidates.append(additional if isinstance(additional, dict) else True)
        return candidates
    return [items]


def _unique_value_union(schema: dict) -> Optional[set]:
    sets = [_finite_value_set(candidate) for candidate in _contains_candidates(schema)]
    if any(item is None for item in sets):
        return None
    union: set = set()
    for item in sets:
        union |= item
    return union


def _declared_types(schema) -> Optional[set]:
    if schema is False:
        return set()
    if schema is True or schema == {} or not isinstance(schema, dict):
        return None
    if "const" in schema:
        return {_json_type(schema["const"])}
    if "enum" in schema:
        return {_json_type(item) for item in schema["enum"]}
    types = schema.get("type")
    if types is None:
        return None
    return set(types) if isinstance(types, list) else {types}


def _json_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _types_overlap(contains, candidate) -> bool:
    left = _declared_types(contains)
    right = _declared_types(candidate)
    if left is None or right is None:
        return True
    if "number" in left and "integer" in right:
        left = left | {"integer"}
    if "number" in right and "integer" in left:
        right = right | {"integer"}
    return bool(left & right)


def _lcm_multiples(values: list):
    result = Decimal(str(values[0]))
    for item in values[1:]:
        result = _lcm_decimal(result, Decimal(str(item)))
    if result == result.to_integral_value():
        return int(result)
    return float(format(result.normalize(), "f"))


def _lcm_decimal(left: Decimal, right: Decimal) -> Decimal:
    def parts(value: Decimal):
        sign, digits, exp = value.normalize().as_tuple()
        numerator = int("".join(str(digit) for digit in digits))
        if sign:
            numerator = -numerator
        if exp >= 0:
            return numerator * (10 ** exp), 1
        return numerator, 10 ** (-exp)

    num_a, den_a = parts(left)
    num_b, den_b = parts(right)
    gcd_a = math.gcd(num_a, den_a)
    num_a //= gcd_a
    den_a //= gcd_a
    gcd_b = math.gcd(num_b, den_b)
    num_b //= gcd_b
    den_b //= gcd_b
    lcm_num = abs(num_a // math.gcd(num_a, num_b) * num_b)
    return (Decimal(lcm_num) / Decimal(math.gcd(den_a, den_b))).normalize()


def _slot_schema(schema: dict, index: int):
    items = schema.get("items", {})
    if isinstance(items, list):
        if index < len(items):
            return items[index]
        additional = schema.get("additionalItems", True)
        return {} if additional is True else additional
    return items


def _merge_schemas(parts: list) -> dict:
    flat = []
    for part in parts:
        if part is False:
            raise GenerationError("allOf includes false")
        if part is True or part == {} or part is None:
            continue
        if not isinstance(part, dict):
            continue
        if "allOf" in part:
            rest = {key: value for key, value in part.items() if key != "allOf"}
            flat.extend(part["allOf"])
            if rest:
                flat.append(rest)
            continue
        flat.append(part)
    merged: dict[str, Any] = {}
    type_sets = []
    properties: dict[str, list] = {}
    required: list[str] = []
    consts = []
    enums = []
    for part in flat:
        if "$ref" in part:
            raise GenerationError("allOf still contains an unresolved $ref")
        if "const" in part:
            consts.append(part["const"])
        if "enum" in part:
            enums.append(list(part["enum"]))
        if "type" in part:
            declared = part["type"]
            type_sets.append(set(declared) if isinstance(declared, list) else {declared})
        for key, value in (part.get("properties") or {}).items():
            properties.setdefault(key, []).append(value)
        required.extend(part.get("required") or [])
        for key in ("minLength", "minItems", "minProperties"):
            if key in part:
                merged[key] = part[key] if key not in merged else max(merged[key], part[key])
        for key in ("maxLength", "maxItems", "maxProperties"):
            if key in part:
                merged[key] = part[key] if key not in merged else min(merged[key], part[key])
        if "minimum" in part and not isinstance(part["minimum"], bool):
            merged.setdefault("_lows", []).append((part["minimum"], False))
        if "exclusiveMinimum" in part and not isinstance(part["exclusiveMinimum"], bool):
            merged.setdefault("_lows", []).append((part["exclusiveMinimum"], True))
        if "maximum" in part and not isinstance(part["maximum"], bool):
            merged.setdefault("_highs", []).append((part["maximum"], False))
        if "exclusiveMaximum" in part and not isinstance(part["exclusiveMaximum"], bool):
            merged.setdefault("_highs", []).append((part["exclusiveMaximum"], True))
        if "multipleOf" in part:
            merged.setdefault("_multiples", []).append(part["multipleOf"])
        if "pattern" in part:
            merged.setdefault("_patterns", []).append(part["pattern"])
        if "format" in part:
            merged.setdefault("_formats", []).append(part["format"])
        if "contains" in part:
            merged.setdefault("_contains", []).append(copy.deepcopy(part["contains"]))
        if "uniqueItems" in part:
            merged["uniqueItems"] = bool(merged.get("uniqueItems") or part["uniqueItems"])
        if "items" in part or "additionalItems" in part:
            merged.setdefault("_array_specs", []).append(
                (part.get("items", _MISSING), part.get("additionalItems", _MISSING))
            )
        if "additionalProperties" in part:
            merged.setdefault("_add_props", []).append(copy.deepcopy(part["additionalProperties"]))
        for key, value in part.items():
            if key.startswith("x-") and key not in merged:
                merged[key] = copy.deepcopy(value)
    if consts:
        if any(_json_key(item) != _json_key(consts[0]) for item in consts[1:]):
            raise GenerationError("allOf const values conflict")
        merged["const"] = copy.deepcopy(consts[0])
    if enums:
        originals: dict[str, Any] = {}
        inter = None
        for group in enums:
            keys = set()
            for item in group:
                key = _json_key(item)
                originals.setdefault(key, item)
                keys.add(key)
            inter = keys if inter is None else inter & keys
        if not inter:
            raise GenerationError("allOf enum intersection is empty")
        merged["enum"] = [originals[key] for key in sorted(inter)]
    if type_sets:
        common = set.intersection(*type_sets)
        if not common:
            raise GenerationError("allOf types do not intersect")
        merged["type"] = next(iter(sorted(common))) if len(common) == 1 else sorted(common)
    if properties:
        merged["properties"] = {
            key: ({"allOf": values} if len(values) > 1 else values[0]) for key, values in properties.items()
        }
    if required:
        merged["required"] = list(dict.fromkeys(required))
    lows = merged.pop("_lows", [])
    highs = merged.pop("_highs", [])
    multiples = merged.pop("_multiples", [])
    if lows:
        low, exclusive = _tighter_low(lows)
        if low is not None:
            merged["exclusiveMinimum" if exclusive else "minimum"] = low
    if highs:
        high, exclusive = _tighter_high(highs)
        if high is not None:
            merged["exclusiveMaximum" if exclusive else "maximum"] = high
    if multiples:
        merged["multipleOf"] = _lcm_multiples(multiples)
    patterns = merged.pop("_patterns", [])
    if patterns:
        merged["pattern"] = patterns[0]
        if len(set(patterns)) > 1:
            merged["_patterns"] = patterns
    formats = merged.pop("_formats", [])
    if formats:
        if any(item != formats[0] for item in formats[1:]):
            raise GenerationError("allOf format values conflict")
        merged["format"] = formats[0]
    contains = merged.pop("_contains", [])
    if contains:
        merged["contains"] = contains[0]
        if len(contains) > 1:
            merged["_contains"] = contains
    specs = merged.pop("_array_specs", [])
    if specs:
        items, additional = _fold_array_constraints(specs)
        merged["items"] = items
        if additional is not True:
            merged["additionalItems"] = additional
    add_props = merged.pop("_add_props", [])
    if add_props:
        extra: Any = True
        for item in add_props:
            extra = _merge_additional_schema(extra, item)
        merged["additionalProperties"] = extra
    return merged or True


def _schema_all_of(left, right):
    if left is False or right is False:
        return False
    if left is True or left == {} or left is None:
        return copy.deepcopy(right)
    if right is True or right == {} or right is None:
        return copy.deepcopy(left)
    if _json_key(left) == _json_key(right):
        return copy.deepcopy(left)
    return {"allOf": [copy.deepcopy(left), copy.deepcopy(right)]}


def _merge_additional_schema(left, right):
    if left is False or right is False:
        return False
    if left is True:
        return True if right is True else copy.deepcopy(right)
    if right is True:
        return copy.deepcopy(left)
    if left == {}:
        return copy.deepcopy(right)
    if right == {}:
        return copy.deepcopy(left)
    return _schema_all_of(left, right)


def _fold_array_constraints(specs: list) -> tuple:
    items, additional = _normalize_array_spec(specs[0])
    for spec in specs[1:]:
        right_items, right_add = _normalize_array_spec(spec)
        items, additional = _merge_array_keywords(items, additional, right_items, right_add)
    return items, additional


def _normalize_array_spec(spec) -> tuple:
    items, additional = spec
    if items is _MISSING:
        items = {}
    if additional is _MISSING:
        additional = True
    return copy.deepcopy(items), copy.deepcopy(additional) if isinstance(additional, dict) else additional


def _merge_array_keywords(left_items, left_add, right_items, right_add) -> tuple:
    left_tuple = isinstance(left_items, list)
    right_tuple = isinstance(right_items, list)
    if left_tuple and right_tuple:
        length = max(len(left_items), len(right_items))
        items = []
        for index in range(length):
            left = left_items[index] if index < len(left_items) else left_add
            right = right_items[index] if index < len(right_items) else right_add
            items.append(_schema_all_of(left, right))
        return items, _merge_additional_schema(left_add, right_add)
    if left_tuple:
        items = [_schema_all_of(slot, right_items) for slot in left_items]
        extra = _merge_additional_schema(left_add, right_items)
        return items, _merge_additional_schema(extra, right_add)
    if right_tuple:
        items = [_schema_all_of(left_items, slot) for slot in right_items]
        extra = _merge_additional_schema(right_add, left_items)
        return items, _merge_additional_schema(extra, left_add)
    return _schema_all_of(left_items, right_items), _merge_additional_schema(left_add, right_add)


def _overlay(base, fixed):
    """Profile values replace generated ones. Nested objects merge; lists are replaced."""
    if isinstance(fixed, dict) and isinstance(base, dict):
        merged = dict(base)
        for key, value in fixed.items():
            merged[key] = _overlay(base.get(key), value) if isinstance(value, dict) else copy.deepcopy(value)
        return merged
    return copy.deepcopy(fixed)
