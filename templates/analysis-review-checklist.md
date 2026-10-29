# Analysis review checklist (inference, modelling, causal, forecasting)

- [ ] Analysis matches the frozen pre-analysis plan; deviations listed with reasons
- [ ] Test set untouched (or the breach is reported and the estimate labelled non-independent)
- [ ] Learned transformations fitted inside resampling (pipelines); no leakage sources (target-derived, future, duplicate entities)
- [ ] Validation respects groups / time / clustering
- [ ] Assumptions checked with diagnostics; remedies justified and cited
- [ ] Metrics as agreed: definition, data partition, baseline, uncertainty
- [ ] Model comparison uses the same folds, reports variability, avoids single-split winners
- [ ] Multiplicity handled
- [ ] Sensitivity analyses run as planned
- [ ] Causal language only with approved causal protocol; associations labelled as such
- [ ] Numbers in the report trace to code outputs
- [ ] Citations have locators; quotes verified (verify_citation.py)
- [ ] Tests run via record_test_run.py; evidence cited; failures resolved or reported
- [ ] audit_notebook.py and check_report.py pass
- [ ] Conclusions/recommendations marked PROPOSED or APPROVED (D-xxx)
