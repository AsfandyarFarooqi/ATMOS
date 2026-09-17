"""Central configuration, driven by environment variables.

Copy .env.example to .env and fill in your values.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# --- Earth Engine -----------------------------------------------------------
# No default: an Earth Engine project belongs to whoever runs the app, so it
# has to come from .env (see .env.example).
EE_PROJECT = os.getenv("EE_PROJECT", "").strip()

# --- Sampling ---------------------------------------------------------------
# Points sampled per image. Higher = more accurate, slower.
SAMPLE_POINTS = _int("SAMPLE_POINTS", 300)
NDVI_SCALE = _int("NDVI_SCALE", 30)      # Landsat 9 optical
GPP_SCALE = _int("GPP_SCALE", 500)       # MODIS MYD17A2H
UVAI_SCALE = _int("UVAI_SCALE", 1000)    # Sentinel-5P
WEATHER_SCALE = _int("WEATHER_SCALE", 1000)  # ECMWF IFS
MAX_CLOUD_PCT = _int("MAX_CLOUD_PCT", 20)

# --- Concurrency ------------------------------------------------------------
# Earth Engine throttles heavy concurrency; 8-12 is a safe, fast band.
MAX_WORKERS = _int("MAX_WORKERS", 10)

# --- Science constants ------------------------------------------------------
CARBON_CONTENT = 0.47   # fraction of biomass that is carbon
CO2_CONVERSION = 3.67   # C -> CO2 molecular weight ratio
BIOMASS_TO_CO2 = CARBON_CONTENT * CO2_CONVERSION

# add_agb()'s NDVI regression has no documented unit. Read as grams of dry
# biomass per m2, its coefficients give plausible values for every class
# (trees ~30 t/ha at NDVI 0.8, grass ~2 t/ha at 0.6, crops ~15 t/ha at 0.7),
# so that is assumed. Override if the coefficients use a different unit.
AGB_MODEL_TO_KG_M2 = _float("AGB_MODEL_TO_KG_M2", 0.001)

# --- Flask ------------------------------------------------------------------
DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"
