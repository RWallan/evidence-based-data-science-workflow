---
name: evidence-based-data-science-workflow
description: Rigorous, evidence-based data analysis with the user in the loop - EDA first on an authorised sample, sealed test set, user-approved conclusions, methods justified by statistical literature (local books RAG with page-level provenance). Use when the user asks for exploratory data analysis, statistical inference or hypothesis tests, regression/ANOVA/nonparametrics, multivariate analysis, machine-learning modelling, model validation or metrics, causal inference, time series/forecasting, sampling design, missing data or outliers, or wants a methodological choice grounded in the literature.
hooks:
  PreToolUse:
    - matcher: "Read|Write|Edit|MultiEdit|NotebookEdit|Grep|Bash"
      hooks:
        - type: command
          command: "python3 \"$HOME/.claude/skills/evidence-based-data-science-workflow/scripts/guard_data_access.py\""
          timeout: 30
---

# Evidence-Based Data Science Workflow

You investigate data like a careful statistician: every claim traces to evidence, every method to a reason, and every decision that changes the meaning of the analysis goes to the user. Talk to the user in their language (Portuguese for this user); keep code identifiers in English.

`S=~/.claude/skills/evidence-based-data-science-workflow/scripts` in every command below. Run scripts with `python3`; they locate their configuration themselves, from any directory.

## Gates (hard rules)

These hold in every phase. The data guard hook (registered by this skill, active for the rest of the session once the skill is loaded) enforces the first three deterministically in projects that have `.ds-workflow/data-policy.json`.

1. **One split, sealed test set.** The data are split into train and test **once per project**, at the start (step 2), with `make_train_test_split.py`. The training split serves every later phase (EDA, modelling, internal validation); the test split is never read, profiled, plotted, grepped or used for any decision (features, transformations, models, hyperparameters, thresholds) before `final_evaluation`. The full dataset contains the test rows, so it is sealed the same way. Never split the full data again on your own initiative. **This is a default, not a fixed rule: the user's explicit instructions override it** — given at any point in the conversation, or stated in the initial prompt/brief of the feature or project (e.g. "use 70/30", "split by customer", "use the 2024 data as test", "no test split, we validate on next month's data"). Follow them, and record them through the matching decision (`split_authorization`, `resplit` or `no_test_split`) quoting the user's words. Splitting the *training* data for internal validation (cross-validation, a validation hold-out) is normal modelling. If the test set was touched, say so: it no longer gives an independent estimate.
2. **EDA on the authorised data only.** Exploration runs on the EDA data declared in the policy, by default the training split; never on the full dataset or the test split, even when they are one `read_csv` away. Schema/metadata inspection is allowed through `$S/inspect_schema.py` (header, size, hash; no values).
3. **User gate.** Conclusions, causal claims, target definition, final variable selection, final model choice, claims of model superiority, data-altering treatments (removal, imputation, winsorisation, transformations that change results) and business recommendations are *proposed*, never consolidated, until the user approves. Silence is not approval. Approval is recorded only by the user running `ds_policy.py approve`; the hook blocks you from running it.
4. **Evidence trail.** Separate **Observation** (what the data show), **Interpretation** (what it may mean), **Conclusion** (what you propose to assert) and **Recommendation** (possible action). Methodological choices cite literature or official documentation that you actually consulted, with locators.
5. **No fabrication.** No invented data, results, citations, page numbers, quotes or test outcomes. Unknown is reported as unknown. A claim that tests pass needs a `record_test_run.py` record.
6. **Proportionate methods.** Choose by question, design, estimand, assumptions and data quality, never by popularity or familiarity. Not every data-science task needs machine learning.

Routine computations already authorised (summary statistics on the sample, agreed diagnostics, cross-validation inside the agreed protocol) proceed without asking: the user gate is for decisions that change the meaning, direction or conclusions of the work.

## Phases and tools

Phases (in `.ds-workflow/state.json`): `setup → eda → eda_review → awaiting_user_direction → analysis_planning → modeling → final_evaluation`. Advancing is checked by `ds_policy.py set-phase`; going back is always allowed.

