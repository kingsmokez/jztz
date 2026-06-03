"""Prometheus metrics — minimal in-process implementation.

We avoid the ``prometheus_client`` third-party dependency because the
dev environment does not have outbound network access.  This module
implements the subset of the Prometheus text exposition format that
jztz_v17 actually uses: Counter, Gauge, Histogram.

Output format (per the Prometheus exposition spec, v0.0.4)::

    # HELP <name> <help text>
    # TYPE <name> counter|gauge|histogram
    <name>{<label>=<value>,...} <number>
    <name>_bucket{le="<upper>"} <cumulative count>
    <name>_sum <sum of observations>
    <name>_count <number of observations>

Test isolation
--------------
All metrics share a single ``CollectorRegistry`` so ``reset()`` can
wipe state between pytest runs.
"""
from __future__ import annotations

import threading
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class CollectorRegistry:
    """Holds the set of registered metrics; renders text format on demand."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._collectors: Dict[str, "_Collector"] = {}

    def register(self, collector: "_Collector") -> None:
        with self._lock:
            if collector.name in self._collectors:
                return
            self._collectors[collector.name] = collector

    def unregister(self, name: str) -> None:
        with self._lock:
            self._collectors.pop(name, None)

    def reset(self) -> None:
        """Wipe all metric values. Test-only."""
        with self._lock:
            for c in self._collectors.values():
                c._reset_state()

    def collect(self) -> List["_Sample"]:
        with self._lock:
            samples: List[_Sample] = []
            for c in self._collectors.values():
                samples.extend(c.collect())
        return samples

    def render(self) -> str:
        lines: List[str] = []
        for collector in sorted(self.collectors_view(), key=lambda c: c.name):
            lines.append(f"# HELP {collector.name} {collector._help_text}")
            lines.append(f"# TYPE {collector.name} {collector._type_name}")
            for sample in collector.collect():
                lines.append(_format_sample(sample))
        return "\n".join(lines) + "\n"

    def collectors_view(self) -> List["_Collector"]:
        with self._lock:
            return list(self._collectors.values())


REGISTRY = CollectorRegistry()


# ---------------------------------------------------------------------------
# Sample + formatting
# ---------------------------------------------------------------------------
class _Sample:
    __slots__ = ("name", "labels", "value")

    def __init__(self, name: str, labels: Dict[str, str], value: float) -> None:
        self.name = name
        self.labels = labels
        self.value = value


def _escape_label_value(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(labels: Dict[str, str]) -> str:
    if not labels:
        return ""
    pairs = ",".join(
        f'{k}="{_escape_label_value(v)}"' for k, v in sorted(labels.items())
    )
    return "{" + pairs + "}"


def _format_value(v: float) -> str:
    if isinstance(v, int) or v.is_integer():
        return str(int(v))
    return repr(float(v))


def _format_sample(sample: _Sample) -> str:
    return f"{sample.name}{_format_labels(sample.labels)} {_format_value(sample.value)}"


# ---------------------------------------------------------------------------
# Base collector
# ---------------------------------------------------------------------------
class _Collector:
    _type_name: str = ""

    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: Sequence[str] = (),
        registry: CollectorRegistry = REGISTRY,
        buckets: Optional[Sequence[float]] = None,
    ) -> None:
        if labelnames and not all(isinstance(n, str) for n in labelnames):
            raise TypeError("labelnames must be a sequence of str")
        self.name = name
        self._help_text = help_text
        self._labelnames: Tuple[str, ...] = tuple(labelnames)
        self._buckets: Optional[Tuple[float, ...]] = tuple(buckets) if buckets else None
        self._registry = registry
        self._children: Dict[Tuple[Tuple[str, str], ...], "_ChildSeries"] = {}
        self._lock = threading.RLock()
        self._registry.register(self)

    def _resolve_child(self, labels: Dict[str, str]) -> "_ChildSeries":
        if set(labels.keys()) != set(self._labelnames):
            raise ValueError(
                f"expected labels {self._labelnames}, got {list(labels.keys())}"
            )
        key = tuple(sorted(labels.items()))
        with self._lock:
            child = self._children.get(key)
            if child is None:
                child = self._make_child()
                self._children[key] = child
            return child

    def _make_child(self) -> "_ChildSeries":
        raise NotImplementedError

    def collect(self) -> List[_Sample]:
        with self._lock:
            return list(self._collect_samples())

    def _collect_samples(self) -> Iterable[_Sample]:
        raise NotImplementedError

    def _reset_state(self) -> None:
        with self._lock:
            self._children.clear()


class _ChildSeries:
    def inc(self, amount: float = 1.0) -> None: ...
    def dec(self, amount: float = 1.0) -> None: ...
    def set(self, value: float) -> None: ...
    def observe(self, value: float) -> None: ...


# ---------------------------------------------------------------------------
# Counter
# ---------------------------------------------------------------------------
class _CounterChild(_ChildSeries):
    def __init__(self) -> None:
        self._value = 0.0

    def inc(self, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("Counter cannot decrease")
        self._value += amount


class Counter(_Collector):
    _type_name = "counter"

    def _make_child(self) -> _CounterChild:
        return _CounterChild()

    def labels(self, **labels: str) -> _CounterChild:
        return self._resolve_child(labels)  # type: ignore[return-value]

    def inc(self, amount: float = 1.0) -> None:
        self._resolve_child({}).inc(amount)

    def _collect_samples(self) -> Iterable[_Sample]:
        for key, child in self._children.items():
            yield _Sample(self.name, dict(key), child._value)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Gauge
# ---------------------------------------------------------------------------
class _GaugeChild(_ChildSeries):
    def __init__(self) -> None:
        self._value = 0.0

    def inc(self, amount: float = 1.0) -> None:
        self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        self._value -= amount

    def set(self, value: float) -> None:
        self._value = float(value)


class Gauge(_Collector):
    _type_name = "gauge"

    def _make_child(self) -> _GaugeChild:
        return _GaugeChild()

    def labels(self, **labels: str) -> _GaugeChild:
        return self._resolve_child(labels)  # type: ignore[return-value]

    # Direct (unlabeled) shortcut.
    def set(self, value: float) -> None:  # type: ignore[override]
        self._resolve_child({}).set(value)

    def inc(self, amount: float = 1.0) -> None:
        self._resolve_child({}).inc(amount)

    def dec(self, amount: float = 1.0) -> None:
        self._resolve_child({}).dec(amount)

    def _collect_samples(self) -> Iterable[_Sample]:
        for key, child in self._children.items():
            yield _Sample(self.name, dict(key), child._value)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Histogram
# ---------------------------------------------------------------------------
class _HistogramChild(_ChildSeries):
    def __init__(self, buckets: Sequence[float]) -> None:
        self._buckets: Tuple[float, ...] = tuple(sorted(buckets)) + (float("inf"),)
        self._counts: List[int] = [0] * len(self._buckets)
        self._sum = 0.0
        self._count = 0

    def observe(self, value: float) -> None:
        self._sum += value
        self._count += 1
        for i, ub in enumerate(self._buckets):
            if value <= ub:
                self._counts[i] += 1


class Histogram(_Collector):
    _type_name = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] = (
            0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
        ),
        registry: CollectorRegistry = REGISTRY,
    ) -> None:
        super().__init__(name, help_text, labelnames, registry, buckets=buckets)

    def _make_child(self) -> _HistogramChild:
        assert self._buckets is not None
        return _HistogramChild(self._buckets[:-1])  # type: ignore[arg-type]

    def labels(self, **labels: str) -> _HistogramChild:
        return self._resolve_child(labels)  # type: ignore[return-value]

    def observe(self, value: float, **labels: str) -> None:
        if not self._labelnames:
            child = self._resolve_child({})
            child.observe(value)
            return
        child = self._resolve_child(labels)
        child.observe(value)

    def _collect_samples(self) -> Iterable[_Sample]:
        for key, child in self._children.items():
            label_dict = dict(key)
            for ub, cnt in zip(child._buckets, child._counts):  # type: ignore[attr-defined]
                bucket_labels = dict(label_dict)
                bucket_labels["le"] = "+Inf" if ub == float("inf") else str(ub)
                yield _Sample(f"{self.name}_bucket", bucket_labels, cnt)
            yield _Sample(f"{self.name}_sum", label_dict, child._sum)  # type: ignore[attr-defined]
            yield _Sample(f"{self.name}_count", label_dict, child._count)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Public metrics
# ---------------------------------------------------------------------------
CACHE_HITS = Counter(
    "jztz_cache_hits_total",
    "Number of cache lookups that returned a fresh value.",
    labelnames=("key_prefix",),
)
CACHE_MISSES = Counter(
    "jztz_cache_misses_total",
    "Number of cache lookups that did NOT find a fresh value.",
    labelnames=("key_prefix",),
)
CACHE_SIZE = Gauge(
    "jztz_cache_entries",
    "Current number of entries in the cache.",
)
CIRCUIT_STATE = Gauge(
    "jztz_circuit_breaker_state",
    "Circuit-breaker state per upstream (0=CLOSED, 1=HALF_OPEN, 2=OPEN).",
    labelnames=("api",),
)
CIRCUIT_TRIPS = Counter(
    "jztz_circuit_breaker_trips_total",
    "Number of times a circuit-breaker has transitioned to OPEN.",
    labelnames=("api",),
)
EXTERNAL_API_LATENCY = Histogram(
    "jztz_external_api_latency_seconds",
    "External API request latency in seconds.",
    labelnames=("api", "endpoint"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)
PICKER_RUNS = Counter(
    "jztz_picker_runs_total",
    "Number of picker invocations.",
    labelnames=("picker", "result"),
)
PICKER_DURATION = Histogram(
    "jztz_picker_duration_seconds",
    "Picker run duration in seconds.",
    labelnames=("picker",),
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)
SSE_CONNECTIONS = Gauge(
    "jztz_sse_connections",
    "Number of currently-open SSE client connections.",
)
HTTP_REQUESTS = Counter(
    "jztz_http_requests_total",
    "Total HTTP requests handled.",
    labelnames=("method", "endpoint", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "jztz_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    labelnames=("method", "endpoint"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
PROCESS_CPU_SECONDS = Gauge(
    "jztz_process_cpu_seconds_total",
    "Cumulative process CPU time in seconds.",
)
PROCESS_RESIDENT_MEMORY = Gauge(
    "jztz_process_resident_memory_bytes",
    "Resident memory size in bytes.",
)
PROCESS_UPTIME = Gauge(
    "jztz_process_uptime_seconds",
    "Seconds since the process started.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_STATE_TO_INT = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}


def record_cache(key_prefix: str, hit: bool) -> None:
    if hit:
        CACHE_HITS.labels(key_prefix=key_prefix).inc()
    else:
        CACHE_MISSES.labels(key_prefix=key_prefix).inc()


def record_circuit_state(api: str, state: str, just_tripped: bool = False) -> None:
    CIRCUIT_STATE.labels(api=api).set(_STATE_TO_INT.get(state, 0))
    if just_tripped:
        CIRCUIT_TRIPS.labels(api=api).inc()


def key_prefix(cache_key: str) -> str:
    if ":" in cache_key:
        return cache_key.split(":", 1)[0]
    return cache_key


def generate_latest(registry: CollectorRegistry = REGISTRY) -> bytes:
    return registry.render().encode("utf-8")


# ---------------------------------------------------------------------------
# Process collector
# ---------------------------------------------------------------------------
class _ProcessCollector:
    """Updates PROCESS_CPU_SECONDS / PROCESS_RESIDENT_MEMORY / PROCESS_UPTIME."""

    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        import os
        import time

        self._pid = os.getpid()
        self._start = time.time()
        self.update()

    def update(self) -> None:
        try:
            import time

            PROCESS_UPTIME.set(time.time() - self._start)
            rss = 0
            cpu = 0.0
            try:
                import psutil  # type: ignore
                proc = psutil.Process(self._pid)
                rss = proc.memory_info().rss
                cpu = proc.cpu_times().user + proc.cpu_times().system
            except Exception:
                try:
                    with open(f"/proc/{self._pid}/status") as f:
                        for line in f:
                            if line.startswith("VmRSS:"):
                                rss = int(line.split()[1]) * 1024
                                break
                except Exception:
                    pass
            PROCESS_RESIDENT_MEMORY.set(rss)
            PROCESS_CPU_SECONDS.set(cpu)
        except Exception:
            pass


__all__ = [
    "REGISTRY",
    "CollectorRegistry",
    "Counter",
    "Gauge",
    "Histogram",
    "generate_latest",
    "CONTENT_TYPE_LATEST",
    "_ProcessCollector",
    "record_cache",
    "record_circuit_state",
    "key_prefix",
]
