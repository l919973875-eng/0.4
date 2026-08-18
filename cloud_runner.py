from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import html
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit, urlparse
from urllib.robotparser import RobotFileParser

import feedparser
import httpx
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from intelligence_engine import build_events, select_latest, severity, story_similarity
from bilingual import enrich_events_bilingual
from site_builder import build_site
from social_connectors import collect_social

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / 'config'
DATA_DIR = ROOT / 'data'
SOURCES_FILE = CONFIG_DIR / 'sources.yaml'
INTERESTS_FILE = CONFIG_DIR / 'china_interest_map.yaml'
TIERS_FILE = CONFIG_DIR / 'source_tiers.yaml'
SOCIAL_FILE = CONFIG_DIR / 'social_sources.yaml'
SOCIAL_KEYWORDS_FILE = CONFIG_DIR / 'social_keywords.yaml'
GEO_ALIASES_FILE = CONFIG_DIR / 'geo_aliases.yaml'
ARTICLES_FILE = DATA_DIR / 'articles.json'
SIGNALS_FILE = DATA_DIR / 'signals.json'
EXTERNAL_SIGNALS_FILE = DATA_DIR / 'signals_external.json'
SOCIAL_REVIEW_FILE = DATA_DIR / 'social_review.json'
EVENTS_FILE = DATA_DIR / 'events.json'
LATEST_FILE = DATA_DIR / 'latest.json'
STATUS_FILE = DATA_DIR / 'run_status.json'
TRANSLATION_CACHE_FILE = DATA_DIR / 'translation_cache.json'

USER_AGENT = os.getenv('USER_AGENT', 'GlobalChinaEarlySignals/0.4 (+research; GitHub Actions)')
FETCH_TIMEOUT = int(os.getenv('FETCH_TIMEOUT_SECONDS', '18'))
MAX_PER_SOURCE = int(os.getenv('MAX_ARTICLES_PER_SOURCE', '5'))
MAX_CONNECTIONS = int(os.getenv('MAX_CONNECTIONS', '36'))
CONCURRENCY = int(os.getenv('CONCURRENCY', '18'))
RETENTION_DAYS = int(os.getenv('RETENTION_DAYS', '45'))
SIGNAL_RETENTION_DAYS = int(os.getenv('SIGNAL_RETENTION_DAYS', '14'))
MAX_STORED = int(os.getenv('MAX_STORED_ARTICLES', '30000'))
MAX_STORED_SIGNALS = int(os.getenv('MAX_STORED_SIGNALS', '15000'))
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '').strip()
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5-mini').strip() or 'gpt-5-mini'
CLASSIFIER_BATCH = int(os.getenv('CLASSIFIER_BATCH_SIZE', '25'))
ENABLE_GDELT = os.getenv('ENABLE_GDELT', 'true').lower() in {'1', 'true', 'yes', 'on'}
GDELT_TIMESPAN = os.getenv('GDELT_TIMESPAN', '1d')


def _social_taxonomy_terms(section: str) -> list[str]:
    """Load canonical social taxonomy terms so discovery and review use one vocabulary."""
    try:
        cfg = yaml.safe_load(SOCIAL_KEYWORDS_FILE.read_text(encoding='utf-8')) or {}
        groups = ((((cfg.get('generator') or {}).get('taxonomy') or {}).get(section)) or {})
    except Exception:
        return []
    if not isinstance(groups, dict):
        return []
    out = []
    seen = set()
    for values in groups.values():
        if not isinstance(values, list):
            continue
        for raw in values:
            value = raw.get('term') if isinstance(raw, dict) else raw
            text = str(value or '').strip()
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                out.append(text)
    return out


def _merge_terms(*groups: list[str]) -> list[str]:
    out = []
    seen = set()
    for group in groups:
        for raw in group:
            text = str(raw or '').strip()
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                out.append(text)
    return out


