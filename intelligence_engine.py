from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlparse

EN_STOP = {
    'the','a','an','and','or','of','to','in','on','for','from','with','at','by','as','is','are','was','were','be','been','being',
    'this','that','these','those','it','its','their','his','her','our','your','after','before','amid','over','under','new','says','say',
    'report','reports','update','latest','live','news','how','what','why','who','when','where','into','about','against','more','could',
}

CRITICAL = re.compile(
    r"\b(nuclear attack|nuclear strike|ballistic missile|missile strike|airstrike|air strike|invasion|martial law|coup d'etat|coup|"
    r"evacuation order|mass casualty|hostage crisis|state of emergency|war declared|blockade)\b|"
    r"核打击|核袭击|导弹袭击|空袭|入侵|政变|戒严|撤侨|大规模伤亡|人质危机|进入紧急状态|封锁",
    re.I,
)
HIGH = re.compile(
    r"\b(military drill|military exercise|troop buildup|troop build-up|mobilization|armed clash|border clash|explosion|bombing|"
    r"terror attack|riot|violent protest|embassy attack|factory shutdown|mine shutdown|port closure|shipping disruption|sanctions|"
    r"export controls?|blacklist|evacuation|detention|arrested|killed|dead|casualties|cyberattack|internet shutdown|power outage)\b|"
    r"军演|军事演习|增兵|军事集结|武装冲突|边境冲突|爆炸|炸弹|恐袭|骚乱|暴力抗议|使馆遇袭|工厂停产|矿山停产|港口关闭|"
    r"航运中断|制裁|出口管制|黑名单|撤离|拘留|被捕|死亡|伤亡|网络攻击|断网|停电",
    re.I,
)
MEDIUM = re.compile(
    r"\b(protest|strike|demonstration|election|government change|cabinet reshuffle|tariff|investigation|probe|investment review|"
    r"military|navy|air force|coast guard|trade restriction|supply chain|mine|port|railway|energy project|semiconductor|critical minerals)\b|"
    r"抗议|罢工|示威|大选|政府更迭|内阁改组|关税|调查|投资审查|军方|海军|空军|海警|贸易限制|供应链|矿山|港口|铁路|能源项目|"
    r"半导体|关键矿产",
    re.I,
)

CATEGORY_RULES = [
    ('安全/冲突', re.compile(r"military|war|conflict|missile|airstrike|troop|navy|army|air force|coast guard|attack|explosion|terror|军|战争|冲突|导弹|空袭|袭击|爆炸|恐袭", re.I)),
    ('台海/印太', re.compile(r"taiwan|taipei|south china sea|philippines|spratly|senkaku|diaoyu|indo-pacific|台海|台湾|南海|菲律宾|钓鱼岛|印太", re.I)),
    ('外交/制裁', re.compile(r"sanction|diplomat|embassy|foreign minister|summit|visa|blacklist|export control|制裁|外交|使馆|峰会|签证|黑名单|出口管制", re.I)),
    ('经贸/科技', re.compile(r"tariff|trade|semiconductor|chip|ai\b|battery|ev\b|electric vehicle|investment|factory|supply chain|关税|贸易|半导体|芯片|人工智能|电池|电动车|投资|工厂|供应链", re.I)),
    ('海外利益', re.compile(r"mine|mining|port|railway|pipeline|power plant|industrial park|workers?|citizens?|矿|港口|铁路|管道|电站|工业园|员工|公民|华人", re.I)),
    ('社会动荡', re.compile(r"protest|strike|riot|demonstration|unrest|抗议|罢工|骚乱|示威|动荡", re.I)),
]

RELATION_BASE = {'direct': 100, 'indirect': 82, 'potential': 60, 'unrelated': 0}
TIER_SCORE = {1: 100, 2: 75, 3: 50, 4: 25}
SEVERITY_SCORE = {'critical': 100, 'high': 75, 'medium': 50, 'low': 25, 'info': 0}

