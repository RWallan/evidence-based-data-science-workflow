# Python library guide

These are options, not dependencies. Inspect the project first (lock files, `pip list`, `uv pip list`, conda env) and match its conventions. Never install packages without telling the user why. Before using an API, check the installed version (`python -c "import pandas; print(pandas.__version__)"`) and read the documentation for that version; when documentation and installed version disagree, trust `help()`/the source of the installed package and say which you used.

Missing packages (Jupyter included): ask the user for permission to install them in the project's own environment, naming the packages, the environment (venv/uv/conda/poetry) and the exact command, then wait for the answer. The skill's venv exists for the skill's own tooling and tests; use it for project work only if the user explicitly asks.

| Area | Libraries | Official documentation |
|---|---|---|
| Data manipulation | NumPy, pandas, Polars, PyArrow | https://numpy.org/doc/ · https://pandas.pydata.org/docs/ · https://docs.pola.rs/ · https://arrow.apache.org/docs/python/ |
| EDA & visualisation | Matplotlib, Seaborn, Plotly, ydata-profiling | https://matplotlib.org/stable/ · https://seaborn.pydata.org/ · https://plotly.com/python/ · https://docs.profiling.ydata.ai/ |
| Statistics | SciPy, statsmodels, Pingouin | https://docs.scipy.org/doc/scipy/ · https://www.statsmodels.org/stable/ · https://pingouin-stats.org/ |
| Machine learning | scikit-learn, CatBoost, XGBoost, LightGBM | https://scikit-learn.org/stable/ · https://catboost.ai/docs/ · https://xgboost.readthedocs.io/ · https://lightgbm.readthedocs.io/ |
| Deep learning | PyTorch | https://docs.pytorch.org/docs/ |
| Feature engineering | feature-engine, scikit-learn | https://feature-engine.trainindata.com/ |
| Explainability | SHAP | https://shap.readthedocs.io/ |
| Imbalance | imbalanced-learn | https://imbalanced-learn.org/stable/ |
| Time series | statsmodels, sktime, StatsForecast, Darts | https://www.sktime.net/ · https://nixtlaverse.nixtla.io/statsforecast/ · https://unit8co.github.io/darts/ |
| Causal inference | DoWhy, EconML, DoubleML | https://www.pywhy.org/dowhy/ · https://econml.azurewebsites.net/ · https://docs.doubleml.org/ |
| Tracking & experiments | MLflow, Optuna, DVC | https://mlflow.org/docs/latest/ · https://optuna.readthedocs.io/ · https://dvc.org/doc |
| Testing | pytest, Hypothesis | https://docs.pytest.org/ · https://hypothesis.readthedocs.io/ |
| Synthetic test data | factory-boy, Faker, Mimesis | https://factoryboy.readthedocs.io/ · https://faker.readthedocs.io/ · https://mimesis.name/ |
| Data validation | Pandera | https://pandera.readthedocs.io/ |

## Methodological fit

- `statsmodels`: statistical models with inference (standard errors, tests, diagnostics, formulas, GLMs, mixed models, time series).
- `scikit-learn`: preprocessing pipelines, resampling, model selection, predictive modelling, metrics. Its `LogisticRegression` is penalised by default: not a drop-in for inferential logistic regression.
- Boosting (CatBoost/XGBoost/LightGBM): tabular prediction with non-linearities/interactions when data size justifies; tune within CV; CatBoost handles categorical features natively.
- PyTorch: only with a justified need (images, text, sequences, very large data, custom architectures).
- feature-engine: sklearn-compatible transformers (imputation, encoding, outlier capping) that keep transformations inside pipelines.
- Causal libraries only after the causal protocol (`causal-inference.md`) justifies the analysis.
- Pingouin: convenient effect sizes and tests; confirm formulas against SciPy/statsmodels for critical results.
- ydata-profiling: a quick overview never replaces question-driven EDA, and it must run on the EDA sample only.
