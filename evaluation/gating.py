# ============================================================
# gating.py
# Final deployment decision logic
# ============================================================

def deployment_decision(p_value, metrics_champion, metrics_challenger, threshold=0.05):
    """
    Determines whether Challenger model is safe to deploy.

    Parameters:
    ----------
    p_value : float
        p-value from paired t-test
    metrics_champion : tuple
        Metrics of champion model (MAE,...)
    metrics_challenger : tuple
        Metrics of challenger model (MAE,...)
    threshold : float
        Significance threshold for p-value
    
    Returns:
    -------
    str : "GO" or "NO-GO"
    """
    if p_value < threshold and metrics_challenger[0] < metrics_champion[0]:
        return "GO - Challenger is statistically better."
    else:
        return "NO-GO - Challenger needs improvement."