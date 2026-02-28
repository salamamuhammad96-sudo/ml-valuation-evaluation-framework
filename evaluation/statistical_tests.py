# ============================================================
# statistical_tests.py
# Perform paired t-tests and bootstrap confidence intervals
# ============================================================

import numpy as np
from scipy import stats

def paired_t_test(errors_champion, errors_challenger):
    """
    Performs paired t-test between champion and challenger model errors.

    Returns:
    -------
    p_value : float
        p-value of the test.
    """
    t_stat, p_value = stats.ttest_rel(errors_champion, errors_challenger)
    return p_value

def bootstrap_ci(y_true, y_pred, metric_func, n_resamples=1000, alpha=0.05):
    """
    Computes bootstrap confidence interval for a given metric.

    Parameters:
    ----------
    y_true : array-like
    y_pred : array-like
    metric_func : function
        Function to compute metric (e.g., MAE)
    n_resamples : int
        Number of bootstrap resamples
    alpha : float
        Confidence level
    
    Returns:
    -------
    mean_metric : float
    lower_bound : float
    upper_bound : float
    """
    metrics = []
    n = len(y_true)
    for _ in range(n_resamples):
        idx = np.random.choice(range(n), size=n, replace=True)
        metric_val = metric_func(y_true[idx], y_pred[idx])
        metrics.append(metric_val)
    metrics = np.array(metrics)
    lower = np.percentile(metrics, 100 * (alpha / 2))
    upper = np.percentile(metrics, 100 * (1 - alpha / 2))
    mean_metric = np.mean(metrics)
    return mean_metric, lower, upper