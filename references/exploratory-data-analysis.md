# Exploratory data analysis (EDA)

EDA is the first analytical product, reviewed with the same rigour as any model. Its job is to understand the data-generating process, the data's defects and the questions the data can and cannot answer [@tukey1977; @bussab_morettin_estatistica_basica]. It is not an automatic licence to start modelling.

Runs only on the authorised EDA data — by default the training split of the project's single train/test split (SKILL.md gate 2), or a documented subsample of it when the user asks. Every claim about the population carries the limitations of that data.

## Question-driven, not plot-driven

Write each exploratory question in a markdown cell before the code that answers it (`## Question 3: Does missingness in income depend on region?`). Pick the statistic or figure that answers that question, with labelled axes, units and legends. Avoid both indiscriminate "plot everything" galleries and silent omission of relevant diagnostics. Unanswered questions are listed as such.

## Scope checklist (cover what the data warrant)

| Area | What to examine | Typical tools |
|---|---|---|
| Structure | granularity, unit of observation, keys, one row = what? tidy layout [@wickham2014] | `df.shape`, key uniqueness, `df.dtypes` |
| Provenance & meaning | origin, collection process, definitions, units, codes for "unknown" | data dictionary, documentation, user |
| Validity | impossible values, inconsistent categories, type errors, unit mix-ups | range checks, Pandera schemas |
| Duplicates | exact and near duplicates, duplicated keys | `duplicated()`, key counts |
| Distributions | location, spread, shape, multimodality, heaping, zero inflation | quantiles, histograms/ECDF, boxplots [@nist_ehandbook] |
| Missing data | amount per variable/row, co-occurrence patterns, dependence on observed variables | missingness matrix, missing-by-group tables (`references/missing-data-outliers-and-transformations.md`) |
| Extremes & influence | legitimate extremes vs errors vs influential observations | robust z (median/MAD), IQR rule as a *flag*, leverage later in models |
| Relations | pairwise association (Pearson/Spearman/Kendall, Cramér's V), non-linearity | scatter/hexbin, contingency tables |
| Group differences | descriptive contrasts across segments, sample sizes per group | grouped summaries with counts |
| Time | ordering, gaps, frequency, trend, seasonality, autocorrelation, regime changes, look-ahead fields | line plots, ACF (`references/time-series-analysis.md`) |
| Target candidates | class balance, base rates, label definition/timing (leakage risk) | counts, prevalence over time |
| Representativeness | coverage of the target population, selection mechanisms, weights | compare with known margins (`references/sampling-and-survey-analysis.md`) |
| Bias sources | selection, measurement, survivorship, label bias, data drift | design discussion with the user |

## Statistical care inside EDA

- Report counts with every percentage and group summary; tiny groups produce noisy contrasts.
- Prefer robust summaries (median, IQR, MAD) alongside mean/SD when distributions are skewed.
- EDA comparisons are **exploratory**: avoid p-value hunting. If you compute tests, label them exploratory and account for multiplicity [@benjamini_hochberg1995; @wasserstein_lazar2016]. Hypotheses suggested by EDA cannot be "confirmed" on the same data.
- Correlation is not causation: describe associations; causal wording waits for `references/causal-inference.md`.
- Keep originals immutable; derived columns/files are named and documented.
- Fixed seeds for any randomness (subsampling for plots, jitter).

## Notebook hygiene

- Sections: context and data provenance → sample description and limitations → questions → findings per question → summary of observations → open questions for the user.
- Code: imports and constants (paths from the policy, `SEED`) at the top; non-trivial helpers in a module with tests; no hidden state; executed top to bottom in a fresh kernel before presentation; no stale outputs.
- Markdown separates **Observation** from **Interpretation**; conclusion-like sentences carry `PROPOSED`.

## Output of the EDA step

1. The executed notebook.
2. A short report (`templates/report-template.md`): questions, key figures/statistics, observations, interpretations with alternatives, limitations, questions for the user, possible directions as options.
3. Completed `.ds-workflow/eda-review-checklist.md`.
