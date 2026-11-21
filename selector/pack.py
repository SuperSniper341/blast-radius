"""Split a set of tests across workers at the lowest simulated cost."""

from __future__ import annotations

WORKER_COST_MS = 2000
MAX_WORKERS = 8


def pack(durations_ms: dict[int, int]) -> list[list[int]]:
    """Cheapest split: min over k of (busiest worker + 2 s per worker)."""
    if not durations_ms:
        return []
    items = sorted(durations_ms.items(), key=lambda kv: (-kv[1], kv[0]))
    best_cost: int | None = None
    best: list[list[int]] = []
    for k in range(1, min(MAX_WORKERS, len(items)) + 1):
        workers: list[list[int]] = [[] for _ in range(k)]
        loads = [0] * k
        for number, duration in items:
            lightest = min(range(k), key=lambda i: loads[i])
            workers[lightest].append(number)
            loads[lightest] += duration
        cost = max(loads) + WORKER_COST_MS * k
        if best_cost is None or cost < best_cost:
            best_cost, best = cost, workers
    return best
