# Soil Mapping Unit (MU) Generator

A QGIS Python console script that automates assignment of **Soil Mapping Units (MU)** to survey polygons for semi-detailed soil surveys, using a hybrid pipeline of ground-truth interpolation, a trained classifier, rule-based physiography/geology matching, and nearest-neighbour fallback — validated at every stage before it's trusted.

## What it does

Given a soil survey polygon layer where **some** polygons already carry a surveyor-verified Mapping Unit and others are still blank, the script fills in the blanks using a decision hierarchy that mirrors how a surveyor would actually reason:

1. **Ground-truth field points (highest authority)** — IDW-weighted spatial interpolation from real point observations within a search radius; auto-accepted above a confidence threshold.
2. **Supervised classifier** — trained on the polygons that already have a valid MU (physiography + geology + location → series). Uses a Random Forest if `scikit-learn`/`numpy` are available — validated with a stratified holdout and stratified k-fold cross-validation before use — otherwise falls back to a pure-Python weighted k-NN. Its prediction is checked against the primary-driver evidence table (see *Method* below).
3. **Rule-based physiography/geology keyword matching** — used when neither the evidence table nor the classifier produces a series.
4. **Nearest mapped polygon** — last resort before an explicitly flagged default.

Every prediction is tagged with *how* it was decided (`MU_Method`), a confidence score (`MU_Conf`), and a flag for anything that needs manual review (`MU_Flag`) — nothing is silently guessed.

## Method: SLUSI two-stage driver hierarchy

Assignment follows the two-stage driver hierarchy used in SLUSI-style soil survey — the **primary drivers confirm the soil series first**; only then do the **secondary drivers complete the mapping unit**.

**Stage 1 — confirm the series (primary drivers: Geology, Physiography, Depth).** From the already-mapped polygons the script builds a *series-evidence table*: for every series, the geology values and physiography values it occurs with, its depth-class distribution, its polygon count (support), and its modal MU components. For an unmapped polygon, candidate series are those whose observed geology **and** physiography both match the polygon's joined values. Candidates are ranked by (1) depth agreement — the share of the candidate's polygons carrying the polygon's depth class when a depth field is configured (`DEPTH_FLD`), otherwise the share carrying the candidate's modal depth class; (2) the classifier's probability for that series; (3) support. The trained classifier (Random Forest / k-NN) still predicts a series, but it operates *within* this evidence: if its top series is among the candidates it is accepted (`ml_classifier`); if candidates exist but the classifier's pick is not one of them, the top-ranked candidate wins (`primary_driver_match`, confidence = 50 + 30 × depth-agreement share + 20 × relative support) and the polygon is flagged "series set by primary-driver evidence over classifier". Only when the candidate set is empty (an unseen geology/physiography combination) does the pipeline fall through to the classifier alone (if it clears `CLASSIFIER_CONF_MIN`), then keyword rules, then the nearest mapped polygon, then the flagged `UNK` default. Tier 0 (IDW from ground-truth points, which returns a complete MU code) stays above Stage 1 as direct field evidence.

**Stage 2 — complete the mapping unit (secondary drivers).** Once the series is fixed, `DepthNum`, `SurTexCode`, `SlopeCode` and `ErosionNum` are filled from the evidence table's modal component values for that series — preferring polygons in the same physiography, falling back to series-wide modal values. A configured `DEPTH_FLD` value supplies the depth class, and `SLOPE_FLD` the slope code, when populated. `MU_New` is then assembled as the full code `Series + Depth + SurfaceTex + Slope + Erosion`. A component with no modal value anywhere is left blank and flagged (`MU incomplete: <component> unknown`) — values are never invented.

### Output MU code format

```
SeriesCode + DepthNum + SurfaceTexCode + SlopeCode + ErosionNum
Example: AD3rB2
```

This example format is illustrative — your own series-code scheme will differ depending on the classification system you follow.

### Pipeline steps

