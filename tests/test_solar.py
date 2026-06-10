import math
from datetime import datetime, timezone

from app import solar
from app.forecast import Plane, parse_horizon, parse_planes


def test_sun_position_summer_solstice_noon():
    # 2026-06-21 ~12:00 UTC at lat 52N, lon 0: near solar noon,
    # elevation ~ 90 - 52 + 23.44 = 61.4 deg, azimuth near south.
    dt = datetime(2026, 6, 21, 12, 2, tzinfo=timezone.utc)
    sun = solar.sun_position(dt, 52.0, 0.0)
    assert abs(sun.elevation - 61.4) < 0.5
    assert 170 < sun.azimuth < 190


def test_sun_below_horizon_at_midnight():
    dt = datetime(2026, 6, 21, 0, 0, tzinfo=timezone.utc)
    sun = solar.sun_position(dt, 52.0, 0.0)
    assert sun.elevation < 0


def test_poa_flat_panel_approximates_ghi():
    # Horizontal panel: POA ~= GHI (plus tiny model differences, no ground term)
    dt = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    sun = solar.sun_position(dt, 52.0, 0.0)
    ghi, dni, dhi = 800.0, 700.0, 150.0
    poa = solar.poa_irradiance(sun, ghi, dni, dhi, tilt=0.0, panel_azimuth_compass=180.0, dt=dt)
    expected = dni * math.cos(math.radians(sun.zenith)) + dhi
    assert abs(poa - expected) < expected * 0.1


def test_tilted_south_beats_flat_in_winter():
    dt = datetime(2026, 12, 21, 12, 0, tzinfo=timezone.utc)
    sun = solar.sun_position(dt, 52.0, 0.0)
    ghi, dni, dhi = 200.0, 400.0, 80.0
    flat = solar.poa_irradiance(sun, ghi, dni, dhi, 0.0, 180.0, dt)
    tilted = solar.poa_irradiance(sun, ghi, dni, dhi, 40.0, 180.0, dt)
    assert tilted > flat


def test_power_temperature_derating():
    cold = solar.dc_power(1000.0, 0.0, kwp=5.0, efficiency=1.0)
    hot = solar.dc_power(1000.0, 35.0, kwp=5.0, efficiency=1.0)
    assert cold > hot
    assert hot > 0


def test_power_zero_at_night():
    assert solar.dc_power(0.0, 10.0, kwp=5.0, efficiency=0.9) == 0.0


def test_horizon_interpolation():
    horizon = [0.0, 10.0, 20.0, 10.0]  # N, E, S, W (90 deg steps)
    assert solar.horizon_elevation(horizon, 0.0) == 0.0
    assert solar.horizon_elevation(horizon, 90.0) == 10.0
    assert abs(solar.horizon_elevation(horizon, 45.0) - 5.0) < 1e-9
    assert abs(solar.horizon_elevation(horizon, 315.0) - 5.0) < 1e-9  # W->N wrap


def test_parse_planes():
    planes = parse_planes("30:0:5.4,25:-90:2.2")
    assert planes == [Plane(30.0, 0.0, 5.4), Plane(25.0, -90.0, 2.2)]
    assert planes[0].azimuth_compass == 180.0
    assert planes[1].azimuth_compass == 90.0


def test_parse_horizon_rejects_short():
    try:
        parse_horizon("1,2")
        assert False, "expected ValueError"
    except ValueError:
        pass
