"""Forecast engine: combines weather, solar geometry and PV model into series.

Feature set mirrors forecast.solar Professional Plus:
- multiple planes per request
- 15-minute or hourly resolution
- horizon profile (beam shading)
- morning/evening damping
- inverter AC clipping
- weather-actual irradiance (Open-Meteo NWP radiation)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import solar
from .weather import WeatherPoint, get_weather


@dataclass
class Plane:
    declination: float  # tilt, 0 horizontal .. 90 vertical
    azimuth: float  # forecast.solar convention: 0=S, -90=E, 90=W
    kwp: float

    @property
    def azimuth_compass(self) -> float:
        return (self.azimuth + 180.0) % 360.0


@dataclass
class ForecastConfig:
    lat: float
    lon: float
    planes: list[Plane]
    horizon: list[float] = field(default_factory=list)
    damping_morning: float = 0.0
    damping_evening: float = 0.0
    inverter_kw: float | None = None
    efficiency: float = 0.90
    albedo: float = 0.2
    days: int = 3
    resolution: int = 15  # minutes: 15 or 60
    tz: str = "UTC"


@dataclass
class ForecastPoint:
    time: datetime  # localized to config tz, end of interval
    watts: float  # average AC power over interval
    watt_hours: float  # energy in interval


@dataclass
class ForecastResult:
    points: list[ForecastPoint]
    timezone: str


def parse_planes(spec: str) -> list[Plane]:
    """Parse 'dec:az:kwp[,dec:az:kwp...]' (also accepts ';' separators)."""
    planes = []
    for part in spec.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        if len(bits) != 3:
            raise ValueError(f"Invalid plane spec '{part}', expected dec:az:kwp")
        planes.append(Plane(declination=float(bits[0]), azimuth=float(bits[1]), kwp=float(bits[2])))
    if not planes:
        raise ValueError("No planes configured")
    return planes


def parse_horizon(spec: str) -> list[float]:
    values = [float(v) for v in spec.split(",") if v.strip() != ""]
    if values and len(values) < 4:
        raise ValueError("Horizon needs at least 4 values (evenly spaced, starting North)")
    return values


async def compute_forecast(cfg: ForecastConfig) -> ForecastResult:
    weather = await get_weather(cfg.lat, cfg.lon, cfg.days, cfg.resolution)
    tz = ZoneInfo(cfg.tz)

    # Pre-compute per-day max sun elevation for damping ramps
    max_elev_by_day: dict = {}
    if cfg.damping_morning > 0 or cfg.damping_evening > 0:
        for wx in weather:
            mid = wx.time - wx.step / 2
            sun = solar.sun_position(mid, cfg.lat, cfg.lon)
            day = wx.time.astimezone(tz).date()
            if sun.elevation > max_elev_by_day.get(day, 0.0):
                max_elev_by_day[day] = sun.elevation

    points: list[ForecastPoint] = []
    for wx in weather:
        watts = _point_power(cfg, wx, tz, max_elev_by_day)
        hours = wx.step.total_seconds() / 3600.0
        points.append(
            ForecastPoint(
                time=wx.time.astimezone(tz),
                watts=watts,
                watt_hours=watts * hours,
            )
        )
    return ForecastResult(points=points, timezone=cfg.tz)


def _point_power(cfg: ForecastConfig, wx: WeatherPoint, tz: ZoneInfo, max_elev_by_day: dict) -> float:
    if wx.ghi <= 0.0:
        return 0.0

    mid = wx.time - wx.step / 2
    sun = solar.sun_position(mid, cfg.lat, cfg.lon)
    if sun.elevation <= -2.0:
        return 0.0

    beam_blocked = False
    if cfg.horizon:
        beam_blocked = sun.elevation < solar.horizon_elevation(cfg.horizon, sun.azimuth)

    total = 0.0
    for plane in cfg.planes:
        poa = solar.poa_irradiance(
            sun,
            ghi=wx.ghi,
            dni=wx.dni,
            dhi=wx.dhi,
            tilt=plane.declination,
            panel_azimuth_compass=plane.azimuth_compass,
            dt=mid,
            albedo=cfg.albedo,
            beam_blocked=beam_blocked,
        )
        total += solar.dc_power(poa, wx.temp, plane.kwp, cfg.efficiency)

    # Morning/evening damping: scale by sun elevation relative to the day's peak
    if cfg.damping_morning > 0 or cfg.damping_evening > 0:
        day = wx.time.astimezone(tz).date()
        max_elev = max_elev_by_day.get(day, 0.0)
        if max_elev > 0:
            progress = max(0.0, min(1.0, sun.elevation / max_elev))
            local_noonish = _is_morning(mid, cfg.lon)
            damping = cfg.damping_morning if local_noonish else cfg.damping_evening
            total *= 1.0 - damping * (1.0 - progress)

    if cfg.inverter_kw is not None:
        total = min(total, cfg.inverter_kw * 1000.0)

    return total


def _is_morning(dt_utc: datetime, lon: float) -> bool:
    """True before local solar noon (approximated by mean solar time)."""
    solar_time = dt_utc + timedelta(hours=lon / 15.0)
    return solar_time.hour < 12


# ---------------------------------------------------------------------------
# Aggregations (forecast.solar response shapes)
# ---------------------------------------------------------------------------

TIME_FMT = "%Y-%m-%d %H:%M:%S"


def to_forecast_solar_result(result: ForecastResult) -> dict:
    watts: dict[str, int] = {}
    wh_period: dict[str, int] = {}
    wh_cumulative: dict[str, int] = {}
    wh_day: dict[str, int] = {}

    running: dict[str, float] = {}
    for p in result.points:
        key = p.time.strftime(TIME_FMT)
        day = p.time.strftime("%Y-%m-%d")
        watts[key] = round(p.watts)
        wh_period[key] = round(p.watt_hours)
        running[day] = running.get(day, 0.0) + p.watt_hours
        wh_cumulative[key] = round(running[day])
        wh_day[day] = round(running[day])

    return {
        "watts": watts,
        "watt_hours_period": wh_period,
        "watt_hours": wh_cumulative,
        "watt_hours_day": wh_day,
    }


def to_ha_summary(result: ForecastResult, now: datetime | None = None) -> dict:
    """Flat key/value summary for Home Assistant REST sensors."""
    tz = ZoneInfo(result.timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    power_now = 0.0
    energy_today = 0.0
    energy_today_remaining = 0.0
    energy_tomorrow = 0.0
    peak_w = 0.0
    peak_time = None
    next_hour_wh = 0.0

    for p in result.points:
        d = p.time.date()
        interval_start = p.time - timedelta(hours=p.watt_hours / p.watts) if p.watts > 0 else p.time
        if interval_start <= now <= p.time:
            power_now = p.watts
        if d == today:
            energy_today += p.watt_hours
            if p.time >= now:
                energy_today_remaining += p.watt_hours
            if p.watts > peak_w:
                peak_w = p.watts
                peak_time = p.time
        elif d == tomorrow:
            energy_tomorrow += p.watt_hours
        if now <= p.time <= now + timedelta(hours=1):
            next_hour_wh += p.watt_hours

    return {
        "power_now_w": round(power_now),
        "energy_today_kwh": round(energy_today / 1000.0, 3),
        "energy_today_remaining_kwh": round(energy_today_remaining / 1000.0, 3),
        "energy_tomorrow_kwh": round(energy_tomorrow / 1000.0, 3),
        "energy_next_hour_kwh": round(next_hour_wh / 1000.0, 3),
        "peak_power_today_w": round(peak_w),
        "peak_time_today": peak_time.isoformat() if peak_time else None,
        "timezone": result.timezone,
        "generated_at": now.isoformat(),
    }