| Step | What happens |
|---|---|
| 0 | Geometry fix + CRS alignment to your target CRS |
| 1 | Load soil, physiography, and geology layers |
| 2 | Load ground-truth point layers for IDW (Tier 0 evidence) |
| 3 | Spatial join: soil ← physiography (largest overlap) |
| 4 | Spatial join: result ← geology (largest overlap) |
| 5 | Disintegrate existing MU codes & build the training set |
| 6 | Train & validate the supervised classifier (stratified holdout, stratified k-fold CV, confusion matrix, feature importance) |
| 7 | Assign series to unmapped polygons (two-stage: primary-driver evidence + classifier confirm the series, then Stage 2 completes the MU) |
| 8 | Flag adjacency conflicts — touching polygons that share an identical MU are flagged for manual review (MU codes are never altered) |
| 9 | Save output shapefile |
| 10 | Print summary report |

## Requirements

- **QGIS** (developed against the QGIS 3.x bundled Python environment) — the script uses `qgis.core` and `processing`, so it must be run from inside QGIS, not a standalone Python interpreter.
- Python packages bundled with QGIS's own environment:
  - `numpy` *(optional — enables the Random Forest backend and its validation metrics)*
  - `scikit-learn` *(optional — enables the Random Forest backend)*

  The script works without either; it automatically falls back to a pure-Python weighted k-NN classifier if they're missing.

## Input data

The script expects, at minimum:

- A soil survey polygon shapefile (e.g. `prefieldpoly.shp`) with an existing `MU` field (blank where unmapped).
- A physiography polygon shapefile with a name/legend field (e.g. `legend_sho`).
- A geology polygon shapefile with a lithology field (e.g. `lithologic`).
- (Optional but recommended) Ground-truth point shapefiles with an `MU` field, for the IDW Tier-0 evidence step.

## How to run

1. Open **QGIS → Plugins → Python Console**.
2. Click **Show Editor**.
3. Open (or paste) the script.
4. Update the paths and field names in the **Configuration** section at the top (see below).
5. Click **▶ Run**.

Progress and a full summary report (MU counts, assignment-method breakdown, confidence distribution, and any flags needing review) print to the console.

## Configuration

All settings live in one block near the top of the script — no need to touch the logic below it:

- **Paths** — `SOIL_LAYER`, `PHYSIO_LAYER`, `GEOLOGY_LAYER`, `OUTPUT_SHP`, and the ground-truth point layer paths.
- **Field names** — `PHYSIO_NAME_FLD`, `GEOLOGY_NAME_FLD`, `EXISTING_MU_FLD`, `POINT_MU_FLD`, plus the optional `DEPTH_FLD` / `SLOPE_FLD` (soil-layer fields holding a known depth class / slope code per polygon; blank = disabled).
- **CRS** — `TARGET_CRS` (set this to the correct UTM zone for your study area).
- **Series lookup table** — the mapping-unit-to-series lookup is specific to whatever region you're surveying; rename/rebuild it for your own area rather than assuming a pre-built table.
- **Tuning parameters** — confidence thresholds, IDW search radius/power, k-NN neighbour count — documented inline where each is defined.

> **Before running:** replace all paths in the Configuration section with paths relevant to your own machine and project folder (e.g. `r"./data/prefieldpoly.shp"`), or move them into a separate untracked `config.py`/`.env`. Don't commit real local file paths, personal folder names, or collaborator names into the public repo.

## Output

An output shapefile with new fields:

| Field | Meaning |
|---|---|
| `SeriesCode`, `DepthNum`, `SurTexCode`, `SlopeCode`, `ErosionNum` | Decomposed MU components |
| `MU_New` | Final assembled MU code |
| `MU_Source` | `existing` or `predicted` |
| `MU_Conf` | Confidence % (predicted polygons only) |
| `MU_Method` | `idw_ground_truth`, `primary_driver_match`, `ml_classifier`, `rule_based`, `nearest_neighbor`, or `fallback` |
| `MU_Flag` | Anything flagged for manual review |

## Known limitations

- Requires the QGIS Python environment; won't run in plain Python.
- Non-soil polygons (habitation, water bodies) are excluded from classification via a configurable exclusion list.
- The series lookup table is region-specific by design. Adapting this script to a new survey area means rebuilding that table for local series names/codes.

## License

See [`LICENSE`](LICENSE).
