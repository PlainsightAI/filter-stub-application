"""Optional semi-Markov process.

Dwell is a minimum residence. Arrival times are absolute. With arrival enabled,
a global priority queue merges instances by time so their rates stay intact.
Payload retries never redraw a pending process event.
"""

from __future__ import annotations

import copy
import heapq
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from filter_stub_application.distributions import (
    DistributionError,
    _is_real,
    next_nhpp_time,
    validate_segments,
)
from filter_stub_application.rng import stream


class ProfileError(ValueError):
    """The process profile cannot be executed."""


@dataclass
class PendingEvent:
    instance_id: int
    time: float
    state: str
    entering: bool
    overlay: dict
    snapshot: dict
    event_id: str
    published_time: float
    observation: tuple[float, float]


@dataclass
class Instance:
    state: str
    entered_at: float
    dwell_until: float
    next_arrival: Optional[float]
    memory: dict = field(default_factory=dict)
    # Tick mode keeps a clock per instance so one instance's emissions do not age the others.
    clock: float = 0.0
    last_emitted: float = 0.0


class ProcessEngine:
    def __init__(self, profile: dict, config: dict):
        self.profile = _validate_profile(copy.deepcopy(profile))
        self.config = config
        self.clock = 0.0
        self.cursor = 0
        self.instances = []
        count = self.profile["instances"]
        self._arrival_rngs = [stream(config["realistic_seed"], i, "arrival") for i in range(count)]
        self._dwell_rngs = [stream(config["realistic_seed"], i, "dwell") for i in range(count)]
        self._transition_rngs = [stream(config["realistic_seed"], i, "transition") for i in range(count)]
        self._ops_rngs = [stream(config["realistic_seed"], i, "ops") for i in range(count)]
        initial = self.profile["initial"]
        for index in range(count):
            dwell = _draw_dwell(self._dwell_rngs[index], self.profile["states"][initial].get("dwell"))
            nxt = None
            if self._uses_arrival():
                nxt = self._schedule(self._arrival_rngs[index], self._arrival_for(initial), 0.0)
            self.instances.append(Instance(initial, 0.0, dwell, nxt))
        self.pending: Optional[PendingEvent] = None
        self._seq = 0
        self._heap: list[tuple[float, int]] = []
        if self._uses_arrival():
            for index, instance in enumerate(self.instances):
                if instance.next_arrival is not None:
                    heapq.heappush(self._heap, (instance.next_arrival, index))

    def next_pending(self) -> Optional[PendingEvent]:
        if self.pending is not None:
            return self.pending
        if self._uses_arrival():
            pending = self._from_arrival()
        else:
            pending = self._from_tick()
        self.pending = pending
        return pending

    def commit(self) -> None:
        if self.pending is None:
            return
        instance = Instance(**self.pending.snapshot)
        self.instances[self.pending.instance_id] = instance
        self.clock = self.pending.published_time
        if instance.next_arrival is not None:
            heapq.heappush(self._heap, (instance.next_arrival, self.pending.instance_id))
        self.pending = None

    def _uses_arrival(self) -> bool:
        if self.profile.get("arrival"):
            return True
        return any(state.get("arrival") for state in self.profile["states"].values())

    def _from_tick(self) -> PendingEvent:
        index = self.cursor
        instance = self.instances[index]
        tick = self.config["process_tick_seconds"]
        # Residence is measured on the instance clock. Publication time stays monotonic.
        time = instance.clock + tick
        published = self.clock + tick
        pending = self._build(index, instance, time, scheduled_arrival=None, published_time=published)
        self.cursor = (self.cursor + 1) % len(self.instances)
        return pending

    def _from_arrival(self) -> Optional[PendingEvent]:
        while self._heap:
            time, index = self._heap[0]
            instance = self.instances[index]
            if instance.next_arrival != time:
                heapq.heappop(self._heap)
                continue
            heapq.heappop(self._heap)
            try:
                return self._build(index, instance, time, scheduled_arrival=time)
            except Exception:
                heapq.heappush(self._heap, (time, index))
                raise
        return None

    def preview_overlay(self) -> dict:
        """Static preview of the initial state's ops. Does not consume process streams."""
        overlay = {}
        memory = {}
        state = self.profile["states"][self.profile["initial"]]
        ops = (state.get("on_enter") or []) + (state.get("on_event") or [])
        rng = stream(self.config["realistic_seed"], 0, "preview")
        _apply_ops(ops, overlay, memory, _iso(self.profile["initial_clock"], 0.0), rng)
        return overlay

    def _schedule(self, rng: random.Random, spec: Optional[dict], start: float) -> Optional[float]:
        if spec:
            return _next_arrival(rng, spec, start, self.profile)
        return start + float(self.config["process_tick_seconds"])

    def _build(
        self,
        index: int,
        instance: Instance,
        time: float,
        scheduled_arrival: Optional[float],
        published_time: Optional[float] = None,
    ) -> PendingEvent:
        streams = (
            self._transition_rngs[index],
            self._dwell_rngs[index],
            self._arrival_rngs[index],
            self._ops_rngs[index],
        )
        saved = [rng.getstate() for rng in streams]
        try:
            return self._build_inner(index, instance, time, published_time if published_time is not None else time)
        except Exception:
            for rng, state in zip(streams, saved):
                rng.setstate(state)
            raise

    def _build_inner(self, index: int, instance: Instance, time: float, published_time: float) -> PendingEvent:
        state = self.profile["states"][instance.state]
        transitions = state.get("transitions") or []
        can_leave = time + 1e-12 >= instance.dwell_until and bool(transitions)
        snapshot = {
            "state": instance.state,
            "entered_at": instance.entered_at,
            "dwell_until": instance.dwell_until,
            "next_arrival": instance.next_arrival,
            "memory": copy.deepcopy(instance.memory),
            "clock": time,
            "last_emitted": time,
        }
        entering = False
        if can_leave:
            entering = True
            target = _weighted(self._transition_rngs[index], [(item["to"], item["p"]) for item in transitions])
            snapshot["state"] = target
            snapshot["entered_at"] = time
            snapshot["dwell_until"] = time + _draw_dwell(self._dwell_rngs[index], self.profile["states"][target].get("dwell"))
            ops = self.profile["states"][target].get("on_enter") or []
        else:
            ops = state.get("on_event") or []
        if self._uses_arrival():
            snapshot["next_arrival"] = self._schedule(
                self._arrival_rngs[index], self._arrival_for(snapshot["state"]), time,
            )
        overlay = {}
        memory = snapshot["memory"]
        _apply_ops(ops, overlay, memory, _iso(self.profile["initial_clock"], published_time), self._ops_rngs[index])
        self._seq += 1
        event_id = f"{index}-{self._seq}"
        previous = instance.last_emitted
        return PendingEvent(
            index, time, snapshot["state"], entering, overlay, snapshot, event_id,
            published_time, (previous, time),
        )

    def _arrival_for(self, state: str) -> Optional[dict]:
        return self.profile["states"][state].get("arrival") or self.profile.get("arrival")


