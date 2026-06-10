"""SolarPredict API — self-hosted forecast.solar alternative.

Endpoints:
- GET /                                     web UI
- GET /estimate/{lat}/{lon}/{dec}/{az}/{kwp}  forecast.solar-compatible estimate
- GET /api/forecast                         full series JSON (UI / generic clients)
- GET /api/ha                               flat summary for Home Assistant REST sensors
- GET /healthz

Defaults can be set via environment variables so /api/ha and /api/forecast
work without query parameters:
  SP_LAT, SP_LON, SP_PLANES (dec:az:kwp[,dec:az:kwp...]), SP_TZ, SP_HORIZON,
  SP_DAMPING_MORNING, SP_DAMPING_EVENING, SP_INVERTER_KW, SP_EFFICIENCY,
  SP_ALBEDO, SP_DAYS, SP_RESOLUTION
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import secrets as _secrets

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .forecast import (
    ForecastConfig,
    Plane,
    compute_forecast,
    parse_horizon,
    parse_planes,
    to_forecast_solar_result,
    to_ha_summary,
)
from .weather import WeatherError

app = FastAPI(title="SolarPredict", version="1.0.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def verify_token(request: Request) -> None:
    """Require an API token on forecast endpoints when SP_API_TOKEN is set.

    Accepted: X-API-Key header, Authorization: Bearer <token>, or ?token= query.
    When SP_API_TOKEN is unset the API is open (typical for LAN/Docker use).
    """
    expected = _env("SP_API_TOKEN")
    if not expected:
        return
    auth = request.headers.get("authorization", "")
    supplied = (
        request.headers.get("x-api-key")
        or (auth[7:] if auth.lower().startswith("bearer ") else None)
        or request.query_params.get("token")
    )
    if not supplied or not _secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


def _build_config(
    lat: float | None,
    lon: float | None,
    planes: str | None,
    horizon: str | None,
    damping_morning: float | None,
    damping_evening: float | None,
    inverter_kw: float | None,
    efficiency: float | None,
    albedo: float | None,
    days: int | None,
    resolution: int | None,
    tz: str | None,
) -> ForecastConfig:
    lat = lat if lat is not None else _float_env("SP_LAT")
    lon = lon if lon is not None else _float_env("SP_LON")
    planes_spec = planes or _env("SP_PLANES")
    if lat is None or lon is None or not planes_spec:
        raise HTTPException(
            status_code=422,
            detail="lat, lon and planes are required (query params or SP_LAT/SP_LON/SP_PLANES env)",
        )
    try:
        plane_list = parse_planes(planes_spec)
        horizon_list = parse_horizon(horizon or _env("SP_HORIZON") or "")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    cfg = ForecastConfig(
        lat=lat,
        lon=lon,
        planes=plane_list,
        horizon=horizon_list,
        damping_morning=_pick(damping_morning, "SP_DAMPING_MORNING", 0.0),
        damping_evening=_pick(damping_evening, "SP_DAMPING_EVENING", 0.0),
        inverter_kw=inverter_kw if inverter_kw is not None else _float_env("SP_INVERTER_KW"),
        efficiency=_pick(efficiency, "SP_EFFICIENCY", 0.90),
        albedo=_pick(albedo, "SP_ALBEDO", 0.2),
        days=int(_pick(days, "SP_DAYS", 3)),
        resolution=int(_pick(resolution, "SP_RESOLUTION", 15)),
        tz=tz or _env("SP_TZ") or "UTC",
    )
    if cfg.resolution not in (15, 60):
        raise HTTPException(status_code=422, detail="resolution must be 15 or 60")
    if not 1 <= cfg.days <= 7:
        raise HTTPException(status_code=422, detail="days must be 1..7")
    if not 0.0 <= cfg.damping_morning <= 1.0 or not 0.0 <= cfg.damping_evening <= 1.0:
        raise HTTPException(status_code=422, detail="damping must be 0..1")
    try:
        ZoneInfo(cfg.tz)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Unknown timezone '{cfg.tz}'")
    return cfg


def _float_env(name: str) -> float | None:
    v = _env(name)
    return float(v) if v is not None else None


def _pick(value, env_name: str, default: float) -> float:
    if value is not None:
        return value
    v = _float_env(env_name)
    return v if v is not None else default


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/api/forecast", dependencies=[Depends(verify_token)])
async def api_forecast(
    lat: float | None = None,
    lon: float | None = None,
    planes: str | None = Query(
        None, description="dec:az:kwp[,dec:az:kwp...]; az accepts -180..180, compass degrees like '113c', or cardinals like 'SE'"
    ),
    horizon: str | None = None,
    damping_morning: float | None = None,
    damping_evening: float | None = None,
    inverter_kw: float | None = None,
    efficiency: float | None = None,
    albedo: float | None = None,
    days: int | None = None,
    resolution: int | None = None,
    tz: str | None = None,
):
    cfg = _build_config(
        lat, lon, planes, horizon, damping_morning, damping_evening,
        inverter_kw, efficiency, albedo, days, resolution, tz,
    )
    result = await _run(cfg)
    return {
        "config": {
            "lat": cfg.lat,
            "lon": cfg.lon,
            "planes": [vars(p) for p in cfg.planes],
            "days": cfg.days,
            "resolution": cfg.resolution,
            "timezone": cfg.tz,
        },
        "series": [
            {"time": p.time.isoformat(), "watts": round(p.watts), "watt_hours": round(p.watt_hours)}
            for p in result.points
        ],
        "summary": to_ha_summary(result),
        "result": to_forecast_solar_result(result),
    }


@app.get("/api/ha", dependencies=[Depends(verify_token)])
async def api_ha(
    lat: float | None = None,
    lon: float | None = None,
    planes: str | None = None,
    horizon: str | None = None,
    damping_morning: float | None = None,
    damping_evening: float | None = None,
    inverter_kw: float | None = None,
    efficiency: float | None = None,
    albedo: float | None = None,
    days: int | None = None,
    resolution: int | None = None,
    tz: str | None = None,
):
    cfg = _build_config(
        lat, lon, planes, horizon, damping_morning, damping_evening,
        inverter_kw, efficiency, albedo, days, resolution, tz,
    )
    result = await _run(cfg)
    return to_ha_summary(result)


@app.get("/estimate/{lat}/{lon}/{dec}/{az}/{kwp}", dependencies=[Depends(verify_token)])
async def estimate(
    lat: float,
    lon: float,
    dec: float,
    az: float,
    kwp: float,
    planes: str | None = None,
    horizon: str | None = None,
    damping_morning: float | None = None,
    damping_evening: float | None = None,
    inverter_kw: float | None = None,
    efficiency: float | None = None,
    albedo: float | None = None,
    days: int | None = None,
    resolution: int | None = None,
    tz: str | None = None,
):
    """forecast.solar-compatible estimate. Extra planes via ?planes=dec:az:kwp,..."""
    plane_list = [Plane(declination=dec, azimuth=az, kwp=kwp)]
    if planes:
        try:
            plane_list += parse_planes(planes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    cfg = _build_config(
        lat, lon, ",".join(f"{p.declination}:{p.azimuth}:{p.kwp}" for p in plane_list),
        horizon, damping_morning, damping_evening,
        inverter_kw, efficiency, albedo, days, resolution, tz,
    )
    result = await _run(cfg)
    now = datetime.now(timezone.utc)
    return {
        "result": to_forecast_solar_result(result),
        "message": {
            "code": 0,
            "type": "success",
            "text": "",
            "info": {
                "latitude": lat,
                "longitude": lon,
                "distance": 0,
                "place": "",
                "timezone": cfg.tz,
                "time": datetime.now(ZoneInfo(cfg.tz)).strftime("%Y-%m-%dT%H:%M:%S%z"),
                "time_utc": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            },
            "ratelimit": {"period": 0, "limit": 0, "remaining": 0},
        },
    }


async def _run(cfg: ForecastConfig):
    try:
        return await compute_forecast(cfg)
    except WeatherError as e:
        raise HTTPException(status_code=502, detail=str(e))
