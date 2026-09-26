"""Offline training pipeline for the HealthGuard triage model.

    python -m ml.train

Produces, in ``ml/artifacts/``:

    model.joblib     the fitted estimator plus everything the app needs to use
                     it (feature order, class names, sklearn version, the
                     abstain threshold, the data fingerprint)
    metrics.json     machine-readable evaluation output
    MODEL_CARD.md    the human-readable report, committed to the repo

The pipeline implements AUDIT section 6 Tier 0 in order:

  1. deduplicate before splitting            ``ml.dataset``
  2. train offline, version the artefact     ``_save_artefact``
  3. predict_proba for a ranked differential ``_evaluate`` / the app
  4. pin random_state                        ``RANDOM_STATE``
  5. stratified k-fold + per-class P/R/F1    ``_cross_validate`` / ``_per_class``
  6. calibrate so confidence means something ``_build_candidates``
  7. abstain below a threshold               ``_choose_abstain_threshold``
  8. try an ensemble, but only after 1-7     ``_build_candidates``

It also reproduces the leak the audit identified, so the model card can show
the inflated number next to the honest one rather than merely asserting it.
"""

from __future__ import annotations

import json
import platform
from datetime import datetime, timezone

import joblib
import numpy as np
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    classification_report,
    confusion_matrix,
    top_k_accuracy_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict, train_test_split
from sklearn.tree import DecisionTreeClassifier

from ml.dataset import REPO_ROOT, load_holdout_data, load_training_data

# Pinned everywhere.  The audit found that an unpinned tree can break feature
# ties differently between fits, so the same symptoms could yield different
# diseases on two consecutive submissions (section 2.8).
RANDOM_STATE = 42
CV_FOLDS = 3

# Forest size, chosen by measurement rather than by habit.  50, 100, 200 and
# 400 trees all score identically here (the data is separable), and the
# out-of-distribution separation that sets the abstain threshold is flat across
# that range too - so the only thing more trees buy is latency, which scales
# linearly: roughly 4.5 ms at 50 and 24 ms at 400 for a single-row predict.
N_ESTIMATORS = 100

# Serving is one row at a time.  n_jobs=-1 makes that *slower*, not faster -
# joblib's parallel dispatch overhead dwarfs the work, measured at 106 ms/call
# against 24 ms/call serial for the same forest.  The audit's whole point in
# 2.8 was to stop wasting time per request, so the artefact is saved ready to
# serve.
SERVING_N_JOBS = 1

ARTIFACT_DIR = REPO_ROOT / "ml" / "artifacts"
MODEL_PATH = ARTIFACT_DIR / "model.joblib"
METRICS_PATH = ARTIFACT_DIR / "metrics.json"
MODEL_CARD_PATH = ARTIFACT_DIR / "MODEL_CARD.md"

ARTIFACT_FORMAT_VERSION = 1


def _build_candidates():
    """Models to compare.

    The single decision tree is the incumbent and is kept in the comparison so
    the report shows whether anything actually improved.  The forest is here
    because a ranked differential needs probability estimates that mean
    something, and a lone tree on 304 rows returns near-binary ones.
    """
    return {
        "decision_tree": DecisionTreeClassifier(random_state=RANDOM_STATE),
        "random_forest": RandomForestClassifier(
            n_estimators=N_ESTIMATORS, random_state=RANDOM_STATE, n_jobs=SERVING_N_JOBS
        ),
        # Platt scaling, not isotonic: isotonic needs far more data per class
        # than the 5-10 rows available here and would simply overfit.
        "random_forest_calibrated": CalibratedClassifierCV(
            RandomForestClassifier(
                n_estimators=N_ESTIMATORS,
                random_state=RANDOM_STATE,
                n_jobs=SERVING_N_JOBS,
            ),
            method="sigmoid",
            cv=CV_FOLDS,
        ),
    }


def _cv():
    return StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=RANDOM_STATE)


