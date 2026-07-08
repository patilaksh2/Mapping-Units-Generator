"""
Soil Mapping Unit (MU) Generator
=================================
QGIS Python console script that fills in blank Mapping Unit (MU) codes on a
soil survey polygon layer using a hybrid pipeline:

    Tier 0 : IDW interpolation from ground-truth points   (highest authority)
    Tier 1 : trained classifier (Random Forest, or k-NN fallback)
    Tier 2 : rule-based physiography/geology keyword matching
    Tier 3 : nearest already-mapped polygon
    Tier 4 : flagged default (last resort)

Must be run from inside QGIS: Plugins -> Python Console -> Show Editor ->
paste this file -> Run.

Everything you need to change lives in the CONFIGURATION block below. The
logic beneath it should not need editing for normal use.
"""

import math
import random
import statistics
from collections import Counter, defaultdict

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsSpatialIndex,
    QgsCoordinateReferenceSystem,
    QgsVectorFileWriter,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant
import processing

# Optional dependencies - the script works without them.
try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import StratifiedKFold, train_test_split
    from sklearn.metrics import confusion_matrix, accuracy_score
    HAVE_SKLEARN = True
except ImportError:
    HAVE_SKLEARN = False


# =============================================================================
# CONFIGURATION - edit everything in this block for your own project.
# Nothing below this block should need to change for normal use.
# =============================================================================

# ---- Paths ------------------------------------------------------------------
# Replace these with paths on your own machine, or better: load them from an
# untracked config.py / .env so real paths never get committed to git.
SOIL_LAYER    = r"./data/prefieldpoly.shp"
PHYSIO_LAYER  = r"./data/physiography.shp"
GEOLOGY_LAYER = r"./data/geology.shp"
OUTPUT_SHP    = r"./output/MappingUnit_Output.shp"

# Ground-truth point layers used for IDW interpolation (Tier 0 evidence).
# Leave this list empty if you don't have field point data.
GROUND_TRUTH_POINT_LAYERS = [
    # r"./data/field_points_2026.shp",
]

# ---- Field names --------------------------------------------------------
PHYSIO_NAME_FLD  = "legend_sho"   # name/legend field on the physiography layer
GEOLOGY_NAME_FLD = "lithologic"   # lithology field on the geology layer
EXISTING_MU_FLD  = "MU"           # field on the soil layer holding known MU codes
POINT_MU_FLD     = "MU"           # field on the ground-truth points holding MU codes

# ---- CRS ------------------------------------------------------------------
# Set this to the correct UTM zone (or other projected CRS) for your study
# area. Using a projected CRS matters here because distances (IDW search
# radius, nearest-neighbour fallback) are computed in map units.
TARGET_CRS = "EPSG:32643"

# ---- Non-soil polygon codes --------------------------------------------
# MU codes (or existing values in EXISTING_MU_FLD) that represent non-soil
# land uses and should be excluded from classification entirely.
NON_SOIL_CODES = {"WB", "HAB", "ROAD", "QUARRY"}  # water body, habitation, etc.

# ---- Series lookup table -------------------------------------------------
# This is the single most project-specific part of the script. It maps a
# short series code to attributes used for validation/reporting. Rebuild
# this table for your own survey area - do not assume the example values
# below apply to your district.
#
#   code : (typical_depth_class, description)
SERIES_LOOKUP = {
    # "AD": (3, "example series - replace with your own"),
    # "CH": (5, "example series - replace with your own"),
}

# ---- Tuning parameters ---------------------------------------------------
IDW_SEARCH_RADIUS_M   = 500.0   # metres
IDW_POWER             = 2.0     # inverse-distance weighting power
IDW_CONFIDENCE_MIN    = 80.0    # % - auto-accept IDW result above this
CLASSIFIER_CONF_MIN   = 60.0    # % - accept classifier result above this
KNN_NEIGHBOURS        = 5
SPATIAL_BLOCK_CV_SIZE = 5       # number of spatial blocks for block CV
RANDOM_SEED           = 42

random.seed(RANDOM_SEED)
if HAVE_NUMPY:
    np.random.seed(RANDOM_SEED)


