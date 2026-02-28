# ============================================================
# uncertainty.py
# Compute conformal prediction intervals
# ============================================================

import numpy as np

def conformal_interval(y_true, y_pred, alpha=0.10):
    """
    Computes simple conformal prediction intervals.
    
    Parameters:
    ----------
    y_true : array-like
        True labels
    y_pred : array-like
        Predicted labels
    alpha : float
        Significance level (1-alpha = coverage)
    
    Returns:
    -------
    lower_bound : array-like
    upper_bound : array-like
    coverage : float
        Fraction of true values within the interval
    """
    residuals = np.abs(y_true - y_pred)
    q_hat = np.quantile(residuals, 1 - alpha)
    lower_bound = y_pred - q_hat
    upper_bound = y_pred + q_hat
    coverage = np.mean((y_true >= lower_bound) & (y_true <= upper_bound))
    return lower_bound, upper_bound, coverage