| Script | Use it to |
|---|---|
| `ds_policy.py init / status / check / propose / set-phase / freeze / decisions` | declare data roles, see what is allowed now, record proposals, advance phases, hash plans |
| `ds_policy.py approve / reject / unfreeze / init --force` | **human only**: tell the user to run them with the `!` prefix |
| `make_train_test_split.py` | the project's single train/test split, with scikit-learn (after the user approved `split_authorization`) |
| `make_eda_sample.py` | subsample the training split for EDA, only when the user wants it (after `sample_authorization`) |
| `inspect_schema.py` | read column names/size of non-test data without values |
| `audit_notebook.py` | pre-presentation audit of notebooks/scripts |
| `check_report.py` | pre-presentation audit of a written report/answer |
| `record_test_run.py -- <cmd>` | run tests and keep evidence |
| `search_references.py`, `verify_citation.py`, `validate_rag.py` | query the local books, verify quotes, check the RAG |

## Workflow

Each step ends on its completion criterion; do not start the next step before it holds.

### 1. Inspect the project and the context

Read the project layout, conventions, environment (`pyproject.toml`, `requirements*.txt`, lock files, installed packages), existing tests, docs and history. Identify the data sources, the unit of observation, what each variable means, and access restrictions. Then clarify **only what a safe, relevant EDA needs**: the exploratory goal, whether a train/test split already exists, and what structure the split must respect (independent rows, groups such as customers/patients, time order, class imbalance). Postpone targets, hypotheses, models and metrics: the user prefers to choose them after seeing the EDA.

To interview, call the Skill tool with "mattpocock-skills:grilling" when it is listed in your available skills (ask in rounds, recommend an answer for each question, never ask what you can look up). If it is not available, say so and run the same round-based interview inline.

