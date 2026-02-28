# ============================================================
# metrics.py
# Compute MAE, RMSE, MAPE, WMAPE, R2 for regression evaluation
# ============================================================

import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

def compute_metrics(y_true, y_pred):
    """
    Computes standard regression metrics.
    
    Parameters:
    ----------
    y_true : array-like
        Ground truth labels.
    y_pred : array-like
        Model predictions.
    
    Returns:
    -------
    tuple : (MAE, RMSE, MAPE, WMAPE, R2)
    """
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    mape = np.mean(np.abs((y_true - y_pred) / y_true)) * 100
    wmape = np.sum(np.abs(y_true - y_pred)) / np.sum(np.abs(y_true)) * 100
    r2 = r2_score(y_true, y_pred)
    
    return mae, rmse, mape, wmape, r2