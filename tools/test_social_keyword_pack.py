from __future__ import annotations

from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from social_keyword_pack import build_plan  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config" / "social_keywords.yaml").read_text(encoding="utf-8")) or {}

    signal = build_plan(cfg, "signal", "xhs", "2026-08-18")
    signal2 = build_plan(cfg, "signal", "xhs", "2026-08-19")
    broad = build_plan(cfg, "broad", "xhs", "2026-08-18")
    dy = build_plan(cfg, "signal", "dy", "2026-08-18")

    signal_queries = [x["query"] for x in signal["queries"]]
    broad_queries = [x["query"] for x in broad["queries"]]
    signal2_queries = [x["query"] for x in signal2["queries"]]

    _assert(signal["mode"] == "generated", "xhs signal should use generator")
    _assert(len(signal_queries) == 36, "xhs signal budget should be 36")
    _assert(len(set(q.casefold() for q in signal_queries)) == len(signal_queries), "signal queries must be unique")
    _assert(len(broad_queries) == 72, "xhs broad budget should be 72")
    _assert(signal["candidate_count"] < 1000, "candidate pool should remain bounded")
    _assert(broad["candidate_count"] < 1000, "broad candidate pool should remain bounded")
    _assert(signal_queries[:8] == [x for x in cfg["generator"]["pinned"]["signal"]["xhs"]], "pinned baseline changed")
    _assert(signal_queries != signal2_queries, "daily rotating queries should change across dates")
    _assert(dy["mode"] == "static", "dy should remain static in v0.4.4")
    _assert(len(dy["queries"]) == 11, "dy static fallback should remain compatible")

    required_fragments = ["绑架", "矿山", "制裁", "关税", "军演"]
    joined = "\n".join(signal_queries)
    for fragment in required_fragments:
        _assert(fragment in joined, f"signal pack lost required category: {fragment}")

    print(
        f"ok: xhs signal={len(signal_queries)}/{signal['candidate_count']} "
        f"broad={len(broad_queries)}/{broad['candidate_count']} dy={len(dy['queries'])}"
    )


if __name__ == "__main__":
    main()
