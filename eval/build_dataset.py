"""Materialize eval/dataset/cases from eval/selection.json and local clones.

    python -m eval.build_dataset --repos DIR

The committed dataset is the output of this script, so nobody needs the clones to run the eval.
"""

import argparse
import json
import shutil
from pathlib import Path

from eval.adversarial import write_adversarial
from eval.cases import DATASET
from eval.mine import forward_diff, git, label_runs, reversed_diff, source_files

SELECTION = Path(__file__).parent / "selection.json"
_EXT_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".go": "go",
}


def clean_diff(text: str) -> str:
    return "".join(ln for ln in text.splitlines(keepends=True) if not ln.startswith("index "))


def build_case(repo_dir: Path, meta: dict[str, str], kind: str, short: str) -> dict[str, object]:
    commit = git(repo_dir, "rev-parse", short).strip()
    subject = git(repo_dir, "log", "-1", "--format=%s", commit).strip()
    date = git(repo_dir, "log", "-1", "--format=%as", commit).strip()
    files = source_files(repo_dir, commit)
    if kind == "bug":
        diff = clean_diff(reversed_diff(repo_dir, commit, files))
        labels = label_runs(diff)
        title = f"Update {Path(files[0]).name}"  # neutral: the fix's own message would give the bug away
        technique = "reversed fix commit"
    else:
        diff = clean_diff(forward_diff(repo_dir, commit, files))
        labels, title, technique = (
            [],
            subject,
            "merged commit, no later fix or revert references it",
        )
    name = repo_dir.name.split("_", 1)[-1]
    return {
        "id": f"{kind}-{name}-{commit[:7]}",
        "kind": kind,
        "language": _EXT_LANG.get(Path(files[0]).suffix, meta["language"]),
        "title": title,
        "labels": [{**lb, "note": ""} for lb in labels],
        "allowed": [],
        "meta": {
            "repo": meta["name"],
            "license": meta["license"],
            "commit": commit,
            "url": f"https://github.com/{meta['name']}/commit/{commit}",
            "original_subject": subject,
            "date": date,
            "technique": technique,
        },
        "_diff": diff,
    }


def write_sources(sel: dict[str, dict[str, object]]) -> None:
    """Attribution for every third-party case (all sources are permissively licensed)."""
    from eval.cases import load_cases

    by_repo: dict[str, list[dict[str, object]]] = {}
    for c in load_cases():
        if c.meta.get("repo"):
            by_repo.setdefault(str(c.meta["repo"]), []).append(
                c.meta | {"id": c.id, "kind": c.kind}
            )
    lines = [
        "# Dataset sources",
        "",
        "Diffs are taken (or, for bug cases, reversed) from the public commits below. Each project's "
        "license permits redistribution with attribution; the original copyright notices apply to those diffs. "
        "Hand-written adversarial cases are CC0.",
        "",
        "| Project | License | Cases |",
        "|---|---|---|",
    ]
    for repo in sorted(by_repo):
        lic = by_repo[repo][0]["license"]
        lines.append(f"| [{repo}](https://github.com/{repo}) | {lic} | {len(by_repo[repo])} |")
    lines += ["", "## Every case", "", "| Case | Kind | Source commit |", "|---|---|---|"]
    for repo in sorted(by_repo):
        for m in sorted(by_repo[repo], key=lambda x: str(x["id"])):
            lines.append(f"| {m['id']} | {m['kind']} | [{str(m['commit'])[:10]}]({m['url']}) |")
    (DATASET.parent / "SOURCES.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", type=Path, required=True)
    args = ap.parse_args()
    sel = json.loads(SELECTION.read_text())
    if DATASET.exists():
        shutil.rmtree(DATASET)
    DATASET.mkdir(parents=True)
    count = 0
    for kind in ("bug", "clean"):
        for repo, hashes in sel[kind].items():
            for short in hashes:
                case = build_case(args.repos / repo, sel["repos"][repo], kind, short)
                diff = case.pop("_diff")
                out = DATASET / str(case["id"])
                out.mkdir()
                (out / "diff.patch").write_text(str(diff))
                (out / "case.json").write_text(
                    json.dumps(case, indent=2, ensure_ascii=False) + "\n"
                )
                count += 1
    count += write_adversarial()
    write_sources(sel)
    print(f"wrote {count} cases to {DATASET}")


if __name__ == "__main__":
    main()
