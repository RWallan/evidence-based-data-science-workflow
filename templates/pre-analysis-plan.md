# Pre-analysis plan

Written before evaluating the corresponding results. Freeze with `ds_policy.py freeze pre_analysis_plan <this file>`; later changes are reported as deviations.

1. **Question and objective**
2. **Population and data design** (sampling design, units, clustering, time structure)
3. **Hypotheses**
   - Confirmatory (stated now, before results):
   - Exploratory (suggested by the EDA; cannot be confirmed on the same data):
4. **Estimand(s)**
5. **Inclusion / exclusion criteria**
6. **Missing data handling** (mechanism assumptions, method, sensitivity) — see references/missing-data-outliers-and-transformations.md
7. **Outliers / transformations** (rules agreed with the user)
8. **Methods and scientific justification** (cite: [@key, p. N])
9. **Assumptions and how they will be checked**
10. **Validation protocol** (splits/resampling respecting groups/time; nested CV if tuning)
11. **Metrics** (definition, direction, baseline, uncertainty)
12. **Comparison criteria** (how a model/analysis will be preferred; ties; simplicity rule)
13. **Multiplicity control**
14. **Sensitivity analyses**
15. **Final evaluation protocol** (test set use: once, after development)
16. **Foreseeable limitations**
17. **Deviations log** (filled later: what changed, why, who approved)
