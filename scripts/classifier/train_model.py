#!/usr/bin/env python3
"""Train candidate replacements for the structural classifier and decide
whether either earns shipping.

Two candidates, per the evaluation design:
- a one-vs-rest logistic model over hashed character n-grams of the two
  free-text fields telemetry already reads (tool-result subjects and
  outcome detail) — task text is never a feature, because the runtime
  classifier never sees it;
- a gradient-boosted tree model over the typed structural evidence
  (extension, command-head, and tool histograms plus spawn and workflow
  counts).

Evaluation is cross-validated two ways and both are reported: grouped by
benchmark task, which measures generalization to unseen tasks, and
ungrouped, which is inflated because episodes of one task are near
duplicates of each other. The shipping question is answered against the
grouped numbers. Calibration is measured on held-out folds (Brier score,
with isotonic and Platt variants).

Usage:
    train_model.py --corpus DIR
"""

import argparse
import collections
import json
import os

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.model_selection import GroupKFold, StratifiedKFold

# The bucket vocabulary: subcategory where the label names one, else the
# top-level category.
def bucket(label):
    return label["subcategory"] or label["top_level"]


def roll_up(name):
    return {"debugging": "programming", "testing": "programming", "build": "programming",
            "refactoring": "programming", "documentation": "programming",
            "data-analysis": "data analysis", "infrastructure": "technology"}.get(name, name)


def load(corpus):
    manifest = [json.loads(l) for l in open(os.path.join(corpus, "manifest.jsonl"))]
    labels = [json.loads(l) for l in open(os.path.join(corpus, "labels-pass1.jsonl"))]
    by_path = {l["path"]: l for l in labels}
    rows = [(r, by_path[r["path"]]) for r in manifest]
    return rows


def text_of(row):
    return "\n".join(row["subjects"]) + "\n" + row["outcome_detail"]


def structural_features(rows):
    """Count features over a fixed vocabulary drawn from the corpus."""
    vocab = sorted({f"e:{t}" for r, _ in rows for t in r["evidence"]["extensions"]}
                   | {f"h:{t}" for r, _ in rows for t in r["evidence"]["heads"]}
                   | {f"t:{t}" for r, _ in rows for t in r["evidence"]["tools"]})
    index = {name: i for i, name in enumerate(vocab)}
    x = np.zeros((len(rows), len(vocab) + 2))
    for i, (r, _) in enumerate(rows):
        ev = r["evidence"]
        for prefix, hist in (("e:", ev["extensions"]), ("h:", ev["heads"]), ("t:", ev["tools"])):
            for token, count in hist.items():
                x[i, index[prefix + token]] = count
        x[i, len(vocab)] = ev["spawns"]
        x[i, len(vocab) + 1] = ev["workflow_nodes"]
    return x, vocab


