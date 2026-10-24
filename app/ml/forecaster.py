"""Surplus forecasting: RandomForest on calendar, weather and footfall features.

The target is surplus per 100 diners (scaled back by footfall at inference), so one model
generalises across restaurants of very different sizes - including ones smaller than any it saw.

Explanations are counterfactual: for each driver we re-predict with that driver reset to a
neutral baseline (clear 28 °C mid-week, no holiday/festival) and report the difference.
"""
import logging
import threading
from datetime import date, datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from app.config import MODEL_PATH
from app.ml.calendar_data import WEATHER_CONDITIONS
from app.ml.synthetic import CATEGORIES

log = logging.getLogger("foodforall.ml")

CATEGORICAL = ["weather_condition", "dish_category"]
NUMERIC = ["day_of_week", "is_weekend", "temperature", "is_holiday", "is_festival",
           "historical_avg_footfall"]
FEATURES = CATEGORICAL + NUMERIC

BASELINE = {"weather_condition": "Clear", "temperature": 28.0, "is_holiday": 0, "is_festival": 0}
BASELINE_WEEKDAY = 2  # Wednesday
VALIDATION_DAYS = 14
ZERO_SURPLUS_THRESHOLD = 3  # servings
MIN_SUPPORT = 5  # nights of a weather condition needed before we trust predictions for it


def build_frame(rows: list[dict]) -> pd.DataFrame:
    """Turn raw records (with a `date` key) into the model's feature frame."""
    df = pd.DataFrame(rows)
    d = pd.to_datetime(df["date"])
    df["day_of_week"] = d.dt.dayofweek
    df["is_weekend"] = (df["day_of_week"] >= 4).astype(int)
    df["is_holiday"] = df["is_holiday"].astype(int)
    df["is_festival"] = df["is_festival"].astype(int)
    return df


def _new_pipeline() -> Pipeline:
    pre = ColumnTransformer([
        ("cat", OneHotEncoder(categories=[WEATHER_CONDITIONS, CATEGORIES], handle_unknown="ignore"),
         CATEGORICAL),
        ("num", "passthrough", NUMERIC),
    ])
    rf = RandomForestRegressor(n_estimators=200, min_samples_leaf=3, random_state=42, n_jobs=-1)
    return Pipeline([("pre", pre), ("rf", rf)])