TRACKING = {'utm_source','utm_medium','utm_campaign','utm_term','utm_content','gclid','fbclid','mc_cid','mc_eid'}
ARTICLE_HINTS = re.compile(r'/(20\d{2}/|article|articles|news|story|stories|analysis|research|report|reports|publication|publications|commentary|insight|insights|press|blog|opinion|policy|event|events)/', re.I)
SKIP_HINTS = re.compile(r'/(tag|topic|author|authors|category|categories|about|contact|privacy|terms|login|subscribe|newsletter|podcast|video|videos)(/|$)', re.I)
DIRECT_TERMS = re.compile(
    r"\b(china|chinese|beijing|prc|people'?s republic of china|xi jinping|ccp|cpc|pla|renminbi|yuan|hong kong|xinjiang|tibet|taiwan)\b|"
    r"\b(huawei|byd|catl|cosco|zte|smic|bytedance|tiktok|cnooc|sinopec|petrochina|crrc|alibaba|tencent|lenovo|geely|saic|chery|great wall motor|china railway|state grid|zijin)\b|"
    r"中国|中方|中资|中国企业|中国员工|中国公民|华人|北京|习近平|中共|中共中央|国务院|外交部|商务部|国台办|央企|人民币|解放军|台湾|台海|南海|华为|比亚迪|宁德时代|中远海运|紫金矿业|中石油|中石化|中国铁路|一带一路",
    re.I,
)
MAJOR_EVENT = re.compile(
    r"\b(election|electoral|vote|government|cabinet|president|prime minister|coalition|parliament|congress|policy|regulation|law|bill|ban|tariff|sanction|blacklist|export control|investment|subsidy|review|probe|investigation|military|defen[cs]e|war|conflict|coup|protest|strike|riot|unrest|terror|port|mine|mining|copper|lithium|nickel|rare earth|oil|gas|energy|rail|railway|telecom|semiconductor|chip|battery|ev|electric vehicle|infrastructure|nuclear|space|satellite|cyber|data|artificial intelligence|\bai\b|trade|supply chain|shipping|shipping lane|currency|central bank|interest rate|foreign policy|diplomatic|diplomacy|alliance|nato|eu|european union|evacuation|attack|explosion|shutdown|closure)\b|"
    r"大选|选举|政府|内阁|总统|总理|政策|监管|法律|禁令|关税|制裁|黑名单|出口管制|投资|补贴|审查|调查|军事|国防|战争|冲突|政变|抗议|罢工|骚乱|恐袭|港口|矿山|铜|锂|镍|稀土|石油|天然气|能源|铁路|电信|半导体|芯片|电池|电动车|基础设施|核|卫星|网络|人工智能|贸易|供应链|航运|央行|利率|外交|联盟|撤离|袭击|爆炸|停产|关闭",
    re.I,
)
SPORT_ENTERTAINMENT = re.compile(r'\b(football|soccer|basketball|baseball|tennis|golf|formula 1|f1|olympic|league|cup final|celebrity|actor|actress|movie|film festival|music|singer|concert|fashion|recipe)\b|足球|篮球|网球|高尔夫|奥运|联赛|明星|演员|电影|音乐|演唱会|时尚|菜谱', re.I)


# 社交平台与新闻不同：仅出现“中国/华为/比亚迪”不能直接入池，必须同时出现“异常事件/风险动作”，
# 或命中已知海外资产/项目。这样保留“低可信但高价值”的苗头，同时过滤消费、生活方式和产品讨论。
SOCIAL_CHINA_ANCHORS = _merge_terms([
    '中资','中国企业','中国公司','中企','中国员工','中国工人','中国公民','中国游客','华人','华侨',
    '一带一路','中国项目','中国投资','中国资本','中国驻','中国使馆','中国大使馆','解放军','台海','南海',
    '华为','比亚迪','宁德时代','中远海运','紫金矿业','中石油','中石化','国家电网','中国铁路','中兴','字节跳动',
    'chinese company','chinese workers','chinese citizens','chinese investment','china-backed','belt and road','pla','huawei','byd','catl','cosco','zijin',
], _social_taxonomy_terms('objects'))
SOCIAL_EVENT_TERMS = _merge_terms([
    '撤离','撤侨','遇袭','袭击','绑架','失踪','死亡','伤亡','被捕','拘留','驱逐','冲突','武装','爆炸','火灾','事故','骚乱','抗议','示威','罢工','封锁',
    '停工','停产','关闭','暂停运营','撤资','裁员','制裁','禁令','调查','审查','黑名单','出口管制','关税','扣押','没收','断网','停电','军演','军事演习','集结','海警','拦截','碰撞','驱离','危机','紧急状态',
    'evacuation','evacuate','attack','kidnap','missing','killed','casualties','detained','arrested','expelled','clash','conflict','explosion','fire','accident','riot','protest','strike','blockade','shutdown','closure','suspend','sanction','ban','probe','investigation','blacklist','export control','tariff','seized','internet shutdown','power outage','military drill','intercept','collision','crisis','emergency',
], _social_taxonomy_terms('actions'))
SOCIAL_STRATEGIC_TERMS = _merge_terms([
    '矿山','铜矿','锂矿','镍矿','稀土','工厂','工业园','港口','码头','铁路','公路','管道','油田','气田','电站','水电站','矿区','园区','供应链','航运','海运','电信','芯片','半导体','电池','电动车','大坝','使馆','领馆','项目',
    'mine','mining','copper','lithium','nickel','rare earth','factory','industrial park','port','terminal','railway','pipeline','oilfield','gas field','power plant','supply chain','shipping','telecom','semiconductor','battery','embassy','consulate','project',
], _social_taxonomy_terms('scenes'))
SOCIAL_NOISE_TERMS = [
    '测评','开箱','价格','优惠','降价','买车','提车','车评','手机评测','手机测评','好用吗','种草','穿搭','美妆','护肤','美食','餐厅','旅游攻略','旅行攻略','留学申请','留学生活','求职','招聘','面试','教程','摄影','壁纸','追星','演唱会','电视剧','电影推荐','游戏','抽奖','购物','代购','二手','闲置','新品发布','产品发布',
    'review','unboxing','discount','shopping','recipe','travel guide','study abroad','job hunting','fashion','beauty','concert','movie review','gaming','giveaway','product launch',
]


def _matched_terms(text: str, terms: list[str]) -> list[str]:
    low = (text or '').lower()
    out = []
    for term in terms:
        t = str(term).lower().strip()
        if t and t in low and term not in out:
            out.append(term)
    return out


