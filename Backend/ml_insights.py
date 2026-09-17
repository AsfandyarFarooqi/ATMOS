"""Machine-learning insights over the monthly series returned by /get_data.

Every method is chosen for very short series (4-60 monthly points) and reports
how far to trust it, so a four-point fit is never presented as a finding.

  trends     Theil-Sen slope + Kendall tau test (robust to outliers)
  anomalies  Isolation Forest, confirmed by a modified z-score that also names
             the variable that made the month unusual
  drivers    ridge regression of GPP on climate, scored by leave-one-out R2
  forecast   Gaussian-process regression with a 95% predictive interval
  regimes    k-means climate regimes, kept only if the silhouette says they exist
"""
import logging
import math
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel
from sklearn.linear_model import RidgeCV
from sklearn.metrics import silhouette_score
from sklearn.model_selection import LeaveOneOut, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import RobustScaler, StandardScaler

log = logging.getLogger(__name__)

# column -> (label, unit)
METRICS = {
    "GPP_CO2_kg": ("GPP uptake", "kg CO₂"),
    "AGB_CO2_kg": ("Biomass stock", "kg CO₂"),
    "Temperature_C": ("Temperature", "°C"),
    "Precipitation_mm": ("Precipitation", "mm"),
    "UVAI_index": ("Aerosol index", ""),
}
CLIMATE = ["Temperature_C", "Precipitation_mm", "UVAI_index"]
# Ratio-scale metrics: a %-change is meaningful and values can't go below 0.
# (Not temperature in C, nor the aerosol index, which can be negative.)
RATIO_SCALE = {"GPP_CO2_kg", "AGB_CO2_kg", "Precipitation_mm"}

MIN_TREND, MIN_ANOMALY, MIN_DRIVERS, MIN_FORECAST, MIN_REGIMES = 4, 5, 6, 4, 6
MODZ_THRESHOLD = 3.5  # Iglewicz & Hoaglin cut-off for the modified z-score


# --- helpers ----------------------------------------------------------------

def _num(x):
    """JSON-safe float: NaN and inf become None (jsonify would emit bare NaN)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _compact(x, unit=""):
    if x is None:
        return "—"
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(x) >= div:
            text = f"{x / div:.2f}".rstrip("0").rstrip(".") + suffix
            break
    else:
        text = f"{x:.3g}"
    return f"{text} {unit}".strip()


def _frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty or "month" not in df:
        return pd.DataFrame()
    df = df.sort_values("month").reset_index(drop=True)
    for col in METRICS:
        df[col] = pd.to_numeric(df[col], errors="coerce") if col in df else np.nan
    return df


def _modified_z(values: np.ndarray) -> np.ndarray:
    """0.6745 * (x - median) / MAD. Robust: one extreme month can't mask itself."""
    med = np.nanmedian(values)
    mad = np.nanmedian(np.abs(values - med))
    if not mad or not math.isfinite(mad):
        return np.zeros_like(values, dtype=float)
    return 0.6745 * (values - med) / mad


def _next_months(last_month: str, count: int) -> list[str]:
    d = datetime.strptime(last_month, "%Y-%m")
    out = []
    for _ in range(count):
        d = datetime(d.year + (d.month == 12), d.month % 12 + 1, 1)
        out.append(d.strftime("%Y-%m"))
    return out


# --- trends -----------------------------------------------------------------

def trends(df: pd.DataFrame) -> list[dict]:
    out = []
    t_all = np.arange(len(df), dtype=float)
    for col, (label, unit) in METRICS.items():
        mask = df[col].notna().to_numpy()
        y, t = df.loc[mask, col].to_numpy(dtype=float), t_all[mask]
        if len(y) < MIN_TREND or np.ptp(y) == 0:
            continue

        slope = stats.theilslopes(y, t)[0]
        tau, p = stats.kendalltau(t, y)
        base = abs(float(np.median(y)))
        pct = slope / base * 100 if col in RATIO_SCALE and base > 0 else None
        significant = bool(p is not None and p < 0.05)

        out.append({
            "metric": col, "label": label, "unit": unit, "n": int(len(y)),
            "slope_per_month": _num(slope), "pct_per_month": _num(pct),
            "kendall_tau": _num(tau), "p_value": _num(p),
            "significant": significant,
            "direction": ("rising" if slope > 0 else "falling") if significant else "no clear trend",
        })
    return out


