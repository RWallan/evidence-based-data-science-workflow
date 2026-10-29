# Regression, ANOVA, experimental design and nonparametric methods

Core sources: [@kutner2005] (linear regression, diagnostics, remedial measures, model building and validation, ANOVA, multi-factor studies, design), [@montgomery_doe] (design of experiments), [@gelman_hill_vehtari2020] (regression as modelling, simulation, interpretation), [@conover1999] (nonparametrics). Search the local copy of Kutner for chapter-level guidance, e.g. `search_references.py "remedial measures heteroscedasticity" --doc kutner2005`.

## Regression workflow

1. **Purpose**: description, prediction or estimation of an effect? The answer changes everything (variable selection for prediction ≠ adjustment set for causal estimation, see `causal-inference.md`).
2. **Specification**: functional form (splines/polynomials when non-linearity is plausible), interactions with a rationale, categorical coding, scaling. Specify before looking at outcome-based fit where the analysis is confirmatory.
3. **Fit** with `statsmodels` for inference (standard errors, diagnostics) or scikit-learn inside a pipeline for prediction.
4. **Diagnostics** [@kutner2005]: residuals vs fitted (non-linearity, heteroscedasticity), Q–Q of residuals, residuals vs omitted candidates and time/order (independence), leverage (hat values), influence (Cook's distance, DFFITS, DFBETAS), multicollinearity (VIF, condition numbers).
5. **Remedies** chosen with justification: transformations (Box–Cox; interpretability consequences), weighted least squares, robust (HC) standard errors, robust regression, adding terms, mixed models for clustering. Never drop influential points silently: report results with and without, and ask the user.
6. **Validation**: out-of-sample checks for predictive use; for inferential use, sensitivity analyses to specification choices.
7. **Interpretation**: coefficients are conditional associations under the model; units; holding other variables fixed may be unrealistic with correlated predictors; no causal reading without a causal design.

Stepwise selection based on p-values distorts inference (biased coefficients, invalid p-values); prefer subject-matter specification, penalisation with honest validation, or report selection as exploratory.

## Logistic and other GLMs

Choose the family/link by outcome type (binary → logistic; counts → Poisson/negative binomial; check overdispersion). Report odds ratios with intervals; check calibration if predictions are used; separation and rare events need special handling.

## ANOVA and designed experiments

- One-way and factorial ANOVA are regression with categorical predictors [@kutner2005]. State fixed vs random factors; check balance; unbalanced designs make Type I/II/III sums of squares differ: state which and why.
- Post-hoc comparisons: Tukey HSD for all pairs, Dunnett vs control, or planned contrasts; control multiplicity.
- Assumptions: independence (design), homogeneity of variances (Welch ANOVA when doubtful), approximate normality of residuals.
- Repeated measures / blocks / split plots require the correct error structure (mixed models).
- Design principles: randomisation, replication, blocking, power analysis before data collection [@montgomery_doe].

## Nonparametric methods [@conover1999]

| Situation | Option | Note |
|---|---|---|
| Two independent groups, ordinal or skewed | Wilcoxon–Mann–Whitney | tests stochastic ordering; report Hodges–Lehmann shift or probability of superiority |
| Paired data | Wilcoxon signed-rank, sign test | signed-rank assumes symmetric differences |
| k groups | Kruskal–Wallis, then Dunn with adjustment | not a test of medians unless shapes are equal |
| Blocks / repeated | Friedman | |
| Association | Spearman, Kendall τ | monotonic association |
| Contingency tables | chi-square, Fisher's exact | expected counts; effect size (Cramér's V, odds ratio) |
| Distributions | Kolmogorov–Smirnov, Anderson–Darling | KS with estimated parameters needs Lilliefors correction |

Permutation tests give exact inference under exchangeability and fit many estimands.

## Reporting

Model formula · estimation method · n used (and rows dropped, why) · diagnostics summary · remedies and sensitivity results · estimates with intervals · interpretation as association unless causal design approved.
