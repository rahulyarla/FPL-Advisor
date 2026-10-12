"""Position-specific model search with rolling development and a sealed season test.

Every fold trains strictly before its first GW. Search, blending and residual
calibration use development predictions only. Test labels are read for evaluation
after the recipe is frozen; production can then learn from all finalized results.
"""

import hashlib
import itertools
import json
import platform
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from .data import POSITIONS, season_order
from .features import FEATURES, LEGACY_FEATURES, FEATURE_VERSION
from .http import DataError, utc_now, write_json

MODEL_VERSION = "feedback-ensemble-v3.0"
EXTERNAL_FEATURES = ["own_xg", "own_xga", "opponent_xg", "opponent_xga", "external_xg_available"]
SPECS = {
    "legacy_v2": {"years": 3, "decay": .85, "kind": "legacy"},
    "hist_long": {"years": 8, "decay": .8, "nodes": 15, "leaf": 60, "iterations": 220, "l2": 20},
    "hist_recent": {"years": 3, "decay": .85, "nodes": 15, "leaf": 60, "iterations": 220, "l2": 20},
    "hist_smooth": {"years": 6, "decay": .65, "nodes": 7, "leaf": 100, "iterations": 180, "l2": 50},
    "hurdle": {"years": 6, "decay": .75, "nodes": 15, "leaf": 60, "iterations": 180, "l2": 20, "kind": "hurdle"},
    "ridge_recent": {"years": 4, "decay": .8, "kind": "ridge"},
    "hist_no_external": {"years": 8, "decay": .8, "nodes": 15, "leaf": 60, "iterations": 220, "l2": 20},
}


def minute_bucket(values):
    return np.where(np.asarray(values) >= 60, "regular", np.where(np.asarray(values) >= 20, "rotation", "limited"))


def fit_bias(frame, predictions):
    """Shrink noisy residual means; never fit a correction on its evaluation rows."""
    residual = frame.target.to_numpy() - np.asarray(predictions)
    buckets = minute_bucket(frame.minutes_mean3)
    result = {}
    for bucket in ("regular", "rotation", "limited"):
        error = residual[buckets == bucket]
        result[bucket] = float(np.clip(error.sum() / (len(error) + 200), -.5, .5))
    return result


def bias_values(frame, bias):
    return np.array([bias.get(b, 0) for b in minute_bucket(frame.minutes_mean3)])


class Candidate:
    def __init__(self, name, position):
        self.name, self.position = name, position

    def fit(self, frame):
        from .model import estimator
        if self.name == "form_baseline":
            self.fitted_rows, self.fitted_seasons = len(frame), sorted(frame.season.unique())
            self.prior = float(frame.target.mean())
            return self
        spec = SPECS[self.name]
        years = sorted(frame.season.unique())[-spec["years"]:]
        frame = frame[frame.season.isin(years)]
        self.fitted_rows, self.fitted_seasons = len(frame), years
        weights = spec["decay"] ** (frame.season.str[:4].astype(int).max() - frame.season.str[:4].astype(int))
        kind = spec.get("kind", "hist")
        self.features = LEGACY_FEATURES if kind == "legacy" else [f for f in FEATURES if self.name != "hist_no_external" or f not in EXTERNAL_FEATURES]
        X = frame[self.features]
        if kind == "legacy":
            self.model = estimator(self.position).fit(X, frame.target, sample_weight=weights)
        elif kind == "ridge":
            self.model = make_pipeline(StandardScaler(), Ridge(alpha=300))
            self.model.fit(X, frame.target, ridge__sample_weight=weights)
        else:
            options = dict(max_iter=spec["iterations"], learning_rate=.05, max_leaf_nodes=spec["nodes"],
                           min_samples_leaf=spec["leaf"], l2_regularization=spec["l2"],
                           early_stopping=False, random_state=42)
            # Disable the estimator's implicit random validation split: all selection is chronological.
            self.model = HistGradientBoostingRegressor(**options)
            if kind == "hurdle":
                played = frame.played_target.astype(bool)
                if played.nunique() < 2:
                    self.play_probability = float(played.mean())
                    self.classifier = None
                else:
                    self.classifier = HistGradientBoostingClassifier(**{**options, "max_iter": 140})
                    self.classifier.fit(X, played.astype(int), sample_weight=weights)
                if played.sum() < 30:
                    raise DataError("Too few appearances for the playing-time candidate.")
                self.model.fit(X[played], frame.target[played], sample_weight=weights[played])
            else:
                self.model.fit(X, frame.target, sample_weight=weights)
        return self

    def predict(self, frame):
        from .model import baseline
        if self.name == "form_baseline":
            return baseline(frame, self.prior)
        values = self.model.predict(frame[self.features]).clip(0, 20)
        if self.name == "hurdle":
            probability = self.classifier.predict_proba(frame[self.features])[:, 1] if self.classifier is not None else self.play_probability
            values *= probability
        return values