def social_relevance_decision(item: RawItem, interests: dict, raw: dict | None = None) -> tuple[Decision, dict]:
    raw = raw or {}
    text = f"{item.title} {item.snippet or ''}".strip()
    anchors = _matched_terms(text, SOCIAL_CHINA_ANCHORS)
    events = _matched_terms(text, SOCIAL_EVENT_TERMS)
    strategic = _matched_terms(text, SOCIAL_STRATEGIC_TERMS)
    noise = _matched_terms(text, SOCIAL_NOISE_TERMS)
    profiles, entities, ctx = match_interest_profiles(item.title, item.snippet, item.source_country, interests)
    sev, sev_score = severity(text)

    score = 0
    if anchors:
        score += 38
    if entities:
        score += 28
    if events:
        score += 34
    if strategic:
        score += 14
    if sev in {'critical', 'high'}:
        score += 10
    elif sev == 'medium':
        score += 5
    # 明显消费/生活内容如果没有风险动作，强力降噪；即使有品牌名也不应自动入池。
    if noise and not events:
        score -= 48
    elif noise:
        score -= 15
    # 单独品牌/中国泛词，没有异常事件，不进入“苗头”。
    if anchors and not events and not entities:
        score -= 25
    if not anchors and not entities:
        score -= 20
    score = max(0, min(100, score))

    accepted = False
    relation = 'unrelated'
    reason = '仅出现泛涉华词或消费/生活内容，未发现值得预警的异常事件'
    if entities and events:
        accepted = score >= 55
        relation = 'indirect' if accepted else 'unrelated'
        reason = f'命中中国海外关联企业/项目，并出现异常事件信号；{ctx}'
    elif anchors and events:
        accepted = score >= 55
        relation = 'direct' if accepted else 'unrelated'
        reason = '直接涉华对象与异常事件/政策动作同时出现，具备苗头价值'
    elif profiles and events and strategic and score >= 60:
        accepted = True
        relation = 'potential'
        reason = f'重大事件发生在中国利益暴露区域，并涉及战略资产/供应链；{ctx}'

    classification_conf = 72 if accepted and (anchors or entities) and events else (55 if accepted else 70)
    decision = Decision(relation, reason, entities, classification_conf, 'social-rules-v2')
    audit = {
        'accepted': accepted,
        'relevance_score': score,
        'matched_anchors': anchors[:12],
        'matched_events': events[:12],
        'matched_strategic': strategic[:12],
        'noise_terms': noise[:12],
        'profiles': profiles[:8],
        'entities': entities[:12],
        'severity': sev,
        'reason': reason,
    }
    return decision, audit

GDELT_QUERIES = [
    '(China OR Chinese OR Beijing OR PRC OR "People\'s Republic of China")',
    '(Huawei OR BYD OR CATL OR COSCO OR ByteDance OR TikTok OR SMIC OR "China Railway" OR CNOOC OR Sinopec OR Zijin)',
    '(Taiwan OR "South China Sea" OR "Belt and Road" OR "Chinese investment" OR "Chinese company")',
]


@dataclass
class RawItem:
    title: str
    url: str
    source_name: str
    source_kind: str = 'news'
    source_country: str | None = None
    language: str | None = None
    published_at: datetime | None = None
    snippet: str | None = None


@dataclass
class Decision:
    relation: str
    reason: str
    entities: list[str]
    confidence: int
    classifier: str


def log(msg: str):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC] {msg}", flush=True)


def compact_text(text, limit=1200) -> str:
    if not text:
        return ''
    return re.sub(r'\s+', ' ', str(text)).strip()[:limit]


def canonicalize_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING]
        path = re.sub(r'/{2,}', '/', parts.path or '/')
        return urlunsplit((parts.scheme.lower() or 'https', parts.netloc.lower(), path.rstrip('/') or '/', urlencode(query), ''))
    except Exception:
        return url.strip()


def parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = dtparser.parse(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def load_yaml(path: Path):
    try:
        with path.open('r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def load_json_list(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def load_interests() -> dict:
    interests = load_yaml(INTERESTS_FILE)
    extra = load_yaml(GEO_ALIASES_FILE).get('aliases', {})
    profiles = interests.setdefault('profiles', {})
    for key, aliases in extra.items():
        if key not in profiles:
            continue
        current = [str(x) for x in (profiles[key].get('aliases') or [])]
        for alias in aliases or []:
            if str(alias) not in current:
                current.append(str(alias))
        profiles[key]['aliases'] = current
    return interests


def profile_aliases(profile_key: str, profile: dict) -> list[str]:
    return [profile_key] + [str(x) for x in (profile.get('aliases') or [])]


def text_has_alias(text: str, alias: str) -> bool:
    a = alias.strip().lower()
    if len(a) < 2:
        return False
    if re.search(r'[a-z]', a):
        return bool(re.search(r'(?<![a-z0-9])' + re.escape(a) + r'(?![a-z0-9])', text))
    return a in text


def match_interest_profiles(title: str, snippet: str | None, source_country: str | None, interests: dict) -> tuple[list[str], list[str], str]:
    profiles = interests.get('profiles', {})
    text = f"{title} {snippet or ''}".lower()
    matched_profiles, matched_entities = [], []
    if source_country:
        c = source_country.strip().lower()
        for key, p in profiles.items():
            if any(c == a.lower() for a in profile_aliases(key, p)):
                matched_profiles.append(key); break
    for key, p in profiles.items():
        if key not in matched_profiles and any(text_has_alias(text, a) for a in profile_aliases(key, p)):
            matched_profiles.append(key)
        for ent in (p.get('entities') or []):
            ent_s = str(ent).strip()
            if len(ent_s) >= 3 and text_has_alias(text, ent_s) and ent_s not in matched_entities:
                matched_entities.append(ent_s)
                if key not in matched_profiles:
                    matched_profiles.append(key)
    contexts = []
    for key in matched_profiles[:4]:
        p = profiles.get(key, {})
        parts = [f'事件关联地区={key}']
        if p.get('interests'):
            parts.append('中国关联利益=' + ', '.join(map(str, p['interests'][:8])))
        if p.get('entities'):
            parts.append('中国关联实体/项目=' + ', '.join(map(str, p['entities'][:10])))
        contexts.append('; '.join(parts))
    return matched_profiles[:8], matched_entities[:15], ' | '.join(contexts)


def heuristic_decision(item: RawItem, interests: dict, social=False) -> Decision:
    text = f"{item.title} {item.snippet or ''}"
    if DIRECT_TERMS.search(text):
        return Decision('direct', '内容直接出现中国、中国机构/企业/人员、台湾台海或其他直接涉华实体', [], 94 if not social else 62, 'rules')
    if (item.source_country or '').strip() in {'China', 'Hong Kong', 'Taiwan'} and MAJOR_EVENT.search(text) and not SPORT_ENTERTAINMENT.search(text):
        return Decision('direct', '事件发生于中国大陆/香港/台湾，且属于政治、外交、经济、产业、安全或社会层面的重大变化', [], 88 if not social else 58, 'rules')
    profiles, entities, ctx = match_interest_profiles(item.title, item.snippet, item.source_country, interests)
    if entities:
        return Decision('indirect', f'命中中国海外关联企业/项目；{ctx}', entities, 84 if not social else 58, 'rules')
    generic = not profiles or all(x.lower() in {'global','europe','africa','nato','european union'} for x in profiles)
    if profiles and MAJOR_EVENT.search(text) and not SPORT_ENTERTAINMENT.search(text) and not generic:
        return Decision('potential', f'发生在存在中国重要利益暴露的国家/地区，且属于重大政治、经济、产业或安全变化；{ctx}', [], 62 if not social else 45, 'rules')
    return Decision('unrelated', '未发现足够的直接、间接或重大潜在涉华关联', [], 55, 'rules')


def candidate_meta(item: RawItem, interests: dict) -> tuple[bool, dict]:
    profiles, entities, ctx = match_interest_profiles(item.title, item.snippet, item.source_country, interests)
    text = f"{item.title} {item.snippet or ''}"
    if DIRECT_TERMS.search(text) or entities:
        return True, {'profiles': profiles, 'entities': entities, 'context': ctx}
    if (item.source_country or '').strip() in {'China', 'Hong Kong', 'Taiwan'} and MAJOR_EVENT.search(text) and not SPORT_ENTERTAINMENT.search(text):
        return True, {'profiles': profiles, 'entities': entities, 'context': ctx}
    if profiles and MAJOR_EVENT.search(text) and not SPORT_ENTERTAINMENT.search(text):
        return True, {'profiles': profiles, 'entities': entities, 'context': ctx}
    return False, {'profiles': profiles, 'entities': entities, 'context': ctx}


async def robots_allowed(client: httpx.AsyncClient, url: str) -> bool:
    try:
        p = urlparse(url); robots_url = f'{p.scheme}://{p.netloc}/robots.txt'
        r = await client.get(robots_url, timeout=min(FETCH_TIMEOUT, 8))
        if r.status_code >= 400: return True
        rp = RobotFileParser(); rp.set_url(robots_url); rp.parse(r.text.splitlines())
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


async def discover_feed(client: httpx.AsyncClient, homepage: str) -> str | None:
    try:
        r = await client.get(homepage); r.raise_for_status(); soup = BeautifulSoup(r.text, 'lxml')
        for link in soup.find_all('link', href=True):
            typ = (link.get('type') or '').lower(); rel = ' '.join(link.get('rel') or []).lower()
            if 'alternate' in rel and ('rss' in typ or 'atom' in typ or 'xml' in typ):
                return urljoin(str(r.url), link['href'])
        for path in ('/feed','/rss','/rss.xml','/feed.xml','/atom.xml'):
            candidate = urljoin(str(r.url), path)
            try:
                rr = await client.get(candidate, timeout=min(FETCH_TIMEOUT, 10)); head = rr.text[:500].lower(); ctype = rr.headers.get('content-type','').lower()
                if rr.status_code < 400 and ('xml' in ctype or '<rss' in head or '<feed' in head): return candidate
            except Exception: pass
    except Exception: pass
    return None


async def fetch_feed(client: httpx.AsyncClient, source: dict, feed_url: str) -> list[RawItem]:
    if not await robots_allowed(client, feed_url): return []
    r = await client.get(feed_url); r.raise_for_status(); feed = feedparser.parse(r.content); out = []
    for e in feed.entries[:MAX_PER_SOURCE]:
        title = compact_text(html.unescape(getattr(e, 'title', '')), 500); url = getattr(e, 'link', '')
        if not title or not url: continue
        summary = getattr(e, 'summary', None) or getattr(e, 'description', None)
        published = getattr(e, 'published', None) or getattr(e, 'updated', None)
        out.append(RawItem(title, url, source['name'], source.get('kind','news'), source.get('country'), source.get('language'), parse_date(published), compact_text(BeautifulSoup(summary or '', 'lxml').get_text(' '), 1000)))
    return out


async def fetch_homepage(client: httpx.AsyncClient, source: dict, url: str) -> list[RawItem]:
    if not await robots_allowed(client, url): return []
    r = await client.get(url); r.raise_for_status(); soup = BeautifulSoup(r.text, 'lxml'); host = urlparse(str(r.url)).netloc
    seen, candidates = set(), []
    for a in soup.find_all('a', href=True):
        title = compact_text(a.get_text(' ', strip=True), 500)
        if len(title) < 18: continue
        href = urljoin(str(r.url), a['href']); p = urlparse(href)
        if p.scheme not in ('http','https') or p.netloc != host or SKIP_HINTS.search(p.path): continue
        if not ARTICLE_HINTS.search(p.path) and len(title) < 42: continue
        canon = canonicalize_url(href)
        if canon in seen: continue
        seen.add(canon)
        candidates.append(RawItem(title, href, source['name'], source.get('kind','news'), source.get('country'), source.get('language')))
        if len(candidates) >= MAX_PER_SOURCE: break
    return candidates


async def fetch_source(client: httpx.AsyncClient, source: dict) -> list[RawItem]:
    mode = source.get('mode', 'auto')
    if mode == 'disabled': return []
    if source.get('feed'):
        try: return await fetch_feed(client, source, source['feed'])
        except Exception: pass
    if mode in {'auto','feed'}:
        feed_url = await discover_feed(client, source['homepage'])
        if feed_url:
            try: return await fetch_feed(client, source, feed_url)
            except Exception: pass
    return await fetch_homepage(client, source, source.get('listing') or source['homepage'])


async def fetch_gdelt(client: httpx.AsyncClient, manual_query='') -> list[RawItem]:
    if not ENABLE_GDELT: return []
    queries = list(GDELT_QUERIES)
    if manual_query.strip(): queries.insert(0, manual_query.strip())
    out, seen = [], set()
    for query in queries[:6]:
        params = {'query': query, 'mode':'ArtList','maxrecords':100,'format':'json','timespan':GDELT_TIMESPAN,'sort':'DateDesc'}
        try:
            r = await client.get('https://api.gdeltproject.org/api/v2/doc/doc', params=params, timeout=30); r.raise_for_status()
            for a in r.json().get('articles', []):
                title, url = compact_text(a.get('title'), 500), a.get('url')
                if not title or not url: continue
                canon = canonicalize_url(url)
                if canon in seen: continue
                seen.add(canon)
                out.append(RawItem(title, url, a.get('domain') or 'GDELT source', 'news', a.get('sourcecountry'), a.get('language'), parse_date(a.get('seendate'))))
        except Exception as e:
            log(f'GDELT query failed: {type(e).__name__}')
    return out


async def collect_news(sources: list[dict], manual_query='') -> tuple[list[RawItem], list[dict]]:
    headers = {'User-Agent': USER_AGENT, 'Accept':'text/html,application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.8'}
    limits = httpx.Limits(max_connections=MAX_CONNECTIONS, max_keepalive_connections=max(10, MAX_CONNECTIONS//2))
    sem, errors = asyncio.Semaphore(CONCURRENCY), []
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=httpx.Timeout(FETCH_TIMEOUT), limits=limits) as client:
        async def one(src):
            async with sem:
                try: return await fetch_source(client, src)
                except Exception as e:
                    errors.append({'source':src.get('name','?'),'error':f'{type(e).__name__}: {compact_text(e,180)}'}); return []
        batches = await asyncio.gather(*(one(s) for s in sources))
        items = [x for batch in batches for x in batch]
        items.extend(await fetch_gdelt(client, manual_query))
    return items, errors


def extract_json_array(text: str):
    text = text.strip()
    if text.startswith('```'): text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.S)
    start, end = text.find('['), text.rfind(']')
    return json.loads(text[start:end+1] if start >= 0 and end > start else text)


def classify_ai_batch(items: list[RawItem], metadata: list[dict], interests: dict, social=False) -> list[Decision]:
    if not OPENAI_API_KEY: return [heuristic_decision(x, interests, social=social) for x in items]
    payload = []
    for i, (item, meta) in enumerate(zip(items, metadata)):
        payload.append({'id':i,'title':compact_text(item.title,500),'snippet':compact_text(item.snippet,700),'source':item.source_name,'source_country':item.source_country,'source_kind':item.source_kind,'known_china_interest_context':meta.get('context',''),'matched_interest_profiles':meta.get('profiles',[]),'matched_entities':meta.get('entities',[])})
    prompt = f"""你是“全球涉华早期信号”入池分类器。输入可能来自{'社交平台苗头' if social else '新闻/官方/智库'}。只判断是否值得中国相关研究人员关注，不判断有利或不利，也不要因为未经核实而拒绝社交苗头。\n\n分类：\n- direct：直接涉及中国、中国政府/军队/台湾台海、中国企业/人员/资本/项目。\n- indirect：没写China，但明确涉及已知中资项目、企业、人员、关键供应链或海外资产。\n- potential：没有直接中国字样，但发生在中国重要利益暴露区域，且属于政权/外交路线变化、战争/政变/重大抗议、制裁/出口管制、关键矿产/能源/港口/产业链等重大变化。\n- unrelated：普通地方新闻、体育娱乐、一般犯罪、日常政治口水、与中国利益联系过于牵强。\n\n重要：本系统要“少而精”。potential 必须同时满足“重大事件 + 明确中国利益暴露”，不能仅因为某国家与中国有经贸关系就收录。社交平台信息可以低可信但高价值，可信度低不等于 unrelated。\n只输出JSON数组，每项：id, relation, reason(一句中文解释中国关联), entities(数组), confidence(0-100，表示你对涉华分类判断的把握，不代表事实真假)。\n\n待判断：\n""" + json.dumps(payload, ensure_ascii=False)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY); resp = client.responses.create(model=OPENAI_MODEL, input=prompt); rows = extract_json_array(resp.output_text); by_id = {int(r.get('id')):r for r in rows if 'id' in r}; out=[]
        for i, item in enumerate(items):
            r = by_id.get(i)
            if not r: out.append(heuristic_decision(item, interests, social=social)); continue
            relation = r.get('relation','unrelated')
            if relation not in {'direct','indirect','potential','unrelated'}: relation='unrelated'
            try: conf=max(0,min(100,int(r.get('confidence',70))))
            except Exception: conf=70
            out.append(Decision(relation, compact_text(r.get('reason'),800), [compact_text(x,120) for x in (r.get('entities') or [])[:12]], conf, OPENAI_MODEL))
        return out
    except Exception as e:
        log(f'AI classification failed, fallback rules: {type(e).__name__}: {compact_text(e,180)}')
        return [heuristic_decision(x, interests, social=social) for x in items]


def classify_items(items: list[RawItem], interests: dict, social=False) -> list[tuple[RawItem, Decision]]:
    direct, candidates, metas = [], [], []
    for item in items:
        text = f"{item.title} {item.snippet or ''}"
        if DIRECT_TERMS.search(text):
            direct.append((item, heuristic_decision(item, interests, social=social))); continue
        ok, meta = candidate_meta(item, interests)
        if not ok: continue
        if OPENAI_API_KEY: candidates.append(item); metas.append(meta)
        else:
            d = heuristic_decision(item, interests, social=social)
            if d.relation != 'unrelated': direct.append((item,d))
    if OPENAI_API_KEY:
        for start in range(0, len(candidates), CLASSIFIER_BATCH):
            batch, meta = candidates[start:start+CLASSIFIER_BATCH], metas[start:start+CLASSIFIER_BATCH]
            for item, d in zip(batch, classify_ai_batch(batch, meta, interests, social=social)):
                if d.relation != 'unrelated': direct.append((item,d))
    return direct


def article_record(item: RawItem, decision: Decision, collected_at: datetime) -> dict:
    canon = canonicalize_url(item.url); key = hashlib.sha256(canon.encode('utf-8',errors='ignore')).hexdigest()[:20]
    sev, _ = severity(f"{item.title} {item.snippet or ''}")
    return {'id':key,'title':item.title,'snippet':item.snippet or '','source':item.source_name,'source_kind':item.source_kind,'country':item.source_country or '','language':item.language or '','published_at':iso(item.published_at),'collected_at':iso(collected_at),'url':item.url,'canonical_url':canon,'relation':decision.relation,'reason':decision.reason,'entities':decision.entities,'confidence':decision.confidence,'classifier':decision.classifier,'severity':sev}


def signal_record(raw: dict, decision: Decision, collected_at: datetime) -> dict:
    text = compact_text(raw.get('text'), 1800); rid = raw.get('id') or hashlib.sha256(f"{raw.get('platform')}:{raw.get('url')}:{text}".encode('utf-8')).hexdigest()[:20]
    sev, sev_score = severity(text)
    relation_weight = {'direct':100,'indirect':82,'potential':60}.get(decision.relation,0)
    freshness = 100
    dt = parse_date(raw.get('published_at'))
    if dt: freshness = max(0, min(100, round(100 - max(0,(collected_at-dt).total_seconds()/3600)*4)))
    priority = round(relation_weight*0.5 + sev_score*0.35 + freshness*0.15)
    confidence = int(raw.get('confidence') or (65 if raw.get('verified') else (55 if raw.get('media_count') else 35)))
    return {
        'id':rid,'platform':raw.get('platform') or 'social','author':raw.get('author') or 'unknown','author_name':raw.get('author_name') or '',
        'text':text,'title':text[:360],'url':raw.get('url') or '','published_at':iso(dt) if dt else raw.get('published_at'),'collected_at':iso(collected_at),
        'query':raw.get('query') or '','engagement':raw.get('engagement') or {},'media_count':int(raw.get('media_count') or 0),'collector':raw.get('collector') or 'unknown',
        'source':f"{raw.get('platform') or 'social'} · {raw.get('author') or 'unknown'}",'source_kind':'social','country':raw.get('country') or '',
        'relation':decision.relation,'reason':decision.reason,'entities':decision.entities,'confidence':max(5,min(95,confidence)),'priority_score':max(0,min(100,priority)),'severity':sev,'classifier':decision.classifier,
    }


def merge_by_id(existing: list[dict], new: list[dict], retention_days: int, limit: int) -> list[dict]:
    by_id = {str(r.get('id') or r.get('canonical_url') or r.get('url')):r for r in existing if (r.get('id') or r.get('url'))}
    for r in new: by_id[str(r.get('id') or r.get('canonical_url') or r.get('url'))] = r
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days); kept=[]
    for r in by_id.values():
        dt = parse_date(r.get('published_at') or r.get('collected_at'))
        if dt is None or dt >= cutoff: kept.append(r)
    kept.sort(key=lambda r:r.get('published_at') or r.get('collected_at') or '', reverse=True)
    return kept[:limit]


