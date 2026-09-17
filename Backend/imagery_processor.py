"""Earth Engine data extraction for the ATMOS carbon model.

Units, verified against the Earth Engine catalog and live samples:

  metric           source band (native unit)                    returned as
  ---------------  -------------------------------------------  --------------------------------
  AGB CO2 stock    NDVI regression, see add_agb (assumed g/m2)  kg CO2, ROI total
  GPP CO2 uptake   MODIS MYD17A2H Gpp (DN x 1e-4 = kg C/m2,     kg CO2, ROI total for the window
                   8-day total)
  Aerosol index    S5P absorbing_aerosol_index (dimensionless)  dimensionless, ROI mean
  Air temperature  ECMWF IFS temperature_2m_sfc (deg C)         deg C, ROI mean
  Precipitation    ECMWF IFS total_precipitation_sfc (m,        mm, ROI mean total for the window
                   cumulative since forecast hour 0)

Point samples are always averaged, never summed: a sum over N random pixels
grows with N, not with anything physical. ROI totals are mean-per-m2 x area.
"""
import json
import logging
from datetime import datetime
from functools import lru_cache

import ee
import numpy as np
import pandas as pd

from config import (
    AGB_MODEL_TO_KG_M2,
    BIOMASS_TO_CO2,
    CO2_CONVERSION,
    GPP_SCALE,
    MAX_CLOUD_PCT,
    NDVI_SCALE,
    SAMPLE_POINTS,
    UVAI_SCALE,
    WEATHER_SCALE,
)
from ee_client import ensure_initialized
from randomForestCalcs import impute_with_xgb

log = logging.getLogger(__name__)

# --- Dataset facts (Earth Engine catalog) -----------------------------------
MODIS_GPP_SCALE = 0.0001       # Gpp DN -> kg C/m2
MODIS_GPP_PERIOD_DAYS = 8      # each MYD17A2H image is an 8-day total

ECMWF = "ECMWF/NRT_FORECAST/IFS/OPER"
ECMWF_RUN_INTERVAL_H = 12      # a new IFS run every 12 h
# 3-hourly steps from each run's first 12 h; two runs a day tile all 24 h,
# so the mean is a true daily mean at the shortest lead times.
ECMWF_TEMP_STEPS = [0, 3, 6, 9]
# Precipitation is cumulative from hour 0, so the hour-12 value is exactly the
# precipitation that fell in each run's first 12 h.
ECMWF_PRECIP_STEP = 12

# ESA WorldCover class -> (NDVI slope, intercept, label) for the AGB regression.
_LANDCOVER_AGB = {
    10: (10000, -5000, "tree"),
    30: (2000, -1000, "grass"),
    40: (5000, -2000, "crop"),
    50: (0, 0, "urban"),
}
_DEFAULT_AGB = (0, 0, "other")


# --- helpers ----------------------------------------------------------------

def _coords(coords_input) -> list:
    return coords_input if isinstance(coords_input, list) else json.loads(coords_input)


def _to_roi(coords_input) -> ee.Geometry:
    return ee.Geometry.Polygon(_coords(coords_input))


def _window_days(start_date: str, end_date: str) -> int:
    fmt = "%Y-%m-%d"
    return (datetime.strptime(end_date, fmt) - datetime.strptime(start_date, fmt)).days


@lru_cache(maxsize=256)
def _area_cached(coords_json: str) -> float:
    return float(ee.Geometry.Polygon(json.loads(coords_json)).area(maxError=1).getInfo())


def roi_area_m2(coords_input) -> float:
    """Geodesic ROI area in m2 -- one round trip per distinct region."""
    ensure_initialized()
    return _area_cached(json.dumps(_coords(coords_input), sort_keys=True))


def _sample_image(image, roi, scale, num_points=SAMPLE_POINTS) -> list[dict]:
    """Sample an image over the ROI and flatten to lon/lat + band values.

    One ``getInfo()`` round trip. Returns [] when the image yields no pixels.
    """
    fc = image.sample(region=roi, scale=scale, numPixels=num_points, geometries=True)
    features = fc.limit(num_points).getInfo().get("features", [])
    rows = []
    for f in features:
        lon, lat = f["geometry"]["coordinates"]
        rows.append({"lon": lon, "lat": lat, **f["properties"]})
    return rows


