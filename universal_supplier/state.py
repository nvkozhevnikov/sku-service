from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveryHealth:
    discovered_count: int
    baseline_discovered_count: int | None
    discovery_ratio: float | None
    anomalous_discovery: bool
    deactivation_allowed: bool


def discovery_health(discovered_count: int, baseline: int | None, minimum_ratio: float = 0.80) -> DiscoveryHealth:
    ratio = None if not baseline else discovered_count / baseline
    anomalous = discovered_count <= 0 or (ratio is not None and ratio < minimum_ratio)
    return DiscoveryHealth(discovered_count, baseline, ratio, anomalous, not anomalous)


def next_missing_state(*, missed_crawls: int, active: bool, healthy_full_crawl: bool, individual_http_status: int | None = None, deactivate_after_misses: int = 3) -> tuple[int, bool]:
    if individual_http_status in {404, 410}:
        misses = missed_crawls + 1
        return misses, active if misses < deactivate_after_misses else False
    if not healthy_full_crawl:
        return missed_crawls, active
    misses = missed_crawls + 1
    return misses, active if misses < deactivate_after_misses else False


def source_active_for_availability(availability_normalized: str) -> bool:
    # Commercial stock does not define whether the supplier card exists.
    return True