# --- anomalies --------------------------------------------------------------

def anomalies(df: pd.DataFrame):
    cols = [c for c in ("GPP_CO2_kg", *CLIMATE) if df[c].notna().sum() >= MIN_ANOMALY]
    if len(df) < MIN_ANOMALY or not cols:
        return None

    complete = df[cols].dropna()
    z = {c: _modified_z(df[c].to_numpy(dtype=float)) for c in cols}

    # Short series: the modified z-score alone, at its strict textbook cut-off.
    # Longer ones: Isolation Forest proposes candidates (it sees combinations,
    # e.g. hot AND dry), and a looser z cut-off confirms and explains them.
    candidate = pd.Series(True, index=df.index)
    method, threshold = "modified z-score", MODZ_THRESHOLD
    if len(complete) >= 8:
        X = RobustScaler().fit_transform(complete)
        forest = IsolationForest(n_estimators=300, contamination=0.15, random_state=42).fit(X)
        candidate = pd.Series(False, index=df.index)
        candidate.loc[complete.index] = forest.predict(X) == -1
        method, threshold = "Isolation Forest + modified z-score", 2.5

    months = []
    for i, row in df.iterrows():
        if not candidate.loc[i]:
            continue
        scores = {c: z[c][i] for c in cols if math.isfinite(z[c][i])}
        if not scores:
            continue
        driver = max(scores, key=lambda c: abs(scores[c]))
        if abs(scores[driver]) < threshold:
            continue
        months.append({
            "month": row["month"], "metric": driver,
            "label": METRICS[driver][0], "unit": METRICS[driver][1],
            "value": _num(row[driver]), "modified_z": _num(scores[driver]),
            "direction": "high" if scores[driver] > 0 else "low",
        })
    return {"method": method, "threshold": threshold, "months": months}


# --- drivers ----------------------------------------------------------------

def drivers(df: pd.DataFrame):
    """Which climate variable moves GPP uptake? Standardised ridge effects."""
    cols = [c for c in CLIMATE if df[c].notna().sum() >= MIN_DRIVERS]
    data = df[["GPP_CO2_kg", *cols]].dropna()
    cols = [c for c in cols if len(data) and data[c].std() > 0]
    if not cols or len(data) < MIN_DRIVERS or data["GPP_CO2_kg"].std() == 0:
        return None

    X, y = data[cols].to_numpy(dtype=float), data["GPP_CO2_kg"].to_numpy(dtype=float)
    model = make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-2, 3, 30)))

    # Leave-one-out R2: every month is predicted by a model that never saw it,
    # so a model that merely memorises a short series scores near or below 0.
    pred = cross_val_predict(model, X, y, cv=LeaveOneOut())
    r2 = 1 - np.sum((y - pred) ** 2) / np.sum((y - y.mean()) ** 2)

    model.fit(X, y)
    effects = model[-1].coef_ / y.std()  # SD change in GPP per SD change in driver

    items = []
    for col, effect in zip(cols, effects):
        rho, p = stats.spearmanr(data[col], y)
        items.append({"metric": col, "label": METRICS[col][0], "effect_sd": _num(effect),
                      "spearman_rho": _num(rho), "spearman_p": _num(p)})
    items.sort(key=lambda d: abs(d["effect_sd"] or 0), reverse=True)

    # Drivers that move together can't be separated: ridge splits the credit
    # between them somewhat arbitrarily. Surface that instead of hiding it.
    # Pearson, not Spearman: linear dependence is what destabilises ridge
    # coefficients (two separated climate clusters can rank-correlate weakly
    # yet be almost perfectly linearly dependent).
    corr = data[cols].corr().abs()
    collinear = sorted({tuple(sorted((METRICS[a][0], METRICS[b][0])))
                        for a in cols for b in cols if a < b and corr.loc[a, b] >= 0.8})

    return {"target": "GPP_CO2_kg", "n": int(len(data)), "r2_loo": _num(r2),
            "reliability": "strong" if r2 >= 0.5 else "moderate" if r2 >= 0.2 else "weak",
            "drivers": items, "collinear": [list(pair) for pair in collinear]}