# 用于跨中英文平台聚类的轻量概念词典。不是翻译器，只把高频情报词映射到共同 token。
CROSS_LANGUAGE_TOKENS = {
    '中国': {'china','chinese'}, '中资': {'china','chinese'}, '中国企业': {'china','company'}, '中国员工': {'china','workers'}, '中国工人': {'china','workers'},
    '台湾': {'taiwan'}, '台海': {'taiwan','strait'}, '海峡': {'strait'}, '南海': {'south','china','sea'}, '解放军': {'pla','military'}, '军演': {'military','drills'}, '军事演习': {'military','drills'},
    '抗议': {'protest'}, '示威': {'protest'}, '罢工': {'strike'}, '骚乱': {'riot'}, '冲突': {'conflict'}, '袭击': {'attack'}, '围殴': {'attack','attacked','assaulted'}, '爆炸': {'explosion'}, '死亡': {'killed','dead'}, '死伤': {'killed','casualties'}, '伤亡': {'casualties'},
    '工人': {'workers'}, '员工': {'workers'}, '矿山': {'mine'}, '铜矿': {'copper','mine'}, '锂矿': {'lithium','mine'}, '镍矿': {'nickel','mine'},
    '港口': {'port'}, '铁路': {'railway'}, '工厂': {'factory'}, '工业园': {'industrial','park'}, '停产': {'shutdown'}, '关闭': {'closure'}, '封锁': {'blockade','blocked'},
    '入口': {'entrance'}, '附近': {'near'}, '赞比亚': {'zambia'}, '刚果': {'congo'}, '巴基斯坦': {'pakistan'}, '菲律宾': {'philippines'},
    '缅甸': {'myanmar'}, '柬埔寨': {'cambodia'}, '印度尼西亚': {'indonesia'}, '印尼': {'indonesia'}, '匈牙利': {'hungary'}, '塞尔维亚': {'serbia'},
}


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def severity(text: str) -> tuple[str, int]:
    if CRITICAL.search(text or ''):
        return 'critical', 100
    if HIGH.search(text or ''):
        return 'high', 75
    if MEDIUM.search(text or ''):
        return 'medium', 50
    return 'low', 25


def category(text: str) -> str:
    for label, rx in CATEGORY_RULES:
        if rx.search(text or ''):
            return label
    return '其他'


def freshness_score(value, now: datetime | None = None, horizon_hours: float = 24.0) -> int:
    now = now or datetime.now(timezone.utc)
    dt = _parse_dt(value)
    if not dt:
        return 35
    hours = max(0.0, (now - dt).total_seconds() / 3600)
    return max(0, min(100, round(100 * (1 - hours / horizon_hours))))


def relation_score(row: dict) -> int:
    base = RELATION_BASE.get(row.get('relation'), 0)
    conf = int(row.get('confidence') or 70)
    # 关联度是“与中国关系有多直接”，不是信息真假；只让分类置信度做轻微修正。
    return max(0, min(100, round(base * (0.8 + 0.2 * conf / 100))))


def source_tier(row: dict, tier_cfg: dict | None = None) -> int:
    tier_cfg = tier_cfg or {}
    name = (row.get('source') or row.get('source_name') or '').lower()
    kind = (row.get('source_kind') or '').lower()
    if kind == 'official':
        return 1
    for tier in (1, 2, 3, 4):
        pats = tier_cfg.get(f'tier{tier}_patterns') or []
        if any(str(p).lower() in name for p in pats):
            return tier
    if kind == 'think_tank':
        return 3
    if kind == 'social':
        return 4
    return int(tier_cfg.get('default_tier', 3))


def publisher_family(row: dict, tier_cfg: dict | None = None) -> str:
    tier_cfg = tier_cfg or {}
    name = (row.get('source') or row.get('source_name') or row.get('author') or '').strip().lower()
    platform = (row.get('platform') or '').strip().lower()
    families = tier_cfg.get('publisher_families') or {}
    for family, patterns in families.items():
        if any(str(p).lower() in name for p in (patterns or [])):
            return str(family).lower()
    if platform:
        # 社交账号以“平台+作者”作为独立来源；同一个平台不同作者仍可视作独立苗头。
        author = re.sub(r'\s+', '-', name or 'unknown')[:80]
        return f'{platform}:{author}'
    host = ''
    try:
        host = urlparse(row.get('url') or '').netloc.lower().removeprefix('www.')
    except Exception:
        pass
    return host or re.sub(r'\W+', '-', name)[:80] or 'unknown'


