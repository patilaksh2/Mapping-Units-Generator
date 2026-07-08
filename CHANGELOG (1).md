# Changelog

All notable changes to this project are documented here.

## [v8] — current
- Widened shapefile field definitions (`MU_Method`, `MU_Flag`, etc.) to fix a save failure caused by field-width limits truncating method/flag strings.
- Added defensive truncation before writing attribute values, so any future over-length string is safely clipped instead of failing the write.

## [v7]
- Replaced the v6 rule-based-only assignment with a **trained supervised classifier** (Random Forest when `scikit-learn`/`numpy` are available, pure-Python weighted k-NN otherwise) for empty-polygon series assignment.
- Added validation: stratified holdout accuracy, k-fold cross-validation, spatial-block cross-validation, confusion matrix, and feature importance — predictions are only trusted above a confidence threshold.
- Below the confidence threshold, falls back in order to: rule-based keyword match → nearest mapped polygon → flagged default.
- Added ground-truth point layer support (IDW-weighted spatial interpolation) as the highest-authority evidence tier, consulted before the classifier.
- Added adjacency-conflict resolution: touching polygons can no longer share an identical MU (surveyor-verified polygons are never altered).
- Fixed the depth-class prevalence issue where `class_weight="balanced_subsample"` over-predicted rarer classes (e.g. `MR`, depth 4) relative to the true prevalence of depth-5 series (`CH`, `AN`, `E`, `H`, `M`, `TI`) — now trains on the true empirical class prior.

## [v6]
- Initial rule-based/heuristic approach: unmapped polygons filled using physiography/geology keyword matching, falling back to "nearest mapped polygon" if no rule matched.
- Identified as a single, unvalidated heuristic that mis-assigned series in ambiguous zones — motivated the v7 rewrite.
