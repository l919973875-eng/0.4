from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml


@dataclass(frozen=True)
class Term:
    text: str
    weight: int = 0


@dataclass(frozen=True)
class Candidate:
    query: str
    template: str
    score: int
    pinned: bool = False
    facets: tuple[str, ...] = ()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value: str) -> str:
    return _clean(value).casefold()


def _term(value: Any) -> Term:
    if isinstance(value, dict):
        return Term(_clean(value.get("term")), int(value.get("weight", 0) or 0))
    return Term(_clean(value), 0)


def _group(cfg: dict, ref: str) -> list[Term]:
    node: Any = (((cfg.get("generator") or {}).get("taxonomy")) or {})
    for part in str(ref).split("."):
        if not isinstance(node, dict):
            return []
        node = node.get(part)
    if not isinstance(node, list):
        return []
    out = []
    seen = set()
    for raw in node:
        item = _term(raw)
        key = _norm(item.text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _rotation_date(cfg: dict, override: str | None = None) -> str:
    if override:
        # Validate format so CI failures are obvious rather than silently changing rotation.
        datetime.strptime(override, "%Y-%m-%d")
        return override
    tz_name = _clean(cfg.get("rotation_timezone") or "Asia/Shanghai")
    return datetime.now(ZoneInfo(tz_name)).date().isoformat()


def _hash_rank(seed: str, value: str) -> str:
    return hashlib.sha256(f"{seed}|{value}".encode("utf-8")).hexdigest()


def _dedupe_candidates(values: list[Candidate]) -> list[Candidate]:
    best: dict[str, Candidate] = {}
    order: list[str] = []
    for item in values:
        key = _norm(item.query)
        if not key:
            continue
        if key not in best:
            best[key] = item
            order.append(key)
            continue
        old = best[key]
        # Pinned wins; otherwise keep the higher-scoring origin for diagnostics.
        if (item.pinned and not old.pinned) or (item.pinned == old.pinned and item.score > old.score):
            best[key] = item
    return [best[k] for k in order]


def _expand_template(cfg: dict, spec: dict, rotation_key: str) -> list[Candidate]:
    name = _clean(spec.get("name") or "unnamed")
    pattern = str(spec.get("pattern") or "").strip()
    refs = spec.get("groups") or {}
    if not pattern or not isinstance(refs, dict) or not refs:
        return []

    slots: list[str] = []
    groups: list[list[Term]] = []
    for slot, ref in refs.items():
        terms = _group(cfg, str(ref))
        if not terms:
            return []
        slots.append(str(slot))
        groups.append(terms)

    base_priority = int(spec.get("priority", 0) or 0)
    out: list[Candidate] = []
    for combo in itertools.product(*groups):
        values = {slot: term.text for slot, term in zip(slots, combo)}
        try:
            query = _clean(pattern.format(**values))
        except (KeyError, ValueError):
            continue
        if not query:
            continue
        score = base_priority + sum(term.weight for term in combo)
        diversity_slots = spec.get("diversity_slots") or []
        if not diversity_slots and spec.get("diversity_slot"):
            diversity_slots = [spec.get("diversity_slot")]
        facets = tuple(_clean(values.get(_clean(slot))) for slot in diversity_slots if _clean(slot))
        out.append(Candidate(query=query, template=name, score=score, facets=facets))

    out = _dedupe_candidates(out)
    # The cap is per high-value template, not over the global object/action/scene space.
    # Equal/near-equal candidates rotate by local date so long-tail queries get sampled.
    out.sort(key=lambda c: (-c.score, _hash_rank(f"{rotation_key}|pool|{name}", c.query)))
    cap = int(spec.get("max_candidates", 0) or 0)
    return out[:cap] if cap > 0 else out


def _static_values(cfg: dict, pack: str, platform: str) -> list[str]:
    values = (((cfg.get("packs") or {}).get(pack) or {}).get(platform) or [])
    out: list[str] = []
    seen = set()
    for value in values:
        text = _clean(value)
        key = _norm(text)
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _budget(cfg: dict, pack: str, platform: str) -> int:
    raw = ((cfg.get("query_budget") or {}).get(pack) or {})
    if isinstance(raw, dict):
        value = raw.get(platform, 0)
    else:
        value = raw
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def build_plan(cfg: dict, pack: str, platform: str, date_override: str | None = None) -> dict:
    packs = cfg.get("packs") or {}
    resolved_pack = pack if pack in packs else _clean(cfg.get("default_pack") or "signal")
    rotation_key = _rotation_date(cfg, date_override)
    generator = cfg.get("generator") or {}
    enabled = platform in (generator.get("enabled_platforms") or [])
    template_specs = ((generator.get("templates") or {}).get(resolved_pack) or [])
    budget = _budget(cfg, resolved_pack, platform)

    if not enabled or not template_specs:
        static = _static_values(cfg, resolved_pack, platform)
        if budget > 0:
            static = static[:budget]
        return {
            "platform": platform,
            "pack": resolved_pack,
            "date": rotation_key,
            "mode": "static",
            "budget": budget or len(static),
            "candidate_count": len(static),
            "queries": [asdict(Candidate(q, "static", 0)) for q in static],
        }

    selected: list[Candidate] = []
    selected_keys: set[str] = set()

    pinned_values = (((generator.get("pinned") or {}).get(resolved_pack) or {}).get(platform) or [])
    for value in pinned_values:
        query = _clean(value)
        key = _norm(query)
        if query and key not in selected_keys:
            selected.append(Candidate(query=query, template="pinned", score=10_000, pinned=True))
            selected_keys.add(key)

    all_candidates: list[Candidate] = []
    per_template: dict[str, int] = {}
    for spec in template_specs:
        name = _clean(spec.get("name") or "unnamed")
        candidates = _expand_template(cfg, spec, rotation_key)
        all_candidates.extend(candidates)
        quota = max(0, int(spec.get("quota", 0) or 0))
        chosen = 0
        ranked = sorted(
            candidates,
            key=lambda c: (-c.score, _hash_rank(f"{rotation_key}|pick|{name}", c.query)),
        )
        # First pass favors different objects/scenes when the template declares a diversity_slot.
        # Second pass fills any remaining quota without the facet restriction.
        used_facets: list[set[str]] = []
        for distinct_only in (True, False):
            for item in ranked:
                if chosen >= quota or (budget and len(selected) >= budget):
                    break
                key = _norm(item.query)
                if key in selected_keys:
                    continue
                facet_keys = [_norm(x) for x in item.facets if _norm(x)]
                while len(used_facets) < len(facet_keys):
                    used_facets.append(set())
                if distinct_only:
                    if not facet_keys:
                        continue
                    if any(facet in used_facets[i] for i, facet in enumerate(facet_keys)):
                        continue
                selected.append(item)
                selected_keys.add(key)
                for i, facet in enumerate(facet_keys):
                    used_facets[i].add(facet)
                chosen += 1
            if chosen >= quota or (budget and len(selected) >= budget):
                break
        per_template[name] = chosen

    # If pinned/template quotas leave spare budget, fill from the remaining high-value pool.
    if not budget:
        budget = len(selected)
    if len(selected) < budget:
        leftovers = _dedupe_candidates(all_candidates)
        leftovers.sort(key=lambda c: (-c.score, _hash_rank(f"{rotation_key}|fill", c.query)))
        for item in leftovers:
            if len(selected) >= budget:
                break
            key = _norm(item.query)
            if key in selected_keys:
                continue
            selected.append(item)
            selected_keys.add(key)

    selected = selected[:budget]
    candidate_count = len(_dedupe_candidates(all_candidates + selected))
    return {
        "platform": platform,
        "pack": resolved_pack,
        "date": rotation_key,
        "mode": "generated",
        "budget": budget,
        "candidate_count": candidate_count,
        "template_selected": per_template,
        "queries": [asdict(x) for x in selected],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build bounded social-media search query packs.")
    ap.add_argument("--config", default="config/social_keywords.yaml")
    ap.add_argument("--platform", choices=["xhs", "dy", "wb"], required=True)
    ap.add_argument("--pack", default="signal")
    ap.add_argument(
        "--field",
        choices=["keywords", "max_per_query", "count", "candidate_count", "budget", "json"],
        default="keywords",
    )
    ap.add_argument("--date", help="Override rotation date (YYYY-MM-DD), useful for tests/replay.")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    pack = args.pack if args.pack in (cfg.get("packs") or {}) else cfg.get("default_pack", "signal")

    if args.field == "max_per_query":
        print(int((cfg.get("max_per_query") or {}).get(pack, 8)))
        return

    plan = build_plan(cfg, pack, args.platform, args.date)
    if args.field == "keywords":
        print(",".join(row["query"] for row in plan["queries"]))
    elif args.field == "count":
        print(len(plan["queries"]))
    elif args.field == "candidate_count":
        print(int(plan["candidate_count"]))
    elif args.field == "budget":
        print(int(plan["budget"]))
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
