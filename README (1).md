# Soil Mapping Unit (MU) Generator

A QGIS Python console script that automates assignment of **Soil Mapping Units (MU)** to survey polygons for semi-detailed soil surveys, using a hybrid pipeline of ground-truth interpolation, a trained classifier, rule-based physiography/geology matching, and nearest-neighbour fallback — validated at every stage before it's trusted.

## What it does

Given a soil survey polygon layer where **some** polygons already carry a surveyor-verified Mapping Unit and others are still blank, the script fills in the blanks using a decision hierarchy that mirrors how a surveyor would actually reason:

1. **Ground-truth field points (highest authority)** — IDW-weighted spatial interpolation from real point observations within a search radius; auto-accepted above a confidence threshold.
2. **Supervised classifier** — trained on the polygons that already have a valid MU (physiography + geology + location → series). Uses a Random Forest if `scikit-learn`/`numpy` are available, otherwise falls back to a pure-Python weighted k-NN — both are cross-validated before use.
3. **Rule-based physiography/geology keyword matching** — used when the classifier isn't confident enough.
4. **Nearest mapped polygon** — last resort before an explicitly flagged default.

Every prediction is tagged with *how* it was decided (`MU_Method`), a confidence score (`MU_Conf`), and a flag for anything that needs manual review (`MU_Flag`) — nothing is silently guessed.

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
| 6 | Train & validate the supervised classifier (holdout, k-fold CV, spatial-block CV, confusion matrix, feature importance) |
| 7 | Assign series to unmapped polygons (hybrid ML + rules + nearest-neighbour) |
| 8 | Resolve adjacency conflicts — no two touching polygons may share an identical MU |
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
- **Field names** — `PHYSIO_NAME_FLD`, `GEOLOGY_NAME_FLD`, `EXISTING_MU_FLD`, `POINT_MU_FLD`.
- **CRS** — `TARGET_CRS` (set this to the correct UTM zone for your study area).
- **Series lookup table** — the mapping-unit-to-series lookup is specific to whatever region you're surveying; rename/rebuild it for your own area rather than assuming a pre-built table.
- **Tuning parameters** — confidence thresholds, IDW search radius/power, spatial-block CV size, k-NN neighbour count — documented inline where each is defined.

> **Before running:** replace all paths in the Configuration section with paths relevant to your own machine and project folder (e.g. `r"./data/prefieldpoly.shp"`), or move them into a separate untracked `config.py`/`.env`. Don't commit real local file paths, personal folder names, or collaborator names into the public repo.

## Output

An output shapefile with new fields:

| Field | Meaning |
|---|---|
| `SeriesCode`, `DepthNum`, `SurTexCode`, `SlopeCode`, `ErosionNum` | Decomposed MU components |
| `MU_New` | Final assembled MU code |
| `MU_Source` | `existing` or `predicted` |
| `MU_Conf` | Confidence % (predicted polygons only) |
| `MU_Method` | `idw_ground_truth`, `ml_classifier`, `rule_based`, `nearest_neighbor`, or `fallback` |
| `MU_Flag` | Anything flagged for manual review |

## Known limitations

- Requires the QGIS Python environment; won't run in plain Python.
- Non-soil polygons (habitation, water bodies) are excluded from classification via a configurable exclusion list.
- The series lookup table is region-specific by design. Adapting this script to a new survey area means rebuilding that table for local series names/codes.

## License

See [`LICENSE`](LICENSE).
