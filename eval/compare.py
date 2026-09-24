"""Paired comparison of two evaluation runs, so "better" means a checked improvement.

    python -m eval.compare eval/results/A.json eval/results/B.json

Both runs score the same cases, so we resample *cases* and recompute each metric for A and B on the
same resample. The interval on the difference (B minus A) says whether an apparent gain could just
be noise from having only 58 cases.
"""

import argparse
import json
import random
from pathlib import Path
from typing import Any

from eval.metrics import CaseResult, _fp_per_clean, _precision, _recall


def load(path: Path) -> tuple[str, dict[str, CaseResult]]:
    d = json.loads(path.read_text())
    fields = CaseResult.__dataclass_fields__
    results = {
        r["id"]: CaseResult(
            **{k: v for k, v in r.items() if k in fields and k not in ("raw", "posted")}
        )
        for r in d["results"]
    }
    return f"{d['model']} / {d['prompt']}", results


METRICS: list[tuple[str, Any, bool]] = [
    ("precision", _precision, True),  # (name, function, higher is better)
    ("recall", _recall, True),
    ("false alarms / no-bug PR", _fp_per_clean, False),
]


def paired(
    a: dict[str, CaseResult], b: dict[str, CaseResult], rounds: int = 4000, seed: int = 11
) -> list[dict[str, Any]]:
    ids = sorted(set(a) & set(b))
    ids = [i for i in ids if a[i].error is None and b[i].error is None]
    rng = random.Random(seed)
    out = []
    for name, fn, higher_better in METRICS:
        base_a, base_b = fn([a[i] for i in ids]), fn([b[i] for i in ids])
        diffs = []
        for _ in range(rounds):
            sample = [rng.choice(ids) for _ in ids]
            va, vb = fn([a[i] for i in sample]), fn([b[i] for i in sample])
            if va is not None and vb is not None:
                diffs.append(vb - va)
        diffs.sort()
        lo, hi = (
            (diffs[int(len(diffs) * 0.025)], diffs[int(len(diffs) * 0.975) - 1])
            if len(diffs) > rounds * 0.5
            else (None, None)
        )
        delta = None if base_a is None or base_b is None else base_b - base_a
        improved = None
        if lo is not None and hi is not None:
            improved = lo > 0 if higher_better else hi < 0
            worse = hi < 0 if higher_better else lo > 0
            verdict = (
                "significantly better"
                if improved
                else "significantly worse"
                if worse
                else "not significant"
            )
        else:
            verdict = "n/a"
        out.append(
            {
                "metric": name,
                "a": base_a,
                "b": base_b,
                "delta": delta,
                "ci": (lo, hi),
                "verdict": verdict,
                "n": len(ids),
            }
        )
    return out


def signed(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.3f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    args = ap.parse_args()
    (name_a, ra), (name_b, rb) = load(args.a), load(args.b)
    rows = paired(ra, rb)
    print(f"A = {name_a}\nB = {name_b}   (paired over {rows[0]['n']} cases; difference is B - A)\n")
    print("| Metric | A | B | Difference (95% CI) | Verdict |\n|---|---|---|---|---|")
    for r in rows:
        ci = (
            "n/a"
            if r["ci"][0] is None
            else f"{signed(r['delta'])} ({signed(r['ci'][0])} to {signed(r['ci'][1])})"
        )
        va = "n/a" if r["a"] is None else f"{r['a']:.3f}"
        vb = "n/a" if r["b"] is None else f"{r['b']:.3f}"
        print(f"| {r['metric']} | {va} | {vb} | {ci} | {r['verdict']} |")


if __name__ == "__main__":
    main()
