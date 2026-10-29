# Causal inference protocol

Core sources: [@hernan_robins2020] (target trials, identification, g-methods), [@pearl2009] (causal graphs, back-door/front-door), [@imbens_rubin2015] (potential outcomes, design), [@rosenbaum2002] (observational studies, sensitivity analysis), [@gelman_hill_vehtari2020] (regression for causal inference).

Any causal wording ("effect of", "impact", "causes", "leads to", "drives") triggers this protocol. Regression coefficients, feature importances and correlations are associations unless identification is argued.

## Before analysis: write the causal question down (user-gated)

1. **Causal question** in plain language.
2. **Treatment/exposure** (well-defined intervention, versions of treatment), **outcome**, **time zero**, **population**.
3. **Estimand**: ATE, ATT, CATE, risk difference/ratio... on which scale.
4. **Identification strategy**: randomised experiment; or observational with conditional exchangeability (adjustment set), instrumental variables, regression discontinuity, difference-in-differences, synthetic control, front-door. Emulate the target trial when possible [@hernan_robins2020].
5. **Causal diagram (DAG)** with the user's domain knowledge: confounders to adjust for; mediators, colliders and descendants of treatment *not* to adjust for [@pearl2009].
6. **Assumptions**: consistency, positivity/overlap, conditional exchangeability (no unmeasured confounding), no interference (SUTVA), correct model specification; strategy-specific assumptions (exclusion restriction, parallel trends, continuity at the cutoff).
7. **Sensitivity analyses** planned: unmeasured confounding (E-values, Rosenbaum bounds [@rosenbaum2002]), alternative adjustment sets, negative controls, alternative estimators.

Record it in the pre-analysis plan; `ds_policy.py propose --kind causal_claim` for any causal conclusion.

## Estimation

Outcome regression, propensity score methods (matching, weighting with overlap diagnostics and weight trimming disclosed), doubly robust estimators (AIPW, TMLE), double/debiased ML for high-dimensional confounding (DoubleML, EconML), g-formula for time-varying treatments. Check balance after adjustment and overlap before trusting any estimate. DoWhy helps make assumptions explicit (model → identify → estimate → refute); its refuters do not prove identification.

## When the effect is not identifiable

If needed confounders are unmeasured, positivity fails, or the design cannot support the estimand: say so plainly, report associations as associations, describe what data or design would identify the effect, and consult the user. Do not upgrade language to "suggests a causal effect" by default.

## Reporting

Question · estimand · DAG and adjustment set · assumptions and their plausibility · estimator · diagnostics (balance, overlap) · estimate with interval · sensitivity results · limitations · status (PROPOSED / APPROVED (D-xxx)).