class SurplusForecaster:
    def __init__(self):
        self.pipeline: Pipeline | None = None
        self.meta: dict = {}
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.pipeline is not None

    # ---------- training ----------
    def train(self, records: list[dict]) -> dict:
        if len(records) < 50:
            raise ValueError(f"Need at least 50 history rows to train, got {len(records)}")
        df = build_frame(records).sort_values("date")
        scale = df["historical_avg_footfall"].clip(lower=1) / 100
        y = df["surplus_servings"].astype(float) / scale

        # Chronological hold-out: train on the past, validate on the most recent fortnight.
        cutoff = pd.to_datetime(df["date"]).max() - pd.Timedelta(days=VALIDATION_DAYS)
        is_val = pd.to_datetime(df["date"]) > cutoff
        metrics = {}
        if is_val.sum() >= 10 and (~is_val).sum() >= 50:
            p = _new_pipeline().fit(df.loc[~is_val, FEATURES], y[~is_val])
            # Metrics in servings, the unit operators care about.
            actual = df.loc[is_val, "surplus_servings"]
            pred = p.predict(df.loc[is_val, FEATURES]) * scale[is_val]
            naive = (y[~is_val]).groupby(df.loc[~is_val, "dish_category"]).mean()
            naive_pred = df.loc[is_val, "dish_category"].map(naive).fillna(y[~is_val].mean()) * scale[is_val]
            metrics = {
                "validation_days": VALIDATION_DAYS,
                "mae": round(float(mean_absolute_error(actual, pred)), 2),
                "r2": round(float(r2_score(actual, pred)), 3),
                "baseline_mae": round(float(mean_absolute_error(actual, naive_pred)), 2),
                "baseline": "per-category historical mean",
            }

        pipeline = _new_pipeline().fit(df[FEATURES], y)
        meta = {
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "rows": int(len(df)),
            "date_range": [str(df["date"].min()), str(df["date"].max())],
            "metrics": metrics,
            "feature_importance": self._grouped_importance(pipeline),
            "weather_support": {w: int((df.drop_duplicates("date")["weather_condition"] == w).sum())
                                for w in WEATHER_CONDITIONS},
        }
        with self._lock:
            self.pipeline, self.meta = pipeline, meta
        joblib.dump({"pipeline": pipeline, "meta": meta}, MODEL_PATH)
        log.info("Surplus model trained on %s rows: %s", meta["rows"], metrics)
        return meta

    def load(self) -> bool:
        if not MODEL_PATH.exists():
            return False
        try:
            blob = joblib.load(MODEL_PATH)
        except Exception as exc:  # stale pickle from another sklearn version etc.
            log.warning("Could not load saved model (%s); will retrain", exc)
            return False
        with self._lock:
            self.pipeline, self.meta = blob["pipeline"], blob["meta"]
        return True

    @staticmethod
    def _grouped_importance(pipeline: Pipeline) -> list[dict]:
        names = pipeline.named_steps["pre"].get_feature_names_out()
        imps = pipeline.named_steps["rf"].feature_importances_
        grouped: dict[str, float] = {}
        for name, imp in zip(names, imps):
            raw = name.split("__", 1)[1]
            key = next((c for c in CATEGORICAL if raw.startswith(c + "_")), raw)
            grouped[key] = grouped.get(key, 0.0) + float(imp)
        return sorted(({"feature": k, "importance": round(v, 4)} for k, v in grouped.items()),
                      key=lambda r: -r["importance"])

    # ---------- inference ----------
    def _predict_frame(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Mean prediction and per-tree spread (std) for each row, in servings."""
        with self._lock:
            pipeline = self.pipeline
        if pipeline is None:
            raise RuntimeError("Surplus model is not trained yet")
        X = pipeline.named_steps["pre"].transform(df[FEATURES])
        scale = (df["historical_avg_footfall"].clip(lower=1) / 100).to_numpy()
        trees = np.stack([t.predict(X) for t in pipeline.named_steps["rf"].estimators_]) * scale
        return np.clip(trees.mean(axis=0), 0, None), trees.std(axis=0)

    def predict(self, *, on: date, weather_condition: str, temperature: float, is_holiday: bool,
                is_festival: bool, historical_avg_footfall: int,
                categories: list[str] | None = None) -> dict:
        categories = categories or CATEGORIES
        base = {"date": on, "weather_condition": weather_condition, "temperature": temperature,
                "is_holiday": is_holiday, "is_festival": is_festival,
                "historical_avg_footfall": historical_avg_footfall}
        df = build_frame([{**base, "dish_category": c} for c in categories])
        mean, std = self._predict_frame(df)
        total = float(mean.sum())
        total_std = float(np.sqrt((std ** 2).sum()))

        breakdown = [{"dish_category": c, "predicted_servings": int(round(m)),
                      "low": int(max(0, round(m - 1.28 * s))), "high": int(round(m + 1.28 * s))}
                     for c, m, s in zip(categories, mean, std)]

        zero = total < ZERO_SURPLUS_THRESHOLD
        margin = 0.0 if zero else min(40.0, max(10.0, 100 * total_std / max(total, 1)))
        prenotify = 0 if zero else int(total * (1 - margin / 100))
        warnings = []
        seen = self.meta.get("weather_support", {}).get(weather_condition)
        if seen is not None and seen < MIN_SUPPORT:
            warnings.append(f"Low confidence: the model has seen only {seen} '{weather_condition}' nights "
                            f"in its training history, so this condition's effect is not learned yet.")
        return {
            "predicted_surplus_servings": int(round(total)),
            "interval_80": [int(max(0, round(total - 1.28 * total_std))), int(round(total + 1.28 * total_std))],
            "ngo_prenotification_qty": prenotify,
            "safety_margin_pct": round(margin, 1),
            "zero_surplus": zero,
            "message": ("No meaningful surplus expected tonight - NGOs will not be pre-notified."
                        if zero else
                        f"Pre-notify NGOs for {prenotify} servings (holding back a {margin:.0f}% safety buffer)."),
            "breakdown": breakdown,
            "drivers": self._explain(df, total),
            "warnings": warnings,
        }

    def _explain(self, df: pd.DataFrame, total: float) -> list[dict]:
        row = df.iloc[0]
        drivers = []

        def counterfactual(label: str, **changes):
            alt = df.copy()
            for k, v in changes.items():
                alt[k] = v
            alt_total = float(self._predict_frame(alt)[0].sum())
            delta = total - alt_total
            pct = 100 * delta / alt_total if alt_total > 0 else 0.0
            if abs(delta) >= 0.5:
                sign = "+" if delta >= 0 else ""
                drivers.append({"factor": label, "delta_servings": round(delta, 1), "pct": round(pct, 1),
                                "text": f"{label} ({sign}{pct:.0f}% surplus expected)"})

        if row["weather_condition"] != "Clear":
            counterfactual(row["weather_condition"], weather_condition="Clear")
        if abs(row["temperature"] - BASELINE["temperature"]) >= 4:
            counterfactual(f"{row['temperature']:.0f}°C temperature", temperature=BASELINE["temperature"])
        if row["is_festival"]:
            counterfactual("Festival over-prep", is_festival=0)
        if row["is_holiday"]:
            counterfactual("Public holiday", is_holiday=0)
        if row["day_of_week"] != BASELINE_WEEKDAY:
            day = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][row["day_of_week"]]
            counterfactual(f"{day} pattern", day_of_week=BASELINE_WEEKDAY,
                           is_weekend=int(BASELINE_WEEKDAY >= 4))
        return sorted(drivers, key=lambda d: -abs(d["delta_servings"]))


forecaster = SurplusForecaster()
