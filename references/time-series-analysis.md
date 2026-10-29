# Time series analysis and forecasting

Core sources: [@hyndman_fpp3] (forecasting workflow, decomposition, ETS/ARIMA, evaluation, time series cross-validation), [@box_jenkins] (ARIMA identification, estimation, diagnostics), [@bergmeir_hyndman_koo2018] (when K-fold is valid for autoregressive models).

## EDA for temporal data (on the authorised sample: sample whole series or contiguous periods, never random rows)

- Frequency, timestamps, time zones, irregular spacing, gaps, duplicates per timestamp.
- Trend, seasonality (possibly multiple), calendar effects, holidays.
- Autocorrelation (ACF/PACF), stationarity (visual first; ADF/KPSS as supporting evidence, with their opposite null hypotheses).
- Structural breaks, regime changes, level shifts, outliers vs real events (ask the user).
- Which covariates are known in advance at forecast time (leakage check).

## Validation

Simulate real use: rolling or expanding origin, forecasts for each horizon from each origin, with a gap if data arrive late [@hyndman_fpp3]. Random K-fold that lets the future inform the past is invalid for general time series; the exception analysed in [@bergmeir_hyndman_koo2018] (purely autoregressive models with uncorrelated errors) must be argued, not assumed.

## Models (proportionate)

Benchmarks first: naive, seasonal naive, drift, mean. Then ETS, ARIMA/SARIMA(X), regression with ARIMA errors, dynamic harmonic regression, state-space models; global ML models (gradient boosting with lag features; StatsForecast/sktime/Darts) when many series or rich covariates justify them. Check residual diagnostics (Ljung–Box, ACF of residuals).

## Metrics

MAE/RMSE per horizon, MASE against (seasonal) naive, prediction-interval coverage and width, pinball loss for quantile forecasts (`metrics-and-model-evaluation.md`).

## Reporting

Frequency and horizon · backtest design (origins, window, gap) · benchmark results · model results per horizon with uncertainty · residual diagnostics · known limitations (breaks, short history).
