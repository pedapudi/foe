#!/usr/bin/env python3
"""Unsupervised check of the taxonomy against the corpus text.

Character n-gram TF-IDF over each episode's task text plus tool-result
subjects, clustered with k-means and NMF over a small k sweep. For each
clustering, prints the dominant hand label per cluster and its purity, so
the report can say which taxonomy buckets have cluster support, which do
not, and whether any coherent cluster lacks a taxonomy name.

Usage:
    cluster_check.py --corpus DIR [--out FILE]
"""

import argparse
import collections
import json
import os

import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import NMF
from sklearn.feature_extraction.text import TfidfVectorizer


def load_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", default=None, help="markdown report (default: cluster-check.md in corpus)")
    args = ap.parse_args()

    manifest = load_jsonl(os.path.join(args.corpus, "manifest.jsonl"))
    labels = load_jsonl(os.path.join(args.corpus, "labels-pass1.jsonl"))
    by_path = {l["path"]: l for l in labels}

    texts, hand = [], []
    for row in manifest:
        text = row["task_text"] + "\n" + "\n".join(row["subjects"])
        texts.append(text)
        label = by_path[row["path"]]
        hand.append(label["subcategory"] or label["top_level"])

    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=20000,
                                 sublinear_tf=True)
    x = vectorizer.fit_transform(texts)

    out = ["# Cluster support for the taxonomy", "",
           f"{len(texts)} episodes; character 3-5-gram TF-IDF over task text plus tool",
           "subjects; hand labels shown per cluster are subcategory where one exists,",
           "top-level otherwise.", ""]

    def describe(name, assign):
        out.append(f"### {name}")
        out.append("")
        out.append("| cluster | size | dominant label | purity | label mix |")
        out.append("| --- | --- | --- | --- | --- |")
        for cluster in sorted(set(assign)):
            members = [hand[i] for i in range(len(hand)) if assign[i] == cluster]
            mix = collections.Counter(members)
            top, count = mix.most_common(1)[0]
            mixture = ", ".join(f"{l}:{c}" for l, c in mix.most_common())
            out.append(f"| {cluster} | {len(members)} | {top} | {count/len(members):.2f} | {mixture} |")
        out.append("")

    for k in (4, 6, 8, 10):
        km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(x)
        describe(f"k-means, k={k}", km.labels_)
        nmf = NMF(n_components=k, init="nndsvd", random_state=0, max_iter=400)
        w = nmf.fit_transform(x)
        describe(f"NMF, k={k}", w.argmax(axis=1))

    path = args.out or os.path.join(args.corpus, "cluster-check.md")
    with open(path, "w") as f:
        f.write("\n".join(out))
    print("wrote", path)


if __name__ == "__main__":
    main()
