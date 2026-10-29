# Scientific bibliography (by topic)

Full metadata and verification status: `bibliography.json` (single source of truth for keys). This list is a starting point, not a closed canon: search further primary literature when a question exceeds it. A reference listed here was not "consulted" until you actually read the relevant passage (local RAG or external source).

**Local library** (user's own copies, searchable with `search_references.py`; edition held may differ from the edition listed — the search result header shows the one held, cite that): `kutner2005`, `johnson_wichern2007`, `bussab_morettin_estatistica_basica` (local: 6th ed., 2010), `lohr_sampling` (local: earlier Duxbury edition). Everything else needs external consultation.

## Descriptive statistics, EDA, hypothesis tests
- [@bussab_morettin_estatistica_basica] — central reference (Portuguese): descriptive statistics, summary measures, probability, estimation, confidence intervals, hypothesis tests, comparison of populations, association, introductory regression.
- [@tukey1977] — the founding text of exploratory data analysis.
- [@wickham2014] — tidy data structure.
- [@donoho2017] — scope of data science.
- [@nist_ehandbook] — practical reference for EDA techniques, distributions, tests (free online).

## Regression, ANOVA, experimental design, inference practice
- [@kutner2005] — regression (simple/multiple), diagnostics and remedies, model building and validation, ANOVA, multi-factor studies, design.
- [@montgomery_doe] — design and analysis of experiments.
- [@gelman_hill_vehtari2020] — applied regression, simulation, causal inference with regression.
- [@wasserstein_lazar2016] — what p-values do and do not mean.
- [@benjamini_hochberg1995] — false discovery rate control.

## Nonparametric methods
- [@conover1999] — rank methods, distribution tests, association, nonparametric alternatives.

## Sampling and surveys
- [@lohr_sampling] — probability sampling, stratification, clusters, weights, nonresponse, variance estimation, non-probability samples.

## Multivariate analysis and statistical learning
- [@johnson_wichern2007] — PCA, covariance structure, discriminant analysis, clustering, MANOVA.
- [@james_islr] — introductory statistical learning.
- [@hastie_esl] — advanced statistical learning, model assessment and selection.

## Machine learning and validation
- [@kuhn_johnson2013] — applied predictive modelling, resampling, preprocessing, imbalance.
- [@cawley_talbot2010] — selection bias from hyperparameter tuning; nested validation.
- [@sklearn_pitfalls] — leakage and randomness pitfalls in practice.
- [@tripod_ai2024] — reporting guideline for clinical prediction models (verify metadata before citing).

## Causal inference
- [@hernan_robins2020] — causal inference, target trial emulation, g-methods (free online).
- [@pearl2009] — structural causal models and graphs.
- [@imbens_rubin2015] — potential outcomes framework.
- [@rosenbaum2002] — observational studies and sensitivity analysis.

## Time series
- [@hyndman_fpp3] — forecasting principles and practice (free online).
- [@box_jenkins] — ARIMA modelling.
- [@bergmeir_hyndman_koo2018] — validity of cross-validation for autoregressive models.

## Missing data
- [@little_rubin] — theory of missing-data mechanisms and likelihood methods.
- [@van_buuren_fimd] — multiple imputation in practice.

## Reproducibility
- [@wilson2017] — good-enough practices in scientific computing.
- [@sandve2013] — ten rules for reproducible computational research.

## Adding a reference

Add an entry to `bibliography.json` with what you verified (`verification.status`, date, detail); never fill fields from memory as if verified. If the book is added to the local library, also add an entry to the local catalog (`<data_dir>/metadata/catalog.json`) read from the copy's title/copyright page, then run `index_local_books.py`. `validate_skill.py` checks that every key cited in these files exists.