def evaluate(name, fit_predict, rows, y, groups, v1_top, out):
    """Cross-validate a model two ways; report top-level accuracy per fold
    against v1 on the same folds."""
    y = np.asarray(y)
    for scheme, splitter in (("grouped by task", GroupKFold(n_splits=5)),
                             ("ungrouped (inflated)", StratifiedKFold(n_splits=5, shuffle=True, random_state=0))):
        accs, v1accs, briers = [], [], []
        split = (splitter.split(np.zeros(len(y)), y, groups) if scheme.startswith("grouped")
                 else splitter.split(np.zeros(len(y)), y))
        for train, test in split:
            proba, classes = fit_predict(train, test)
            pred = classes[np.argmax(proba, axis=1)]
            top_pred = np.array([roll_up(p) for p in pred])
            top_true = np.array([roll_up(t) for t in y[test]])
            accs.append(float(np.mean(top_pred == top_true)))
            v1accs.append(float(np.mean(np.asarray(v1_top)[test] == top_true)))
            # One-vs-rest Brier over the fold, averaged across classes.
            fold_briers = []
            for ci, cname in enumerate(classes):
                fold_briers.append(brier_score_loss((y[test] == cname).astype(int), proba[:, ci]))
            briers.append(float(np.mean(fold_briers)))
        out.append(f"| {name} | {scheme} | "
                   f"{np.mean(accs):.3f} ± {np.std(accs):.3f} | "
                   f"{np.mean(v1accs):.3f} ± {np.std(v1accs):.3f} | "
                   f"{np.mean(accs) - np.mean(v1accs):+.3f} | "
                   f"{np.mean(briers):.4f} |")
        yield scheme, accs, v1accs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", required=True)
    args = ap.parse_args()

    rows = load(args.corpus)
    # Subcategory training is unsupportable at this corpus size: debugging
    # has 2 labeled episodes, technology-without-subcategory 2, and
    # documentation 3, which no cross-validation split survives. The
    # candidates therefore predict the top-level category only, and any
    # shipping decision must weigh that a replacement would forfeit v1's
    # subcategory output.
    y = [roll_up(bucket(l)) for _, l in rows]
    groups = [r["task"] or "local" for r, _ in rows]
    v1_top = [r["v1"]["top_level"] for r, _ in rows]

    counts = collections.Counter(y)
    print("bucket support:", dict(counts))

    vec = HashingVectorizer(analyzer="char_wb", ngram_range=(3, 5), n_features=2**15,
                            alternate_sign=False, norm="l2")
    xt = vec.transform([text_of(r) for r, _ in rows])
    xs, vocab = structural_features(rows)
    y = np.asarray(y)

    def logistic(train, test, calibration=None):
        base = LogisticRegression(max_iter=2000, C=1.0)
        model = base if calibration is None else CalibratedClassifierCV(base, method=calibration, cv=2)
        model.fit(xt[train], y[train])
        return model.predict_proba(xt[test]), model.classes_

    def trees(train, test, calibration=None):
        base = HistGradientBoostingClassifier(max_iter=200, random_state=0)
        model = base if calibration is None else CalibratedClassifierCV(base, method=calibration, cv=2)
        model.fit(xs[train], y[train])
        return model.predict_proba(xs[test]), model.classes_

    out = ["# Trained-candidate evaluation", "",
           f"{len(rows)} episodes, buckets {dict(counts)}", "",
           "Top-level accuracy is after subcategory roll-up; v1 accuracy is measured",
           "on the identical test folds. Brier is one-vs-rest, averaged over classes.", "",
           "| model | scheme | accuracy | v1 accuracy | margin | Brier |",
           "| --- | --- | --- | --- | --- | --- |"]

    results = {}
    for name, fn in (("logistic / hashed text", logistic),
                     ("boosted trees / structure", trees),
                     ("logistic + isotonic", lambda tr, te: logistic(tr, te, "isotonic")),
                     ("logistic + Platt", lambda tr, te: logistic(tr, te, "sigmoid")),
                     ("trees + isotonic", lambda tr, te: trees(tr, te, "isotonic")),
                     ("trees + Platt", lambda tr, te: trees(tr, te, "sigmoid"))):
        for scheme, accs, v1accs in evaluate(name, fn, rows, y, groups, v1_top, out):
            results[(name, scheme)] = (accs, v1accs)

    out.append("")
    # The shipping question: does the candidate beat v1 on grouped folds by a
    # margin the fold variance supports (mean margin minus one standard
    # deviation of the per-fold margin still positive)?
    for name in ("logistic / hashed text", "boosted trees / structure"):
        accs, v1accs = results[(name, "grouped by task")]
        margins = np.array(accs) - np.array(v1accs)
        supported = margins.mean() - margins.std() > 0
        out.append(f"- {name}: per-fold margin over v1 {margins.mean():+.3f} ± {margins.std():.3f} "
                   f"(grouped); margin {'IS' if supported else 'is NOT'} supported by fold variance.")

    path = os.path.join(args.corpus, "training-report.md")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    print("wrote", path)


if __name__ == "__main__":
    main()
