import logging

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBRegressor

log = logging.getLogger(__name__)


def impute_with_xgb(data: pd.DataFrame, features_name: list, target_name: str,
                    calc_name: str) -> pd.DataFrame:
    """Fill missing values in ``target_name`` with XGBoost predictions.

    Adds a ``{calc_name}_Predicted`` column holding observed values where
    present and model predictions where the target was NaN.

    Args:
        data: frame containing the features and target.
        features_name: [numeric_feature, second_feature]; the second is label
            encoded when it is categorical.
        target_name: column to impute.
        calc_name: prefix for the output column.
    """
    df = data.copy()
    predicted_col = f"{calc_name}_Predicted"

    if target_name not in df.columns or df.empty:
        df[predicted_col] = np.nan
        return df

    missing = df[target_name].isnull()

    # Fast path: nothing to impute.
    if not missing.any():
        df[predicted_col] = df[target_name]
        return df

    # Degenerate path: no training rows, so nothing can be learned.
    if missing.all():
        log.warning("All %s values missing; cannot impute.", target_name)
        df[predicted_col] = np.nan
        return df

    numeric_feat, second_feat = features_name[0], features_name[1]
    encoded_name = f"{second_feat}_encoded"

    # Test for numeric rather than `dtype == "object"`: pandas 3 gives string
    # columns the `str` dtype, so the old check missed them and handed raw
    # strings to XGBoost.
    if pd.api.types.is_numeric_dtype(df[second_feat]):
        df[encoded_name] = df[second_feat]
    else:
        df[encoded_name] = LabelEncoder().fit_transform(df[second_feat].astype(str))

    features = [numeric_feat, encoded_name]

    # Start from the observed values, then overwrite the gaps.
    df[predicted_col] = df[target_name]

    try:
        model = XGBRegressor(
            # Tuned for the ~300-row samples this actually sees; the previous
            # 300 trees at depth 6 was heavy overfit and far slower.
            n_estimators=150,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=2,
            verbosity=0,
        )
        model.fit(df.loc[~missing, features], df.loc[~missing, target_name])
        df.loc[missing, predicted_col] = model.predict(df.loc[missing, features])
    except Exception as exc:
        log.error("XGBoost imputation failed for %s: %s", target_name, exc)

    return df.drop(columns=[encoded_name])
