# Sampling and survey analysis

Core source: [@lohr_sampling] (probability sampling, stratification, ratio/regression estimation, cluster and multistage sampling, unequal probabilities, weights, nonresponse, variance estimation in complex surveys). The local copy is an earlier Duxbury edition: cite the edition you actually consulted (the search result header shows it).

## Two different "samples" in this workflow

1. **The data's own sampling design** (how the dataset was collected from the population): decides what inference is possible.
2. **The project's train/test split and the EDA data** (SKILL.md step 2): the training split is the EDA data by default; an extra EDA subsample of it is drawn only on the user's request. Both inherit every limitation of the source data.

## Questions to ask about the data's design

- Target population vs sampling frame vs respondents: where can coverage error and selection bias enter? [@lohr_sampling]
- Probability sample? With stratification, clustering, unequal selection probabilities? Are design variables (strata, PSU, weights) available?
- Nonresponse: unit and item; are there nonresponse adjustments in the weights?
- Convenience / opt-in / administrative data: no design-based inference to a population without strong assumptions; say so.

## Analysing complex samples

- Use weights for population estimates; ignoring them biases estimates when selection relates to the outcome.
- Variance must reflect the design: Taylor linearisation, replication (jackknife, bootstrap, BRR) [@lohr_sampling]. Treating clustered data as i.i.d. understates standard errors.
- Python: `statsmodels` supports weights in many models but not full design-based variance; `samplics` (if installed) implements design-based estimation; R `survey` is the reference implementation. Verify what the installed version supports before claiming design-based inference.
- Domain (subpopulation) estimation: keep the full design, do not subset the data before variance estimation.

## Splitting and subsampling

The structure rules below apply both to the project's train/test split (`make_train_test_split.py`: `random`, `stratified`, `group`, `temporal`) and to an optional EDA subsample of the training split (`make_eda_sample.py`):

| Data structure | Method | Why |
|---|---|---|
| Independent rows | `simple` (reservoir) | exact n, one pass, reproducible with seed |
| Important small segments | `stratified --strata COL` | proportional allocation keeps segment shares; for rare segments consider discussing oversampling with weights |
| Multiple rows per unit (customer, patient, store) | `group --group COL` | whole units, no partial histories: avoids underestimating within-unit dependence |
| Ordered data (time) | `systematic` or a contiguous block chosen with the user | preserves temporal structure; random rows destroy it |

Record: origin, size, criterion, unit, seed, limitations. A random sample is representative of its source, not necessarily of the target population.

## Representativeness checks (EDA)

Compare sample margins with known population margins (census, frame totals, the development data's own metadata) when available; report discrepancies; do not "fix" them by weighting without the user's agreement.
