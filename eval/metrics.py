import random
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from eval.cases import Case, Label

LINE_TOLERANCE = 2  # a finding within this many lines of a labeled range still counts as a hit


@dataclass
class FindingView:
    file: str
    line: int
    severity: str
    category: str
    message: str
    confidence: float


@dataclass
class CaseResult:
    id: str
    kind: str
    raw: list[FindingView] = field(default_factory=list)  # model output before verification
    posted: list[FindingView] = field(default_factory=list)  # what would be posted to GitHub
    tp: int = 0  # posted findings that hit a label
    fp: int = 0  # posted findings that hit nothing and were not expected
    detected: bool = False  # at least one label was hit
    labels_hit: int = 0
    labels_total: int = 0
    raw_on_diff: int = 0  # raw findings that point at a real diff line
    exact_hits: int = 0  # true positives inside the labeled range itself, not just near it
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def hits(file: str, line: int, label: Label, tol: int = LINE_TOLERANCE) -> bool:
    return file == label.file and label.start - tol <= line <= label.end + tol


def score_case(
    case: Case,
    raw: list[FindingView],
    posted: list[FindingView],
    commentable: dict[str, set[int]],
) -> CaseResult:
    res = CaseResult(
        id=case.id, kind=case.kind, raw=raw, posted=posted, labels_total=len(case.labels)
    )
    hit_labels: set[int] = set()
    for f in posted:
        if any(a.file == f.file and a.line == f.line for a in case.allowed):
            continue  # an expected note (e.g. the injection warning): neither a hit nor a false alarm
        matched = [i for i, lb in enumerate(case.labels) if hits(f.file, f.line, lb)]
        if matched:
            res.tp += 1
            hit_labels.update(matched)
            if any(case.labels[i].start <= f.line <= case.labels[i].end for i in matched):
                res.exact_hits += 1
        else:
            res.fp += 1
    res.labels_hit = len(hit_labels)
    res.detected = bool(hit_labels)
    res.raw_on_diff = sum(1 for f in raw if f.line in commentable.get(f.file, set()))
    return res


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


@dataclass
class Summary:
    n_cases: int
    n_bug: int
    n_clean: int
    n_errors: int
    precision: float | None  # None when the reviewer posted nothing
    precision_ci: tuple[float | None, float | None]
    recall: float  # share of bug cases where a labeled bug was found
    recall_ci: tuple[float, float]
    label_recall: float
    fp_per_clean_pr: float  # false alarms per PR that has nothing to find
    fp_per_clean_pr_ci: tuple[float, float]
    clean_prs_with_fp: float  # share of clean PRs that got at least one false alarm
    line_accuracy_raw: float | None  # raw model findings that point at a real diff line
    exact_hit_rate: float | None  # true positives inside the labeled range (vs. merely nearby)
    findings_per_pr: float
    latency_p50: float
    latency_p95: float
    tokens_per_pr: float
    cost_per_pr: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ratio(num: float, den: float) -> float:
    return num / den if den else 0.0


def _bootstrap(
    results: list[CaseResult], stat: Any, *, rounds: int = 2000, seed: int = 7
) -> tuple[Any, Any]:
    """95% percentile interval, resampling whole cases (cases are the unit of independence)."""
    if not results:
        return (None, None)
    rng = random.Random(seed)
    drawn = (stat([rng.choice(results) for _ in results]) for _ in range(rounds))
    values = sorted(v for v in drawn if v is not None)
    if len(values) < rounds * 0.5:
        return (None, None)
    return (values[int(len(values) * 0.025)], values[int(len(values) * 0.975) - 1])


def _precision(rs: list[CaseResult]) -> float | None:
    posted = sum(r.tp + r.fp for r in rs)
    return sum(r.tp for r in rs) / posted if posted else None


def _recall(rs: list[CaseResult]) -> float:
    bug = [r for r in rs if r.labels_total]
    return _ratio(sum(r.detected for r in bug), len(bug))


def _fp_per_clean(rs: list[CaseResult]) -> float:
    clean = [r for r in rs if not r.labels_total]
    return _ratio(sum(r.fp for r in clean), len(clean))


def summarize(results: list[CaseResult]) -> Summary:
    ok = [r for r in results if r.error is None]
    bug = [r for r in ok if r.labels_total]
    clean = [r for r in ok if not r.labels_total]  # nothing to find: any finding is a false alarm
    raw_total = sum(len(r.raw) for r in ok)
    posted_total = sum(len(r.posted) for r in ok)
    tps = sum(r.tp for r in ok)
    latencies = [r.latency_s for r in ok]
    return Summary(
        n_cases=len(results),
        n_bug=len(bug),
        n_clean=len(clean),
        n_errors=len(results) - len(ok),
        precision=_precision(ok),
        precision_ci=_bootstrap(ok, _precision),
        recall=_recall(ok),
        recall_ci=_bootstrap(ok, _recall),
        label_recall=_ratio(sum(r.labels_hit for r in bug), sum(r.labels_total for r in bug)),
        fp_per_clean_pr=_fp_per_clean(ok),
        fp_per_clean_pr_ci=_bootstrap(ok, _fp_per_clean),
        clean_prs_with_fp=_ratio(sum(1 for r in clean if r.fp), len(clean)),
        line_accuracy_raw=sum(r.raw_on_diff for r in ok) / raw_total if raw_total else None,
        exact_hit_rate=sum(r.exact_hits for r in ok) / tps if tps else None,
        findings_per_pr=_ratio(posted_total, len(ok)),
        latency_p50=percentile(latencies, 0.5),
        latency_p95=percentile(latencies, 0.95),
        tokens_per_pr=statistics.fmean(r.prompt_tokens + r.completion_tokens for r in ok)
        if ok
        else 0.0,
        cost_per_pr=statistics.fmean(r.cost_usd for r in ok) if ok else 0.0,
    )
