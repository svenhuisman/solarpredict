"""Energy platform — feeds the HA energy dashboard solar production forecast."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import SolarPredictCoordinator


async def async_get_solar_forecast(
    hass: HomeAssistant, config_entry_id: str
) -> dict[str, dict[str, float]] | None:
    """Return the solar forecast as {wh_hours: {iso_timestamp: Wh}}."""
    coordinator: SolarPredictCoordinator | None = hass.data.get(DOMAIN, {}).get(
        config_entry_id
    )
    if coordinator is None or coordinator.data is None:
        return None

    return {
        "wh_hours": {
            point["time"]: point["watt_hours"]
            for point in coordinator.data["series"]
        }
    }