def _is_empty(collection) -> bool:
    return collection.size().getInfo() == 0


def _mean_or_none(series: pd.Series):
    value = series.mean()
    return None if pd.isna(value) else float(value)


# --- AGB / NDVI -------------------------------------------------------------

def add_agb(feature):
    """Server-side AGB estimate from NDVI, keyed on ESA WorldCover class.

    Output is in the regression's own unit; see config.AGB_MODEL_TO_KG_M2.
    """
    ndvi = ee.Number(feature.get("NDVI"))
    lc = ee.Number(feature.get("Map"))

    slope, intercept, label = _DEFAULT_AGB
    slope_expr, intercept_expr, label_expr = ee.Number(slope), ee.Number(intercept), ee.String(label)
    for code, (s, i, lbl) in _LANDCOVER_AGB.items():
        match = lc.eq(code)
        slope_expr = ee.Number(ee.Algorithms.If(match, s, slope_expr))
        intercept_expr = ee.Number(ee.Algorithms.If(match, i, intercept_expr))
        label_expr = ee.String(ee.Algorithms.If(match, lbl, label_expr))

    agb = ndvi.multiply(slope_expr).add(intercept_expr).max(0)
    return feature.set("AGB", agb).set("label", label_expr)


def _apply_scale_factors(image):
    # Landsat C2 L2 surface reflectance: DN x 2.75e-5 - 0.2 (catalog).
    optical = image.select("SR_B.").multiply(0.0000275).add(-0.2)
    return image.addBands(optical, None, True)


def fetch_sentinel_imagery_ndvi(coords_input, start_date, end_date,
                                max_cloud_pct=MAX_CLOUD_PCT) -> float:
    """Above-ground biomass as a CO2-equivalent stock: kg CO2 for the whole ROI."""
    ensure_initialized()
    roi = _to_roi(coords_input)

    collection = (ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
                  .filterBounds(roi)
                  .filterDate(start_date, end_date)
                  .filter(ee.Filter.lt("CLOUD_COVER", max_cloud_pct))
                  .map(_apply_scale_factors))

    try:
        if _is_empty(collection):
            log.info("No Landsat imagery for %s", start_date)
            return 0.0

        image = collection.median().select(["SR_B4", "SR_B5"])
        nir, red = image.select("SR_B5"), image.select("SR_B4")
        ndvi = nir.subtract(red).divide(nir.add(red)).rename("NDVI")

        points = ndvi.sample(region=roi, scale=NDVI_SCALE,
                             numPixels=SAMPLE_POINTS, geometries=True).limit(SAMPLE_POINTS)

        esa = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map")
        sampled = esa.sampleRegions(collection=points, properties=["NDVI"],
                                    scale=10, geometries=True)

        features = sampled.map(add_agb).getInfo().get("features", [])
        if not features:
            return 0.0

        records = [{
            "NDVI": f["properties"]["NDVI"],
            "Land_Cover": f["properties"]["label"],
            # model unit -> kg biomass/m2 -> kg CO2/m2, converted exactly once
            "AGB_CO2_kg_m2": f["properties"]["AGB"] * AGB_MODEL_TO_KG_M2 * BIOMASS_TO_CO2,
        } for f in features]

        df = pd.DataFrame(records)
        df["AGB_CO2_kg_m2"] = df["AGB_CO2_kg_m2"].where(df["AGB_CO2_kg_m2"] > 0, np.nan)
        df = impute_with_xgb(df, ["NDVI", "Land_Cover"], "AGB_CO2_kg_m2", "AGB")

        mean_kg_m2 = _mean_or_none(df["AGB_Predicted"])
        return 0.0 if mean_kg_m2 is None else mean_kg_m2 * roi_area_m2(coords_input)

    except Exception as exc:
        log.error("NDVI/AGB processing failed for %s: %s", start_date, exc)
        return 0.0


# --- GPP --------------------------------------------------------------------

