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
    '中国': {'china','chinese'}, '中资': {'china','chinese'}, '中国企业': {'china','company'}, '中国员工': {'china','workers'},
    '台湾': {'taiwan'}, '台海': {'taiwan','strait'}, '海峡': {'strait'}, '南海': {'south','china','sea'}, '解放军': {'pla','military'}, '军演': {'military','drills'}, '军事演习': {'military','drills'},
    '抗议': {'protest'}, '示威': {'protest'}, '罢工': {'strike'}, '骚乱': {'riot'}, '冲突': {'conflict'}, '袭击': {'attack'}, '爆炸': {'explosion'},
    '工人': {'workers'}, '员工': {'workers'}, '矿山': {'mine'}, '铜矿': {'copper','mine'}, '锂矿': {'lithium','mine'}, '镍矿': {'nickel','mine'},
    '港口': {'port'}, '铁路': {'railway'}, '工厂': {'factory'}, '停产': {'shutdown'}, '关闭': {'closure'}, '封锁': {'blockade','blocked'},
    '入口': {'entrance'}, '附近': {'near'}, '赞比亚': {'zambia'}, '刚果': {'congo'}, '巴基斯坦': {'pakistan'}, '菲律宾': {'philippines'},
    '缅甸': {'myanmar'}, '印度尼西亚': {'indonesia'}, '印尼': {'indonesia'}, '匈牙利': {'hungary'}, '塞尔维亚': {'serbia'},
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


def build_events(articles: list[dict], signals: list[dict], tier_cfg: dict | None = None, now: datetime | None = None) -> list[dict]:
    tier_cfg = tier_cfg or {}
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

    # 只用近 7 天形成当前事件，历史原始材料仍保留在 articles/signals 中。
    recent: list[dict] = []
    for r in unified:
        dt = _event_time(r)
        if (now - dt).total_seconds() <= 7 * 86400:
            recent.append(r)

    clusters = _cluster_rows(recent)
    events: list[dict] = []
    for cluster in clusters:
        cluster.sort(key=_event_time, reverse=True)
        news = [r for r in cluster if r.get('origin_type') == 'news']
        social = [r for r in cluster if r.get('origin_type') == 'social']
        families = {publisher_family(r, tier_cfg) for r in cluster}
        platforms = sorted({r.get('platform') for r in social if r.get('platform')})
        rel = max((relation_score(r) for r in cluster), default=0)
        sev_label, sev = max((severity(_event_text(r)) for r in cluster), key=lambda x: x[1], default=('low', 25))
        fresh = max((freshness_score(r.get('published_at') or r.get('collected_at'), now) for r in cluster), default=0)
        tier_best = min((source_tier(r, tier_cfg) for r in news), default=4)
        source_score = TIER_SCORE.get(tier_best, 25)
        corroboration = min(100, max(20, len(families) * 20))

        # WorldMonitor式“严重性/来源/独立来源/新鲜度”重要度，保留为新闻重要度。
        importance = round(sev * 0.55 + source_score * 0.20 + corroboration * 0.15 + fresh * 0.10)

        # 对本项目更重要的 Priority：涉华关联与潜在影响权重更高，不用可信度把苗头压下去。
        cross_platform = min(100, len(set(platforms)) * 25 + min(50, len(families) * 10))
        priority = round(rel * 0.38 + sev * 0.32 + fresh * 0.10 + corroboration * 0.10 + cross_platform * 0.10)

        evidence_scores = [_evidence_score(r, tier_cfg) for r in cluster]
        # Confidence 是证据强度：独立来源越多越高；但绝不作为是否展示的门槛。
        confidence = round(min(100, (max(evidence_scores) if evidence_scores else 20) * 0.65 + min(100, len(families) * 20) * 0.35))

        # 代表条目优先官方/Tier1新闻；没有正式报道时取最新高优先级社交信号。
        def rep_key(r: dict):
            origin_bonus = 20 if r.get('source_kind') == 'official' else (12 if r.get('origin_type') == 'news' else 0)
            return origin_bonus + (5 - source_tier(r, tier_cfg)) * 10 + severity(_event_text(r))[1] + freshness_score(r.get('published_at') or r.get('collected_at'), now)
        rep = max(cluster, key=rep_key)
        title = _event_text(rep)[:360]
        first_seen = min(_event_time(r) for r in cluster)
        last_seen = max(_event_time(r) for r in cluster)
        if social and not news:
            status = '苗头' if len(families) == 1 else '多源苗头'
        elif any(r.get('source_kind') == 'official' for r in news):
            status = '官方信号'
        elif news and social:
            status = '持续发展'
        else:
            status = '报道中'
        event_id = hashlib.sha256((normalize_text(title) + first_seen.strftime('%Y-%m-%d')).encode('utf-8')).hexdigest()[:20]
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
        events.append({
            'id': event_id,
            'title': title,
            'language': rep.get('language') or '',
            'status': status,
            'category': category(' '.join(_event_text(r) for r in cluster[:8])),
            'severity': sev_label,
            'priority_score': max(0, min(100, priority)),
            'confidence_score': max(0, min(100, confidence)),
            'importance_score': max(0, min(100, importance)),
            'china_relevance_score': rel,
            'first_seen': first_seen.isoformat(),
            'last_seen': last_seen.isoformat(),
            'source_count': len(families),
            'news_count': len(news),
            'social_count': len(social),
            'platforms': platforms,
            'countries': countries[:8],
            'entities': entities[:16],
            'reason': reasons[0] if reasons else '',
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
                }
                for r in sorted(cluster, key=_event_time, reverse=True)[:30]
            ],
        })

    events.sort(key=lambda e: (e['priority_score'], e['last_seen']), reverse=True)
    return events


def select_latest(events: list[dict], hours: int = 24, limit: int = 40, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - hours * 3600
    rows = []
    for e in events:
        dt = _parse_dt(e.get('last_seen'))
        if dt and dt.timestamp() >= cutoff and int(e.get('priority_score') or 0) >= 55:
            rows.append(e)
    # 首页不让同一板块占满，先按优先级取，再做轻量类别配额。
    out: list[dict] = []
    per_cat = Counter()
    for e in rows:
        cat = e.get('category') or '其他'
        if per_cat[cat] >= 12:
            continue
        out.append(e)
        per_cat[cat] += 1
        if len(out) >= limit:
            break
    return out