class ForecastEnsemble:
    def __init__(self, components, weights, bias=None, gain=0):
        self.components, self.weights = components, weights
        self.bias, self.gain = bias or {}, gain

    def predict(self, frame):
        values = sum(w * model.predict(frame) for w, model in zip(self.weights, self.components))
        return (values + self.gain * bias_values(frame, self.bias)).clip(0, 20)


def split_plan(frame):
    complete = sorted(s for s, end in frame.groupby("season").gw.max().items() if end >= 38)
    if len(complete) < 3:
        raise DataError("Three complete seasons are needed for the expanded chronological validation.")
    older, development, test = complete[-3:]
    blocks = [(older, 20, 38, "development"), (development, 1, 12, "development"),
              (development, 13, 25, "development"), (development, 26, 38, "development"),
              (test, 1, 12, "test"), (test, 13, 25, "test"), (test, 26, 38, "test")]
    return blocks


def score(frame, values):
    from .model import metrics
    regular = frame.minutes_mean3.to_numpy() >= 45
    all_score = metrics(frame.target, values)
    active = metrics(frame.target[regular], np.asarray(values)[regular]) if regular.any() else all_score
    # Most weight goes to potential squad players, without ignoring non-appearance errors.
    return .75 * active["rmse"] + .25 * all_score["rmse"], all_score, active


def prequential_calibration(frames, values, gain):
    adjusted, observed_frames, observed_predictions = [], [], []
    for frame, prediction in zip(frames, values):
        bias = fit_bias(pd.concat(observed_frames), np.concatenate(observed_predictions)) if observed_frames else {}
        adjusted.append((prediction + gain * bias_values(frame, bias)).clip(0, 20))
        observed_frames.append(frame)
        observed_predictions.append(prediction)
    return np.concatenate(adjusted)


def choose_recipe(frames, candidate_predictions):
    joined = pd.concat(frames, ignore_index=True)
    ranking = sorted(candidate_predictions, key=lambda n: score(joined, np.concatenate(candidate_predictions[n]))[0])
    recipes = [([name], [1.0]) for name in ranking]
    for a, b in itertools.combinations(ranking[:3], 2):
        recipes.extend(([a, b], [weight, 1 - weight]) for weight in (.25, .5, .75))
    outcomes = []
    for names, weights in recipes:
        values = [sum(w * candidate_predictions[n][i] for n, w in zip(names, weights)) for i in range(len(frames))]
        for gain in (0, .5, 1):
            prediction = prequential_calibration(frames, values, gain)
            objective, all_score, active = score(joined, prediction)
            outcomes.append({"components": names, "weights": weights, "calibration_gain": gain,
                             "objective": objective, "all_players": all_score, "regular_players": active})
    # Ties prefer simpler, uncorrected forecasts; tuning never consults test outcomes.
    best = min(outcomes, key=lambda r: (round(r["objective"], 8), len(r["components"]), r["calibration_gain"]))
    return best, sorted(outcomes, key=lambda r: r["objective"])[:12]