def fetch_gpp_data(coords_input, start_date, end_date) -> float:
    """GPP CO2 uptake over the window: kg CO2 for the whole ROI."""
    ensure_initialized()
    roi = _to_roi(coords_input)

    try:
        collection = (ee.ImageCollection("MODIS/061/MYD17A2H")
                      .filterBounds(roi)
                      .filterDate(start_date, end_date)
                      .select("Gpp"))
        if _is_empty(collection):
            log.info("No GPP imagery for %s", start_date)
            return 0.0

        # Mean 8-day total (kg C/m2) -> window total -> kg CO2/m2.
        periods = _window_days(start_date, end_date) / MODIS_GPP_PERIOD_DAYS
        image = (collection.mean()
                 .multiply(MODIS_GPP_SCALE * periods * CO2_CONVERSION)
                 .rename("GPP_CO2_kg_m2")
                 .clip(roi)
                 .reproject(crs="EPSG:4326", scale=GPP_SCALE))

        rows = _sample_image(image, roi, GPP_SCALE)
        if not rows:
            return 0.0

        df = impute_with_xgb(pd.DataFrame(rows), ["lat", "lon"], "GPP_CO2_kg_m2", "GPP_CO2")
        mean_kg_m2 = _mean_or_none(df["GPP_CO2_Predicted"])
        return 0.0 if mean_kg_m2 is None else mean_kg_m2 * roi_area_m2(coords_input)

    except Exception as exc:
        log.error("GPP processing failed for %s: %s", start_date, exc)
        return 0.0


# --- UVAI -------------------------------------------------------------------

def get_uvai_estimate(coords_input, start_date, end_date) -> float:
    """ROI-mean absorbing aerosol index (dimensionless)."""
    ensure_initialized()
    roi = _to_roi(coords_input)

    try:
        collection = (ee.ImageCollection("COPERNICUS/S5P/OFFL/L3_AER_AI")
                      .filterDate(start_date, end_date)
                      .filterBounds(roi)
                      .select("absorbing_aerosol_index"))
        if _is_empty(collection):
            log.info("No UVAI imagery for %s", start_date)
            return 0.0

        rows = _sample_image(collection.median().clip(roi), roi, UVAI_SCALE)
        if not rows:
            return 0.0

        df = pd.DataFrame(rows).rename(columns={"absorbing_aerosol_index": "UVAI"})
        df = impute_with_xgb(df, ["lat", "lon"], "UVAI", "UVAI")
        mean = _mean_or_none(df["UVAI_Predicted"])
        return 0.0 if mean is None else mean

    except Exception as exc:
        log.error("UVAI processing failed for %s: %s", start_date, exc)
        return 0.0


# --- Weather ----------------------------------------------------------------

def fetch_weather(coords_input, start_date, end_date) -> dict:
    """ROI-mean air temperature (deg C) and total precipitation (mm) for the window."""
    ensure_initialized()
    roi = _to_roi(coords_input)
    empty = {"temperature_c": None, "precipitation_mm": None}

    try:
        runs = ee.ImageCollection(ECMWF).filterDate(start_date, end_date).filterBounds(roi)
        temp_col = (runs.filter(ee.Filter.inList("forecast_hours", ECMWF_TEMP_STEPS))
                    .select("temperature_2m_sfc"))
        precip_col = (runs.filter(ee.Filter.eq("forecast_hours", ECMWF_PRECIP_STEP))
                      .select("total_precipitation_sfc"))

        n_temp, n_precip = ee.List([temp_col.size(), precip_col.size()]).getInfo()
        if not n_temp or not n_precip:
            # The NRT forecast archive only starts on 2024-11-12.
            log.info("No ECMWF imagery for %s", start_date)
            return empty

        # One hour-12 image per 12 h, so the mean 12-h accumulation times the
        # number of 12-h periods is the window total (robust to missing runs).
        periods = _window_days(start_date, end_date) * 24 / ECMWF_RUN_INTERVAL_H
        image = (temp_col.mean().rename("Temp_C")   # already deg C -- no conversion
                 .addBands(precip_col.mean().multiply(periods * 1000).rename("Precip_mm"))
                 .clip(roi))

        rows = _sample_image(image, roi, WEATHER_SCALE)
        if not rows:
            return empty

        df = pd.DataFrame(rows)
        df = impute_with_xgb(df, ["lat", "lon"], "Temp_C", "Temp")
        df = impute_with_xgb(df, ["lat", "lon"], "Precip_mm", "Precip")

        return {
            "temperature_c": _mean_or_none(df["Temp_Predicted"]),
            "precipitation_mm": _mean_or_none(df["Precip_Predicted"]),
        }

    except Exception as exc:
        log.error("Weather processing failed for %s: %s", start_date, exc)
        return empty
