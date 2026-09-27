from __future__ import annotations

from dataclasses import dataclass
from statistics import quantiles


@dataclass(slots=True)
class LatencySample:
    latency_ms: int
    cached: bool
    provider: str = ""
    model: str = ""


class LatencyTracker:
    """Rolling window over the recent translations (P50 / P95 / cache hit rate)."""

    def __init__(self, window: int = 50) -> None:
        self.window = max(2, int(window))
        self._samples: list[LatencySample] = []

    def record(self, latency_ms: int, cached: bool, provider: str = "", model: str = "") -> None:
        self._samples.append(LatencySample(int(latency_ms), bool(cached), provider, model))
        if len(self._samples) > self.window:
            del self._samples[0]

    def clear(self) -> None:
        self._samples.clear()

    @property
    def samples(self) -> list[LatencySample]:
        return list(self._samples)

    def latencies(self) -> list[int]:
        return [sample.latency_ms for sample in self._samples]

    def percentile(self, fraction: float) -> int:
        values = sorted(self.latencies())
        if not values:
            return 0
        if len(values) == 1:
            return values[0]
        # statistics.quantiles needs at least two data points.
        buckets = quantiles(values, n=100, method="inclusive")
        index = min(99, max(0, int(round(fraction * 100)) - 1))
        return int(round(buckets[index]))

    @property
    def p50(self) -> int:
        return self.percentile(0.50)

    @property
    def p95(self) -> int:
        return self.percentile(0.95)

    @property
    def cache_hit_rate(self) -> float:
        if not self._samples:
            return 0.0
        hits = sum(1 for sample in self._samples if sample.cached)
        return hits / len(self._samples)

    def latest(self) -> LatencySample | None:
        return self._samples[-1] if self._samples else None

    def snapshot(self) -> dict:
        latest = self.latest()
        return {
            "count": len(self._samples),
            "p50_ms": self.p50,
            "p95_ms": self.p95,
            "cache_hit_rate": self.cache_hit_rate,
            "provider": latest.provider if latest else "--",
            "model": latest.model if latest else "--",
            "latest_ms": latest.latency_ms if latest else 0,
            "series": self.latencies(),
        }
