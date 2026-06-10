"""Constants for the SolarPredict integration."""

from datetime import timedelta

DOMAIN = "solarpredict"

CONF_HOST = "host"
CONF_PLANES = "planes"
CONF_HORIZON = "horizon"
CONF_DAMPING_MORNING = "damping_morning"
CONF_DAMPING_EVENING = "damping_evening"
CONF_INVERTER_KW = "inverter_kw"
CONF_EFFICIENCY = "efficiency"
CONF_DAYS = "days"
CONF_RESOLUTION = "resolution"

DEFAULT_HOST = "http://localhost:8000"
DEFAULT_PLANES = "30:0:5.0"

UPDATE_INTERVAL = timedelta(minutes=15)