def external_signal_rows() -> list[dict]:
    rows = load_json_list(EXTERNAL_SIGNALS_FILE)
    # 外部采集工作流已经做基础标准化；这里仅过滤空数据。
    return [r for r in rows if r.get('text') and r.get('platform')]


def raw_social_to_items(rows: list[dict]) -> list[tuple[RawItem, dict]]:
    out=[]
    for r in rows:
        text=compact_text(r.get('text'),1800)
        if not text: continue
        out.append((RawItem(text[:500], r.get('url') or f"https://example.invalid/{r.get('id','signal')}", f"{r.get('platform','social')} · {r.get('author','unknown')}", 'social', r.get('country'), r.get('language'), parse_date(r.get('published_at')), text), r))
    return out


def self_test():
    interests={'profiles':{'Hungary':{'aliases':['Hungary','Hungarian','Budapest'],'interests':['中国制造业投资','欧盟对华政策'],'entities':['BYD Szeged','CATL Debrecen']}}}
    tests=[
        (RawItem('EU announces new restrictions on Chinese chip firms','https://x/a','X',source_country='European Union'),'direct'),
        (RawItem("Hungary's new government vows to restore relations with Brussels after election",'https://x/b','Global',source_country='Hungary'),'potential'),
        (RawItem('Hungary wins dramatic football match in extra time','https://x/c','Global',source_country='Hungary'),'unrelated'),
        (RawItem('Budapest reviews subsidies for CATL Debrecen battery plant','https://x/d','Global',source_country='Hungary'),'direct'),
    ]
    for item, expected in tests:
        got=heuristic_decision(item,interests).relation
        if got!=expected: raise AssertionError(f'classifier self-test: expected {expected}, got {got}: {item.title}')
    sim_same=story_similarity('China launches military drills around Taiwan','Chinese military begins new drills around Taiwan')
    sim_diff=story_similarity('China launches military drills around Taiwan','Argentina central bank cuts interest rates')
    if not (sim_same > sim_diff and sim_same >= .45): raise AssertionError(f'cluster self-test failed: same={sim_same} diff={sim_diff}')
    print('self-test: classifier 4/4 + clustering passed')


