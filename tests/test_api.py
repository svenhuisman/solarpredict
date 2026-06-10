from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.weather as weather
from app.forecast import (
    ForecastConfig,
    ForecastPoint,
    ForecastResult,
    Plane,
    compute_forecast,
    to_forecast_solar_result,
    to_ha_summary,
)
from app.main import app as fastapi_app
from app.weather import WeatherPoint


def fake_weather_day(resolution: int = 60) -> list[WeatherPoint]:
    """One synthetic clear June day at UTC, hourly."""
    base = datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)
    step = timedelta(minutes=resolution)
    points = []
    t = base + step
    while t <= base + timedelta(days=1):
        hour = t.hour + t.minute / 60
        daylight = max(0.0, 1 - abs(hour - 12) / 7)  # crude bell, ~05:00-19:00
        points.append(
            WeatherPoint(
                time=t, step=step,
                ghi=850 * daylight, dni=800 * daylight, dhi=120 * daylight,
                temp=18.0,
            )
        )
        t += step
    return points


@pytest.fixture
def patched_weather(monkeypatch):
    async def fake_get_weather(lat, lon, days, resolution):
        return fake_weather_day(resolution)
    monkeypatch.setattr(weather, "get_weather", fake_get_weather)
    # forecast.py imported the symbol directly
    import app.forecast as fc
    monkeypatch.setattr(fc, "get_weather", fake_get_weather)


@pytest.mark.anyio
async def test_compute_forecast_produces_energy(patched_weather):
    cfg = ForecastConfig(lat=52.0, lon=5.0, planes=[Plane(30, 0, 5.0)], days=1, resolution=60)
    result = await compute_forecast(cfg)
    total_kwh = sum(p.watt_hours for p in result.points) / 1000
    assert 10 < total_kwh < 45  # plausible clear June day for 5 kWp
    assert all(p.watts >= 0 for p in result.points)


@pytest.mark.anyio
async def test_inverter_clipping(patched_weather):
    cfg = ForecastConfig(lat=52.0, lon=5.0, planes=[Plane(30, 0, 10.0)],
                         inverter_kw=3.0, days=1, resolution=60)
    result = await compute_forecast(cfg)
    assert max(p.watts for p in result.points) <= 3000.0 + 1e-6


@pytest.mark.anyio
async def test_two_planes_more_than_one(patched_weather):
    one = ForecastConfig(lat=52.0, lon=5.0, planes=[Plane(30, 0, 5.0)], days=1, resolution=60)
    two = ForecastConfig(lat=52.0, lon=5.0,
                         planes=[Plane(30, 0, 5.0), Plane(30, -90, 2.0)], days=1, resolution=60)
    r1 = await compute_forecast(one)
    r2 = await compute_forecast(two)
    assert sum(p.watt_hours for p in r2.points) > sum(p.watt_hours for p in r1.points)


def test_forecast_solar_result_shape():
    tz = timezone.utc
    pts = [
        ForecastPoint(datetime(2026, 6, 11, 11, 0, tzinfo=tz), 1000.0, 1000.0),
        ForecastPoint(datetime(2026, 6, 11, 12, 0, tzinfo=tz), 2000.0, 2000.0),
        ForecastPoint(datetime(2026, 6, 12, 12, 0, tzinfo=tz), 1500.0, 1500.0),
    ]
    out = to_forecast_solar_result(ForecastResult(points=pts, timezone="UTC"))
    assert out["watts"]["2026-06-11 12:00:00"] == 2000
    assert out["watt_hours"]["2026-06-11 12:00:00"] == 3000  # cumulative within day
    assert out["watt_hours_day"] == {"2026-06-11": 3000, "2026-06-12": 1500}


def test_ha_summary():
    tz = timezone.utc
    now = datetime(2026, 6, 11, 11, 30, tzinfo=tz)
    pts = [
        ForecastPoint(datetime(2026, 6, 11, 11, 0, tzinfo=tz), 1000.0, 1000.0),
        ForecastPoint(datetime(2026, 6, 11, 12, 0, tzinfo=tz), 2000.0, 2000.0),
        ForecastPoint(datetime(2026, 6, 12, 12, 0, tzinfo=tz), 1500.0, 1500.0),
    ]
    s = to_ha_summary(ForecastResult(points=pts, timezone="UTC"), now=now)
    assert s["power_now_w"] == 2000  # we're inside the 11:00-12:00 interval
    assert s["energy_today_kwh"] == 3.0
    assert s["energy_today_remaining_kwh"] == 2.0
    assert s["energy_tomorrow_kwh"] == 1.5
    assert s["peak_power_today_w"] == 2000


def test_api_validation_errors():
    client = TestClient(fastapi_app)
    r = client.get("/api/forecast")  # no params, no env defaults
    assert r.status_code == 422
    r = client.get("/api/forecast", params={"lat": 52, "lon": 5, "planes": "bad"})
    assert r.status_code == 422
    r = client.get("/api/forecast", params={"lat": 52, "lon": 5, "planes": "30:0:5", "resolution": 30})
    assert r.status_code == 422


def test_api_token_required_when_set(monkeypatch, patched_weather):
    monkeypatch.setenv("SP_API_TOKEN", "sekrit")
    client = TestClient(fastapi_app)
    params = {"lat": 52, "lon": 5, "planes": "30:0:5"}

    assert client.get("/api/forecast", params=params).status_code == 401
    assert client.get("/api/ha", params=params).status_code == 401
    assert client.get("/estimate/52/5/30/0/5").status_code == 401
    # healthz and UI stay open
    assert client.get("/healthz").status_code == 200

    # all three auth mechanisms work
    assert client.get("/api/ha", params={**params, "token": "sekrit"}).status_code == 200
    assert client.get("/api/ha", params=params, headers={"X-API-Key": "sekrit"}).status_code == 200
    assert (
        client.get("/api/ha", params=params, headers={"Authorization": "Bearer sekrit"}).status_code
        == 200
    )
    # wrong token rejected
    assert client.get("/api/ha", params={**params, "token": "wrong"}).status_code == 401


def test_api_open_when_no_token_configured(monkeypatch, patched_weather):
    monkeypatch.delenv("SP_API_TOKEN", raising=False)
    client = TestClient(fastapi_app)
    r = client.get("/api/ha", params={"lat": 52, "lon": 5, "planes": "30:0:5"})
    assert r.status_code == 200


@pytest.fixture
def anyio_backend():
    return "asyncio"
