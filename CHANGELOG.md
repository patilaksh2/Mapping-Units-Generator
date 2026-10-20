# Changelog

All notable changes to this project are documented here.

## [v10.1] — 2026-10-20 — Audit fixes
- **B3 (adjacency):** the adjacency flag step now only considers polygons that actually received a prediction (`MU_Source` = "predicted"), on both sides of the comparison. Non-soil polygons — skipped during assignment, so their `MU_Source`/`MU_New` are NULL — can no longer receive spurious adjacency flags (NULL == NULL plus `touches()` previously earned them one).
- **Null-geometry guard (D2):** the QGIS script had the same hole as the ArcGIS port — an unmapped polygon without geometry would be positioned at (0, 0) by `centroid().asPoint()` and silently assigned from there. Such polygons are now skipped in assignment, left blank, and flagged `missing geometry - skipped`; geometry-less polygons are also excluded from the training set and the nearest-neighbour source index.
- **Documentation corrections:** the no-`DEPTH_FLD` depth term is now called a *depth-concentration weight* in the README and code comments (it is true *depth agreement* only when `DEPTH_FLD` supplies the polygon's own depth class); `MU_Conf` is documented as a heuristic confidence score (0–100), not a calibrated probability, and its output-table description now notes that existing polygons carry 100; the deliberate design rule that an in-candidate classifier pick is accepted at its model probability *without* the `CLASSIFIER_CONF_MIN` gate (B4) is now stated explicitly in the README and at the acceptance code; the intro's "validated at every stage" claim was softened to match reality (classifier scores are reported, not used as a gate); the run summary prints the *mean* confidence (the promised "confidence distribution" was removed); the "Disintegrate" typo in the step table was fixed; the limitations section now discloses random (non-spatial) cross-validation, coordinates as classifier features, Stage 2 modal homogenisation of within-series variation, and literal geology/physiography name matching.

## [v10] — 2026-10-20 — SLUSI two-stage driver method
- Assignment restructured into the SLUSI two-stage driver hierarchy. **Stage 1 (primary drivers — geology, physiography, depth)** confirms the soil series: a series-evidence table is built from the already-mapped polygons (per series: observed geology/physiography values, depth-class distribution, support, modal MU components overall and per physiography), and an unmapped polygon's candidate series must match its geology AND physiography. Candidates are ranked by depth agreement, then classifier probability, then support.
- The classifier now works within the evidence: its top series is accepted (`ml_classifier`) only when it is among the candidates; otherwise the top-ranked candidate wins as `primary_driver_match` (confidence = 50 + 30 × depth-agreement share + 20 × relative support) and the polygon is flagged "series set by primary-driver evidence over classifier". With no candidates (unseen geology/physiography combination), the previous fall-through order is preserved: classifier alone above `CLASSIFIER_CONF_MIN` → keyword rules → nearest mapped polygon → flagged `UNK`.
- **Stage 2 (secondary drivers)** completes the mapping unit for every Stage-1 assignment (`primary_driver_match`, `ml_classifier`, `rule_based`, `nearest_neighbor`): DepthNum/SurTexCode/SlopeCode/ErosionNum are filled with the series' modal component values (same-physiography polygons preferred, series-wide modal as fallback) and `MU_New` is assembled as the full Series+Depth+Texture+Slope+Erosion code — predicted polygons no longer carry a bare series code. Components with no modal value anywhere stay blank and are flagged `MU incomplete: <component> unknown`; nothing is invented.
- New optional configuration: `DEPTH_FLD` (soil-layer field with a known depth class per polygon — feeds Stage 1 depth agreement and supplies the Stage 2 depth) and `SLOPE_FLD` (soil-layer slope-code field — overrides the Stage 2 modal slope). Empty (the default) disables each.
- Tier 0 (IDW ground truth) unchanged and still the highest authority; surveyor-verified polygons unchanged. Added `tests/test_series_evidence.py` covering single-candidate matching, depth-agreement tie-breaking, the empty-candidate case, the confidence formula and Stage 2 completion.

## [v9] — 2026-10-20 — Review fixes
- Renamed the misnamed ` (1)` files (`README (1).md`, `LICENSE (1)`, `gitignore (1)`, etc.) to their proper names so GitHub/git actually recognise them; filled in the LICENSE copyright name.
- Fixed the spatial joins silently duplicating polygons: `native:joinattributesbylocation` now uses METHOD 2 (largest overlap) instead of 0 (one-to-many).
- Rewrote `decompose_mu()` with a strict regex for the Series+Depth+Texture+Slope+Erosion grammar — the old positional parser wrote the wrong character into SlopeCode; added unit tests (`tests/test_decompose_mu.py`).
- All input layers (soil, physiography, geology, ground-truth points) are now reprojected to `TARGET_CRS` and verified, instead of the soil layer only — CRS mismatches previously failed silently.
- k-NN fallback: coordinates are now z-score normalised from the training set, so raw UTM magnitudes no longer dominate the distance calculation.
- Adjacency handling no longer rewrites MU codes to break ties; conflicts are flagged in `MU_Flag` for manual review instead.
- Tier 0 (IDW) results are now decomposed like every other tier, so `SeriesCode` holds the series and `MU_New` the full MU code.
- Documentation corrected: the validation actually performed is a stratified holdout + stratified k-fold CV (an earlier draft claimed spatial-block CV, which was never implemented); the dead `SPATIAL_BLOCK_CV_SIZE` setting was removed.
- Robustness: whitespace-only MU values are treated as unmapped; the classifier now prints top feature names instead of raw indices; an untracked `config.py` (see `config.py.example`) now genuinely overrides the built-in configuration defaults.

## [v8]
- Widened shapefile field definitions (`MU_Method`, `MU_Flag`, etc.) to fix a save failure caused by field-width limits truncating method/flag strings.
- Added defensive truncation before writing attribute values, so any future over-length string is safely clipped instead of failing the write.

## [v7]
- Replaced the v6 rule-based-only assignment with a **trained supervised classifier** (Random Forest when `scikit-learn`/`numpy` are available, pure-Python weighted k-NN otherwise) for empty-polygon series assignment.
- Added validation: stratified holdout accuracy, stratified k-fold cross-validation, confusion matrix, and feature importance — predictions are only trusted above a confidence threshold.
- Below the confidence threshold, falls back in order to: rule-based keyword match → nearest mapped polygon → flagged default.
- Added ground-truth point layer support (IDW-weighted spatial interpolation) as the highest-authority evidence tier, consulted before the classifier.
- Added adjacency-conflict resolution: touching polygons can no longer share an identical MU (surveyor-verified polygons are never altered).
- Fixed the depth-class prevalence issue where `class_weight="balanced_subsample"` over-predicted rarer classes (e.g. `MR`, depth 4) relative to the true prevalence of depth-5 series (`CH`, `AN`, `E`, `H`, `M`, `TI`) — now trains on the true empirical class prior.

## [v6]
- Initial rule-based/heuristic approach: unmapped polygons filled using physiography/geology keyword matching, falling back to "nearest mapped polygon" if no rule matched.
- Identified as a single, unvalidated heuristic that mis-assigned series in ambiguous zones — motivated the v7 rewrite.