# --- forecast ---------------------------------------------------------------

def _fit_gpr(t: np.ndarray, y: np.ndarray) -> GaussianProcessRegressor:
    # Smooth signal + noise. With few points the RBF reverts to the mean when
    # extrapolating, and the interval widens -- a conservative, honest default.
    kernel = (ConstantKernel(1.0, (1e-2, 1e2))
              * RBF(length_scale=max(2.0, len(y) / 3), length_scale_bounds=(1.0, 60.0))
              + WhiteKernel(0.1, (1e-4, 1.0)))
    gpr = GaussianProcessRegressor(kernel=kernel, normalize_y=True,
                                   n_restarts_optimizer=3, random_state=42)
    return gpr.fit(t.reshape(-1, 1), y)


def forecast(df: pd.DataFrame, horizon: int = 3) -> dict:
    out = {}
    future_t = np.arange(len(df), len(df) + horizon, dtype=float).reshape(-1, 1)
    future_months = _next_months(df["month"].iloc[-1], horizon)

    for col in ("GPP_CO2_kg", "Temperature_C", "Precipitation_mm"):
        series = df[["month", col]].dropna()
        if len(series) < MIN_FORECAST or series[col].std() == 0:
            continue
        t = series.index.to_numpy(dtype=float)  # keeps true spacing across gaps
        y = series[col].to_numpy(dtype=float)
        floor = 0.0 if col in RATIO_SCALE else -np.inf

        mean, std = _fit_gpr(t, y).predict(future_t, return_std=True)
        lower, upper = np.maximum(mean - 1.96 * std, floor), mean + 1.96 * std
        mean = np.maximum(mean, floor)

        # Backtest: hide the last month, forecast it, check the 95% interval.
        backtest = None
        if len(y) >= 6:
            m1, s1 = _fit_gpr(t[:-1], y[:-1]).predict(t[-1:].reshape(-1, 1), return_std=True)
            err = abs(m1[0] - y[-1])
            backtest = {"month": series["month"].iloc[-1], "actual": _num(y[-1]),
                        "predicted": _num(m1[0]),
                        "error_pct": _num(err / abs(y[-1]) * 100) if col in RATIO_SCALE and y[-1] else None,
                        "error_abs": _num(err),
                        "within_interval": bool(err <= 1.96 * s1[0])}

        out[col] = {
            "label": METRICS[col][0], "unit": METRICS[col][1],
            "history": [{"month": m, "value": _num(v)} for m, v in zip(series["month"], y)],
            "forecast": [{"month": m, "mean": _num(a), "lower": _num(lo), "upper": _num(hi)}
                         for m, a, lo, hi in zip(future_months, mean, lower, upper)],
            "backtest": backtest,
        }
    return out


# --- climate regimes --------------------------------------------------------