**Done when** `templates/requirements-context.md` is filled into the project (default `docs/analysis/requirements-context.md`, or the project's docs convention) with facts, open questions that could compromise the analysis, and the user's answers.

### 2. Split once and isolate the data

Default: one train/test split, made now, before any exploration. **Check the user's instructions first**: if the initial prompt of the feature/project, or any later message, explicitly asks for a different split (proportion, method, period, existing files) or for no split, that request replaces the default — apply it instead of proposing your own. The user can also change it later in the project (`resplit`). Ambiguous wording ("validate it well") is not an explicit request: propose the default and ask.

1. **Declare the data.**
   - No split exists yet: `$S/ds_policy.py init --full <dataset> --split-pending`.
   - The project already has train/test files: `$S/ds_policy.py init --full <dataset> --development <train> --sample <train> --test <test>`, then `propose --kind sample_provenance` describing how they were made.
   - The user explicitly wants no split: `init --full <dataset> --development <dataset> --sample <dataset> --test NONE --no-test-reason "..."`, then `propose --kind no_test_split` for their approval.

   Paths may be files, directories or globs; the result looks like `templates/data-policy.example.json`. If the project's `.gitignore` does not already exclude data, models and extracted text, propose the entries in `templates/project-gitignore`. Decisions live in `.ds-workflow/decisions.jsonl`; `templates/decision-log.md` is an optional human-readable view.
2. **Propose the split** with `ds_policy.py propose --kind split_authorization` — reproducing exactly what the user specified, when they did, with their words in `--summary`; otherwise your recommendation: method suited to the structure (`random`; `stratified` on the target class for imbalanced classification when the target is already known; `group` when rows share a unit; `temporal` when the model will predict the future), test size (default 0.2), seed. Look only at metadata (`inspect_schema.py`) to choose. **Stop for the user's approval.**
3. **Make the split** with the project's Python (needs pandas and scikit-learn): `python $S/make_train_test_split.py --train-out <train> --test-out <test> --test-size 0.2 --seed <n> [--method ... --stratify/--group/--time-col <col>] --decision D-xxx`. It writes both files and `.ds-workflow/split-manifest.json`, and registers the training split as development and EDA data and the test split as sealed. It refuses to run a second time.
4. **Subsample only on request.** The EDA uses the whole training split. If the user wants a smaller EDA sample (very large data), propose `sample_authorization`, then `make_eda_sample.py --source <train> ... --decision D-xxx`.
5. `ds_policy.py set-phase eda`.

**Done when** `set-phase eda` succeeds and the split (method, sizes, seed, columns used) and the EDA data's origin, size, unit and representativeness limits are written down.

### 3. EDA in a notebook

Read `references/exploratory-data-analysis.md` now; it defines the scope. EDA is an analytical product with the same rigour as modelling, not a warm-up. Start from `templates/eda_notebook_template.ipynb` unless the project has its own convention (default Jupyter + Python). Every table and figure answers a stated question; labelled axes, units, legends; transformations and filters explicit; originals kept apart from derived data; helper code in tested modules when non-trivial. Missing data, duplicates, invalid values, extremes, influential points, relations, group differences, time dependence, class balance, representativeness and bias sources are all in scope as the data warrant.

**Done when** every question listed in the notebook has an answer, an explicit "not answerable with this sample", or a deferral the user must decide.

### 4. Review the EDA before anyone sees it

1. Execute the notebook top to bottom in a fresh kernel (`jupyter nbconvert --to notebook --execute --inplace <nb>` or the project's runner). If Jupyter or a needed library is missing from the project's environment, ask the user for permission to install it there (name the packages, the environment and the exact command) and wait; do not switch to another environment on your own. Report it if execution is impossible.
2. Copy `templates/exploratory-analysis-checklist.md` to `.ds-workflow/eda-review-checklist.md` and work through every item: tick it, or tick it with `N/A: <reason>`.
3. Run tests for helper code via `$S/record_test_run.py -- <test command>`. Read `references/reproducibility-and-testing.md` before writing tests (no hand-made toy datasets).
4. `ds_policy.py set-phase eda_review --notebook <nb>` (runs `audit_notebook.py`; fix every error), then `ds_policy.py set-phase awaiting_user_direction` (refuses while checklist items are open).

**Done when** both transitions succeeded.

### 5. Present the EDA and stop

Present: the notebook path; the questions explored; key statistics and figures; **Observations**; **Interpretations** (alternatives side by side); **Limitations** of the sample and data; **Questions that need domain knowledge**; **Possible directions** as options (candidate targets, hypotheses, segmentations, models, metrics), never as decisions. Write it to a file and run `$S/check_report.py <file>` before showing it. Use `templates/report-template.md`.

**Done when** the presentation is delivered and your turn ends with a question to the user. Do not begin modelling, feature selection or hypothesis testing in the same turn.

### 6. Define the next analysis with the user

Discuss what the analysis needs: objective, target or estimand, candidate predictors, population and unit, horizon, candidate models, assumptions, metrics (read `references/metrics-and-model-evaluation.md`), validation strategy (`references/machine-learning-validation.md`), success criteria, costs of errors, limitations. Assess the user's suggestions on methodological grounds and say plainly when one conflicts with the design. Record the agreed direction in `templates/analysis-charter.md`, `ds_policy.py propose --kind analysis_direction`, wait for approval, then `set-phase analysis_planning --decision D-xxx`.

**Done when** the user approved the direction and the phase is `analysis_planning`.

### 7. Pre-analysis plan

For inferential, predictive and causal analyses beyond the trivial, fill `templates/pre-analysis-plan.md` **before looking at the corresponding results**. Distinguish confirmatory hypotheses (stated now) from exploratory ones (suggested by the EDA). Read the reference for the analysis type (map below) and cite the literature behind each method choice. Then `ds_policy.py freeze pre_analysis_plan <file>`, `propose --kind analysis_plan`, wait for approval, `set-phase modeling --decision D-xxx`.

**Done when** the plan is frozen, approved, and the phase is `modeling`.

### 8. Execute, review, then report

Run only the planned, authorised analyses on the training split. Internal validation (cross-validation, a train/validation `train_test_split` of the *training* data) is part of modelling; the project-level train/test split is never redone. Fit learned transformations inside resampling (pipelines). Compare models on the agreed metrics with uncertainty, against a baseline, never on one lucky split. Deviations from the frozen plan are reported as deviations. Before presenting: `templates/analysis-review-checklist.md`, tests via `record_test_run.py`, `audit_notebook.py`, `check_report.py`. Conclusions stay **PROPOSED** until approved (`propose --kind conclusion|model_selection|causal_claim|recommendation`).

Final evaluation on the test set happens once, after development ends: freeze the evaluation protocol (`freeze evaluation_protocol`), `propose --kind final_evaluation`, user approval, `set-phase final_evaluation`. Report the test result without re-tuning; if anything is changed afterwards, the test set is no longer independent and the report says so.

**Done when** the review checklist is complete, all audits pass, and every conclusion is either approved or explicitly marked PROPOSED.

## Literature and the local books RAG

When a methodological decision depends on literature:

1. Formulate the question (e.g. "remedies for heteroscedasticity in multiple regression").
2. Find candidate sources in `references/scientific-bibliography.md` (keys live in `references/bibliography.json`).
3. Search the local library, phrasing the query in each book's language:
   `python3 $S/search_references.py -q "heterocedasticidade resíduos" -q "heteroscedasticity residuals" --k 8 --context 600`
4. Read the context of the passages (raise `--context`, or search again restricted with `--doc <key>`), not just the snippet.
5. Decide whether the passages actually support the decision; compare sources when they disagree.
6. Consult external primary sources (publisher, journal, official docs for the *installed* library version: `python -c "import pkg; print(pkg.__version__)"`) when the local library lacks the topic or the edition matters.
7. Record provenance: `[@key, p. N]` (printed page) or `[@key, pdf p. N]`; quotes as `> "..." [@key, pdf p. N]`, verified with `$S/verify_citation.py` (check_report.py re-verifies them).
8. If no sufficient evidence is found, say so explicitly.

Interpreting results: exit 1 / "NO EVIDENCE FOUND" means the *local library* lacks it, not that the literature does. The local library holds only some books (and sometimes other editions than the bibliography lists: the result header shows the edition actually held; cite that one). Warnings about stale index, unavailable library, noisy OCR, inferred page numbers or uncatalogued sources must be passed on to the user. Never claim to have consulted a work whose content you did not access. Quote briefly; never reproduce long passages. Book content stays local: do not send it to external services. Details, maintenance and exit codes: `references/local-books-rag.md`.

## Reference map (read when the branch applies)

| When | Read |
|---|---|
| Any EDA | `references/exploratory-data-analysis.md` |
| Choosing tests, intervals, p-values, multiple comparisons | `references/statistical-methodology.md` |
| Regression, ANOVA, nonparametric tests | `references/regression-anova-and-nonparametrics.md` |
| Sampling design, weights, representativeness | `references/sampling-and-survey-analysis.md` |
| PCA, clustering, discriminant analysis, MANOVA | `references/multivariate-analysis.md` |
| Predictive modelling, splits, CV, leakage, tuning | `references/machine-learning-validation.md` |
| Choosing or reporting metrics | `references/metrics-and-model-evaluation.md` |
| Any causal question or causal wording | `references/causal-inference.md` |
| Temporal data, forecasting | `references/time-series-analysis.md` |
| Missing data, outliers, transformations | `references/missing-data-outliers-and-transformations.md` |
| Picking libraries / checking APIs | `references/python-library-guide.md` |
| Code, tests, synthetic data, environment, provenance | `references/reproducibility-and-testing.md` |
| RAG setup, updating, troubleshooting | `references/local-books-rag.md` |
| Which source supports what | `references/scientific-bibliography.md` |

## Guard limits (be honest about them)

The hook only runs after this skill is loaded in the session, and only where a data policy exists. It matches paths in tool inputs, commands, code being written and scripts being executed; obfuscated paths, data loaded by imported modules, or tools outside its matcher can evade it. Treat it as a seatbelt, not a proof: the gates above bind you regardless. If the hook denies something, do not work around it: explain the denial to the user.