def train(frame, directory, progress=lambda message: None, force=False, search="standard"):
    from .model import data_fingerprint, metrics
    if search not in ("standard", "quick"):
        raise DataError("model_search must be standard or quick.")
    directory = Path(directory)
    path = directory / "model.joblib"
    fingerprint = training_fingerprint(frame, search)
    if path.exists() and not force:
        try:
            saved = joblib.load(path)
        except Exception:
            saved = {}
        if saved.get("fingerprint") == fingerprint and saved.get("sklearn_version") == sklearn.__version__:
            progress("Finalized training data unchanged; keeping the validated ensemble")
            return saved, False
    blocks = split_plan(frame)
    candidates = ["form_baseline", "legacy_v2", "hist_long", "hist_recent", "hist_smooth", "hurdle", "ridge_recent", "hist_no_external"]
    if search == "quick":
        candidates = ["form_baseline", "legacy_v2", "hist_recent", "hist_smooth"]
    report = {"created_at": utc_now(), "model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION,
              "validation_season": blocks[-1][0], "test_season": blocks[-1][0], "rows": len(frame),
              "seasons": {s: int(n) for s, n in frame.groupby("season").size().items()},
              "validation_policy": "Four rolling development blocks across two seasons; freeze the recipe; test all 38 GWs of the following season in three forward blocks. Refit production on all finalized results.",
              "regular_player_definition": "Pre-game rolling three-fixture average minutes >=45.",
              "target": "Points per scheduled fixture; player-GW totals are also evaluated.",
              "selection_objective": "75% regular-player RMSE + 25% all-player RMSE; chronological residual calibration.",
              "runtime": {"python": platform.python_version(), "scikit_learn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},
              "legacy_reference_policy": "FPL-Auto/v2 gradient boosting parameters, refitted on each fold's latest three available seasons and the expanded lag history. This is an estimator reference, not the previous saved model's predictions.",
              "search": search, "candidate_count": len(candidates), "feature_count": len(FEATURES), "positions": {},
              "limitations": ["Historical tests use final schedules, not archived deadline availability or fixture snapshots.",
                              "The old model previously reported the last six GWs of the historical test season; this is an expanded historical benchmark, not a new prospective experiment.",
                              "Rules and bonus scoring changed across seasons; older data is downweighted and missing-stat indicators are retained.",
                              "Longer-horizon forecasts reuse current form and availability; historical tests assess one-GW forecasts.",
                              "Future outcomes have irreducible uncertainty; lower historical error does not guarantee better future results."]}
    models, methods, priors, audit_rows = {}, {}, {}, []
    directory.mkdir(parents=True, exist_ok=True)
    with threadpool_limits(limits=2):
        for position in POSITIONS.values():
            part = frame[frame.position == position].copy()
            predictions = {name: [] for name in candidates}
            dev_frames = []
            fold_reports = []
            for season, start, stop, role in blocks[:4]:
                past = part[part.order < season_order(season, start)]
                holdout = part[(part.season == season) & part.gw.between(start, stop)].copy()
                if len(past) < 100 or holdout.empty:
                    raise DataError(f"Too little chronological data for {position} {season} GW{start}.")
                progress(f"Tuning {position}: {season} GW{start}-{stop}, {len(past):,} earlier rows")
                for name in candidates:
                    model = Candidate(name, position).fit(past)
                    predictions[name].append(model.predict(holdout))
                dev_frames.append(holdout)
            recipe, leaderboard = choose_recipe(dev_frames, predictions)
            joined_dev = pd.concat(dev_frames, ignore_index=True)
            dev_raw = np.concatenate([sum(w * predictions[n][i] for n, w in zip(recipe["components"], recipe["weights"])) for i in range(4)])
            frozen_bias = fit_bias(joined_dev, dev_raw)
            development_bias = dict(frozen_bias)
            # The concrete recipe is written before any test prediction or test metric is computed.
            write_json(directory / f"frozen_recipe_{position}.json", {"created_at": utc_now(), "model_version": MODEL_VERSION, "test_season": blocks[-1][0], "recipe": recipe, "development_bias": development_bias})
            test_frames, selected_values, raw_values, legacy_values, baseline_values = [], [], [], [], []
            for i, (season, start, stop, role) in enumerate(blocks):
                if role == "development":
                    holdout = dev_frames[i]
                    # Development metrics use corrections learned strictly from earlier development folds.
                    before = pd.concat(dev_frames[:i]) if i else None
                    past_raw = dev_raw[:sum(len(f) for f in dev_frames[:i])]
                    b = fit_bias(before, past_raw) if i else {}
                    raw = sum(w * predictions[n][i] for n, w in zip(recipe["components"], recipe["weights"]))
                    selected = (raw + recipe["calibration_gain"] * bias_values(holdout, b)).clip(0, 20)
                    legacy, base = predictions["legacy_v2"][i], predictions["form_baseline"][i]
                else:
                    progress(f"Testing frozen {position} recipe: {season} GW{start}-{stop}")
                    past = part[part.order < season_order(season, start)]
                    holdout = part[(part.season == season) & part.gw.between(start, stop)].copy()
                    required = set(recipe["components"]) | {"legacy_v2", "form_baseline"}
                    fitted = {name: Candidate(name, position).fit(past) for name in required}
                    raw = sum(w * fitted[n].predict(holdout) for n, w in zip(recipe["components"], recipe["weights"]))
                    selected = (raw + recipe["calibration_gain"] * bias_values(holdout, development_bias)).clip(0, 20)
                    legacy, base = fitted["legacy_v2"].predict(holdout), fitted["form_baseline"].predict(holdout)
                    test_frames.append(holdout)
                    selected_values.append(selected); raw_values.append(raw)
                    legacy_values.append(legacy); baseline_values.append(base)
                regular = holdout.minutes_mean3.to_numpy() >= 45
                fold_reports.append({"season": season, "start_gw": start, "end_gw": stop, "role": role,
                                     "train_before": season_order(season, start),
                                     "all_players": {"selected_ensemble": metrics(holdout.target, selected), "gradient_boosting": metrics(holdout.target, legacy), "form_baseline": metrics(holdout.target, base)},
                                     "regular_players": {"selected_ensemble": metrics(holdout.target[regular], selected[regular]), "gradient_boosting": metrics(holdout.target[regular], legacy[regular]), "form_baseline": metrics(holdout.target[regular], base[regular])}})
                audit = holdout[["season", "element", "code", "gw", "position", "target", "fixture_count", "minutes_mean3"]].copy()
                audit["role"], audit["prediction"], audit["legacy_prediction"], audit["baseline_prediction"] = role, selected, legacy, base
                audit["train_before"] = season_order(season, start)
                audit_rows.append(audit)
            test_frame = pd.concat(test_frames, ignore_index=True)
            selected, legacy, base = map(np.concatenate, (selected_values, legacy_values, baseline_values))
            _, all_score, active = score(test_frame, selected)
            _, legacy_all, legacy_active = score(test_frame, legacy)
            _, baseline_all, baseline_active = score(test_frame, base)
            regular = test_frame.minutes_mean3.to_numpy() >= 45
            gw_actual, gw_prediction = test_frame.target * test_frame.fixture_count, selected * test_frame.fixture_count
            residuals = test_frame.target.to_numpy()[regular] - selected[regular]
            role_report = {"selected_method": "adaptive_ensemble", "recipe": recipe, "leaderboard": leaderboard,
                           "development_rmse_ml": recipe["regular_players"]["rmse"],
                           "development_rmse_baseline": score(joined_dev, np.concatenate(predictions["form_baseline"]))[2]["rmse"],
                           "development_rows": len(joined_dev), "test_all_players": all_score, "test_regular_players": active,
                           "test_legacy_all_players": legacy_all, "test_legacy_regular_players": legacy_active,
                           "test_baseline_all_players": baseline_all, "test_baseline_regular_players": baseline_active,
                           "test_gameweek_totals": metrics(gw_actual, gw_prediction),
                           "test_gameweek_regular_players": metrics(gw_actual[regular], gw_prediction[regular]),
                           "rmse_change_vs_legacy_percent": 100 * (active["rmse"] / legacy_active["rmse"] - 1),
                           "blocks": fold_reports, "training_rows": len(part), "top_features": [],
                           "residual_quantiles_regular": {"p10": float(np.quantile(residuals, .1)), "p90": float(np.quantile(residuals, .9))},
                           "test_mean_error": float((test_frame.target.to_numpy() - selected).mean())}
            # Production residual tuning may use these now-historical out-of-fold test errors.
            # This happens only AFTER the frozen recipe's reported test is completed.
            calibration_frame = pd.concat([joined_dev, test_frame], ignore_index=True)
            production_bias = fit_bias(calibration_frame, np.concatenate([dev_raw, np.concatenate(raw_values)]))
            progress(f"Refitting {position}: {' + '.join(recipe['components'])}; full-season regular RMSE {active['rmse']:.3f} (old model {legacy_active['rmse']:.3f})")
            final = [Candidate(name, position).fit(part) for name in recipe["components"]]
            models[position] = ForecastEnsemble(final, recipe["weights"], production_bias, recipe["calibration_gain"])
            methods[position], priors[position] = "adaptive_ensemble", float(part.target.mean())
            role_report["production_bias"] = production_bias
            role_report["component_training_rows"] = {model.name: {"rows": int(getattr(model, "fitted_rows", len(part))),
                                                                   "seasons": list(getattr(model, "fitted_seasons", sorted(part.season.unique())))} for model in final}
            report["positions"][position] = role_report
    audit = pd.concat(audit_rows, ignore_index=True)
    report["development_rows"] = int((audit.role == "development").sum())
    report["test_rows"] = int((audit.role == "test").sum())
    test_audit = audit[audit.role == "test"]
    regular = test_audit.minutes_mean3 >= 45
    report["aggregate_test_regular"] = {n: metrics(test_audit.target[regular], test_audit[c][regular]) for n, c in [("selected", "prediction"), ("legacy", "legacy_prediction"), ("baseline", "baseline_prediction")]}
    report["aggregate_test_all"] = {n: metrics(test_audit.target, test_audit[c]) for n, c in [("selected", "prediction"), ("legacy", "legacy_prediction"), ("baseline", "baseline_prediction")]}
    audit.to_csv(directory / "backtest_predictions.csv.gz.tmp", index=False, compression="gzip")
    (directory / "backtest_predictions.csv.gz.tmp").replace(directory / "backtest_predictions.csv.gz")
    bundle = {"model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION, "features": FEATURES,
              "fingerprint": fingerprint, "models": models, "methods": methods, "priors": priors,
              "report": report, "sklearn_version": sklearn.__version__, "trained_through": int(frame.order.max())}
    joblib.dump(bundle, directory / "model.joblib.tmp", compress=3)
    (directory / "model.joblib.tmp").replace(path)
    return bundle, True


def training_fingerprint(frame, search="standard"):
    from .model import data_fingerprint
    signature = data_fingerprint(frame) + MODEL_VERSION + search + json.dumps(SPECS, sort_keys=True)
    signature += hashlib.sha256(pd.util.hash_pandas_object(frame[["played_target", "fixture_count"]].astype(float).round(8), index=False).values.tobytes()).hexdigest()
    return hashlib.sha256(signature.encode()).hexdigest()