def regimes(df: pd.DataFrame):
    data = df.dropna(subset=["Temperature_C", "Precipitation_mm"])
    if len(data) < MIN_REGIMES:
        return None
    raw = data[["Temperature_C", "Precipitation_mm"]]
    if (raw.std() == 0).any():
        return {"found": False, "silhouette": None}
    X = StandardScaler().fit_transform(raw)

    best = None
    for k in (2, 3):
        if len(data) < 3 * k:
            continue
        labels = KMeans(n_clusters=k, n_init=20, random_state=42).fit_predict(X)
        if len(set(labels)) == k:
            score = silhouette_score(X, labels)
            if best is None or score > best[0]:
                best = (score, k, labels)

    # Below 0.25 the "clusters" are an artefact of forcing k, not real regimes.
    if best is None or best[0] < 0.25:
        return {"found": False, "silhouette": _num(best[0]) if best else None}
    score, k, labels = best

    # Re-number coolest -> warmest so a regime's colour is stable between runs.
    temps = [raw["Temperature_C"][labels == g].mean() for g in range(k)]
    rank = {g: r for r, g in enumerate(np.argsort(temps))}
    labels = np.array([rank[g] for g in labels])

    t_med, p_med = raw["Temperature_C"].median(), raw["Precipitation_mm"].median()
    groups = []
    for g in range(k):
        part = data[labels == g]
        mt, mp = part["Temperature_C"].mean(), part["Precipitation_mm"].mean()
        groups.append({"id": g, "label": f"{'Warm' if mt >= t_med else 'Cool'} & {'wet' if mp >= p_med else 'dry'}",
                       "months": part["month"].tolist(), "mean_temperature_c": _num(mt),
                       "mean_precipitation_mm": _num(mp), "mean_gpp_kg": _num(part["GPP_CO2_kg"].mean())})

    # Two regimes can land on the same median-based label; tell them apart.
    for label in {g["label"] for g in groups}:
        same = sorted((g for g in groups if g["label"] == label), key=lambda g: -g["mean_temperature_c"])
        if len(same) > 1:
            for g, word in zip(same, ("hotter", "milder", "mildest")):
                g["label"] += f" · {word}"

    points = [{"month": m, "temperature_c": _num(t), "precipitation_mm": _num(p), "regime": int(r)}
              for m, t, p, r in zip(data["month"], raw["Temperature_C"], raw["Precipitation_mm"], labels)]
    return {"found": True, "k": k, "silhouette": _num(score), "groups": groups, "points": points}


# --- plain-language findings ------------------------------------------------

def _p(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.2g}"


