"""Solar geometry and PV power models.

Implements:
- NOAA solar position algorithm (declination, equation of time, elevation, azimuth)
- HDKR (Hay-Davies-Klucher-Reindl) anisotropic transposition to plane-of-array
- PVWatts-style cell temperature and DC power model with temperature derating

Conventions:
- Solar azimuth: compass degrees, 0 = North, 90 = East, 180 = South, 270 = West.
- Panel azimuth (user-facing, forecast.solar convention): -180..180,
  0 = South, -90 = East, 90 = West. Converted internally to compass.
- Panel tilt (declination): 0 = horizontal, 90 = vertical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

SOLAR_CONSTANT = 1361.0  # W/m^2
TEMP_COEFF_PMP = -0.004  # 1/degC, typical crystalline silicon
NOCT = 45.0  # degC, nominal operating cell temperature


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def julian_day(dt: datetime) -> float:
    """Julian day from an aware UTC datetime."""
    dt = dt.astimezone(timezone.utc)
    y, m = dt.year, dt.month
    d = dt.day + (dt.hour + dt.minute / 60.0 + dt.second / 3600.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def sun_declination_eot(dt: datetime) -> tuple[float, float]:
    """Return (declination in radians, equation of time in minutes)."""
    t = (julian_day(dt) - 2451545.0) / 36525.0

    l0 = (280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    mr = math.radians(m)
    c = (
        math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
        + math.sin(3 * mr) * 0.000289
    )
    true_long = l0 + c
    omega = math.radians(125.04 - 1934.136 * t)
    app_long = true_long - 0.00569 - 0.00478 * math.sin(omega)

    eps0 = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    eps = math.radians(eps0 + 0.00256 * math.cos(omega))

    lam = math.radians(app_long)
    decl = math.asin(_clamp(math.sin(eps) * math.sin(lam)))

    y = math.tan(eps / 2.0) ** 2
    l0r = math.radians(l0)
    eot = 4.0 * math.degrees(
        y * math.sin(2 * l0r)
        - 2 * e * math.sin(mr)
        + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
        - 0.5 * y * y * math.sin(4 * l0r)
        - 1.25 * e * e * math.sin(2 * mr)
    )
    return decl, eot


@dataclass
class SunPosition:
    elevation: float  # apparent elevation, degrees
    azimuth: float  # compass degrees
    zenith: float  # true zenith, degrees


def sun_position(dt: datetime, lat: float, lon: float) -> SunPosition:
    """Sun position for an aware UTC datetime at lat/lon (degrees, lon east positive)."""
    decl, eot = sun_declination_eot(dt)
    dt = dt.astimezone(timezone.utc)

    minutes = dt.hour * 60.0 + dt.minute + dt.second / 60.0
    tst = (minutes + eot + 4.0 * lon) % 1440.0
    ha = math.radians(tst / 4.0 - 180.0)

    latr = math.radians(lat)
    cos_zen = _clamp(math.sin(latr) * math.sin(decl) + math.cos(latr) * math.cos(decl) * math.cos(ha))
    zen = math.acos(cos_zen)
    elev = 90.0 - math.degrees(zen)

    sin_zen = math.sin(zen)
    if sin_zen < 1e-6:
        az = 180.0
    else:
        ac = math.degrees(
            math.acos(_clamp((math.sin(latr) * cos_zen - math.sin(decl)) / (math.cos(latr) * sin_zen)))
        )
        az = (ac + 180.0) % 360.0 if math.degrees(ha) > 0 else (540.0 - ac) % 360.0

    # Bennett atmospheric refraction, only meaningful near the horizon
    if -1.0 < elev < 85.0:
        elev_app = elev + 1.0 / (60.0 * math.tan(math.radians(elev + 7.31 / (elev + 4.4))))
    else:
        elev_app = elev

    return SunPosition(elevation=elev_app, azimuth=az, zenith=math.degrees(zen))


def extraterrestrial_normal(dt: datetime) -> float:
    """Extraterrestrial normal irradiance for day of year (W/m^2)."""
    doy = dt.timetuple().tm_yday
    return SOLAR_CONSTANT * (1.0 + 0.033 * math.cos(2.0 * math.pi * doy / 365.0))


def poa_irradiance(
    sun: SunPosition,
    ghi: float,
    dni: float,
    dhi: float,
    tilt: float,
    panel_azimuth_compass: float,
    dt: datetime,
    albedo: float = 0.2,
    beam_blocked: bool = False,
) -> float:
    """Plane-of-array global irradiance (W/m^2) via HDKR transposition.

    beam_blocked: True when a horizon obstruction shades the direct beam.
    """
    if ghi <= 0.0:
        return 0.0

    tiltr = math.radians(tilt)
    zenr = math.radians(sun.zenith)
    cos_zen = math.cos(zenr)

    cos_aoi = _clamp(
        cos_zen * math.cos(tiltr)
        + math.sin(zenr) * math.sin(tiltr) * math.cos(math.radians(sun.azimuth - panel_azimuth_compass)),
        -1.0,
        1.0,
    )
    cos_aoi = max(cos_aoi, 0.0)

    sun_up = sun.elevation > 0.0
    # Clamp cos(zenith) to cos(88 deg) in ratios to avoid blow-up at grazing angles
    cos_zen_safe = max(cos_zen, 0.0349)
    rb = cos_aoi / cos_zen_safe

    e0 = extraterrestrial_normal(dt)
    ai = _clamp(dni / e0, 0.0, 1.0) if sun_up else 0.0

    beam_horizontal = dni * max(cos_zen, 0.0)
    f = math.sqrt(_clamp(beam_horizontal / ghi, 0.0, 1.0))
    horizon_brightening = 1.0 + f * math.sin(tiltr / 2.0) ** 3

    iso_view = (1.0 + math.cos(tiltr)) / 2.0
    ground_view = (1.0 - math.cos(tiltr)) / 2.0

    poa_beam = dni * cos_aoi if (sun_up and not beam_blocked) else 0.0
    circumsolar = dhi * ai * rb if (sun_up and not beam_blocked) else 0.0
    poa_diffuse = dhi * (1.0 - ai) * iso_view * horizon_brightening + circumsolar
    poa_ground = ghi * albedo * ground_view

    return max(poa_beam + poa_diffuse + poa_ground, 0.0)


def cell_temperature(poa: float, ambient_temp: float) -> float:
    """NOCT-based cell temperature (degC)."""
    return ambient_temp + poa * (NOCT - 20.0) / 800.0


def dc_power(poa: float, ambient_temp: float, kwp: float, efficiency: float) -> float:
    """PVWatts-style power output in watts for a plane of kwp at given POA irradiance."""
    if poa <= 0.0:
        return 0.0
    t_cell = cell_temperature(poa, ambient_temp)
    temp_factor = 1.0 + TEMP_COEFF_PMP * (t_cell - 25.0)
    return max(kwp * 1000.0 * (poa / 1000.0) * temp_factor * efficiency, 0.0)


def horizon_elevation(horizon: list[float], azimuth: float) -> float:
    """Interpolated horizon elevation (deg) at a compass azimuth.

    horizon: N elevation values, evenly spaced over 360 deg, first value at North.
    """
    if not horizon:
        return 0.0
    n = len(horizon)
    step = 360.0 / n
    pos = (azimuth % 360.0) / step
    i = int(pos) % n
    frac = pos - int(pos)
    return horizon[i] * (1.0 - frac) + horizon[(i + 1) % n] * frac