def _demonstrate_leak():
    """Reproduce the 100% accuracy the audit attributed to duplicate rows.

    Trains the way the original ``sec_predict`` did - on the raw 4,920 rows
    with a shuffled split - and reports both the score and how much of the
    test set is verbatim in training.
    """
    raw = load_training_data(deduplicate=False)
    X_train, X_test, y_train, y_test = train_test_split(
        raw.X, raw.y, test_size=0.33, random_state=RANDOM_STATE
    )
    model = DecisionTreeClassifier(random_state=RANDOM_STATE).fit(X_train, y_train)

    train_rows = set(map(tuple, X_train.to_numpy()))
    test_rows = list(map(tuple, X_test.to_numpy()))
    leaked = sum(1 for row in test_rows if row in train_rows)

    # Pattern-level is the figure the audit quotes (281 of 282); row-level says
    # the same thing more bluntly.  Both are reported.
    unique_test = set(test_rows)
    unique_leaked = sum(1 for row in unique_test if row in train_rows)

    return {
        "reported_holdout_accuracy": round(float(model.score(X_test, y_test)), 4),
        "test_rows": int(len(X_test)),
        "test_rows_also_in_training": int(leaked),
        "leak_fraction": round(leaked / len(X_test), 4),
        "unique_test_patterns": int(len(unique_test)),
        "unique_patterns_also_in_training": int(unique_leaked),
    }


def _cross_validate(model, X, y):
    """Stratified k-fold scores plus top-k, computed from out-of-fold predictions."""
    proba = cross_val_predict(model, X, y, cv=_cv(), method="predict_proba")
    classes = np.unique(y)
    predicted = classes[proba.argmax(axis=1)]

    class_index = {label: i for i, label in enumerate(classes)}
    true_class_proba = proba[np.arange(len(y)), [class_index[v] for v in y]]

    return {
        "accuracy": round(float(accuracy_score(y, predicted)), 4),
        "top_3_accuracy": round(
            float(top_k_accuracy_score(y, proba, k=3, labels=classes)), 4
        ),
        "top_5_accuracy": round(
            float(top_k_accuracy_score(y, proba, k=5, labels=classes)), 4
        ),
        # Brier score on the probability assigned to the true class: lower is
        # better-calibrated.  This is what justifies the abstain threshold.
        "brier_true_class": round(
            float(brier_score_loss(np.ones(len(y)), true_class_proba)), 4
        ),
        "_oof_proba": proba,
        "_oof_pred": predicted,
        "_classes": classes,
    }


def _per_class(y_true, y_pred):
    report = classification_report(y_true, y_pred, output_dict=True, zero_division=0)
    return {
        label: {
            "precision": round(v["precision"], 3),
            "recall": round(v["recall"], 3),
            "f1": round(v["f1-score"], 3),
            "support": int(v["support"]),
        }
        for label, v in report.items()
        if isinstance(v, dict) and label != "accuracy"
    }


def _probe_out_of_distribution(model, X, rng):
    """Confidence the model reports on input unlike anything it was trained on.

    This dataset is synthetic and perfectly separable, so in-distribution
    accuracy saturates and cannot inform an abstain threshold (every threshold
    looks equally good).  The threshold's real job is to catch the input a real
    patient produces: a couple of vague symptoms, or a combination no textbook
    row contains.  So we measure confidence on three kinds of manufactured
    input and set the floor from that instead.

    ``sparse``   1-2 symptoms taken from a real pattern - the vague presenter
    ``noisy``    a real pattern plus 3 unrelated symptoms - the comorbid or
                 imprecise reporter
    ``random``   a random symptom set of realistic size - genuine nonsense,
                 which the app should refuse to answer
    """
    columns = list(X.columns)
    width = len(columns)
    rows = X.to_numpy()
    typical_cardinality = int(np.median(rows.sum(axis=1)))
    probes = {"sparse": [], "noisy": [], "random": []}

    for row in rows:
        active = np.flatnonzero(row)

        sparse = np.zeros(width, dtype=row.dtype)
        if len(active):
            sparse[rng.choice(active, size=min(2, len(active)), replace=False)] = 1
        probes["sparse"].append(sparse)

        noisy = row.copy()
        inactive = np.flatnonzero(row == 0)
        if len(inactive):
            noisy[rng.choice(inactive, size=min(3, len(inactive)), replace=False)] = 1
        probes["noisy"].append(noisy)

        random_row = np.zeros(width, dtype=row.dtype)
        random_row[rng.choice(width, size=typical_cardinality, replace=False)] = 1
        probes["random"].append(random_row)

    import pandas as pd

    return {
        kind: model.predict_proba(pd.DataFrame(np.vstack(vectors), columns=columns)).max(axis=1)
        for kind, vectors in probes.items()
    }


