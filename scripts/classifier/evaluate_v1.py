#!/usr/bin/env python3
"""Compare the structural classifier's output to hand labels.

Reads the corpus manifest and a labels file (one JSON object per episode
with `top_level` and `subcategory`), and writes `evaluation.md` into the
corpus directory: a top-level confusion matrix, per-bucket precision and
recall at both levels, the unclassified rate, and the sufficiency gate
defined for this evaluation (fails when more than one episode in five is
unclassified or wrong at top level, or any labeled subcategory has recall
below 0.6).

Usage:
    evaluate_v1.py --corpus DIR [--labels FILE]
"""

import argparse
import collections
import json
import os

GATE_TOP_LEVEL_ERROR = 0.20
GATE_SUBCATEGORY_RECALL = 0.60


def load_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def matrix_markdown(pairs, row_names, col_names, row_axis):
    counts = collections.Counter(pairs)
    head = "| " + row_axis + " \\ predicted | " + " | ".join(col_names) + " | total |"
    sep = "| --- " * (len(col_names) + 2) + "|"
    lines = [head, sep]
    for row in row_names:
        cells = [counts.get((row, col), 0) for col in col_names]
        lines.append(f"| {row} | " + " | ".join(str(c) for c in cells) + f" | {sum(cells)} |")
    return "\n".join(lines)


def precision_recall(pairs, names):
    counts = collections.Counter(pairs)
    rows = []
    for name in names:
        tp = counts.get((name, name), 0)
        fn = sum(c for (t, p), c in counts.items() if t == name and p != name)
        fp = sum(c for (t, p), c in counts.items() if t != name and p == name)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        rows.append((name, tp + fn, precision, recall))
    return rows


def pr_markdown(rows):
    fmt = lambda x: "n/a" if x is None else f"{x:.3f}"
    lines = ["| bucket | labeled episodes | precision | recall |", "| --- | --- | --- | --- |"]
    for name, support, precision, recall in rows:
        lines.append(f"| {name} | {support} | {fmt(precision)} | {fmt(recall)} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--labels", default=None, help="labels jsonl (default: labels-pass1.jsonl in corpus)")
    args = ap.parse_args()

    manifest = load_jsonl(os.path.join(args.corpus, "manifest.jsonl"))
    labels = load_jsonl(args.labels or os.path.join(args.corpus, "labels-pass1.jsonl"))
    assert len(manifest) == len(labels)
    by_path = {l["path"]: l for l in labels}

    top_pairs = []       # (labeled top level, predicted top level)
    sub_pairs = []       # (labeled subcategory, predicted bucket), labeled subs only
    unclassified = 0
    top_wrong_or_unclassified = 0
    for row in manifest:
        label = by_path[row["path"]]
        predicted_top = row["v1"]["top_level"]
        predicted_bucket = row["v1"]["bucket"]
        top_pairs.append((label["top_level"], predicted_top))
        if predicted_top == "unclassified":
            unclassified += 1
        if predicted_top != label["top_level"]:
            top_wrong_or_unclassified += 1
        if label["subcategory"]:
            sub_pairs.append((label["subcategory"], predicted_bucket))

    n = len(manifest)
    top_names = sorted({t for t, _ in top_pairs})
    pred_names = sorted({p for _, p in top_pairs})
    sub_names = sorted({s for s, _ in sub_pairs})
    sub_pred = sorted({p for _, p in sub_pairs})

    top_pr = precision_recall(top_pairs, top_names)
    sub_pr = precision_recall(sub_pairs, sub_names)

    error_rate = top_wrong_or_unclassified / n
    weak_subs = [(name, support, recall) for name, support, _, recall in sub_pr
                 if recall is not None and recall < GATE_SUBCATEGORY_RECALL]
    gate_fired = error_rate > GATE_TOP_LEVEL_ERROR or bool(weak_subs)

    out = []
    out.append("# Structural classifier (taxonomy 1, ruleset 1) against hand labels")
    out.append("")
    out.append(f"{n} labeled episodes. Predictions come from the shipped binary's emission.")
    out.append("")
    out.append("## Top-level confusion matrix")
    out.append("")
    out.append(matrix_markdown(top_pairs, top_names, pred_names, "labeled"))
    out.append("")
    out.append("## Top-level precision and recall")
    out.append("")
    out.append(pr_markdown(top_pr))
    out.append("")
    out.append("## Subcategory recall (episodes whose label names a subcategory)")
    out.append("")
    out.append("Predicted value is the classifier's bucket; a subcategory prediction is")
    out.append("correct only when it names the labeled subcategory exactly.")
    out.append("")
    out.append(matrix_markdown(sub_pairs, sub_names, sub_pred, "labeled"))
    out.append("")
    out.append(pr_markdown(sub_pr))
    out.append("")
    out.append("Subcategories defined by the taxonomy with no labeled episodes have no")
    out.append("measurable recall here: " + ", ".join(
        s for s in ["debugging", "testing", "build", "refactoring", "documentation",
                    "data-analysis", "infrastructure"] if s not in sub_names) + ".")
    out.append("")
    out.append("## Gate")
    out.append("")
    out.append(f"- Episodes unclassified at top level: {unclassified}/{n} ({unclassified/n:.1%})")
    out.append(f"- Episodes wrong or unclassified at top level: {top_wrong_or_unclassified}/{n} "
               f"({error_rate:.1%}); the gate line is {GATE_TOP_LEVEL_ERROR:.0%}")
    for name, support, recall in weak_subs:
        out.append(f"- Subcategory `{name}` recall {recall:.3f} on {support} labeled episodes; "
                   f"the gate line is {GATE_SUBCATEGORY_RECALL:.2f}")
    out.append("")
    out.append(f"**Gate verdict: {'INSUFFICIENT' if gate_fired else 'sufficient'}.**")
    out.append("")

    path = os.path.join(args.corpus, "evaluation.md")
    with open(path, "w") as f:
        f.write("\n".join(out))
    print(f"wrote {path}; error rate {error_rate:.3f}; gate {'fired' if gate_fired else 'passed'}")


if __name__ == "__main__":
    main()
