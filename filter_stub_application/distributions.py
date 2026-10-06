"""Seeded numeric distributions.

Sampling stays inside the requested support. Values are rejected, not clipped,
so probability mass is not piled on the bounds.
"""

from __future__ import annotations

import math
import random
from decimal import Decimal
from typing import Optional

DEFAULT_UNBOUNDED_NUMBER_BOUND = 1000


class DistributionError(ValueError):
    """A distribution specification or its support is invalid."""


def _is_real(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _require_number(name: str, value) -> float:
    if not _is_real(value):
        raise DistributionError(f"{name} must be a finite number")
    return float(value)


def _require_positive(name: str, value: float) -> float:
    number = _require_number(name, value)
    if number <= 0:
        raise DistributionError(f"{name} must be finite and > 0")
    return number


def poisson(rng: random.Random, rate: float) -> int:
    """Draw a Poisson count. Knuth for small rates, Atkinson rejection above 30."""
    if not _is_real(rate) or rate < 0:
        raise DistributionError("poisson rate must be finite and >= 0")
    if rate == 0:
        return 0
    if rate <= 30:
        # Knuth (1973). Mean of Exp is not used; this is the product-of-uniforms form.
        limit = math.exp(-rate)
        count = 0
        product = 1.0
        while product > limit:
            count += 1
            product *= rng.random()
        return count - 1
    return _poisson_atkinson(rng, float(rate))


def _poisson_atkinson(rng: random.Random, rate: float) -> int:
    """Atkinson (1979) rejection sampler. Exact for the Poisson pmf, not a normal approximation."""
    beta = math.pi / math.sqrt(3.0 * rate)
    alpha = beta * rate
    k = math.log(0.767 - 3.36 / rate) - rate - math.log(beta)
    while True:
        u = rng.random()
        if u <= 0.0 or u >= 1.0:
            continue
        x = (alpha - math.log((1.0 - u) / u)) / beta
        n = math.floor(x + 0.5)
        if n < 0:
            continue
        v = rng.random()
        y = alpha - beta * x
        lhs = y + math.log(v / (1.0 + math.exp(y)) ** 2)
        rhs = k + n * math.log(rate) - math.lgamma(n + 1)
        if lhs <= rhs:
            return int(n)


def truncated_normal(
    rng: random.Random,
    mean: float,
    std: float,
    low: Optional[float],
    high: Optional[float],
    *,
    low_exclusive: bool = False,
    high_exclusive: bool = False,
    attempts: int = 10000,
) -> float:
    std = _require_positive("std", std)
    for _ in range(attempts):
        value = rng.gauss(float(mean), std)
        if _in_bounds(value, low, high, low_exclusive, high_exclusive):
            return value
    raise DistributionError("truncated normal support was not hit within the attempt budget")


def truncated_exponential(
    rng: random.Random,
    mean: float,
    low: Optional[float],
    high: Optional[float],
    *,
    low_exclusive: bool = False,
    high_exclusive: bool = False,
    attempts: int = 10000,
) -> float:
    mean = _require_positive("mean", mean)
    for _ in range(attempts):
        value = rng.expovariate(1.0 / mean)
        if _in_bounds(value, low, high, low_exclusive, high_exclusive):
            return value
    raise DistributionError("truncated exponential support was not hit within the attempt budget")


def poisson_in_range(
    rng: random.Random,
    rate: float,
    low: Optional[int],
    high: Optional[int],
    *,
    attempts: int = 10000,
) -> int:
    """Rejection sample. Exhaustion fails instead of clamping onto a bound."""
    for _ in range(attempts):
        value = poisson(rng, rate)
        if (low is None or value >= low) and (high is None or value <= high):
            return value
    raise DistributionError("poisson support was not hit within the attempt budget")


def integer_window(
    low: Optional[float],
    high: Optional[float],
    low_exclusive: bool,
    high_exclusive: bool,
) -> tuple[Optional[int], Optional[int]]:
    """Smallest and largest integers inside the numeric bounds. Exclusive bounds stay outside."""
    start = None if low is None else (math.floor(low) + 1 if low_exclusive else math.ceil(low))
    stop = None if high is None else (math.ceil(high) - 1 if high_exclusive else math.floor(high))
    if start is not None and stop is not None and start > stop:
        raise DistributionError("integer bounds are empty")
    return None if start is None else int(start), None if stop is None else int(stop)


def sample_distribution(
    rng: random.Random,
    spec: dict,
    *,
    low: Optional[float] = None,
    high: Optional[float] = None,
    low_exclusive: bool = False,
    high_exclusive: bool = False,
    integer: bool = False,
    multiple: Optional[Decimal] = None,
    attempts: int = 10000,
    unbound: float = DEFAULT_UNBOUNDED_NUMBER_BOUND,
) -> int | float:
    if multiple is not None:
        return sample_lattice(
            rng, spec, low, high, low_exclusive, high_exclusive, multiple, integer=integer,
            attempts=attempts, unbound=unbound,
        )
    kind = spec.get("dist")
    if integer and kind in {"normal", "exponential"}:
        start, stop = integer_window(low, high, low_exclusive, high_exclusive)
        if kind == "normal":
            return _integer_normal(rng, float(spec["mean"]), _require_positive("std", spec["std"]), start, stop)
        return _integer_exponential(rng, _require_positive("mean", spec["mean"]), start, stop)
    if kind == "normal":
        return truncated_normal(
            rng, spec["mean"], spec["std"], low, high,
            low_exclusive=low_exclusive, high_exclusive=high_exclusive, attempts=attempts,
        )
    if kind == "exponential":
        return truncated_exponential(
            rng, spec["mean"], low, high,
            low_exclusive=low_exclusive, high_exclusive=high_exclusive, attempts=attempts,
        )
    if kind == "poisson":
        if not integer:
            raise DistributionError("poisson is only valid for integer nodes")
        start, stop = integer_window(low, high, low_exclusive, high_exclusive)
        return poisson_in_range(rng, spec["rate"], start, stop, attempts=attempts)
    raise DistributionError(f"unsupported distribution {kind!r}")


def sample_lattice(
    rng: random.Random,
    spec: Optional[dict],
    low: Optional[float],
    high: Optional[float],
    low_exclusive: bool,
    high_exclusive: bool,
    step: Decimal,
    *,
    integer: bool,
    attempts: int = 10000,
    unbound: float = DEFAULT_UNBOUNDED_NUMBER_BOUND,
) -> int | float:
    """Uniform over the lattice, or weighted by the continuous density of one cell per point."""
    start, count = lattice_span(low, high, step, not low_exclusive, not high_exclusive, unbound=unbound)
    kind = None if spec is None else spec.get("dist")
    if kind in {None, "uniform"}:
        index = rng.randrange(count)
    elif count > 20000:
        # Containing cell of a continuous draw. That is the integral of the
        # density over [x_i, x_i + step), not a nearest-neighbour snap.
        index = _reject_lattice_index(rng, spec, start, step, count, attempts)
    else:
        weights = [_lattice_weight(kind, spec, float(start + step * index)) for index in range(count)]
        if sum(weights) <= 0:
            raise DistributionError("distribution gives no mass to the multipleOf lattice")
        index = _weighted_index(rng, weights)
    chosen = start + step * index
    return _json_number(chosen, integer)


def validate_distribution_spec(spec: dict, *, integer: bool) -> None:
    if not isinstance(spec, dict) or "dist" not in spec:
        raise DistributionError("x-distribution must be an object with dist")
    kind = spec["dist"]
    if kind == "normal":
        _require_positive("std", spec.get("std"))
        _require_number("mean", spec.get("mean"))
    elif kind == "exponential":
        _require_positive("mean", spec.get("mean"))
    elif kind == "poisson":
        if not integer:
            raise DistributionError("poisson is only valid for integer nodes")
        rate = spec.get("rate")
        if not _is_real(rate) or rate < 0:
            raise DistributionError("poisson rate must be >= 0")
    elif kind == "nonhomogeneous_poisson":
        if not integer:
            raise DistributionError("nonhomogeneous_poisson is only valid for integer nodes")
        validate_segments(
            spec.get("segments"), spec.get("period"), spec.get("allow_gaps", False),
            spec.get("on_exhaustion"),
        )
    else:
        raise DistributionError(f"unknown distribution {kind!r}")


def validate_segments(segments, period, allow_gaps: bool, on_exhaustion=None) -> None:
    if not isinstance(segments, list) or not segments:
        raise DistributionError("segments must be a non-empty list")
    parsed = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise DistributionError("segment must be an object")
        start = segment.get("start")
        end = segment.get("end", None)
        rate = segment.get("rate")
        if not _is_real(start) or start < 0:
            raise DistributionError("segment start must be >= 0")
        if end is not None and (not _is_real(end) or end <= start):
            raise DistributionError("segment end must be greater than start")
        if not _is_real(rate) or rate < 0:
            raise DistributionError("segment rate must be >= 0")
        parsed.append((float(start), None if end is None else float(end), float(rate)))
    parsed.sort()
    segments.sort(key=lambda item: item["start"])
    if period is not None:
        if not _is_real(period) or period <= 0:
            raise DistributionError("period must be > 0")
        if any(end is None or end > period or start >= period for start, end, _ in parsed):
            raise DistributionError("periodic segments must lie inside [0, period)")
    else:
        last_end = parsed[-1][1]
        if last_end is not None and on_exhaustion != "stop":
            raise DistributionError("a non-periodic profile needs a positive unbounded tail or on_exhaustion=stop")
    if on_exhaustion not in (None, "stop"):
        raise DistributionError("on_exhaustion must be stop when set")
    for prev, nxt in zip(parsed, parsed[1:]):
        prev_end = prev[1]
        if prev_end is None:
            raise DistributionError("only the last segment may be unbounded")
        if nxt[0] < prev_end:
            raise DistributionError("segments overlap")
        if nxt[0] > prev_end and not allow_gaps:
            raise DistributionError("segment gap requires allow_gaps=true")
    if period is None and parsed[0][0] > 0 and not allow_gaps:
        raise DistributionError("segment gap requires allow_gaps=true")
    if not any(rate > 0 for _, _, rate in parsed):
        raise DistributionError("at least one segment rate must be positive")


def integrate_intensity(segments: list, start: float, end: float, period: Optional[float]) -> float:
    """Exact integral of a piecewise-constant rate over [start, end)."""
    if end < start:
        raise DistributionError("observation window is reversed")
    if end == start:
        return 0.0
    total = 0.0
    cursor = start
    guard = 0
    while cursor < end:
        guard += 1
        if guard > 100000:
            raise DistributionError("intensity integral did not terminate")
        _local, seg_end, rate = _segment_at(segments, cursor, period)
        step_end = end if seg_end is None else min(end, seg_end)
        total += rate * (step_end - cursor)
        if step_end <= cursor:
            raise DistributionError("intensity integral stalled")
        cursor = step_end
    return total


def next_nhpp_time(rng: random.Random, segments: list, start: float, period: Optional[float], on_exhaustion: str) -> Optional[float]:
    """Invert cumulative intensity. Returns the absolute time of the next event, or None if exhausted."""
    remaining = rng.expovariate(1.0)  # E ~ Exp(1), mean 1
    cursor = start
    guard = 0
    while remaining > 0:
        guard += 1
        if guard > 100000:
            raise DistributionError("NHPP inversion did not terminate")
        _local, seg_end, rate = _segment_at(segments, cursor, period)
        if seg_end is None and rate == 0:
            if on_exhaustion == "stop":
                return None
            raise DistributionError("NHPP has no future event")
        if rate == 0:
            if seg_end is None:
                if on_exhaustion == "stop":
                    return None
                raise DistributionError("NHPP has no future event")
            cursor = seg_end
            continue
        available = math.inf if seg_end is None else seg_end - cursor
        need = remaining / rate
        if need < available:
            return cursor + need
        remaining -= rate * available
        if seg_end is None:
            return None if on_exhaustion == "stop" else cursor + need
        cursor = seg_end
    return cursor


def _segment_at(segments: list, time: float, period: Optional[float]):
    local = time if period is None else math.fmod(time, period)
    if local < 0:
        local += period
    for segment in segments:
        end = segment.get("end")
        if segment["start"] <= local and (end is None or local < end):
            abs_end = None if end is None else time + (end - local)
            return local, abs_end, float(segment["rate"])
    if period is not None:
        # Gap inside the period: jump to the next segment start.
        starts = [segment["start"] for segment in segments if segment["start"] > local]
        next_local = starts[0] if starts else segments[0]["start"] + period
        return local, time + (next_local - local), 0.0
    starts = [segment["start"] for segment in segments if segment["start"] > local]
    if starts:
        return local, time + (starts[0] - local), 0.0
    last = segments[-1]
    if last.get("end") is None:
        return local, None, float(last["rate"])
    return local, None, 0.0


def lattice_span(
    low: Optional[float],
    high: Optional[float],
    step: Decimal,
    low_inclusive: bool,
    high_inclusive: bool,
    unbound: float = DEFAULT_UNBOUNDED_NUMBER_BOUND,
) -> tuple[Decimal, int]:
    """First lattice point and how many points lie in the bounds. The list itself is not built."""
    if step <= 0:
        raise DistributionError("multipleOf must be > 0")
    lo = Decimal(str(-unbound)) if low is None else Decimal(str(low))
    hi = Decimal(str(unbound)) if high is None else Decimal(str(high))
    start = (lo / step).to_integral_value(rounding="ROUND_CEILING") * step
    if not low_inclusive and start <= lo:
        start += step
    if start > hi or (not high_inclusive and start >= hi):
        raise DistributionError("multipleOf lattice is empty")
    span = (hi - start) / step
    steps = int(span.to_integral_value(rounding="ROUND_FLOOR"))
    last = start + step * steps
    if not high_inclusive and last >= hi:
        steps -= 1
    if steps < 0:
        raise DistributionError("multipleOf lattice is empty")
    return start, steps + 1


def _lattice_weight(kind: str, spec: dict, value: float) -> float:
    if kind == "normal":
        std = float(spec["std"])
        z = (value - float(spec["mean"])) / std
        return math.exp(-0.5 * z * z)
    if kind == "exponential":
        if value < 0:
            return 0.0
        return math.exp(-value / float(spec["mean"]))
    if kind == "poisson":
        rate = float(spec["rate"])
        if value < 0 or not float(value).is_integer():
            return 0.0
        # Unnormalized pmf. lgamma keeps large counts finite.
        k = int(value)
        if rate == 0:
            return 1.0 if k == 0 else 0.0
        return math.exp(k * math.log(rate) - rate - math.lgamma(k + 1))
    raise DistributionError(f"cannot weight lattice for {kind!r}")


def _weighted_index(rng: random.Random, weights: list[float]) -> int:
    total = sum(weights)
    draw = rng.random() * total
    cursor = 0.0
    for index, weight in enumerate(weights):
        cursor += weight
        if draw <= cursor:
            return index
    return len(weights) - 1


def _reject_lattice_index(
    rng: random.Random,
    spec: dict,
    start: Decimal,
    step: Decimal,
    count: int,
    attempts: int,
) -> int:
    """Map a continuous draw onto the containing lattice cell by flooring, then reject indexes outside the span."""
    kind = spec["dist"]
    origin = float(start)
    width = float(step)
    for _ in range(attempts):
        if kind == "normal":
            draw = rng.gauss(float(spec["mean"]), float(spec["std"]))
        elif kind == "exponential":
            draw = rng.expovariate(1.0 / float(spec["mean"]))
        elif kind == "poisson":
            draw = float(poisson(rng, float(spec["rate"])))
        else:
            raise DistributionError(f"cannot sample lattice for {kind!r}")
        index = math.floor((draw - origin) / width)
        if 0 <= index < count:
            return index
    raise DistributionError("multipleOf lattice was not hit within the attempt budget")


def _json_number(chosen: Decimal, integer: bool) -> int | float:
    """Keep the decimal spelling so 0.3 * 7 stays 2.1, not an IEEE neighbour."""
    if integer or chosen == chosen.to_integral_value():
        return int(chosen)
    return float(format(chosen.normalize(), "f"))


def _integer_normal(rng: random.Random, mean: float, std: float, start: Optional[int], stop: Optional[int]) -> int:
    lo = int(math.floor(mean - 8 * std)) if start is None else start
    hi = int(math.ceil(mean + 8 * std)) if stop is None else stop
    if start is not None:
        lo = max(lo, start)
    if stop is not None:
        hi = min(hi, stop)
    if lo > hi:
        raise DistributionError("integer bounds are empty")
    weights = [_normal_cell(mean, std, value) for value in range(lo, hi + 1)]
    if sum(weights) <= 0:
        raise DistributionError("normal gives no mass to the integer window")
    return lo + _weighted_index(rng, weights)


def _integer_exponential(rng: random.Random, mean: float, start: Optional[int], stop: Optional[int]) -> int:
    lo = 0 if start is None else start
    hi = int(math.ceil(mean * 20)) if stop is None else stop
    if start is not None:
        lo = max(lo, start)
    if stop is not None:
        hi = min(hi, stop)
    if lo > hi:
        raise DistributionError("integer bounds are empty")
    weights = [_exponential_cell(mean, value) for value in range(lo, hi + 1)]
    if sum(weights) <= 0:
        raise DistributionError("exponential gives no mass to the integer window")
    return lo + _weighted_index(rng, weights)


def _phi(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def _normal_cell(mean: float, std: float, value: int) -> float:
    return _phi((value + 0.5 - mean) / std) - _phi((value - 0.5 - mean) / std)


def _exponential_cell(mean: float, value: int) -> float:
    if value < 0:
        return 0.0
    upper = math.exp(-(value + 1) / mean)
    lower = math.exp(-value / mean)
    return lower - upper


def _in_bounds(value, low, high, low_exclusive, high_exclusive) -> bool:
    if math.isnan(value) or math.isinf(value):
        return False
    if low is not None and (value < low or (low_exclusive and value <= low)):
        return False
    if high is not None and (value > high or (high_exclusive and value >= high)):
        return False
    return True