# =============================================================================
# PIPELINE LOGIC - shouldn't normally need edits below this line.
# =============================================================================

def log(step, total, msg):
    print(f"[{step}/{total}] {msg}")


def load_layer(path, name):
    layer = QgsVectorLayer(path, name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Failed to load layer: {path}")
    return layer


def ensure_field(layer, name, qtype, length=None):
    """Add a field to a layer if it doesn't already exist."""
    if layer.fields().indexFromName(name) == -1:
        field = QgsField(name, qtype)
        if length:
            field.setLength(length)
        layer.dataProvider().addAttributes([field])
        layer.updateFields()


def safe_str(value, max_len):
    """Defensive truncation before writing a string attribute (fixes the
    field-width save failure some shapefile drivers hit on long strings)."""
    if value is None:
        return ""
    s = str(value)
    return s[:max_len] if len(s) > max_len else s


# ---- Step 0: geometry fix + CRS alignment ----------------------------------

def fix_geometry_and_reproject(layer, target_crs):
    fixed = processing.run(
        "native:fixgeometries", {"INPUT": layer, "OUTPUT": "memory:"}
    )["OUTPUT"]

    if fixed.crs().authid() != target_crs:
        fixed = processing.run(
            "native:reprojectlayer",
            {"INPUT": fixed, "TARGET_CRS": QgsCoordinateReferenceSystem(target_crs),
             "OUTPUT": "memory:"},
        )["OUTPUT"]
    return fixed


# ---- Steps 3-4: spatial joins (largest overlap) ----------------------------

def spatial_join_largest_overlap(base_layer, join_layer, join_field, output_field):
    joined = processing.run(
        "native:joinattributesbylocation",
        {
            "INPUT": base_layer,
            "JOIN": join_layer,
            "PREDICATE": [0],  # intersects
            "JOIN_FIELDS": [join_field],
            "METHOD": 0,       # take attributes of the feature with the largest overlap
            "DISCARD_NONMATCHING": False,
            "PREFIX": "",
            "OUTPUT": "memory:",
        },
    )["OUTPUT"]
    return joined


# ---- Step 5: decompose existing MU codes / build training set -------------

def decompose_mu(mu_code):
    """
    Split a composite MU code into its components. This parser assumes the
    SeriesCode+DepthNum+SurfaceTexCode+SlopeCode+ErosionNum convention
    described in the README - adjust the parsing rule if your own MU code
    convention differs.
    """
    if not mu_code:
        return None
    mu_code = str(mu_code).strip()
    letters = "".join(c for c in mu_code if c.isalpha())
    digits = "".join(c for c in mu_code if c.isdigit())
    series = letters[:2] if len(letters) >= 2 else letters
    depth = digits[0] if len(digits) >= 1 else ""
    slope = letters[2:3] if len(letters) >= 3 else ""
    erosion = digits[1] if len(digits) >= 2 else ""
    return {
        "SeriesCode": series,
        "DepthNum": depth,
        "SurTexCode": letters[len(series):len(series) + 1],
        "SlopeCode": slope,
        "ErosionNum": erosion,
    }


def build_training_set(layer, existing_mu_fld, non_soil_codes):
    rows, labels = [], []
    for feat in layer.getFeatures():
        mu = feat[existing_mu_fld]
        if not mu or str(mu).strip().upper() in non_soil_codes:
            continue
        comps = decompose_mu(mu)
        if not comps or not comps["SeriesCode"]:
            continue
        physio = feat[PHYSIO_NAME_FLD] if PHYSIO_NAME_FLD in feat.fields().names() else ""
        geology = feat[GEOLOGY_NAME_FLD] if GEOLOGY_NAME_FLD in feat.fields().names() else ""
        centroid = feat.geometry().centroid().asPoint()
        rows.append({
            "physio": physio or "",
            "geology": geology or "",
            "x": centroid.x(),
            "y": centroid.y(),
            "fid": feat.id(),
        })
        labels.append(comps["SeriesCode"])
    return rows, labels


# ---- Feature encoding for the classifier -----------------------------------

def encode_features(rows, physio_vocab, geology_vocab):
    """One-hot-ish encode physiography/geology + normalised coordinates."""
    encoded = []
    for r in rows:
        vec = [1.0 if r["physio"] == p else 0.0 for p in physio_vocab]
        vec += [1.0 if r["geology"] == g else 0.0 for g in geology_vocab]
        vec += [r["x"], r["y"]]
        encoded.append(vec)
    return encoded


# ---- Step 6: train & validate classifier -----------------------------------

def train_classifier(rows, labels):
    physio_vocab = sorted({r["physio"] for r in rows})
    geology_vocab = sorted({r["geology"] for r in rows})
    X = encode_features(rows, physio_vocab, geology_vocab)
    y = labels

    class_counts = Counter(y)
    print(f"  Training set: {len(y)} samples, {len(class_counts)} series classes")
    print(f"  Class distribution: {dict(class_counts)}")

    if HAVE_SKLEARN and HAVE_NUMPY and len(y) >= 10:
        X_arr = np.array(X)
        y_arr = np.array(y)

        # Stratified holdout
        try:
            X_train, X_test, y_train, y_test = train_test_split(
                X_arr, y_arr, test_size=0.2, stratify=y_arr, random_state=RANDOM_SEED
            )
        except ValueError:
            # some class has too few members to stratify - fall back to plain split
            X_train, X_test, y_train, y_test = train_test_split(
                X_arr, y_arr, test_size=0.2, random_state=RANDOM_SEED
            )

        # NOTE: trains on the true empirical class prior (no artificial
        # rebalancing), which avoids over-predicting rare classes relative
        # to their real prevalence.
        clf = RandomForestClassifier(
            n_estimators=300, random_state=RANDOM_SEED, class_weight=None
        )
        clf.fit(X_train, y_train)
        holdout_acc = accuracy_score(y_test, clf.predict(X_test))
        print(f"  Holdout accuracy: {holdout_acc * 100:.1f}%")

        # k-fold CV
        try:
            skf = StratifiedKFold(n_splits=min(5, min(class_counts.values())),
                                   shuffle=True, random_state=RANDOM_SEED)
            cv_scores = []
            for train_idx, test_idx in skf.split(X_arr, y_arr):
                fold_clf = RandomForestClassifier(
                    n_estimators=300, random_state=RANDOM_SEED
                )
                fold_clf.fit(X_arr[train_idx], y_arr[train_idx])
                cv_scores.append(fold_clf.score(X_arr[test_idx], y_arr[test_idx]))
            print(f"  k-fold CV accuracy: {statistics.mean(cv_scores) * 100:.1f}% "
                  f"(+/- {statistics.pstdev(cv_scores) * 100:.1f})")
        except ValueError:
            print("  k-fold CV skipped (too few samples in a class)")

        # Confusion matrix + feature importance
        cm = confusion_matrix(y_test, clf.predict(X_test), labels=sorted(class_counts))
        print(f"  Confusion matrix (labels={sorted(class_counts)}):\n{cm}")
        top_features = np.argsort(clf.feature_importances_)[::-1][:5]
        print(f"  Top feature indices by importance: {list(top_features)}")

        # Retrain on all data for final model
        clf.fit(X_arr, y_arr)
        return ("random_forest", clf, physio_vocab, geology_vocab)

    else:
        print("  numpy/scikit-learn not available (or too few samples) - "
              "using pure-Python weighted k-NN instead")
        return ("knn", (X, y), physio_vocab, geology_vocab)


def classify_knn(point_vec, training_X, training_y, k):
    dists = []
    for i, xv in enumerate(training_X):
        d = math.sqrt(sum((a - b) ** 2 for a, b in zip(point_vec, xv)))
        dists.append((d, training_y[i]))
    dists.sort(key=lambda t: t[0])
    nearest = dists[:k]
    weights = defaultdict(float)
    for d, label in nearest:
        w = 1.0 / (d + 1e-6)
        weights[label] += w
    total = sum(weights.values())
    best_label = max(weights, key=weights.get)
    confidence = (weights[best_label] / total) * 100 if total else 0.0
    return best_label, confidence


def predict_series(model_info, physio, geology, x, y):
    kind, model, physio_vocab, geology_vocab = model_info
    vec = [1.0 if physio == p else 0.0 for p in physio_vocab]
    vec += [1.0 if geology == g else 0.0 for g in geology_vocab]
    vec += [x, y]

    if kind == "random_forest":
        proba = model.predict_proba([vec])[0]
        classes = model.classes_
        best_idx = int(proba.argmax())
        return classes[best_idx], float(proba[best_idx] * 100)
    else:
        train_X, train_y = model
        return classify_knn(vec, train_X, train_y, KNN_NEIGHBOURS)


# ---- Tier 0: IDW from ground-truth points ----------------------------------

def load_ground_truth_points(paths, mu_field):
    points = []
    for p in paths:
        layer = load_layer(p, "ground_truth")
        for feat in layer.getFeatures():
            mu = feat[mu_field]
            if not mu:
                continue
            pt = feat.geometry().asPoint()
            points.append((pt.x(), pt.y(), str(mu)))
    return points


def idw_predict(x, y, points, radius, power):
    candidates = [(px, py, mu) for px, py, mu in points
                  if math.hypot(px - x, py - y) <= radius]
    if not candidates:
        return None, 0.0

    weights = defaultdict(float)
    total_weight = 0.0
    for px, py, mu in candidates:
        d = math.hypot(px - x, py - y)
        w = 1.0 / (d ** power + 1e-6)
        weights[mu] += w
        total_weight += w

    best_mu = max(weights, key=weights.get)
    confidence = (weights[best_mu] / total_weight) * 100 if total_weight else 0.0
    return best_mu, confidence


# ---- Tier 2: rule-based keyword matching -----------------------------------

def rule_based_match(physio, geology, series_lookup):
    """
    Very simple keyword fallback: looks for a series whose description
    shares a keyword with the physiography/geology text. Replace this with
    real domain rules for your own survey area.
    """
    text = f"{physio} {geology}".lower()
    for code, (depth, description) in series_lookup.items():
        if description and any(word in text for word in description.lower().split()):
            return code, 40.0  # low-ish confidence, rule-based tier
    return None, 0.0


# ---- Tier 3: nearest mapped polygon -----------------------------------------

def build_mapped_index(layer, existing_mu_fld, non_soil_codes):
    index = QgsSpatialIndex()
    lookup = {}
    for feat in layer.getFeatures():
        mu = feat[existing_mu_fld]
        if mu and str(mu).strip().upper() not in non_soil_codes:
            index.addFeature(feat)
            lookup[feat.id()] = str(mu)
    return index, lookup


def nearest_neighbor_mu(geom, index, lookup, layer):
    centroid = geom.centroid().asPoint()
    nearest_ids = index.nearestNeighbor(centroid, 1)
    if not nearest_ids:
        return None
    fid = nearest_ids[0]
    return lookup.get(fid)


# ---- Step 8: adjacency conflict resolution ----------------------------------

def resolve_adjacency_conflicts(layer, mu_field, source_field):
    """
    Ensures no two touching polygons share an identical MU, unless both are
    surveyor-verified ('existing') - those are never altered.
    """
    features = list(layer.getFeatures())
    index = QgsSpatialIndex()
    for f in features:
        index.addFeature(f)

    by_id = {f.id(): f for f in features}
    flagged = 0

    for f in features:
        if f[source_field] == "existing":
            continue
        neighbours = index.intersects(f.geometry().boundingBox())
        for nid in neighbours:
            if nid == f.id():
                continue
            other = by_id[nid]
            if other[mu_field] == f[mu_field] and f.geometry().touches(other.geometry()):
                if other[source_field] == "existing":
                    continue  # never override a surveyor-verified polygon
                # nudge the erosion digit to break the tie, flag for review
                mu = str(f[mu_field])
                if mu and mu[-1].isdigit():
                    bumped = mu[:-1] + str((int(mu[-1]) + 1) % 10)
                else:
                    bumped = mu + "1"
                layer.changeAttributeValue(
                    f.id(), layer.fields().indexFromName(mu_field), bumped
                )
                flagged += 1
    return flagged


# ---- Main pipeline -----------------------------------------------------------

def run_pipeline():
    TOTAL_STEPS = 10

    log(0, TOTAL_STEPS, "Fixing geometry and aligning CRS...")
    soil = load_layer(SOIL_LAYER, "soil")
    physio = load_layer(PHYSIO_LAYER, "physiography")
    geology = load_layer(GEOLOGY_LAYER, "geology")
    soil = fix_geometry_and_reproject(soil, TARGET_CRS)

    log(1, TOTAL_STEPS, "Layers loaded.")

    log(2, TOTAL_STEPS, "Loading ground-truth points for IDW...")
    gt_points = load_ground_truth_points(GROUND_TRUTH_POINT_LAYERS, POINT_MU_FLD)
    print(f"  Loaded {len(gt_points)} ground-truth points")

    log(3, TOTAL_STEPS, "Spatial join: soil <- physiography...")
    joined = spatial_join_largest_overlap(soil, physio, PHYSIO_NAME_FLD, PHYSIO_NAME_FLD)

    log(4, TOTAL_STEPS, "Spatial join: result <- geology...")
    joined = spatial_join_largest_overlap(joined, geology, GEOLOGY_NAME_FLD, GEOLOGY_NAME_FLD)

    log(5, TOTAL_STEPS, "Decomposing existing MU codes / building training set...")
    rows, labels = build_training_set(joined, EXISTING_MU_FLD, NON_SOIL_CODES)
    print(f"  {len(rows)} verified polygons available for training")

    log(6, TOTAL_STEPS, "Training & validating classifier...")
    model_info = None
    if rows:
        model_info = train_classifier(rows, labels)
    else:
        print("  No training data available - classifier tier will be skipped")

    log(7, TOTAL_STEPS, "Assigning series to unmapped polygons...")
    ensure_field(joined, "SeriesCode", QVariant.String, 20)
    ensure_field(joined, "DepthNum", QVariant.String, 5)
    ensure_field(joined, "SurTexCode", QVariant.String, 10)
    ensure_field(joined, "SlopeCode", QVariant.String, 10)
    ensure_field(joined, "ErosionNum", QVariant.String, 5)
    ensure_field(joined, "MU_New", QVariant.String, 30)
    ensure_field(joined, "MU_Source", QVariant.String, 20)
    ensure_field(joined, "MU_Conf", QVariant.Double)
    ensure_field(joined, "MU_Method", QVariant.String, 40)   # widened (v8 fix)
    ensure_field(joined, "MU_Flag", QVariant.String, 100)    # widened (v8 fix)

    mapped_index, mapped_lookup = build_mapped_index(joined, EXISTING_MU_FLD, NON_SOIL_CODES)
    method_counts = Counter()
    confidences = []

    joined.startEditing()
    for feat in joined.getFeatures():
        existing_mu = feat[EXISTING_MU_FLD]

        if existing_mu and str(existing_mu).strip().upper() not in NON_SOIL_CODES:
            comps = decompose_mu(existing_mu)
            method, conf, flag = "existing", 100.0, ""
            mu_new = str(existing_mu)
        elif existing_mu and str(existing_mu).strip().upper() in NON_SOIL_CODES:
            continue  # leave non-soil polygons untouched
        else:
            centroid = feat.geometry().centroid().asPoint()
            physio_val = feat[PHYSIO_NAME_FLD] if PHYSIO_NAME_FLD in feat.fields().names() else ""
            geology_val = feat[GEOLOGY_NAME_FLD] if GEOLOGY_NAME_FLD in feat.fields().names() else ""

            series, conf, method, flag = None, 0.0, "fallback", ""

            # Tier 0: IDW
            if gt_points:
                idw_mu, idw_conf = idw_predict(
                    centroid.x(), centroid.y(), gt_points, IDW_SEARCH_RADIUS_M, IDW_POWER
                )
                if idw_mu and idw_conf >= IDW_CONFIDENCE_MIN:
                    series, conf, method = idw_mu, idw_conf, "idw_ground_truth"

            # Tier 1: classifier
            if series is None and model_info:
                pred_series, pred_conf = predict_series(
                    model_info, physio_val, geology_val, centroid.x(), centroid.y()
                )
                if pred_conf >= CLASSIFIER_CONF_MIN:
                    series, conf, method = pred_series, pred_conf, "ml_classifier"

            # Tier 2: rule-based
            if series is None:
                rule_series, rule_conf = rule_based_match(physio_val, geology_val, SERIES_LOOKUP)
                if rule_series:
                    series, conf, method = rule_series, rule_conf, "rule_based"

            # Tier 3: nearest neighbour
            if series is None:
                nn_mu = nearest_neighbor_mu(feat.geometry(), mapped_index, mapped_lookup, joined)
                if nn_mu:
                    comps = decompose_mu(nn_mu)
                    series = comps["SeriesCode"] if comps else nn_mu
                    conf, method = 30.0, "nearest_neighbor"

            # Tier 4: flagged default
            if series is None:
                series, conf, method = "UNK", 0.0, "fallback"
                flag = "needs manual review - no evidence matched"

            comps = {"SeriesCode": series, "DepthNum": "", "SurTexCode": "",
                     "SlopeCode": "", "ErosionNum": ""}
            mu_new = series

        method_counts[method] += 1
        if method != "existing":
            confidences.append(conf)

        fields = joined.fields()
        joined.changeAttributeValue(feat.id(), fields.indexFromName("SeriesCode"),
                                     safe_str(comps.get("SeriesCode"), 20))
        joined.changeAttributeValue(feat.id(), fields.indexFromName("DepthNum"),
                                     safe_str(comps.get("DepthNum"), 5))
        joined.changeAttributeValue(feat.id(), fields.indexFromName("SurTexCode"),
                                     safe_str(comps.get("SurTexCode"), 10))
        joined.changeAttributeValue(feat.id(), fields.indexFromName("SlopeCode"),
                                     safe_str(comps.get("SlopeCode"), 10))
        joined.changeAttributeValue(feat.id(), fields.indexFromName("ErosionNum"),
                                     safe_str(comps.get("ErosionNum"), 5))
        joined.changeAttributeValue(feat.id(), fields.indexFromName("MU_New"),
                                     safe_str(mu_new, 30))
        joined.changeAttributeValue(feat.id(), fields.indexFromName("MU_Source"),
                                     "existing" if method == "existing" else "predicted")
        joined.changeAttributeValue(feat.id(), fields.indexFromName("MU_Conf"), conf)
        joined.changeAttributeValue(feat.id(), fields.indexFromName("MU_Method"),
                                     safe_str(method, 40))
        joined.changeAttributeValue(feat.id(), fields.indexFromName("MU_Flag"),
                                     safe_str(flag, 100))

    joined.commitChanges()

    log(8, TOTAL_STEPS, "Resolving adjacency conflicts...")
    joined.startEditing()
    flagged = resolve_adjacency_conflicts(joined, "MU_New", "MU_Source")
    joined.commitChanges()
    print(f"  {flagged} polygons adjusted to resolve adjacency conflicts")

    log(9, TOTAL_STEPS, "Saving output shapefile...")
    error = QgsVectorFileWriter.writeAsVectorFormat(
        joined, OUTPUT_SHP, "UTF-8", joined.crs(), "ESRI Shapefile"
    )
    if error[0] != QgsVectorFileWriter.NoError:
        raise RuntimeError(f"Failed to save output shapefile: {error}")
    print(f"  Saved: {OUTPUT_SHP}")

    log(10, TOTAL_STEPS, "Summary report")
    print("  MU assignment method breakdown:")
    for method, count in method_counts.most_common():
        print(f"    {method}: {count}")
    if confidences:
        print(f"  Mean confidence (predicted polygons): "
              f"{statistics.mean(confidences):.1f}%")
    flagged_count = sum(
        1 for f in joined.getFeatures()
        if f["MU_Flag"] not in (None, "")
    )
    print(f"  Polygons flagged for manual review: {flagged_count}")

    QgsProject.instance().addMapLayer(joined)
    print("\nDone. Output layer added to the project.")


if __name__ == "__main__":
    run_pipeline()
