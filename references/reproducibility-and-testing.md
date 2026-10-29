# Reproducibility, code quality and testing

Sources: [@wilson2017], [@sandve2013], [@sklearn_pitfalls] (randomness control).

## Project hygiene

- Follow the existing structure; do not create a parallel architecture. If none exists, a minimal layout: `data/` (raw immutable, `eda_sample/`, `interim/`, `processed/`; never committed when private), `notebooks/`, `src/<package>/` for reusable code, `tests/`, `docs/analysis/`, `.ds-workflow/`.
- Raw data are read-only; every derived dataset is produced by code.
- Record environment (lock file or `pip freeze`/`uv pip freeze` into the run record), seeds, parameters, data versions (hashes: `inspect_schema.py --hash`), and decisions (`ds_policy.py` log).
- Git: ignore data, models, extracted book text and secrets (`templates/project-gitignore`). Commit only when the user asks.

## Tests

Every function written or changed gets tests appropriate to its role. Distinguish:

| Kind | Purpose | Example |
|---|---|---|
| Implementation tests | the code does what it claims | a cleaning function maps known invalid codes to NaN |
| Statistical-property tests | a procedure has known mathematical properties | an estimator recovers a parameter from data simulated with that parameter; a CI covers at ≈ nominal rate over many simulations |
| Data contracts | inputs/outputs satisfy a schema | Pandera schema for the sample |
| Workflow tests | the agent's gates hold | this skill's `tests/` |
| Empirical evaluation | performance on authorised real data | CV results — never presented as a unit test |

**Synthetic data are generated, not typed.** Do not hand-write records or toy datasets as fixtures. Use Hypothesis strategies (`hypothesis.extra.pandas`, `st.floats`, ...) for property-based tests, Faker/Mimesis/factory-boy for realistic attributes, and NumPy generators with fixed seeds for simulations with known parameters. Generated data never count as evidence about real-world performance.

Run tests through `record_test_run.py -- <command>`; cite the printed `evidence:` file when reporting. A failing test means the work is not done: fix it, or report the failure and the impediment explicitly. Never state tests passed without a record showing `exit_code: 0`.

## Notebook reproducibility

Execute top to bottom in a fresh kernel (`jupyter nbconvert --execute --inplace` or `papermill`); `audit_notebook.py` checks execution order, stored errors, seeds, protected-data references and unmarked conclusions. Strip or refresh outputs consistently with the project's convention.

## Provenance of claims

Each number in a report should be traceable to a notebook cell or script output; each methodological claim to a citation `[@key, p. N]`; each quotation verified with `verify_citation.py`; each test claim to an evidence record. `check_report.py` enforces the mechanical parts.
