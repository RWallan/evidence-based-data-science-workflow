# Statistical methodology: estimation, tests and their interpretation

Core sources: [@bussab_morettin_estatistica_basica] (descriptive statistics, probability, estimation, intervals, tests, comparison of populations), [@kutner2005] (inference in regression), [@conover1999] (nonparametrics), [@wasserstein_lazar2016] (p-values), [@benjamini_hochberg1995] (multiplicity).

## Start from the estimand

State what quantity answers the question (difference in means, ratio of odds, median difference, correlation, regression coefficient under a specified model, population total) and for which population. The test or interval follows from the estimand and the design, not the other way round.

## Choosing a procedure

Consider, in this order:
1. **Design**: independent vs paired/repeated measures, clustering, stratification and weights (complex samples need design-based variance, see `sampling-and-survey-analysis.md`), time dependence.
2. **Measurement scale**: continuous, ordinal, count, binary, categorical.
3. **Estimand**: mean vs median vs distribution vs proportion.
4. **Assumptions** and how robust the procedure is to their violation (independence matters most; normality of means is often rescued by sample size; equal variances is a frequent pitfall, prefer Welch's t-test by default for two independent means).
5. **Sample size and power**: small samples make assumption checks weak and intervals wide.

Nonparametric is not automatically "safer": rank tests answer different questions (e.g. Wilcoxon–Mann–Whitney concerns stochastic ordering, not means) and have their own assumptions [@conover1999]. Choose by estimand and design.

## Intervals over bare tests

Report effect estimates with confidence (or credible) intervals and their units. A p-value measures incompatibility of the data with a specified model; it does not measure the size or importance of an effect, nor the probability that the hypothesis is true [@wasserstein_lazar2016]. Never equate "not significant" with "no effect".

## Practical vs statistical significance

Practical relevance needs a domain threshold agreed with the user. With large n, trivial effects become "significant"; with small n, important effects can be "non-significant". When significance and relevance diverge, present both readings and ask the user (user gate).

## Multiplicity

Many tests → inflated false positives. Plan the family of tests; control FWER (Bonferroni/Holm) or FDR (Benjamini–Hochberg [@benjamini_hochberg1995]) as appropriate, and report how many comparisons were run, including those not shown.

## Assumption diagnostics

Use plots first (Q–Q, residuals vs fitted, spread-location) [@nist_ehandbook; @kutner2005]; formal tests of assumptions have low power in small samples and excess power in large ones. Document what you checked and what you did about violations.

## Bootstrap and permutation

Useful when analytic sampling distributions are doubtful; respect the design (resample clusters, blocks for time series). Fix seeds and report the number of resamples.

## Reporting checklist

estimand · population · design · method and why (with citation) · assumptions checked · estimate · interval · test statistic and p-value when relevant · multiplicity handling · practical relevance (proposed, to be agreed) · limitations.
