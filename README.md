# ☀️ SolarPredict

Self-hosted solar production forecasting — a free alternative to the
**forecast.solar Professional Plus** plan, usable from **Home Assistant**.

Weather data comes from [Open-Meteo](https://open-meteo.com/) (free, no API key,
no rate limits for personal use). Open-Meteo provides numerical-weather-model
irradiance (GHI / DNI / DHI, cloud-adjusted), which SolarPredict converts to PV
output using standard industry models:

- **NOAA solar position** algorithm (sun elevation/azimuth)
- **HDKR anisotropic transposition** (plane-of-array irradiance)
- **PVWatts-style power model** with NOCT cell-temperature derating

## Feature comparison vs forecast.solar

| Feature | forecast.solar free | forecast.solar Pro+ | SolarPredict |
|---|---|---|---|
| Forecast days | 3 | up to 7 | up to 7 |
| Resolution | 1 h | 15 min | 15 min or 1 h |
| Multiple planes | ✗ | ✓ | ✓ (unlimited) |
| Horizon profile | ✗ | ✓ | ✓ |
| Morning/evening damping | ✓ | ✓ | ✓ |
| Inverter clipping | ✗ | ✓ | ✓ |
| Cloud/weather-actual data | limited | ✓ | ✓ (Open-Meteo NWP) |
| API rate limit | 12/h | high | none (self-hosted) |
| Cost | free | €X/month | free |

## Quick start

```bash
docker compose up -d --build
# open http://localhost:8000
```

Or without Docker:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The web UI at `http://localhost:8000` lets you configure location, planes,
horizon, damping and inverter limit, shows the forecast chart, and **generates
ready-to-paste Home Assistant YAML** for your exact configuration.

### Deploy to Vercel (no server needed)

The repo includes `vercel.json` + `api/index.py`, so it deploys as-is:

```bash
npm i -g vercel@latest
vercel deploy --prod
```

Set `SP_LAT`, `SP_LON`, `SP_PLANES`, `SP_TZ` as environment variables in the
Vercel dashboard (or `vercel env add`) so `/api/ha` works without query
parameters. Runs fine on the free Hobby tier — HA polls every 15 minutes,
which is a trivial load. Note: the deployment must stay publicly reachable
for HA to poll it (don't enable Deployment Protection on production).

**Protect a public deployment:** set the `SP_API_TOKEN` environment variable
(any long random string, e.g. `openssl rand -hex 24`). All forecast endpoints
then require the token via `X-API-Key` header, `Authorization: Bearer` or
`?token=` query parameter; only `/healthz` and the UI page stay open. Enter
the same token in the HA integration's setup form (or the UI's Advanced
section). When `SP_API_TOKEN` is unset the API is open — fine for
LAN-only Docker. Vercel additionally applies automatic DDoS mitigation on
all plans; per-rule rate limiting via Vercel WAF is available on Pro if you
ever need it.

## Home Assistant integration

### Option A — custom integration (recommended, energy dashboard support)

Copy [custom_components/solarpredict/](custom_components/solarpredict/) into
`<config>/custom_components/`, restart HA, then:

**Settings → Devices & Services → Add Integration → SolarPredict**

Enter your server URL (local Docker or Vercel URL), location and planes.
Advanced settings (horizon, damping, inverter limit, efficiency, resolution)
live under the integration's **Configure** button.

You get the 7 sensors below **plus native energy dashboard support**: the
integration implements HA's energy platform, so your production forecast
shows up in **Settings → Dashboards → Energy → Solar production forecast**,
exactly like the official forecast.solar integration.

Also installable via HACS as a custom repository
(HACS → Integrations → ⋮ → Custom repositories → this repo URL).

### Option B — REST sensors (no custom component)

Copy [homeassistant/solarpredict.yaml](homeassistant/solarpredict.yaml) into
`<config>/packages/`, edit the resource URL, restart HA. You get:

- `sensor.solar_forecast_power_now` (W)
- `sensor.solar_forecast_energy_today` / `_remaining` / `_tomorrow` / `_next_hour` (kWh)
- `sensor.solar_forecast_peak_power_today` (W) and `_peak_time_today`

Or click **"Home Assistant YAML"** in the web UI to generate the package with
your settings filled in.

> **Energy dashboard note:** REST sensors cannot feed the energy dashboard's
> solar forecast graph — use Option A (custom integration) for that.

## API

### forecast.solar-compatible

```
GET /estimate/{lat}/{lon}/{dec}/{az}/{kwp}
```

Same response shape as forecast.solar (`result.watts`,
`result.watt_hours_period`, `result.watt_hours`, `result.watt_hours_day`), so
existing forecast.solar client code can be pointed here.

- `dec`: tilt, 0 (horizontal) – 90 (vertical)
- `az`: azimuth, forecast.solar convention: 0 = South, −90 = East, 90 = West

### Query parameters (all endpoints)

| Param | Meaning | Default |
|---|---|---|
| `planes` | extra planes `dec:az:kwp,dec:az:kwp,...` | — |
| `horizon` | elevation degrees, comma-separated, evenly spaced starting North (≥4 values) | none |
| `damping_morning` / `damping_evening` | 0–1, attenuates low-sun production | 0 |
| `inverter_kw` | AC clipping limit | none |
| `efficiency` | total system efficiency (inverter, wiring, soiling) | 0.90 |
| `albedo` | ground reflectance | 0.2 |
| `days` | forecast days 1–7 | 3 |
| `resolution` | 15 or 60 (minutes) | 15 |
| `tz` | IANA timezone for output timestamps | UTC |

### Other endpoints

- `GET /api/forecast?...` — full series + summary JSON (used by the web UI)
- `GET /api/ha?...` — flat key/value summary for HA REST sensors
- `GET /healthz`

### Server-side defaults

Set env vars so `/api/ha` needs no query string (see `docker-compose.yml`):
`SP_LAT`, `SP_LON`, `SP_PLANES`, `SP_TZ`, `SP_HORIZON`, `SP_DAMPING_MORNING`,
`SP_DAMPING_EVENING`, `SP_INVERTER_KW`, `SP_EFFICIENCY`, `SP_ALBEDO`,
`SP_DAYS`, `SP_RESOLUTION`.

## Examples

```bash
# Single south-facing 5.4 kWp plane, 15-min resolution, Dutch timezone
curl "http://localhost:8000/estimate/52.37/4.90/30/0/5.4?tz=Europe/Amsterdam"

# East-west split system with horizon shading and a 4 kW inverter
curl "http://localhost:8000/api/forecast?lat=52.37&lon=4.90\
&planes=35:-90:2.5,35:90:2.5&horizon=0,0,8,15,10,5,0,0&inverter_kw=4.0"
```

## Accuracy notes

- Open-Meteo blends multiple weather models (ICON, GFS, HARMONIE/KNMI in NL)
  and provides cloud-adjusted irradiance — the same class of input
  forecast.solar uses on paid tiers.
- The default `efficiency=0.90` covers inverter, wiring, soiling and mismatch
  losses; temperature losses are modelled separately. Tune it after comparing
  a few days against your actual inverter readings.
- Damping is an elevation-proportional approximation of forecast.solar's
  morning/evening damping (their exact algorithm is unpublished).

## Tests

```bash
.venv/bin/pip install pytest anyio
.venv/bin/python -m pytest tests/ -q
```
