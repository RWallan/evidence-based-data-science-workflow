# Multivariate analysis

Core sources: [@johnson_wichern2007] (multivariate normal, inference on mean vectors, MANOVA, multivariate regression, PCA, factor analysis, canonical correlation, discrimination and classification, clustering), [@james_islr] and [@hastie_esl] (statistical-learning view). Local search example: `search_references.py "principal components scaling correlation matrix" --doc johnson_wichern2007`.

## Principal component analysis

- Decide covariance vs correlation matrix: variables on different scales → standardise (correlation) [@johnson_wichern2007].
- Report variance explained, scree plot, loadings; interpret components only when loadings are stable (check with bootstrap or split halves).
- PCA for preprocessing in predictive models must be fitted inside the CV pipeline (leakage otherwise).
- Outliers dominate PCA; inspect scores; consider robust PCA.

## Factor analysis

Latent-variable model, distinct from PCA. Choose number of factors with theory plus diagnostics; rotations change interpretation, not fit; report communalities and uniqueness.

## Inference on mean vectors / MANOVA

Hotelling's T² and MANOVA test statistics (Wilks, Pillai, Hotelling–Lawley, Roy) assume multivariate normality and equal covariance matrices (Box's M is sensitive to non-normality) [@johnson_wichern2007]. Pillai's trace is the most robust; follow up with appropriately adjusted univariate analyses or discriminant analysis.

## Discriminant analysis and classification

LDA assumes equal covariance matrices, QDA does not; evaluate with honest out-of-sample error, account for prior probabilities and misclassification costs [@johnson_wichern2007]. Compare with logistic regression and flexible learners within the same validation protocol (`machine-learning-validation.md`).

## Clustering

- Distance/similarity choice and scaling drive the result; document both.
- Hierarchical (linkage choice), k-means (spherical clusters, scale sensitivity, multiple random starts with a seed), model-based (Gaussian mixtures, BIC), density-based (DBSCAN/HDBSCAN).
- Validate: internal indices (silhouette, Davies–Bouldin, Calinski–Harabasz), stability under resampling, external validation when labels exist, and domain interpretability with the user. Clusters always "exist" in output: whether they are meaningful is a user-gated interpretation.

## Canonical correlation and multivariate regression

Use when two sets of variables or several responses are studied jointly; check assumptions as in the univariate case per response, plus residual covariance structure [@johnson_wichern2007].

## Reporting

Preprocessing (scaling, missing data handling) · method and why · number of components/factors/clusters and the criterion · stability evidence · interpretation proposed to the user.
