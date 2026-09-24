"""Score the reviewer on the labeled cases and print a comparison table.

    python -m eval.run --model gemini-2.5-flash --prompt v1
    python -m eval.run --model baseline:null --model baseline:regex --mode replay
    python -m eval.run --model gemini-2.5-flash --prompt v1 --prompt v2 --model gemini-2.5-pro

Modes: `auto` uses a recorded reply when one exists and calls the provider otherwise; `live`
always calls the provider (and records); `replay` never calls it, so it is free, offline and
deterministic. Results are written to eval/results/ and recordings to eval/recordings/.
"""

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

from app.core.logging import configure_logging
from eval.cases import load_cases
from eval.metrics import summarize
from eval.report import print_table
from eval.runner import run_all

ROOT = Path(__file__).parent


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def load_env_key() -> str | None:
    if key := os.environ.get("REVIEWLY_GEMINI_API_KEY"):
        return key
    env = Path(".env")
    if env.is_file():
        for line in env.read_text().splitlines():
            if line.startswith("REVIEWLY_GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"') or None
    return None


async def run_combo(model: str, prompt: str, args: argparse.Namespace) -> Path:
    cases = load_cases(kinds=set(args.kinds) if args.kinds else None, limit=args.limit)
    recordings = ROOT / "recordings" / f"{slug(model)}__{prompt}.jsonl"
    started = time.time()
    results = await run_all(
        cases, model, prompt, args.mode, args.concurrency, recordings, load_env_key(), args.rpm
    )
    summary = summarize(results)
    out = ROOT / "results" / f"{slug(model)}__{prompt}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "mode": args.mode,
                "cases": len(cases),
                "kinds": args.kinds or "all",
                "wall_seconds": round(time.time() - started, 1),
                "summary": summary.to_dict(),
                "results": [r.to_dict() for r in results],
            },
            indent=1,
        )
        + "\n"
    )
    errors = [r for r in results if r.error]
    print(
        f"{model} / {prompt}: {len(results)} cases, {len(errors)} errors -> {out.relative_to(ROOT.parent)}"
    )
    for r in errors[:5]:
        print(f"  ! {r.id}: {r.error}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--model",
        action="append",
        required=True,
        help="gemini-* or baseline:null / baseline:regex; repeatable",
    )
    ap.add_argument("--prompt", action="append", help="prompt version (default v1); repeatable")
    ap.add_argument("--mode", choices=["auto", "live", "replay"], default="auto")
    ap.add_argument("--kinds", nargs="*", choices=["bug", "clean", "adversarial"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument(
        "--rpm", type=float, help="cap provider requests per minute (free Gemini tier: 4)"
    )
    args = ap.parse_args()
    configure_logging("WARNING")  # the pipeline logs every review; the table is what matters here
    outputs = [
        asyncio.run(run_combo(m, p, args)) for m in args.model for p in (args.prompt or ["v1"])
    ]
    print()
    print_table(outputs)


if __name__ == "__main__":
    main()