async def run(mode='all', manual_query='', rebuild_only=False):
    started=datetime.now(timezone.utc); DATA_DIR.mkdir(parents=True,exist_ok=True)
    sources=load_yaml(SOURCES_FILE).get('sources',[]); interests=load_interests(); tiers=load_yaml(TIERS_FILE); social_cfg=load_yaml(SOCIAL_FILE)
    existing_articles=load_json_list(ARTICLES_FILE); existing_signals=load_json_list(SIGNALS_FILE)
    errors=[]; platform_status=[]; items_seen=items_new=items_relevant=signals_seen=signals_relevant=0

    if mode in {'all','news'} and not rebuild_only:
        log(f'news crawl: {len(sources)} sources; MAX_PER_SOURCE={MAX_PER_SOURCE}; AI={"on" if OPENAI_API_KEY else "off"}')
        seen_urls={r.get('canonical_url') or canonicalize_url(r.get('url','')) for r in existing_articles if r.get('url')}
        items, errors = await collect_news(sources, manual_query)
        items_seen=len(items); unique={}
        for item in items:
            canon=canonicalize_url(item.url)
            if not canon or canon in seen_urls: continue
            unique.setdefault(canon,item)
        new_items=list(unique.values()); items_new=len(new_items)
        relevant=classify_items(new_items,interests,social=False); now=datetime.now(timezone.utc)
        new_records=[article_record(i,d,now) for i,d in relevant]; items_relevant=len(new_records)
        existing_articles=merge_by_id(existing_articles,new_records,RETENTION_DAYS,MAX_STORED)
        ARTICLES_FILE.write_text(json.dumps(existing_articles,ensure_ascii=False,indent=2),encoding='utf-8')

    if mode in {'all','social'} and not rebuild_only:
        log('social signal crawl: X / Telegram / RSS bridges (connectors are optional)')
        raw_social, platform_status = await collect_social(social_cfg, manual_query)
        raw_social.extend(external_signal_rows())
        signals_seen=len(raw_social)
        existing_signal_ids = {r.get('id') for r in existing_signals if r.get('id')}
        raw_social = [r for r in raw_social if not r.get('id') or r.get('id') not in existing_signal_ids]
        pairs=raw_social_to_items(raw_social)
        now=datetime.now(timezone.utc); new_signals=[]
        for item,raw in pairs:
            d,audit=social_relevance_decision(item,interests,raw)
            if audit['accepted']:
                rec=signal_record(raw,d,now)
                rec['relevance_score']=audit['relevance_score']
                rec['matched_anchors']=audit['matched_anchors']
                rec['matched_events']=audit['matched_events']
                new_signals.append(rec)
        signals_relevant=len(new_signals)
        existing_signals=merge_by_id(existing_signals,new_signals,SIGNAL_RETENTION_DAYS,MAX_STORED_SIGNALS)
        SIGNALS_FILE.write_text(json.dumps(existing_signals,ensure_ascii=False,indent=2),encoding='utf-8')

    # MediaCrawler 外部结果在 rebuild 时重新按“社交苗头规则 v2”评估。
    # 旧版只要命中“中国/华为/比亚迪”就很容易入池；这里先清除旧 MediaCrawler 结果再重建，避免历史噪声残留。
    external_rows = external_signal_rows()
    social_review = []
    if external_rows:
        existing_signals = [r for r in existing_signals if r.get('collector') != 'MediaCrawler-external']
        pairs = raw_social_to_items(external_rows)
        now = datetime.now(timezone.utc)
        ext_signals = []
        for item, raw in pairs:
            d, audit = social_relevance_decision(item, interests, raw)
            review_row = {
                'id': raw.get('id'), 'platform': raw.get('platform'), 'author': raw.get('author'),
                'published_at': raw.get('published_at'), 'collected_at': raw.get('collected_at'),
                'query': raw.get('query'), 'text': raw.get('text'), 'url': raw.get('url'),
                'accepted': audit['accepted'], 'relation': d.relation, 'relevance_score': audit['relevance_score'],
                'severity': audit['severity'], 'reason': audit['reason'],
                'matched_anchors': audit['matched_anchors'], 'matched_events': audit['matched_events'],
                'matched_strategic': audit['matched_strategic'], 'noise_terms': audit['noise_terms'],
                'profiles': audit['profiles'], 'entities': audit['entities'],
            }
            social_review.append(review_row)
            if audit['accepted']:
                rec = signal_record(raw, d, now)
                rec['relevance_score'] = audit['relevance_score']
                rec['matched_anchors'] = audit['matched_anchors']
                rec['matched_events'] = audit['matched_events']
                ext_signals.append(rec)
        existing_signals = merge_by_id(existing_signals, ext_signals, SIGNAL_RETENTION_DAYS, MAX_STORED_SIGNALS)
        SIGNALS_FILE.write_text(json.dumps(existing_signals, ensure_ascii=False, indent=2), encoding='utf-8')
        signals_seen += len(external_rows)
        signals_relevant += len(ext_signals)
    SOCIAL_REVIEW_FILE.write_text(json.dumps(social_review, ensure_ascii=False, indent=2), encoding='utf-8')
    mc_status = load_json_list(DATA_DIR / 'mediacrawler_status.json')
    if mc_status:
        platform_status.extend(mc_status)

    # 任何模式运行后都重建事件与站点，因此手动 social-only 也能马上看到网站变化。
    events=build_events(existing_articles,existing_signals,tiers)
    translation_status = enrich_events_bilingual(events, TRANSLATION_CACHE_FILE, title_limit=300, reason_limit=80)
    latest=select_latest(events,hours=24,limit=30)
    EVENTS_FILE.write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
    LATEST_FILE.write_text(json.dumps(latest,ensure_ascii=False,indent=2),encoding='utf-8')
    status={
        'version':'0.4.3','mode':mode,'manual_query':manual_query,'started_at':iso(started),'finished_at':iso(datetime.now(timezone.utc)),
        'sources_scanned':len(sources) if mode in {'all','news'} and not rebuild_only else 0,'items_seen':items_seen,'items_new':items_new,'items_relevant':items_relevant,
        'signals_seen':signals_seen,'signals_relevant':signals_relevant,'social_review_total':len(social_review),'social_review_accepted':sum(1 for x in social_review if x.get('accepted')),'stored_articles':len(existing_articles),'stored_signals':len(existing_signals),'events':len(events),'latest_events':len(latest),
        'errors':len(errors),'error_samples':errors[:25],'platform_status':platform_status,'ai_enabled':bool(OPENAI_API_KEY),'classifier':OPENAI_MODEL if OPENAI_API_KEY else 'rules',
        'translation': translation_status,
        'retention_days':RETENTION_DAYS,'signal_retention_days':SIGNAL_RETENTION_DAYS,'max_per_source':MAX_PER_SOURCE,
    }
    STATUS_FILE.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    build_site(ROOT,existing_articles,existing_signals,events,latest,status,sources,platform_status,tiers,social_review)
    log(f'done: articles={len(existing_articles)} signals={len(existing_signals)} events={len(events)} latest={len(latest)}')
    return status


def main():
    ap=argparse.ArgumentParser(description='Global China Early Signals cloud runner')
    ap.add_argument('--self-test',action='store_true'); ap.add_argument('--mode',choices=['all','news','social','rebuild'],default=os.getenv('RUN_MODE','all')); ap.add_argument('--query',default=os.getenv('MANUAL_QUERY',''))
    args=ap.parse_args()
    if args.self_test: self_test(); return
    self_test(); status=asyncio.run(run(mode=args.mode,manual_query=args.query,rebuild_only=args.mode=='rebuild')); print(json.dumps(status,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
