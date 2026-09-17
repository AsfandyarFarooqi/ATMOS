"""Month-by-month carbon aggregation.

The original implementation ran every month sequentially, and within each month
ran five fetchers sequentially -- roughly ten blocking Earth Engine round trips
per month. A twelve-month query meant ~120 serial network calls.

Here every (month, dataset) pair is submitted to one bounded thread pool, and
results are memoised per month so adjusting one end of the date range reuses
everything already computed.
"""
import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from config import MAX_WORKERS
from imagery_processor import (
    fetch_gpp_data,
    fetch_sentinel_imagery_ndvi,
    fetch_weather,
    get_uvai_estimate,
)

log = logging.getLogger(__name__)

_cache: dict[tuple, object] = {}
_cache_lock = threading.Lock()
_CACHE_LIMIT = 2048

# kind -> (callable, default value when the fetch fails)
_FETCHERS = {
    "agb": (fetch_sentinel_imagery_ndvi, 0.0),
    "gpp": (fetch_gpp_data, 0.0),
    "uvai": (get_uvai_estimate, 0.0),
    "weather": (fetch_weather, {"temperature_c": None, "precipitation_mm": None}),
}


def get_monthly_ranges(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """Calendar-month [start, end) pairs, clamped to end_date.

    The previous version let the final range run past end_date, pulling in
    imagery from outside the window the user asked for.
    """
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    months = []
    while current < end:
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        stop = min(next_month, end)
        months.append((current.strftime("%Y-%m-%d"), stop.strftime("%Y-%m-%d")))
        current = next_month
    return months


def _roi_key(coords) -> str:
    return hashlib.sha1(json.dumps(coords, sort_keys=True).encode()).hexdigest()[:16]


def _fetch(kind: str, coords, roi_key: str, start: str, end: str):
    """Run one fetcher, memoised on (kind, roi, window)."""
    key = (kind, roi_key, start, end)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    func, fallback = _FETCHERS[kind]
    try:
        value = func(coords, start, end)
    except Exception as exc:
        log.error("%s failed for %s: %s", kind, start, exc)
        value = fallback

    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[key] = value
    return value


def compute_monthly_carbon_data(coords_input, start_date: str, end_date: str) -> list[dict]:
    coords = coords_input if isinstance(coords_input, list) else json.loads(coords_input)
    roi_key = _roi_key(coords)
    ranges = get_monthly_ranges(start_date, end_date)

    if not ranges:
        return []

    # month label -> partial results, filled in as futures land.
    buckets: dict[str, dict] = {s[:7]: {} for s, _ in ranges}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch, kind, coords, roi_key, s, e): (s[:7], kind)
            for s, e in ranges
            for kind in _FETCHERS
        }
        for future in as_completed(futures):
            month, kind = futures[future]
            try:
                buckets[month][kind] = future.result()
            except Exception as exc:
                log.error("Task %s/%s raised: %s", month, kind, exc)
                buckets[month][kind] = _FETCHERS[kind][1]

    results = []
    for s, _ in ranges:
        month = s[:7]
        data = buckets[month]
        agb = data.get("agb") or 0.0
        gpp = data.get("gpp") or 0.0
        uvai = data.get("uvai") or 0.0
        weather = data.get("weather") or {}
        temp = weather.get("temperature_c")
        precip = weather.get("precipitation_mm")

        results.append({
            "month": month,
            "AGB_CO2_kg": round(agb, 2),
            "GPP_CO2_kg": round(gpp, 2),
            "UVAI_index": round(uvai, 4),
            # Only kg CO2 terms belong in a kg balance. The aerosol index is
            # dimensionless, so it is reported on its own, not subtracted.
            "Carbon_Balance_kg": round(agb + gpp, 2),
            "Temperature_C": None if temp is None else round(temp, 2),
            "Precipitation_mm": None if precip is None else round(precip, 2),
        })

    return results
