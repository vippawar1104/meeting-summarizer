"""Render results as a comparison table, optionally into the README.

python -m eval.report                # print every result in eval/results/
python -m eval.report --update-readme
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parent
README = ROOT.parent / "README.md"
START, END = "<!-- eval-table:start -->", "<!-- eval-table:end -->"


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def ci(v: float | None, lo_hi: list[float | None], as_pct: bool = True) -> str:
    if v is None:
        return "n/a (nothing posted)"
    fmt = pct if as_pct else (lambda x: f"{x:.2f}")
    if lo_hi[0] is None or lo_hi[1] is None:
        return fmt(v)
    return f"{fmt(v)} ({fmt(lo_hi[0])}–{fmt(lo_hi[1])})"


def table(files: list[Path]) -> str:
    rows = [
        "| Model | Prompt | Cases | Precision (95% CI) | Recall (95% CI) | False alarms / no-bug PR | Line accuracy (raw) | Latency p50 / p95 | Tokens / PR | Cost / PR |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for f in sorted(files):
        d = json.loads(f.read_text())
        s = d["summary"]
        cost = f"${s['cost_per_pr']:.4f}" if s["cost_per_pr"] else "n/a"
        rows.append(
            f"| {d['model']} | {d['prompt']} | {s['n_cases']} ({s['n_errors']} err) "
            f"| {ci(s['precision'], s['precision_ci'])} | {ci(s['recall'], s['recall_ci'])} "
            f"| {ci(s['fp_per_clean_pr'], s['fp_per_clean_pr_ci'], as_pct=False)} "
            f"| {pct(s['line_accuracy_raw'])} | {s['latency_p50']:.1f}s / {s['latency_p95']:.1f}s "
            f"| {s['tokens_per_pr']:.0f} | {cost} |"
        )
    return "\n".join(rows)


def print_table(files: list[Path]) -> None:
    print(table(files))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-readme", action="store_true")
    args = ap.parse_args()
    files = sorted((ROOT / "results").glob("*.json"))
    if not files:
        raise SystemExit("no results yet: run `python -m eval.run` first")
    text = table(files)
    print(text)
    if args.update_readme:
        readme = README.read_text()
        if START not in readme:
            raise SystemExit(f"README has no {START} marker")
        head, rest = readme.split(START, 1)
        _, tail = rest.split(END, 1)
        README.write_text(f"{head}{START}\n{text}\n{END}{tail}")
        print("README updated")


if __name__ == "__main__":
    main()