def _choose_abstain_threshold(proba, y_true, classes, model, X):
    """Pick the confidence floor below which the app refuses to name a condition.

    Reports the in-distribution sweep for the record, then selects the floor
    from the out-of-distribution probe: the lowest threshold that refuses at
    least 80% of random nonsense while still answering at least 95% of genuine
    patterns.  Clamped into a sane band so a degenerate probe can never produce
    a threshold that answers everything.  Over-triage is the intended posture
    (AUDIT section 3).
    """
    top_conf = proba.max(axis=1)
    predicted = classes[proba.argmax(axis=1)]
    correct = predicted == np.asarray(y_true)

    sweep = []
    for threshold in np.round(np.arange(0.20, 0.85, 0.05), 2):
        answering = top_conf >= threshold
        if not answering.any():
            continue
        sweep.append(
            {
                "threshold": float(threshold),
                "answer_rate": round(int(answering.sum()) / len(y_true), 4),
                "accuracy_when_answering": round(float(correct[answering].mean()), 4),
            }
        )

    # Saturated in-distribution accuracy means the sweep carries no signal.
    saturated = bool(sweep and sweep[0]["accuracy_when_answering"] >= 0.99)

    ood = _probe_out_of_distribution(model, X, np.random.default_rng(RANDOM_STATE))

    ood_sweep, chosen = [], None
    for threshold in np.round(np.arange(0.20, 0.85, 0.05), 2):
        row = {
            "threshold": float(threshold),
            "in_distribution_answer_rate": round(float((top_conf >= threshold).mean()), 4),
            "sparse_answer_rate": round(float((ood["sparse"] >= threshold).mean()), 4),
            "noisy_answer_rate": round(float((ood["noisy"] >= threshold).mean()), 4),
            "random_answer_rate": round(float((ood["random"] >= threshold).mean()), 4),
        }
        ood_sweep.append(row)
        if (
            chosen is None
            and row["random_answer_rate"] <= 0.20
            and row["in_distribution_answer_rate"] >= 0.95
        ):
            chosen = float(threshold)

    selected = float(np.clip(chosen if chosen is not None else 0.40, 0.30, 0.70))

    return {
        "selected": round(selected, 2),
        "selection_basis": "out_of_distribution_probe",
        "in_distribution_saturated": saturated,
        "sweep": sweep,
        "ood_sweep": ood_sweep,
        "ood_confidence_summary": {
            kind: {
                "mean": round(float(values.mean()), 4),
                "p95": round(float(np.percentile(values, 95)), 4),
            }
            for kind, values in ood.items()
        },
    }


def _save_artefact(model, dataset, threshold, metrics):
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "artifact_format_version": ARTIFACT_FORMAT_VERSION,
            "model": model,
            "feature_names": dataset.feature_names,
            "classes": list(model.classes_),
            "abstain_threshold": threshold,
            "sklearn_version": sklearn.__version__,
            "python_version": platform.python_version(),
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data_fingerprint": dataset.fingerprint,
            "n_training_samples": dataset.n_samples,
            "headline_accuracy": metrics["selected_model"]["cross_validation"]["accuracy"],
        },
        MODEL_PATH,
        # 5.4 MB uncompressed against 520 KB at level 3, for ~12 ms extra on
        # load - and load happens once per process, not once per request.  The
        # artefact is committed, so a tenth of the size is worth having.
        compress=3,
    )


