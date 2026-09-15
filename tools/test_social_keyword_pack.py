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

    plans = {
        platform: build_plan(cfg, "signal", platform, "2026-09-15")
        for platform in ("xhs", "dy", "wb")
    }
    broad = build_plan(cfg, "broad", "xhs", "2026-09-15")

    baseline = plans["xhs"]["queries"]
    baseline_queries = [x["query"] for x in baseline]

    for platform, plan in plans.items():
        queries = [x["query"] for x in plan["queries"]]
        _assert(plan["mode"] == "static", f"{platform} must use the auditable event query pack")
        _assert(len(queries) == 14, f"{platform} signal pack must cover 14 topic queries")
        _assert(len(set(q.casefold() for q in queries)) == len(queries), f"{platform} queries must be unique")
        _assert(queries == baseline_queries, f"{platform} should scan the same eight-direction baseline")

    _assert(broad["mode"] == "static", "broad pack should stay explicit and auditable")
    _assert(len(broad["queries"]) == 8, "broad pack should contain eight direction-level probes")

    joined = "\n".join(baseline_queries)
    for fragment in ("重庆", "货车司机", "三峡", "危化品", "农资", "AI", "航运"):
        _assert(fragment in joined, f"signal pack lost required monitoring direction: {fragment}")

    print(f"ok: platform packs={len(baseline_queries)} broad={len(broad['queries'])}")


if __name__ == "__main__":
    main()
