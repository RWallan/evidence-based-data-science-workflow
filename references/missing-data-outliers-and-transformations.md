# Missing data, outliers and transformations

Core sources: [@little_rubin] (missing-data mechanisms, likelihood-based methods), [@van_buuren_fimd] (multiple imputation in practice), [@kutner2005] (outlying and influential observations, remedial transformations), [@nist_ehandbook] (outlier detection, transformations).

Rule: no silent removal, imputation, winsorisation or result-changing transformation. Present alternatives, foreseeable consequences and a justification; the user decides unless a rule was approved earlier (`ds_policy.py propose --kind missing_data_treatment|outlier_treatment|data_cleaning`).

## Missing data

1. **Describe** (EDA): per variable, per row, co-occurrence patterns, missingness by group/time, special codes (-1, 999, "N/A") that hide missingness.
2. **Reason about the mechanism** [@little_rubin]: MCAR (unrelated to anything), MAR (explained by observed data), MNAR (depends on unobserved values). Data cannot prove MAR vs MNAR; domain knowledge and the user can make one more plausible.
3. **Options and consequences**:
   - Complete-case analysis: unbiased under MCAR (and some MAR situations), loses power; report how many rows drop.
   - Single imputation (mean/median/mode, LOCF): understates uncertainty; distorts distributions and correlations; acceptable mainly as a model preprocessing step validated inside CV.
   - Multiple imputation (MICE) with an imputation model at least as rich as the analysis model; pool with Rubin's rules [@van_buuren_fimd].
   - Likelihood-based methods (mixed models, FIML) under MAR.
   - Missingness indicators as features (prediction) – may help prediction, can mislead inference.
4. **Sensitivity**: compare approaches; delta-adjustment / pattern-mixture for MNAR scenarios.
5. In predictive pipelines, imputers are fitted inside each fold.

## Outliers and extreme values

Classify before acting:
- **Errors** (impossible values, unit mistakes, entry errors): correct from source if possible; otherwise set to missing with documentation.
- **Legitimate extremes**: part of the population; removing them biases estimates and understates risk.
- **Influential observations** (model-specific: leverage, Cook's distance [@kutner2005]): report results with and without; consider robust methods.

Flags (IQR rule, robust z with median/MAD) identify candidates; they never justify deletion alone. A user request to remove outliers is assessed: show how many, which, why they are extreme, and how key results change; then the user decides and the decision is logged.

## Transformations

Purpose first: stabilise variance, linearise, reduce skew for a model's assumptions, or interpretability. Log (needs positive values; offsets change results), Box–Cox / Yeo–Johnson, standardisation, splines. Consequences: coefficients change meaning (log → multiplicative effects), back-transformation bias for means, metric comparability. Fit learned transformations inside CV. Document every transformation in the notebook and the decision log when it alters results.