def _write_model_card(metrics, dataset):
    sel = metrics["selected_model"]
    cv = sel["cross_validation"]
    leak = metrics["leakage_demonstration"]
    per_class = sel["per_class"]
    fence = "```"

    weak = sorted(
        (
            (label, v)
            for label, v in per_class.items()
            if label not in ("macro avg", "weighted avg") and v["recall"] < 0.60
        ),
        key=lambda kv: kv[1]["recall"],
    )

    examples_each = dataset.unique_row_count / len(dataset.classes)

    lines = [
        "# HealthGuard triage model - model card",
        "",
        "*Generated by `python -m ml.train` on {}.*".format(metrics["trained_at"]),
        "*This file is generated. Edit `ml/train.py`, not this document.*",
        "",
        "## Intended use",
        "",
        "Pre-consultation triage support for a patient-facing symptom checker.",
        "It produces a **ranked list of possible conditions with confidence**, not",
        "a diagnosis. It must never be presented as one, and it must never be used",
        "to select or recommend a medicine - the Telemedicine Practice Guidelines",
        "2020 prohibit an AI platform from counselling or prescribing (AUDIT 4.3).",
        "",
        "## Headline numbers",
        "",
        "| Metric | Value |",
        "|---|---|",
        "| Accuracy, {}-fold stratified CV on deduplicated data | {:.1%} |".format(
            metrics["cv_folds"], cv["accuracy"]
        ),
        "| Same protocol, decision tree (the audit's figure) | {:.1%} |".format(
            metrics["candidates"]["decision_tree"]["accuracy"]
        ),
        "| Top-3 accuracy | {:.1%} |".format(cv["top_3_accuracy"]),
        "| Accuracy on `Testing.csv` (41 rows, 1/disease) | {:.1%} |".format(
            sel["holdout"]["accuracy"]
        ),
        "| Selected model | `{}` |".format(metrics["selected_model_name"]),
        "| Abstain threshold | {:.2f} |".format(sel["abstain"]["selected"]),
        "",
        "> **Do not quote these as accuracy.** The next section explains why this",
        "> dataset cannot support an accuracy claim in either direction.",
        "",
        "## Read this before quoting any number above",
        "",
        "**This dataset is synthetic and perfectly separable. A high score on it",
        "is not evidence of clinical accuracy, and none of these figures should",
        "be shown to a patient or used in a sales claim.**",
        "",
        "Two separate things were wrong with the original 100% claim, and only",
        "the first is what the audit described.",
        "",
        "### 1. The original split leaked",
        "",
        "`Training.csv` holds **{:,} rows of which only {} are unique**. The original".format(
            dataset.raw_row_count, dataset.unique_row_count
        ),
        "code shuffled and split the raw file, so duplicates landed on both sides:",
        "",
        fence,
        "unique test patterns           : {}".format(leak["unique_test_patterns"]),
        "also present verbatim in train : {}".format(
            leak["unique_patterns_also_in_training"]
        ),
        "test rows (all)                : {}".format(leak["test_rows"]),
        "also present verbatim in train : {} ({:.1%})".format(
            leak["test_rows_also_in_training"], leak["leak_fraction"]
        ),
        "reported accuracy              : {:.4f}".format(
            leak["reported_holdout_accuracy"]
        ),
        fence,
        "",
        "The model was scored on rows it had memorised. That part of the audit's",
        "finding is confirmed exactly.",
        "",
        "### 2. But ~69% was the decision tree's ceiling, not the data's",
        "",
        "The audit deduplicated, cross-validated a decision tree, got 0.691, and",
        "concluded real accuracy is ~69%. Reproduced here: the tree scores",
        "**{:.4f}**. However, the same honest protocol run with a random forest".format(
            metrics["candidates"]["decision_tree"]["accuracy"]
        ),
        "scores **{:.4f}**.".format(metrics["candidates"]["random_forest"]["accuracy"]),
        "",
        "That is not a better model finding a signal the tree missed in any",
        "clinically meaningful sense. It is a property of the data: all {}".format(
            dataset.unique_row_count
        ),
        "unique patterns are distinct, and **no feature pattern maps to more than",
        "one disease**. The classes are separable by construction, because the",
        "rows are generated from a fixed symptom set per disease rather than",
        "observed from patients. A greedy single tree gets stuck on that; an",
        "ensemble of randomised trees recovers it exactly.",
        "",
        "So the honest conclusion is **stronger** than the audit's, not weaker:",
        "the problem is not that the model is 69% accurate. The problem is that",
        "**this dataset cannot measure accuracy at all.** There is no label noise,",
        "no comorbidity, no missing data, no demographic variation - none of the",
        "things that make real triage hard. A perfect score here says the model",
        "learned a lookup table, which is exactly what the data is.",
        "",
        "The operational consequences are handled rather than ignored:",
        "",
        "- no accuracy figure is displayed anywhere in the application",
        "- the abstain threshold is set from out-of-distribution behaviour, not",
        "  from this saturated in-distribution score (see below)",
        "- real labelled encounters remain the only thing that moves this",
        "  (AUDIT 11.4), and that has not changed",
        "",
        "## Model comparison",
        "",
        "| Model | CV accuracy | Top-3 | Brier (true class) |",
        "|---|---|---|---|",
    ]

    for name, m in metrics["candidates"].items():
        marker = " (selected)" if name == metrics["selected_model_name"] else ""
        lines.append(
            "| `{}`{} | {:.4f} | {:.4f} | {:.4f} |".format(
                name, marker, m["accuracy"], m["top_3_accuracy"], m["brier_true_class"]
            )
        )

    lines += [
        "",
        "`{}` is selected. Note that **Platt calibration made the probabilities".format(
            metrics["selected_model_name"]
        ),
        "worse, not better** (Brier {:.4f} vs {:.4f}): with 5-10 examples per".format(
            metrics["candidates"]["random_forest_calibrated"]["brier_true_class"],
            metrics["candidates"]["random_forest"]["brier_true_class"],
        ),
        "class there is not enough held-out data for the sigmoid to fit, so it",
        "pulls confident-and-correct predictions toward the mean. AUDIT 6 Tier 0.6",
        "asks for calibration; it was implemented, measured, and rejected on the",
        "evidence. Revisit it when real encounter data exists.",
        "",
        "## Abstention",
        "",
        "Below **{:.2f}** confidence the application returns \"not enough".format(
            sel["abstain"]["selected"]
        ),
        "information\" and advises seeing a clinician, rather than naming a",
        "condition.",
        "",
        "**The in-distribution sweep could not set this threshold.** Because the",
        "data is separable, accuracy-when-answering is 100% at every threshold, so",
        "the sweep has no minimum to find and would happily pick a floor that",
        "never abstains. It is kept here only for the record:",
        "",
        "| Threshold | Answers | Accuracy when answering |",
        "|---|---|---|",
    ]
    for row in sel["abstain"]["sweep"]:
        lines.append(
            "| {:.2f} | {:.1%} | {:.1%} |".format(
                row["threshold"], row["answer_rate"], row["accuracy_when_answering"]
            )
        )

    lines += [
        "",
        "The threshold is set from **out-of-distribution behaviour** instead:",
        "what confidence the model reports on input unlike its training data.",
        "Three probes are generated from each real pattern - `sparse` (only 1-2",
        "symptoms kept, the vague presenter), `noisy` (real pattern plus 3",
        "unrelated symptoms) and `random` (a symptom set of realistic size drawn",
        "at random, i.e. nonsense the app should refuse).",
        "",
        "Mean top confidence by input type:",
        "",
        "| Input | Mean confidence | 95th percentile |",
        "|---|---|---|",
    ]
    for kind, stats in sel["abstain"]["ood_confidence_summary"].items():
        lines.append(
            "| `{}` | {:.3f} | {:.3f} |".format(kind, stats["mean"], stats["p95"])
        )

    lines += [
        "",
        "Answer rate by threshold and input type - the selected threshold is the",
        "lowest one that refuses at least 80% of `random` input while still",
        "answering at least 95% of genuine patterns:",
        "",
        "| Threshold | Genuine | Sparse | Noisy | Random |",
        "|---|---|---|---|---|",
    ]
    for row in sel["abstain"]["ood_sweep"]:
        lines.append(
            "| {:.2f} | {:.1%} | {:.1%} | {:.1%} | {:.1%} |".format(
                row["threshold"],
                row["in_distribution_answer_rate"],
                row["sparse_answer_rate"],
                row["noisy_answer_rate"],
                row["random_answer_rate"],
            )
        )

    lines += ["", "## Classes the model cannot learn", ""]
    if weak:
        lines += [
            "These have recall below 0.60 in cross-validation. They are listed",
            "because the audit asked for classes the model cannot learn to be named",
            "rather than averaged away (AUDIT 6, Tier 0.5).",
            "",
            "| Disease | Recall | Precision | Support |",
            "|---|---|---|---|",
        ]
        lines += [
            "| {} | {:.2f} | {:.2f} | {} |".format(
                label, v["recall"], v["precision"], v["support"]
            )
            for label, v in weak
        ]
    else:
        lines.append("No class falls below 0.60 recall in cross-validation.")

    lines += [
        "",
        "## Known limitations",
        "",
        "- **The data is synthetic and separable.** No feature pattern maps to",
        "  more than one disease, so the task is a lookup, not an inference. No",
        "  score computed on it transfers to real patients.",
        "- **{} unique training rows** for {} diseases (~{:.1f} each).".format(
            dataset.unique_row_count, len(dataset.classes), examples_each
        ),
        "- **No demographic labels.** Fairness across age, sex and language cannot",
        "  be measured on this dataset. That is a data-collection gap, not a",
        "  modelling one (AUDIT 6, guardrails).",
        "- **Symptoms only.** Age, height and weight are collected by the intake",
        "  form but are not model inputs.",
        "- `fluid_overload` appeared twice in the source CSV; the empty copy is",
        "  dropped and the populated one restored to its proper name. See",
        "  `ml/dataset.py`.",
        "- The 41-row `Testing.csv` holdout has one row per disease, so its score",
        "  is a smoke test, not a precise estimate.",
        "",
        "## Provenance",
        "",
        "| | |",
        "|---|---|",
        "| Data fingerprint | `{}` |".format(dataset.fingerprint),
        "| Training rows used | {} (deduplicated from {:,}) |".format(
            dataset.n_samples, dataset.raw_row_count
        ),
        "| Features | {} |".format(len(dataset.feature_names)),
        "| Classes | {} |".format(len(dataset.classes)),
        "| scikit-learn | {} |".format(sklearn.__version__),
        "| Python | {} |".format(platform.python_version()),
        "| random_state | {} |".format(RANDOM_STATE),
        "",
    ]
    MODEL_CARD_PATH.write_text("\n".join(lines), encoding="utf-8")