def _latin_tokens(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-z0-9][a-z0-9'\-]{2,}", text.lower()) if x not in EN_STOP}


def _cjk_tokens(text: str) -> set[str]:
    chunks = re.findall(r'[\u3400-\u9fff]{2,}', text)
    out: set[str] = set()
    for chunk in chunks:
        if len(chunk) <= 4:
            out.add(chunk)
        for n in (2, 3):
            for i in range(max(0, len(chunk) - n + 1)):
                out.add(chunk[i:i+n])
    return out


def text_tokens(text: str) -> set[str]:
    raw = text or ''
    tokens = _latin_tokens(raw) | _cjk_tokens(raw)
    for phrase, mapped in CROSS_LANGUAGE_TOKENS.items():
        if phrase in raw:
            tokens.update(mapped)
    return tokens


def _concept_tokens(text: str) -> set[str]:
    raw = (text or '').lower()
    vocab = set().union(*CROSS_LANGUAGE_TOKENS.values())
    out = {t for t in _latin_tokens(raw) if t in vocab}
    for phrase, mapped in CROSS_LANGUAGE_TOKENS.items():
        if phrase in raw:
            out.update(mapped)
    return out


def normalize_text(text: str) -> str:
    text = (text or '').lower()
    text = re.sub(r'https?://\S+', ' ', text)
    text = re.sub(r'[^a-z0-9\u3400-\u9fff%$€£¥]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def story_similarity(a: str, b: str) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    ta, tb = text_tokens(na), text_tokens(nb)
    if not ta or not tb:
        return SequenceMatcher(None, na, nb).ratio()
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    containment = len(ta & tb) / max(1, min(len(ta), len(tb)))
    char = SequenceMatcher(None, na, nb).ratio()
    score = 0.45 * jaccard + 0.35 * containment + 0.20 * char
    # 中英文跨平台标题没有字符相似度时，使用共同情报概念 token 做第二视角。
    if bool(re.search(r'[\u3400-\u9fff]', a or '')) != bool(re.search(r'[\u3400-\u9fff]', b or '')):
        ca, cb = _concept_tokens(a), _concept_tokens(b)
        shared = ca & cb
        if len(shared) >= 4:
            concept_containment = len(shared) / max(1, min(len(ca), len(cb)))
            concept_jaccard = len(shared) / max(1, len(ca | cb))
            score = max(score, 0.78 * concept_containment + 0.22 * concept_jaccard)
    nums_a = set(re.findall(r'\b\d+(?:\.\d+)?%?\b', na))
    nums_b = set(re.findall(r'\b\d+(?:\.\d+)?%?\b', nb))
    if nums_a and nums_b and not (nums_a & nums_b):
        score -= 0.10
    return max(0.0, min(1.0, score))


def _event_text(row: dict) -> str:
    return (row.get('title') or row.get('text') or row.get('snippet') or '').strip()


def _event_time(row: dict) -> datetime:
    return _parse_dt(row.get('published_at') or row.get('collected_at')) or datetime.now(timezone.utc)


def _evidence_score(row: dict, tier_cfg: dict) -> int:
    if row.get('origin_type') == 'social' or row.get('source_kind') == 'social':
        # 社交信息的 confidence 不是“能否展示”的门槛，只表示证据强度。
        base = int(row.get('confidence') or 35)
        media = int(row.get('media_count') or 0)
        engagement = row.get('engagement') or {}
        engagement_total = sum(int(engagement.get(k) or 0) for k in ('like_count','retweet_count','reply_count','quote_count','share_count','comment_count'))
        return max(5, min(90, base + min(12, media * 4) + min(8, int(math.log10(engagement_total + 1) * 3))))
    tier = source_tier(row, tier_cfg)
    return TIER_SCORE.get(tier, 50)


def _cluster_rows(rows: list[dict], threshold: float = 0.60, max_compare: int = 300) -> list[list[dict]]:
    # 只对最近记录做事件化；按时间倒序，优先把新信息归入已有事件。
    ordered = sorted(rows, key=_event_time, reverse=True)
    clusters: list[list[dict]] = []
    reps: list[str] = []
    rep_times: list[datetime] = []
    for row in ordered:
        text = _event_text(row)
        if not text:
            continue
        rt = _event_time(row)
        best_idx, best_sim = -1, 0.0
        start = max(0, len(clusters) - max_compare)
        for idx in range(len(clusters) - 1, start - 1, -1):
            # 相差超过 72 小时的标题，即使相似也倾向视为新事件。
            if abs((rt - rep_times[idx]).total_seconds()) > 72 * 3600:
                continue
            sim = story_similarity(text, reps[idx])
            if sim > best_sim:
                best_idx, best_sim = idx, sim
        if best_idx >= 0 and best_sim >= threshold:
            clusters[best_idx].append(row)
            # 代表文本采用更长且信息量更多的那个，但不频繁改变以避免聚类漂移。
            if len(text) > len(reps[best_idx]) * 1.25:
                reps[best_idx] = text
                rep_times[best_idx] = rt
        else:
            clusters.append([row])
            reps.append(text)
            rep_times.append(rt)
    return clusters



def _term_hit(text: str, term: str) -> bool:
    raw = (text or '').casefold()
    needle = str(term or '').strip().casefold()
    if not needle:
        return False
    # CJK and punctuation-heavy phrases are safest as literal substrings.
    if re.search(r'[\u3400-\u9fff]', needle) or not re.fullmatch(r"[a-z0-9 ._\-/]+", needle):
        return needle in raw
    pattern = r'(?<![a-z0-9])' + re.escape(needle).replace(r'\ ', r'\s+') + r'(?![a-z0-9])'
    return bool(re.search(pattern, raw, re.I))


def risk_taxonomy(text: str, taxonomy_cfg: dict | None = None) -> list[dict]:
    taxonomy_cfg = taxonomy_cfg or {}
    categories = taxonomy_cfg.get('risk_categories') or {}
    hits: list[dict] = []
    for key, spec in categories.items():
        if not isinstance(spec, dict):
            continue
        matched = [str(t) for t in (spec.get('terms') or []) if _term_hit(text, str(t))]
        if not matched:
            continue
        hits.append({
            'key': str(key),
            'label_zh': spec.get('label_zh') or str(key),
            'label_en': spec.get('label_en') or str(key),
            'exposure_weight': int(spec.get('exposure_weight') or 60),
            'matched_terms': matched[:12],
        })
    hits.sort(key=lambda x: (x['exposure_weight'], len(x['matched_terms'])), reverse=True)
    return hits


def _relation_name_score(row: dict) -> int:
    relation = str(row.get('relation') or '').lower()
    return {'direct': 100, 'indirect': 82, 'potential': 58, 'unrelated': 0}.get(relation, relation_score(row))


def china_relevance_event_score(cluster: list[dict]) -> int:
    """Event-level China relevance, deliberately independent from factual confidence."""
    scores = sorted((_relation_name_score(r) for r in cluster), reverse=True)
    if not scores:
        return 0
    best = scores[0]
    top = scores[:3]
    # One explicit direct-China evidence item is enough to establish direct relevance;
    # additional items stabilize the score but do not act as factual corroboration.
    consensus = sum(top) / len(top)
    return max(0, min(100, round(best * 0.78 + consensus * 0.22)))


def novelty_score(first_seen: datetime, now: datetime, matched_previous: bool) -> int:
    if not matched_previous:
        return 100
    hours = max(0.0, (now - first_seen).total_seconds() / 3600)
    if hours <= 6:
        return 95
    if hours <= 24:
        return 82
    if hours <= 72:
        return 60
    if hours <= 120:
        return 40
    return 25


def _severity_value(label: str) -> int:
    return SEVERITY_SCORE.get(str(label or '').lower(), 25)


def _previous_event_match(title: str, entities: list[str], countries: list[str], previous_events: list[dict], last_seen: datetime) -> tuple[dict | None, float]:
    best, best_score = None, 0.0
    entity_set = {str(x).casefold() for x in entities if x}
    country_set = {str(x).casefold() for x in countries if x}
    for prev in previous_events or []:
        p_last = _parse_dt(prev.get('last_seen'))
        if p_last and abs((last_seen - p_last).total_seconds()) > 8 * 86400:
            continue
        sim = story_similarity(title, prev.get('title_original') or prev.get('title') or '')
        prev_entities = {str(x).casefold() for x in (prev.get('entities') or []) if x}
        prev_countries = {str(x).casefold() for x in (prev.get('countries') or []) if x}
        ent_overlap = len(entity_set & prev_entities) / max(1, min(len(entity_set), len(prev_entities))) if entity_set and prev_entities else 0.0
        geo_overlap = 1.0 if country_set and prev_countries and (country_set & prev_countries) else 0.0
        score = sim * 0.76 + ent_overlap * 0.16 + geo_overlap * 0.08
        if score > best_score:
            best, best_score = prev, score
    return (best, best_score) if best_score >= 0.54 else (None, best_score)


def _momentum_score(previous: dict | None, current_source_count: int, current_evidence_count: int, current_severity: str,
                    official_now: bool, last_seen: datetime, now: datetime, escalation_hit: bool) -> tuple[int, dict]:
    if not previous:
        score = 55 + min(15, max(0, current_source_count - 1) * 8) + (10 if escalation_hit else 0)
        return min(100, score), {
            'new_sources': current_source_count,
            'new_evidence': current_evidence_count,
            'severity_change': 0,
            'official_added': official_now,
        }
    prev_sources = int(previous.get('source_count') or 0)
    prev_evidence = int(previous.get('evidence_count') or len(previous.get('evidence') or []))
    source_delta = max(0, current_source_count - prev_sources)
    evidence_delta = max(0, current_evidence_count - prev_evidence)
    sev_delta = _severity_value(current_severity) - _severity_value(previous.get('severity'))
    official_before = bool(previous.get('official_evidence')) or previous.get('status') == '官方信号'
    official_added = official_now and not official_before
    latest_age_h = max(0.0, (now - last_seen).total_seconds() / 3600)
    score = 18
    score += min(30, source_delta * 15)
    score += min(22, evidence_delta * 5)
    score += 14 if official_added else 0
    score += 12 if sev_delta > 0 else 0
    score += 10 if escalation_hit else 0
    score += 8 if latest_age_h <= 12 and (source_delta or evidence_delta) else 0
    if not (source_delta or evidence_delta or official_added or sev_delta > 0 or escalation_hit):
        score = 12
    return max(0, min(100, score)), {
        'new_sources': source_delta,
        'new_evidence': evidence_delta,
        'severity_change': sev_delta,
        'official_added': official_added,
    }


def _lifecycle(previous: dict | None, first_seen: datetime, last_seen: datetime, now: datetime, momentum: int,
               resolution_hit: bool, escalation_hit: bool, deltas: dict) -> str:
    if resolution_hit:
        return 'resolved'
    age_h = max(0.0, (now - first_seen).total_seconds() / 3600)
    stale_h = max(0.0, (now - last_seen).total_seconds() / 3600)
    if not previous and age_h <= 36:
        return 'emerging'
    if momentum >= 55 or escalation_hit or deltas.get('severity_change', 0) > 0 or deltas.get('new_sources', 0) >= 2:
        return 'escalating'
    prev_lifecycle = (previous or {}).get('lifecycle')
    if prev_lifecycle == 'emerging' and age_h <= 24 and stale_h <= 24:
        return 'emerging'
    if prev_lifecycle == 'escalating' and stale_h <= 12 and momentum > 20:
        return 'escalating'
    if stale_h >= 36 or momentum <= 20:
        return 'stabilizing'
    return 'emerging' if age_h <= 24 else 'stabilizing'


def _why_now(lifecycle: str, novelty: int, momentum: int, source_count: int, deltas: dict,
             risk_zh: str, risk_en: str, official_added: bool) -> tuple[str, str]:
    zh_bits = [f'{risk_zh}信号'] if risk_zh else ['涉华异常信号']
    en_bits = [f'{risk_en} signal'] if risk_en else ['China-related anomaly']
    if lifecycle == 'emerging':
        zh_bits.append('近期首次进入事件池')
        en_bits.append('newly entered the event pool')
    elif lifecycle == 'escalating':
        zh_bits.append('正在升级或扩散')
        en_bits.append('is escalating or spreading')
    elif lifecycle == 'stabilizing':
        zh_bits.append('近期新增证据有限，进入趋稳观察')
        en_bits.append('has limited new evidence and is stabilizing')
    elif lifecycle == 'resolved':
        zh_bits.append('出现解决/恢复迹象')
        en_bits.append('shows signs of resolution or recovery')
    if deltas.get('new_sources', 0):
        zh_bits.append(f"新增{deltas['new_sources']}条独立信息链")
        en_bits.append(f"{deltas['new_sources']} new independent source chain(s)")
    elif source_count >= 2:
        zh_bits.append(f'已有{source_count}条独立信息链')
        en_bits.append(f'{source_count} independent source chains')
    if official_added:
        zh_bits.append('本轮首次出现官方证据')
        en_bits.append('official evidence appeared for the first time')
    zh_bits.append(f'新颖度{novelty} / 动量{momentum}')
    en_bits.append(f'novelty {novelty} / momentum {momentum}')
    return '；'.join(zh_bits) + '。', '; '.join(en_bits) + '.'


def build_events(articles: list[dict], signals: list[dict], tier_cfg: dict | None = None, now: datetime | None = None,
                 previous_events: list[dict] | None = None, taxonomy_cfg: dict | None = None) -> list[dict]:
    tier_cfg = tier_cfg or {}
    taxonomy_cfg = taxonomy_cfg or {}
    previous_events = previous_events or []
    now = now or datetime.now(timezone.utc)
    unified: list[dict] = []
    for r in articles:
        x = dict(r)
        x['origin_type'] = 'news'
        unified.append(x)
    for r in signals:
        x = dict(r)
        x['origin_type'] = 'social'
        x.setdefault('source_kind', 'social')
        x.setdefault('source', f"{x.get('platform','social')} · {x.get('author','unknown')}")
        x.setdefault('title', x.get('text', '')[:260])
        unified.append(x)

    # Seven-day active-event window. Historical raw evidence remains retained separately.
    recent: list[dict] = []
    for r in unified:
        dt = _event_time(r)
        if (now - dt).total_seconds() <= 7 * 86400:
            recent.append(r)

    clusters = _cluster_rows(recent)
    events: list[dict] = []
    escalation_terms = taxonomy_cfg.get('escalation_terms') or []
    resolution_terms = taxonomy_cfg.get('resolution_terms') or []

    for cluster in clusters:
        cluster.sort(key=_event_time, reverse=True)
        news = [r for r in cluster if r.get('origin_type') == 'news']
        social = [r for r in cluster if r.get('origin_type') == 'social']
        families = {publisher_family(r, tier_cfg) for r in cluster}
        platforms = sorted({r.get('platform') for r in social if r.get('platform')})
        combined_text = ' '.join(_event_text(r) for r in cluster[:12])
        risk_hits = risk_taxonomy(combined_text, taxonomy_cfg)
        primary_risk = risk_hits[0] if risk_hits else None
        risk_zh = primary_risk['label_zh'] if primary_risk else category(combined_text)
        risk_en = primary_risk['label_en'] if primary_risk else ''
        rel = china_relevance_event_score(cluster)
        sev_label, sev = max((severity(_event_text(r)) for r in cluster), key=lambda x: x[1], default=('low', 25))
        fresh = max((freshness_score(r.get('published_at') or r.get('collected_at'), now) for r in cluster), default=0)
        tier_best = min((source_tier(r, tier_cfg) for r in news), default=4)
        source_score = TIER_SCORE.get(tier_best, 25)
        corroboration = min(100, max(15, len(families) * 20))
        evidence_types = {('official' if r.get('source_kind') == 'official' else r.get('origin_type') or 'unknown') for r in cluster}
        evidence_diversity = min(100, len(evidence_types) * 34)

        # Representative evidence remains source-quality aware, but source quality does not gate entry.
        def rep_key(r: dict):
            origin_bonus = 20 if r.get('source_kind') == 'official' else (12 if r.get('origin_type') == 'news' else 0)
            return origin_bonus + (5 - source_tier(r, tier_cfg)) * 10 + severity(_event_text(r))[1] + freshness_score(r.get('published_at') or r.get('collected_at'), now)
        rep = max(cluster, key=rep_key)
        title = _event_text(rep)[:360]
        cluster_first_seen = min(_event_time(r) for r in cluster)
        last_seen = max(_event_time(r) for r in cluster)

        entities: list[str] = []
        reasons: list[str] = []
        countries: list[str] = []
        for r in cluster:
            for ent in (r.get('entities') or []):
                if ent and ent not in entities:
                    entities.append(ent)
            reason = (r.get('reason') or '').strip()
            if reason and reason not in reasons:
                reasons.append(reason)
            country = (r.get('country') or '').strip()
            if country and country not in countries:
                countries.append(country)

        previous, previous_match_score = _previous_event_match(title, entities, countries, previous_events, last_seen)
        first_seen = _parse_dt(previous.get('first_seen')) if previous else cluster_first_seen
        first_seen = first_seen or cluster_first_seen
        novelty = novelty_score(first_seen, now, previous is not None)
        escalation_hit = any(_term_hit(combined_text, str(t)) for t in escalation_terms)
        resolution_hit = any(_term_hit(combined_text, str(t)) for t in resolution_terms)
        official_now = any(r.get('source_kind') == 'official' for r in news)
        momentum, deltas = _momentum_score(previous, len(families), len(cluster), sev_label, official_now, last_seen, now, escalation_hit)
        lifecycle = _lifecycle(previous, first_seen, last_seen, now, momentum, resolution_hit, escalation_hit, deltas)

        # Exposure estimates how much Chinese people/assets/strategic interests could be affected.
        exposure_base = primary_risk['exposure_weight'] if primary_risk else 55
        exposure = exposure_base + min(8, len(entities) * 2) + (5 if rel >= 95 else 0)
        exposure = max(0, min(100, exposure))

        # Legacy WorldMonitor-style importance remains for reference, not as the homepage gate.
        importance = round(sev * 0.50 + source_score * 0.18 + corroboration * 0.17 + fresh * 0.15)

        # v0.5 Priority = "worth watching now if true". Confidence is intentionally absent.
        priority = round(rel * 0.30 + sev * 0.25 + novelty * 0.15 + momentum * 0.15 + exposure * 0.10 + fresh * 0.05)
        if lifecycle == 'resolved':
            priority = round(priority * 0.78)

        evidence_scores = [_evidence_score(r, tier_cfg) for r in cluster]
        best_evidence = max(evidence_scores) if evidence_scores else 20
        # Confidence = evidence strength + independent publisher families + evidence-type diversity.
        confidence = round(min(100, best_evidence * 0.55 + corroboration * 0.30 + evidence_diversity * 0.15))

        if social and not news:
            evidence_status = '苗头' if len(families) == 1 else '多源苗头'
        elif official_now:
            evidence_status = '官方信号'
        elif news and social:
            evidence_status = '持续发展'
        else:
            evidence_status = '报道中'

        event_id = previous.get('id') if previous and previous.get('id') else hashlib.sha256((normalize_text(title) + cluster_first_seen.strftime('%Y-%m-%d')).encode('utf-8')).hexdigest()[:20]
        why_zh, why_en = _why_now(lifecycle, novelty, momentum, len(families), deltas, risk_zh, risk_en, deltas.get('official_added', False))
        history = list((previous or {}).get('history') or [])[-12:]
        snapshot = {
            'observed_at': now.isoformat(), 'last_seen': last_seen.isoformat(), 'lifecycle': lifecycle,
            'priority_score': max(0, min(100, priority)), 'confidence_score': max(0, min(100, confidence)),
            'novelty_score': novelty, 'momentum_score': momentum, 'source_count': len(families), 'evidence_count': len(cluster),
        }
        if not history or history[-1] != snapshot:
            history.append(snapshot)

        events.append({
            'id': event_id,
            'title': title,
            'language': rep.get('language') or '',
            'status': evidence_status,
            'evidence_status': evidence_status,
            'lifecycle': lifecycle,
            'category': risk_zh,
            'risk_category': primary_risk['key'] if primary_risk else 'other',
            'risk_category_en': risk_en,
            'risk_categories': [x['key'] for x in risk_hits[:4]],
            'matched_risk_terms': list(dict.fromkeys(t for x in risk_hits[:4] for t in x['matched_terms']))[:20],
            'severity': sev_label,
            'priority_score': max(0, min(100, priority)),
            'confidence_score': max(0, min(100, confidence)),
            'importance_score': max(0, min(100, importance)),
            'china_relevance_score': rel,
            'novelty_score': novelty,
            'momentum_score': momentum,
            'exposure_score': exposure,
            'freshness_score': fresh,
            'corroboration_score': corroboration,
            'evidence_diversity_score': evidence_diversity,
            'first_seen': first_seen.isoformat(),
            'last_seen': last_seen.isoformat(),
            'source_count': len(families),
            'evidence_count': len(cluster),
            'news_count': len(news),
            'social_count': len(social),
            'official_evidence': official_now,
            'platforms': platforms,
            'countries': countries[:8],
            'entities': entities[:16],
            'reason': reasons[0] if reasons else '',
            'why_now_zh': why_zh,
            'why_now_en': why_en,
            'previous_event_matched': bool(previous),
            'previous_match_score': round(previous_match_score, 3) if previous else 0,
            'change': deltas,
            'history': history[-12:],
            'evidence': [
                {
                    'id': r.get('id'),
                    'origin_type': r.get('origin_type'),
                    'source': r.get('source'),
                    'source_kind': r.get('source_kind'),
                    'platform': r.get('platform'),
                    'author': r.get('author'),
                    'title': _event_text(r)[:500],
                    'url': r.get('url'),
                    'published_at': r.get('published_at'),
                    'collected_at': r.get('collected_at'),
                    'relation': r.get('relation'),
                    'confidence': r.get('confidence'),
                    'publisher_family': publisher_family(r, tier_cfg),
                }
                for r in sorted(cluster, key=_event_time, reverse=True)[:30]
            ],
        })

    # Priority first; when equal, favor events that are moving now.
    events.sort(key=lambda e: (e['priority_score'], e['momentum_score'], e['last_seen']), reverse=True)
    return events


def select_latest(events: list[dict], hours: int = 24, limit: int = 18, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - hours * 3600
    rows = []
    for e in events:
        dt = _parse_dt(e.get('last_seen'))
        if not dt or dt.timestamp() < cutoff:
            continue
        if int(e.get('priority_score') or 0) < 58:
            continue
        rows.append(e)
    # Focus should be small and diverse. Avoid a single risk category occupying the page.
    out: list[dict] = []
    per_cat = Counter()
    for e in rows:
        cat = e.get('risk_category') or e.get('category') or 'other'
        cap = 4 if cat != 'military_security' else 5
        if per_cat[cat] >= cap:
            continue
        out.append(e)
        per_cat[cat] += 1
        if len(out) >= limit:
            break
    return out
