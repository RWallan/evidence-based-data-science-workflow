# Machine learning: validation, leakage and model selection

Core sources: [@kuhn_johnson2013], [@james_islr], [@hastie_esl] (model assessment and selection), [@cawley_talbot2010] (selection bias from tuning), [@sklearn_pitfalls] (leakage, pipelines, randomness), [@bergmeir_hyndman_koo2018] and [@hyndman_fpp3] (temporal validation). Clinical prediction models: follow reporting guidance such as TRIPOD+AI [@tripod_ai2024] when applicable.

## Before modelling (agreed with the user, step 6)

Prediction target and its timing (what is known at prediction time?), unit and population, horizon, costs of each error type, baseline, metric (`metrics-and-model-evaluation.md`), validation scheme, success criterion. Simple, interpretable baselines first; complexity must earn its place.

## Splits

- **Project split (once)**: made at the start with `make_train_test_split.py` (scikit-learn `train_test_split` / `GroupShuffleSplit`, or a temporal cut). Never repeated: re-splitting after exploring or modelling puts already-seen rows into the test set. This is the default: the user's explicit instructions — in the initial prompt of the feature/project or at any later moment — override it, whether they ask for a different split (`split_authorization` / `resplit`) or for none (`no_test_split`). Follow them and record them with the user's words.
- **Test set**: sealed until `final_evaluation` (SKILL.md gate 1). Used once.
- **Training split (development data)**: everything else — EDA, feature work, model selection and tuning — with resampling *inside* it (CV, or a validation hold-out cut from the training split).

The same structure rules apply to the project split and to internal validation:

| Data structure | Validation |
|---|---|
| i.i.d. rows | (repeated) K-fold; stratified for classification |
| repeated measures / groups (patients, customers, stores) | GroupKFold / StratifiedGroupKFold: a unit never spans train and validation |
| time-ordered | forward-chaining (expanding/rolling origin) with gap when labels mature late; never random K-fold when the future informs the past [@bergmeir_hyndman_koo2018] |
| spatial | spatial/block CV |
| tuning + performance estimate | nested CV, or a separate validation split, to avoid optimistic selection bias [@cawley_talbot2010] |

## Leakage: the usual suspects [@sklearn_pitfalls; @kuhn_johnson2013]

- Preprocessing fitted on all data (scaling, imputation, encoding, feature selection, PCA, resampling for imbalance) → wrap everything in a `Pipeline` fitted per fold.
- Target-derived or post-outcome features (status flags, "days since closure", aggregates including the label period).
- Duplicates/near-duplicates across splits; the same entity in train and test.
- Time leakage: features computed with future information; random splits of temporal data.
- Test-set peeking: any decision changed after seeing test results.
- Oversampling (SMOTE etc.) before splitting.

## Model selection

- Compare candidates on the same folds; report mean and variability (SD or CI across folds/repeats), not a single best number.
- Prefer the simplest model within noise of the best (one-standard-error rule) unless the user's criteria say otherwise.
- Threshold choice for classifiers is a decision with costs: tune on validation folds (or out-of-fold predictions), never on the test set; agree the criterion with the user.
- Calibration matters when probabilities are used (`metrics-and-model-evaluation.md`).
- Final model choice and claims of superiority are user-gated: present the evidence, the uncertainty, the trade-offs (performance, interpretability, cost, robustness) and ask.

## Gradient boosting, deep learning, AutoML

Use when justified by data size, structure and the agreed goal. Control randomness (seeds), early stopping on validation folds only, log hyperparameter search spaces and budgets (Optuna/MLflow when available).

## Interpretation

Feature importance ≠ causal effect. Permutation importance and SHAP describe the model, not the world; correlated features split importance. Present them as model diagnostics.

## Final evaluation protocol (freeze before running)

Model artefact (hash) · preprocessing · threshold · metrics with CI (bootstrap on the test set) · subgroups to report · what will be reported if results disappoint. Run once. No re-tuning afterwards; if anything changes, state that the test estimate is no longer independent.