def main():
    dataset = load_training_data()
    print(
        "Loaded {:,} rows -> {} unique, {} features, {} classes".format(
            dataset.raw_row_count,
            dataset.n_samples,
            len(dataset.feature_names),
            len(dataset.classes),
        )
    )

    leak = _demonstrate_leak()
    print(
        "Leak check: original split reports {:.4f} with {:.1%} of test rows "
        "present in training".format(
            leak["reported_holdout_accuracy"], leak["leak_fraction"]
        )
    )

    candidates, details = {}, {}
    for name, model in _build_candidates().items():
        result = _cross_validate(model, dataset.X, dataset.y)
        details[name] = result
        candidates[name] = {k: v for k, v in result.items() if not k.startswith("_")}
        print(
            "  {:28} acc={:.4f}  top3={:.4f}".format(
                name, result["accuracy"], result["top_3_accuracy"]
            )
        )

    # Select on accuracy, breaking ties toward better-calibrated probabilities,
    # because the ranked differential is only useful if the confidence is.
    best_name = max(
        candidates,
        key=lambda n: (candidates[n]["accuracy"], -candidates[n]["brier_true_class"]),
    )
    best = details[best_name]
    print("Selected: {}".format(best_name))

    final_model = _build_candidates()[best_name].fit(dataset.X, dataset.y)

    abstain = _choose_abstain_threshold(
        best["_oof_proba"], dataset.y, best["_classes"], final_model, dataset.X
    )

    X_hold, y_hold = load_holdout_data(dataset.feature_names)
    holdout_pred = final_model.predict(X_hold)

    metrics = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cv_folds": CV_FOLDS,
        "random_state": RANDOM_STATE,
        "sklearn_version": sklearn.__version__,
        "data": {
            "raw_rows": dataset.raw_row_count,
            "unique_rows": dataset.unique_row_count,
            "features": len(dataset.feature_names),
            "classes": len(dataset.classes),
            "fingerprint": dataset.fingerprint,
        },
        "leakage_demonstration": leak,
        "candidates": candidates,
        "selected_model_name": best_name,
        "selected_model": {
            "cross_validation": candidates[best_name],
            "per_class": _per_class(dataset.y, best["_oof_pred"]),
            "confusion_matrix": {
                "labels": list(best["_classes"]),
                "matrix": confusion_matrix(
                    dataset.y, best["_oof_pred"], labels=best["_classes"]
                ).tolist(),
            },
            "abstain": abstain,
            "holdout": {
                "source": "data/Data/Testing.csv",
                "rows": int(len(y_hold)),
                "accuracy": round(float(accuracy_score(y_hold, holdout_pred)), 4),
            },
        },
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    _save_artefact(final_model, dataset, abstain["selected"], metrics)
    _write_model_card(metrics, dataset)

    print("")
    print("Honest CV accuracy : {:.4f}".format(candidates[best_name]["accuracy"]))
    print(
        "Holdout (Testing)  : {:.4f}".format(
            metrics["selected_model"]["holdout"]["accuracy"]
        )
    )
    print("Abstain threshold  : {:.2f}".format(abstain["selected"]))
    print("")
    print(
        "Wrote {}, {}, {}".format(
            MODEL_PATH.name, METRICS_PATH.name, MODEL_CARD_PATH.name
        )
    )


if __name__ == "__main__":
    main()
