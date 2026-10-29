# EDA review checklist

Copy to `.ds-workflow/eda-review-checklist.md`. Tick each item (`- [x]`) or tick it with `N/A: <reason>`. `ds_policy.py set-phase awaiting_user_direction` refuses while any `- [ ]` remains.

## Data access
- [ ] Only the authorised EDA data (training split by default) was read for exploration (no full dataset, no test split)
- [ ] Split and EDA data documented: split method, sizes, seed, columns used; origin, unit, limitations, restrictions
- [ ] Schema/metadata inspection of other files used only `inspect_schema.py` or documentation

## Questions and coverage
- [ ] Every analysis answers a stated question; unanswered questions are listed
- [ ] Structure, granularity and unit of observation described
- [ ] Variable meanings and units verified (dictionary/user), unknown-value codes found
- [ ] Distributions and summary measures (with counts)
- [ ] Missing data amount and patterns
- [ ] Duplicates, inconsistencies, invalid values
- [ ] Extreme values / potentially influential observations flagged (not removed)
- [ ] Relations between variables
- [ ] Group differences (with group sizes)
- [ ] Temporal behaviour and dependence (or N/A with reason)
- [ ] Class balance of candidate targets (or N/A)
- [ ] Representativeness of the sample and possible bias sources
- [ ] Limitations affecting later analyses

## Correctness
- [ ] Calculations, filters and transformations re-checked (spot checks against raw sample rows or independent computation)
- [ ] Figures: appropriate type, labelled axes with units, legends, readable scales
- [ ] Code matches outputs; notebook executed top to bottom in a fresh kernel
- [ ] Helper code has tests, run via record_test_run.py (evidence file cited)
- [ ] audit_notebook.py passes (no errors)

## Claims
- [ ] Observations separated from interpretations; alternatives listed
- [ ] No conclusions, causal claims, target/variable/model decisions presented as settled (PROPOSED where needed)
- [ ] Exploratory tests (if any) labelled exploratory, multiplicity acknowledged
- [ ] Every statement is supported by a cell/figure/table in the notebook
