"""Open-Meteo weather client with in-memory caching.

Open-Meteo radiation values are averages over the interval *ending* at each
timestamp (backward-averaged). Solar position is therefore evaluated at the
interval midpoint by the forecast engine.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_TTL = 900  # seconds

_cache: dict[tuple, tuple[float, list["WeatherPoint"]]] = {}


@dataclass
class WeatherPoint:
    time: datetime  # UTC, end of averaging interval
    step: timedelta  # averaging interval length
    ghi: float  # W/m^2
    dni: float  # W/m^2
    dhi: float  # W/m^2
    temp: float  # degC


class WeatherError(Exception):
    pass


async def get_weather(lat: float, lon: float, days: int, resolution: int) -> list[WeatherPoint]:
    """Fetch irradiance + temperature forecast. resolution: 15 or 60 minutes."""
    key = (round(lat, 3), round(lon, 3), days, resolution)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]

    variables = "temperature_2m,shortwave_radiation,direct_normal_irradiance,diffuse_radiation"
    params = {
        "latitude": lat,
        "longitude": lon,
        "forecast_days": days,
        "timezone": "UTC",
    }
    if resolution == 15:
        params["minutely_15"] = variables
        section = "minutely_15"
    else:
        params["hourly"] = variables
        section = "hourly"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(OPEN_METEO_URL, params=params)
    if resp.status_code != 200:
        raise WeatherError(f"Open-Meteo returned HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json().get(section)
    if not data:
        raise WeatherError(f"Open-Meteo response missing '{section}' block")

    step = timedelta(minutes=resolution)
    points: list[WeatherPoint] = []
    for i, ts in enumerate(data["time"]):
        t = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        points.append(
            WeatherPoint(
                time=t,
                step=step,
                ghi=_val(data["shortwave_radiation"], i),
                dni=_val(data["direct_normal_irradiance"], i),
                dhi=_val(data["diffuse_radiation"], i),
                temp=_val(data["temperature_2m"], i, default=15.0),
            )
        )

    _cache[key] = (now, points)
    return points


def _val(arr: list, i: int, default: float = 0.0) -> float:
    v = arr[i] if i < len(arr) else None
    return float(v) if v is not None else default
