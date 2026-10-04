"""Offline evaluation of the relevance filter against hand-labelled recordings.

    python -m app.eval_filter                       # local rules, eval/recordings/*.json
    FILTER_PROVIDER=jev JEV_API_KEY=... python -m app.eval_filter --provider jev
    python -m app.eval_filter --min-precision 0.9   # exit 1 below the bar (use in CI)

"Positive" = the label says drop. Precision of `drop` is what matters most: a wrongly dropped
step is a broken skill, a missed drop is only an extra step for the reviewer to grey out.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .filtering import RelevanceFilter, Thresholds
from .skillgen import cleanup

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "eval" / "recordings"


def evaluate(files: list[Path], flt: RelevanceFilter, th: Thresholds) -> dict:
    tot = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "flag_tp": 0, "flag_fp": 0, "seg_ok": 0, "n": 0}
    per = []
    for f in files:
        sc = json.loads(f.read_text())
        cleaned = cleanup(sc["events"])
        res = flt.run(cleaned, sc.get("intent"), th)
        row = {"name": sc["name"], "errors": []}
        for e in cleaned:
            label = sc["labels"].get(str(e["seq"]))
            if label is None:
                continue
            d = res.decisions[e["seq"]]
            want_drop = label == "drop"
            got_drop = d.decision == "drop"
            flagged = d.decision != "keep"
            key = ("tp" if want_drop else "fp") if got_drop else ("fn" if want_drop else "tn")
            tot[key] += 1
            if flagged:
                tot["flag_tp" if want_drop else "flag_fp"] += 1
            if (got_drop != want_drop) or (flagged and not want_drop):
                row["errors"].append(f"seq {e['seq']} {e['type']} want={label} got={d.decision}"
                                     f" p={d.p_drop:.2f} ({d.reason})")
        want_segs = sc.get("expected_segments")
        row["segments"] = f"{len(res.segments)}/{want_segs}"
        if want_segs is None or want_segs == len(res.segments):
            tot["seg_ok"] += 1
        tot["n"] += 1
        row["source"] = res.source
        per.append(row)

    def ratio(a: int, b: int) -> float | None:
        return round(a / b, 4) if b else None

    return {
        "recordings": tot["n"],
        "drop_precision": ratio(tot["tp"], tot["tp"] + tot["fp"]),
        "drop_recall": ratio(tot["tp"], tot["tp"] + tot["fn"]),
        "flag_precision": ratio(tot["flag_tp"], tot["flag_tp"] + tot["flag_fp"]),
        "flag_recall": ratio(tot["flag_tp"], tot["tp"] + tot["fn"]),
        "segmentation_accuracy": ratio(tot["seg_ok"], tot["n"]),
        "confusion": {k: tot[k] for k in ("tp", "fp", "fn", "tn")},
        "per_recording": per,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--provider", choices=["local", "jev"], default=None)
    ap.add_argument("--drop", type=float, default=0.9)
    ap.add_argument("--review", type=float, default=0.6)
    ap.add_argument("--min-precision", type=float, default=None)
    ap.add_argument("--min-recall", type=float, default=None)
    args = ap.parse_args(argv)

    overrides = {"filter_provider": args.provider} if args.provider else {}
    settings = Settings(**overrides)
    flt = RelevanceFilter(settings)
    files = sorted(args.dir.glob("*.json"))
    if not files:
        print(f"no recordings in {args.dir}", file=sys.stderr)
        return 2
    report = evaluate(files, flt, Thresholds(drop=args.drop, review=args.review,
                                             split=settings.filter_split_threshold,
                                             min_segment_steps=settings.filter_min_segment_steps))
    report["provider"] = flt.name
    print(json.dumps(report, indent=2))
    ok = True
    if args.min_precision is not None and (report["drop_precision"] or 0) < args.min_precision:
        ok = False
    if args.min_recall is not None and (report["drop_recall"] or 0) < args.min_recall:
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