def _findings(n, tr, an, dr, fc, rg) -> list[dict]:
    items = []

    def add(kind, title, text, confidence):
        items.append({"kind": kind, "title": title, "text": text, "confidence": confidence})

    if n < MIN_DRIVERS:
        add("data", "Short series",
            f"Only {n} month{'' if n == 1 else 's'} of data. Drivers and climate regimes need at least "
            f"{MIN_DRIVERS} months; widen the date range for stronger results.", "low")

    gpp = fc.get("GPP_CO2_kg")
    if gpp:
        nxt, bt = gpp["forecast"][0], gpp["backtest"]
        width = (nxt["upper"] - nxt["lower"]) / nxt["mean"] if nxt["mean"] else math.inf
        conf = ("high" if width < 0.3 and (bt is None or bt["within_interval"])
                else "medium" if width < 0.8 else "low")
        extra = (f" Held-out check: the model's forecast for {bt['month']} was off by {bt['error_pct']:.0f}%."
                 if bt and bt["error_pct"] is not None else "")
        add("forecast", f"Forecast for {nxt['month']}",
            f"GPP uptake is forecast at {_compact(nxt['mean'], 'kg CO₂')} "
            f"(95% interval {_compact(nxt['lower'])}–{_compact(nxt['upper'])}; Gaussian process).{extra}", conf)

    for t in tr:
        if not t["significant"]:
            continue
        rate = (f"{abs(t['pct_per_month']):.1f}% per month" if t["pct_per_month"] is not None
                else f"{abs(t['slope_per_month']):.2g} {t['unit']} per month".replace("  ", " "))
        add("trend", f"{t['label']} {t['direction']}",
            f"{t['label']} is {t['direction']} by about {rate} "
            f"(Theil–Sen slope; Kendall {_p(t['p_value'])}, n = {t['n']}).",
            "high" if t["p_value"] < 0.01 else "medium")
    if tr and not any(t["significant"] for t in tr):
        add("trend", "No clear trends",
            f"No metric shows a statistically clear trend over {n} months (Kendall test, p ≥ 0.05).", "low")

    if an:
        for a in sorted(an["months"], key=lambda a: -abs(a["modified_z"]))[:3]:
            add("anomaly", f"{a['month']} stands out",
                f"{a['label']} was unusually {a['direction']} ({_compact(a['value'], a['unit'])}, "
                f"modified z = {a['modified_z']:+.1f}; {an['method']}).",
                "high" if abs(a["modified_z"]) >= 5 else "medium")

    if dr:
        top = dr["drivers"][0]
        if dr["reliability"] == "weak":
            add("driver", "No reliable climate driver",
                "Temperature, precipitation and aerosols explain little of the month-to-month change in "
                f"GPP uptake (leave-one-out R² = {dr['r2_loo']:.2f}, n = {dr['n']}).", "low")
        else:
            verb = "raises" if top["effect_sd"] > 0 else "lowers"
            tangled = [pair for pair in dr["collinear"] if top["label"] in pair]
            caveat = (f" {' and '.join(tangled[0])} move together in this period, so their separate "
                      "effects are uncertain." if tangled else "")
            add("driver", f"{top['label']} drives uptake",
                f"{top['label']} is the strongest driver: one standard deviation more {verb} GPP uptake "
                f"by {abs(top['effect_sd']):.2f} SD (ridge regression, leave-one-out R² = "
                f"{dr['r2_loo']:.2f}, n = {dr['n']}).{caveat}",
                "high" if dr["reliability"] == "strong" and not tangled else "medium")

    if rg and rg.get("found"):
        text = f"{rg['k']} distinct climate regimes (k-means, silhouette {rg['silhouette']:.2f})."
        with_gpp = [g for g in rg["groups"] if g["mean_gpp_kg"]]
        if len(with_gpp) >= 2:
            hi = max(with_gpp, key=lambda g: g["mean_gpp_kg"])
            lo = min(with_gpp, key=lambda g: g["mean_gpp_kg"])
            if lo["mean_gpp_kg"] > 0:
                text += (f" {hi['label']} months take up {hi['mean_gpp_kg'] / lo['mean_gpp_kg']:.1f}× "
                         f"more CO₂ than {lo['label'].lower()} months.")
        add("regime", "Climate regimes", text, "high" if rg["silhouette"] >= 0.5 else "medium")

    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(items, key=lambda f: rank[f["confidence"]])


# --- entry point ------------------------------------------------------------

def analyze(rows) -> dict:
    df = _frame(rows)
    n = len(df)
    result = {"n_months": n, "findings": [], "trends": [], "anomalies": None,
              "drivers": None, "forecasts": {}, "regimes": None, "skipped": []}
    if n == 0:
        result["skipped"].append({"analysis": "all", "reason": "no monthly data"})
        return result

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # optimiser chatter on tiny series
        tr, an, dr, fc, rg = trends(df), anomalies(df), drivers(df), forecast(df), regimes(df)

    skipped = result["skipped"]
    if not tr:
        skipped.append({"analysis": "trends", "reason": f"needs ≥ {MIN_TREND} months with changing values"})
    if an is None:
        skipped.append({"analysis": "anomalies", "reason": f"needs ≥ {MIN_ANOMALY} months"})
    if dr is None:
        skipped.append({"analysis": "drivers", "reason": f"needs ≥ {MIN_DRIVERS} complete months"})
    if not fc:
        skipped.append({"analysis": "forecast", "reason": f"needs ≥ {MIN_FORECAST} months"})
    if rg is None:
        skipped.append({"analysis": "regimes", "reason": f"needs ≥ {MIN_REGIMES} months of climate data"})
    elif not rg.get("found"):
        skipped.append({"analysis": "regimes", "reason": "no distinct regimes (silhouette < 0.25)"})

    result.update(findings=_findings(n, tr, an, dr, fc, rg), trends=tr, anomalies=an,
                  drivers=dr, forecasts=fc, regimes=rg)
    return result
