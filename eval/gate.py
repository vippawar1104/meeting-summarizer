"""Fail if a (model, prompt) pair scores worse than its committed baseline.

    python -m eval.gate --model baseline:regex --prompt v1
    python -m eval.gate --model gemini-2.5-flash --prompt v2 --update     # accept new numbers

Runs in replay mode: it re-scores recorded replies (or a deterministic baseline reviewer), so it
needs no API key and is safe for CI. It therefore guards the whole non-LLM pipeline (parsing,
redaction, line verification, merging, scoring) plus any prompt change whose replies were recorded.
"""

import argparse
import asyncio
import json
import sys

from eval.cases import load_cases
from eval.metrics import Summary, summarize
from eval.run import ROOT, slug
from eval.runner import run_all

BASELINE = ROOT / "baseline.json"
TRACKED = ("precision", "recall", "fp_per_clean_pr")


def load_baseline() -> dict[str, dict[str, float | None]]:
    return json.loads(BASELINE.read_text()) if BASELINE.is_file() else {}


def regressions(base: dict[str, float | None], now: Summary, tol: float) -> list[str]:
    out = []
    for name in ("precision", "recall"):
        was, is_ = base.get(name), getattr(now, name)
        if was is not None and (is_ is None or is_ < was - tol):
            out.append(
                f"{name} fell from {was:.3f} to {'n/a' if is_ is None else f'{is_:.3f}'} (tolerance {tol})"
            )
    fp_was = base.get("fp_per_clean_pr")
    if fp_was is not None and now.fp_per_clean_pr > fp_was + 0.10:
        out.append(
            f"false alarms per no-bug PR rose from {fp_was:.2f} to {now.fp_per_clean_pr:.2f}"
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt", default="v1")
    ap.add_argument("--tolerance", type=float, default=0.02)
    ap.add_argument(
        "--update", action="store_true", help="write the current numbers as the new baseline"
    )
    args = ap.parse_args()

    from app.core.logging import configure_logging

    configure_logging("WARNING")
    recordings = ROOT / "recordings" / f"{slug(args.model)}__{args.prompt}.jsonl"
    results = asyncio.run(
        run_all(load_cases(), args.model, args.prompt, "replay", 3, recordings, None)
    )
    now = summarize(results)
    key = f"{args.model}|{args.prompt}"
    print(
        f"{key}: precision={now.precision} recall={now.recall:.3f} fp/no-bug PR={now.fp_per_clean_pr:.2f} errors={now.n_errors}"
    )

    if args.update:
        data = load_baseline()
        data[key] = {
            "precision": now.precision,
            "recall": now.recall,
            "fp_per_clean_pr": now.fp_per_clean_pr,
        }
        BASELINE.write_text(json.dumps(data, indent=2) + "\n")
        print(f"baseline for {key} written")
        return 0
    base = load_baseline().get(key)
    if base is None:
        print(f"no baseline for {key}: run with --update to create one")
        return 1
    if now.n_errors:
        print(f"{now.n_errors} case(s) errored (e.g. a missing recording)")
        return 1
    problems = regressions(base, now, args.tolerance)
    for p in problems:
        print("REGRESSION:", p)
    print("gate passed" if not problems else "gate FAILED")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
