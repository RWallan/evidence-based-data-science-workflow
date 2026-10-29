# evidence-based-data-science-workflow

A Claude Code skill for rigorous, evidence-based data analysis: EDA first on an authorised sample, sealed test set, user-approved conclusions, and methodological choices backed by statistical literature retrieved from a local book library with page-level provenance.

`SKILL.md` is the agent-facing entry point; this README is for humans.

## Layout

```
SKILL.md                  agent instructions + PreToolUse guard hook (frontmatter)
references/               on-demand guidance (EDA, inference, regression/ANOVA/nonparametrics,
                          sampling, multivariate, ML validation, metrics, causal, time series,
                          missing data/outliers, libraries, reproducibility, RAG, bibliography)
  bibliography.json       citation keys + what was verified, when
templates/                requirements context, analysis charter, pre-analysis plan, EDA and
                          analysis review checklists, decision log, report, EDA notebook,
                          data-policy example, project .gitignore
scripts/                  deterministic tooling (standard library except index_local_books.py)
  ds_policy.py            data roles, phase gate, decision log, human-only approvals
  guard_data_access.py    PreToolUse hook enforcing the data policy
  make_train_test_split.py  the project's single train/test split (scikit-learn), registered in the policy
  make_eda_sample.py      optional EDA subsample of the training split (simple/stratified/group/systematic)
  inspect_schema.py       header/size/hash without values
  audit_notebook.py       pre-presentation notebook audit
  check_report.py         pre-presentation report audit (conclusions, test claims, citations, quotes)
  record_test_run.py      run tests and keep evidence
  setup_rag.py            durable RAG configuration
  discover_books.py       read-only inventory of the library
  index_local_books.py    incremental extraction + SQLite FTS5 index (needs PyMuPDF)
  search_references.py    BM25 search with provenance
  verify_citation.py      check a quote against the cited page
  validate_rag.py         RAG health check (incl. retrieval from another cwd)
  validate_skill.py       structural validation of this skill
tests/                    pytest suite (+ behavioral/ harness using `claude -p`)
```

## Install / activate

The skill is a personal skill: `~/.claude/skills/evidence-based-data-science-workflow/`. Claude Code loads it in every project; it activates when a request matches its description, or explicitly with `/evidence-based-data-science-workflow`. Invoking it registers the data guard hook for the rest of the session.

To install on another machine: copy this directory to `~/.claude/skills/`, then set up the RAG (below). The hook command in the frontmatter uses `$HOME/.claude/skills/...`; if you install elsewhere, edit that path (`validate_skill.py` checks it resolves).

## Local books RAG

```bash
S=~/.claude/skills/evidence-based-data-science-workflow/scripts
# isolated environment for indexing (PyMuPDF); search itself needs only python3
uv venv ~/.local/share/evidence-based-data-science-workflow/venv
uv pip install --python ~/.local/share/evidence-based-data-science-workflow/venv/bin/python pymupdf pytest hypothesis pyyaml \
    pandas scikit-learn jupyter nbclient matplotlib seaborn statsmodels   # skill tests only; projects use their own env
cd /path/to/books && python3 $S/setup_rag.py --library-root "$PWD"
python3 $S/index_local_books.py
python3 $S/validate_rag.py
```

Locations: config `~/.config/evidence-based-data-science-workflow/config.json` (override with `EBDSW_CONFIG`), artifacts `~/.local/share/evidence-based-data-science-workflow/` (manifest, extracted text, index, catalog, logs, venv). Books are never modified or copied. After adding/removing books run `index_local_books.py` (incremental). After moving the library: `setup_rag.py --library-root <new> --force`. Bibliographic metadata for local copies lives in `metadata/catalog.json` (matched by SHA-256). Details: `references/local-books-rag.md`.

## Using it in a project

The agent will: inspect the project, interview you briefly (via `mattpocock-skills:grilling` when available), run `ds_policy.py init`, propose the project's single train/test split and make it after your approval (`make_train_test_split.py`; the training split then serves EDA, modelling and internal validation, the test split stays sealed until the final evaluation; this is only the default — an explicit instruction from you, in the initial prompt of the feature/project or at any later moment, replaces it with a different split or no split), explore in a notebook, audit and review it, present observations and options, and stop. You steer the next step. Approvals are yours only:

```
! python3 ~/.claude/skills/evidence-based-data-science-workflow/scripts/ds_policy.py approve D-003 --note "ok"
```

(`ds_policy.py status` shows the phase, pending decisions and what the next phase requires.)

## Tests

```bash
cd ~/.claude/skills/evidence-based-data-science-workflow
~/.local/share/evidence-based-data-science-workflow/venv/bin/python -m pytest -q          # unit + integration
~/.local/share/evidence-based-data-science-workflow/venv/bin/python -m pytest -q -m real_library   # real library only
python3 tests/behavioral/run_behavioral.py --list                                          # agent scenarios
python3 tests/behavioral/run_behavioral.py --all --model sonnet --budget 1.0               # costs API usage
```

## Limitations

- The guard hook runs only after the skill is loaded in a session and only in projects with `.ds-workflow/data-policy.json`. It matches paths in tool inputs, shell commands, code being written and scripts being executed; obfuscated paths, data read by imported modules, MCP tools or user-typed `!` commands are outside its reach.
- "Human-only approval" relies on the hook blocking the agent's own `approve` calls; someone with shell access can still edit `.ds-workflow/decisions.jsonl`. The log is an audit trail, not a security boundary.
- Retrieval is lexical (BM25): no synonyms or paraphrases. Query in each book's language and with several phrasings. Semantic search was not installed (model download) and can be added later.
- Scanned books depend on their existing OCR layer; noisy OCR is flagged per passage. No OCR engine is installed.
- Printed page numbers are inferred from running headers when PDFs lack page labels; the method is shown per result.
- Behavioural tests grade observable behaviour with heuristics; passing them is evidence, not proof, that the agent follows the workflow.