def _validate_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ProfileError("profile must be an object")
    profile.setdefault("instances", 1)
    profile.setdefault("initial_clock", "2026-01-01T00:00:00Z")
    if not isinstance(profile["instances"], int) or profile["instances"] < 1:
        raise ProfileError("instances must be an integer >= 1")
    states = profile.get("states")
    initial = profile.get("initial")
    if not isinstance(states, dict) or not states or initial not in states:
        raise ProfileError("profile needs initial and states")
    for name, state in states.items():
        transitions = state.get("transitions") or []
        if not isinstance(transitions, list):
            raise ProfileError(f"state {name} transitions must be a list")
        weights = []
        for item in transitions:
            if item.get("to") not in states:
                raise ProfileError(f"state {name} transitions to an unknown state")
            weight = item.get("p", 1)
            if not _finite_weight(weight):
                raise ProfileError("transition weights must be finite and >= 0")
            weights.append(weight)
        if transitions and sum(weights) <= 0:
            raise ProfileError(f"state {name} has no positive transition weight")
        if "dwell" in state:
            _check_dwell(state["dwell"])
        if state.get("arrival"):
            _check_arrival(state["arrival"])
        for op in (state.get("on_enter") or []) + (state.get("on_event") or []):
            _check_op(op)
    arrival = profile.get("arrival")
    if arrival:
        _check_arrival(arrival)
    try:
        datetime.fromisoformat(profile["initial_clock"].replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ProfileError("initial_clock must be ISO-8601") from exc
    return profile


def _finite_weight(weight) -> bool:
    return isinstance(weight, (int, float)) and not isinstance(weight, bool) and math.isfinite(weight) and weight >= 0


def _check_dwell(spec: dict) -> None:
    if not isinstance(spec, dict):
        raise ProfileError("dwell must be an object")
    kind = spec.get("dist")
    if kind == "constant":
        seconds = spec.get("seconds", -1)
        if not _is_real(seconds) or seconds < 0:
            raise ProfileError("constant dwell seconds must be finite and >= 0")
    elif kind == "exponential":
        mean = spec.get("mean", 0)
        if not _is_real(mean) or mean <= 0:
            raise ProfileError("exponential dwell mean must be finite and > 0")
    elif kind == "lognormal":
        mu = spec.get("mu")
        sigma = spec.get("sigma", 0)
        if not _is_real(mu):
            raise ProfileError("lognormal dwell mu must be a finite number")
        if not _is_real(sigma) or sigma <= 0:
            raise ProfileError("lognormal dwell sigma must be finite and > 0")
    else:
        raise ProfileError(f"unknown dwell distribution {kind!r}")


def _check_op(op: dict) -> None:
    if not isinstance(op, dict) or "op" not in op:
        raise ProfileError("operation must be an object with op")
    kind = op["op"]
    if kind in {"set", "set_clock", "append_sample", "difference"} and "pointer" not in op:
        raise ProfileError(f"{kind} requires pointer")
    if kind == "set" and "value" not in op:
        raise ProfileError("set requires value")
    if kind == "copy" and ("from" not in op or "to" not in op):
        raise ProfileError("copy requires from and to")
    if kind == "difference" and ("a" not in op or "b" not in op):
        raise ProfileError("difference requires a and b")
    if kind in {"sample", "append_sample"}:
        choices = op.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProfileError(f"{kind} requires choices")
        weights = []
        for item in choices:
            weight = item.get("p", 1) if isinstance(item, dict) else None
            if not _finite_weight(weight):
                raise ProfileError("choice weights must be finite and >= 0")
            weights.append(weight)
        if sum(weights) <= 0:
            raise ProfileError(f"{kind} has no positive weight")
    elif kind not in {"set", "set_clock", "copy", "difference"}:
        raise ProfileError(f"unknown op {kind!r}")


def _check_arrival(spec: dict) -> None:
    if not isinstance(spec, dict):
        raise ProfileError("arrival must be an object")
    kind = spec.get("dist")
    if kind == "poisson":
        rate = spec.get("rate")
        if not _is_real(rate) or rate <= 0:
            raise ProfileError("poisson arrival rate must be finite and > 0")
    elif kind == "nonhomogeneous_poisson":
        try:
            validate_segments(
                spec.get("segments"), spec.get("period"), spec.get("allow_gaps", False),
                spec.get("on_exhaustion"),
            )
        except DistributionError as exc:
            raise ProfileError(str(exc)) from exc
    else:
        raise ProfileError(f"unknown arrival distribution {kind!r}")


def _draw_dwell(rng: random.Random, spec: Optional[dict]) -> float:
    if not spec:
        return 0.0
    kind = spec["dist"]
    if kind == "constant":
        return float(spec["seconds"])
    if kind == "exponential":
        return rng.expovariate(1.0 / float(spec["mean"]))
    # lognormal parameters are mu and sigma of the underlying normal.
    return rng.lognormvariate(float(spec["mu"]), float(spec["sigma"]))


def _next_arrival(rng: random.Random, spec: Optional[dict], start: float, profile: dict) -> Optional[float]:
    if not spec:
        return None
    if spec["dist"] == "poisson":
        return start + rng.expovariate(float(spec["rate"]))
    return next_nhpp_time(
        rng, spec["segments"], start, spec.get("period"), spec.get("on_exhaustion", profile.get("on_exhaustion", "stop")),
    )


def _weighted(rng: random.Random, pairs: list[tuple[Any, float]]):
    total = sum(weight for _, weight in pairs)
    draw = rng.random() * total
    cursor = 0.0
    for item, weight in pairs:
        cursor += weight
        if draw <= cursor:
            return item
    return pairs[-1][0]


def _apply_ops(ops: list, document: dict, memory: dict, clock: str, rng: random.Random) -> None:
    for op in ops or []:
        kind = op["op"]
        if kind == "set":
            _put(document, memory, op["pointer"], copy.deepcopy(op["value"]))
        elif kind == "set_clock":
            _put(document, memory, op["pointer"], clock)
        elif kind == "sample":
            chosen = _weighted(rng, [(item["value"], item.get("p", 1)) for item in op["choices"]])
            _put(document, memory, op["pointer"], copy.deepcopy(chosen))
        elif kind == "copy":
            _put(document, memory, op["to"], copy.deepcopy(_get(document, memory, op["from"])))
        elif kind == "append_sample":
            target = _get(document, memory, op["pointer"])
            if not isinstance(target, list):
                raise ProfileError("append_sample target must be a list")
            chosen = _weighted(rng, [(item["value"], item.get("p", 1)) for item in op["choices"]])
            target.append(copy.deepcopy(chosen))
        elif kind == "difference":
            left = _get(document, memory, op["a"])
            right = _get(document, memory, op["b"])
            if not isinstance(left, list) or not isinstance(right, list):
                raise ProfileError("difference requires lists")
            _put(document, memory, op["pointer"], [item for item in left if item not in right])
        else:
            raise ProfileError(f"unknown op {kind!r}")


def _put(document: dict, memory: dict, pointer: str, value) -> None:
    store, path = _store(document, memory, pointer)
    _assign(store, path, value)


def _get(document: dict, memory: dict, pointer: str):
    store, path = _store(document, memory, pointer)
    node = store
    for part in path:
        try:
            if isinstance(node, list):
                node = node[_index(part)]
            elif isinstance(node, dict):
                node = node[part]
            else:
                raise ProfileError(f"pointer {pointer!r} walks through a scalar")
        except (KeyError, IndexError, TypeError) as exc:
            raise ProfileError(f"pointer {pointer!r} does not exist") from exc
    return node


def _store(document: dict, memory: dict, pointer: str):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ProfileError(f"pointer must be an RFC 6901 pointer, got {pointer!r}")
    parts = [_unescape(part) for part in pointer[1:].split("/") if part != ""]
    if parts and parts[0] == "memory":
        return memory, parts[1:]
    return document, parts


def _assign(store, path: list[str], value) -> None:
    if not path:
        raise ProfileError("cannot replace the document root")
    node = store
    for offset, part in enumerate(path[:-1]):
        upcoming = path[offset + 1]
        if isinstance(node, list):
            idx = _index(part)
            while len(node) <= idx:
                node.append(None)
            if not isinstance(node[idx], (dict, list)):
                node[idx] = [] if _is_index(upcoming) else {}
            node = node[idx]
            continue
        if not isinstance(node, dict):
            raise ProfileError("pointer collides with an existing scalar")
        nxt = node.get(part)
        if isinstance(nxt, (dict, list)):
            node = nxt
            continue
        if nxt is not None:
            raise ProfileError(f"pointer collides with an existing value at {part!r}")
        node[part] = [] if _is_index(upcoming) else {}
        node = node[part]
    last = path[-1]
    if isinstance(node, list):
        idx = _index(last)
        while len(node) <= idx:
            node.append(None)
        node[idx] = value
        return
    if not isinstance(node, dict):
        raise ProfileError("pointer collides with an existing scalar")
    node[last] = value


def _is_index(part: str) -> bool:
    return part == "0" or (part.isdigit() and not part.startswith("0"))


def _index(part: str) -> int:
    if not _is_index(part):
        raise ProfileError(f"list pointer segment must be an index, got {part!r}")
    return int(part)


def _unescape(part: str) -> str:
    return part.replace("~1", "/").replace("~0", "~")


def _iso(initial: str, seconds: float) -> str:
    base = datetime.fromisoformat(initial.replace("Z", "+00:00"))
    stamp = base.astimezone(timezone.utc) + timedelta(seconds=float(seconds))
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
