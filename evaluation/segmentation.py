# ============================================================
# segmentation.py
# Analyze model performance across segments
# ============================================================

import pandas as pd
from sklearn.metrics import mean_absolute_error

def segment_evaluation(data, segment_column, y_true_col="true_value",
                       champion_col="pred_champion", challenger_col="pred_challenger"):
    """
    Computes MAE per segment for Champion and Challenger models.
    
    Parameters:
    ----------
    data : pd.DataFrame
    segment_column : str
        Column to group by (e.g., asset type)
    y_true_col : str
    champion_col : str
    challenger_col : str
    
    Returns:
    -------
    pd.DataFrame : Segment-level MAE
    """
    segment_results = (
        data.groupby(segment_column)
        .apply(lambda df: pd.Series({
            "MAE_Champion": mean_absolute_error(df[y_true_col], df[champion_col]),
            "MAE_Challenger": mean_absolute_error(df[y_true_col], df[challenger_col])
        }))
        .reset_index()
        .rename(columns={segment_column: "Segment"})
    )
    return segment_results