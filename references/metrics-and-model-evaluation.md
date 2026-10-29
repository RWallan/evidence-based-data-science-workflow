# Metrics and model evaluation

Training loss is not an evaluation. Agree with the user, before comparing models: what is predicted, which errors matter and how much, and how performance will be measured. There are no universal "good" thresholds for any metric: acceptability depends on the problem, baseline and costs (user-gated). Sources: [@kuhn_johnson2013], [@james_islr], [@hastie_esl], [@hyndman_fpp3], scikit-learn metric documentation for exact definitions in the installed version.

## Classification

| Metric | Measures | Watch out |
|---|---|---|
| ROC-AUC | ranking/discrimination across thresholds | insensitive to calibration; can look good under heavy imbalance |
| Gini | `Gini = 2 × ROC-AUC − 1` (binary; state this convention, other "Gini" definitions exist) | same as ROC-AUC |
| PR-AUC / average precision | ranking quality focused on the positive class | baseline = prevalence; preferred when positives are rare |
| Precision, recall (sensitivity), specificity | error types at a chosen threshold | threshold-dependent; report the threshold and how it was chosen |
| F1, F-beta | precision–recall trade-off (β > 1 weights recall) | ignores true negatives; pick β from costs |
| Accuracy, balanced accuracy | overall correctness | plain accuracy is misleading under imbalance; never report it alone then |
| Confusion matrix | full error profile at a threshold | always show counts with rates |
| Log loss | probabilistic accuracy | punishes overconfidence |
| Brier score | mean squared error of probabilities | decomposes into calibration and refinement |
| Calibration (reliability curve, calibration slope/intercept, ECE) | agreement of predicted probability with observed frequency | discrimination ≠ calibration |

Imbalance: choose metrics that reflect the minority class (PR-AUC, recall at fixed precision, cost-weighted metrics), keep the natural prevalence in validation/test, and justify any resampling (inside CV only).

## Regression

MAE (robust, in units), MSE/RMSE (penalise large errors, in units for RMSE), R² (relative to the mean model; can be negative out of sample), median absolute error, MAPE (undefined/unstable near zero, asymmetric), sMAPE (has its own pathologies), domain-specific cost functions. Report units and the baseline (mean/median/last value).

## Time series [@hyndman_fpp3]

MAE, RMSE, MASE (scaled by in-sample naive/seasonal naive error; comparable across series), coverage and width of prediction intervals, pinball loss for quantiles. Evaluate per horizon with rolling-origin backtests.

## Clustering

Silhouette, Davies–Bouldin, Calinski–Harabasz (internal; favour certain shapes), stability under resampling, external indices (ARI, NMI) only with meaningful labels.

## Inference and causal analysis

Predictive metrics do not validate inferential or causal claims. Report effect estimates, standard errors, intervals, effect sizes, diagnostics, identification assumptions and sensitivity analyses (`statistical-methodology.md`, `causal-inference.md`).

## Reporting a metric

For each: definition (formula/convention), observed value, dataset (CV folds / validation / test), direction of preference, baseline value, uncertainty (CV spread, bootstrap CI), limitations. State whether the number comes from development resampling or the one-shot test evaluation.
