from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

CJK_RE = re.compile(r'[\u3400-\u9fff]')
TRANSLATE_URL = 'https://translate.googleapis.com/translate_a/single'


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ''))


def _load_cache(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _cache_key(text: str, target: str) -> str:
    return hashlib.sha256((target + '\0' + text).encode('utf-8', errors='ignore')).hexdigest()


def _google_translate(text: str, target: str, timeout: float = 5.5) -> str:
    if not text.strip():
        return ''
    params = {'client': 'gtx', 'sl': 'auto', 'tl': target, 'dt': 't', 'q': text[:1800]}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={'User-Agent': 'Mozilla/5.0'}) as client:
        r = client.get(TRANSLATE_URL, params=params)
        r.raise_for_status()
        data = r.json()
    parts = []
    if isinstance(data, list) and data and isinstance(data[0], list):
        for seg in data[0]:
            if isinstance(seg, list) and seg and isinstance(seg[0], str):
                parts.append(seg[0])
    return ''.join(parts).strip()


def _translate_one(text: str, target: str, cache: dict) -> tuple[str, str, str]:
    key = _cache_key(text, target)
    if key in cache:
        return key, cache[key], 'cache'
    try:
        translated = _google_translate(text, target)
        if translated:
            return key, translated, 'google'
    except Exception:
        pass
    return key, '', 'failed'


def reason_en_fallback(event: dict) -> str:
    relation = ''
    for ev in event.get('evidence') or []:
        if ev.get('relation'):
            relation = ev.get('relation')
            break
    entities = ', '.join((event.get('entities') or [])[:4])
    countries = ', '.join((event.get('countries') or [])[:3])
    if relation == 'direct':
        base = 'Direct China-related signal involving China, Chinese entities, personnel, Taiwan, or other explicit China-linked subjects.'
    elif relation == 'indirect':
        base = 'Indirect China-related signal linked to a known Chinese overseas company, project, asset, or supply-chain exposure.'
    else:
        base = 'Potential China-related signal: a material political, economic, industrial, or security change in an area with identified Chinese interests.'
    extra = []
    if countries:
        extra.append(f'Area: {countries}.')
    if entities:
        extra.append(f'Linked entities/projects: {entities}.')
    return ' '.join([base] + extra)




def _openai_translate_jobs(jobs: dict[tuple[int, str], tuple[str, str]]) -> dict[tuple[int, str], str]:
    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    if not api_key or not jobs:
        return {}
    model = os.getenv('OPENAI_MODEL', 'gpt-5-mini').strip() or 'gpt-5-mini'
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
    except Exception:
        return {}
    out: dict[tuple[int, str], str] = {}
    slots = list(jobs.items())
    for start in range(0, len(slots), 25):
        batch = slots[start:start + 25]
        payload = []
        for (idx, field), (text, target) in batch:
            payload.append({'key': f'{idx}:{field}', 'target': target, 'text': text[:1800]})
        prompt = (
            'Translate each item faithfully for a bilingual OSINT dashboard. Preserve names, numbers, organizations and uncertainty. '
            'Do not add facts or commentary. Return ONLY a JSON array of objects with key and translation. '
            'Target zh-CN means concise Simplified Chinese; target en means concise natural English.\n'
            + json.dumps(payload, ensure_ascii=False)
        )
        try:
            resp = client.responses.create(model=model, input=prompt)
            text = (resp.output_text or '').strip()
            if text.startswith('```'):
                text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.S)
            arr = json.loads(text[text.find('['):text.rfind(']') + 1])
            by_key = {str(x.get('key')): str(x.get('translation') or '').strip() for x in arr if isinstance(x, dict)}
            for (idx, field), _ in batch:
                value = by_key.get(f'{idx}:{field}', '')
                if value:
                    out[(idx, field)] = value
        except Exception:
            continue
    return out

def enrich_events_bilingual(events: list[dict], cache_path: Path, title_limit: int = 300, reason_limit: int = 80) -> dict:
    """Best-effort bilingual enrichment.

    The site never depends on translation success. Original text remains the source of truth.
    Uses a persistent cache so only newly observed event text is translated on later runs.
    """
    cache = _load_cache(cache_path)
    enabled = os.getenv('ENABLE_BILINGUAL_TRANSLATION', 'true').lower() in {'1', 'true', 'yes', 'on'}
    title_limit = max(0, min(len(events), int(os.getenv('BILINGUAL_TITLE_LIMIT', str(title_limit)))))
    reason_limit = max(0, min(title_limit, int(os.getenv('BILINGUAL_REASON_LIMIT', str(reason_limit)))))

    jobs: dict[tuple[int, str], tuple[str, str]] = {}
    for i, e in enumerate(events[:title_limit]):
        title = (e.get('title') or '').strip()
        reason = (e.get('reason') or '').strip()
        e['title_original'] = title
        e['reason_original'] = reason
        lang = str(e.get('language') or '').lower()
        chinese_title = lang.startswith('zh') or (not lang and has_cjk(title))
        if chinese_title:
            e['title_zh'] = title
            e.setdefault('title_en', '')
            if title:
                jobs[(i, 'title_en')] = (title, 'en')
        else:
            e['title_en'] = title
            e.setdefault('title_zh', '')
            if title:
                jobs[(i, 'title_zh')] = (title, 'zh-CN')
        if reason:
            e['reason_zh'] = reason
            e.setdefault('reason_en', '')
            if i < reason_limit:
                jobs[(i, 'reason_en')] = (reason, 'en')
        else:
            e['reason_zh'] = ''
            e['reason_en'] = ''

    translated = cached = failed = 0
    pending: dict[tuple[int, str], tuple[str, str]] = {}
    for slot, (text, target) in jobs.items():
        key = _cache_key(text, target)
        if key in cache:
            events[slot[0]][slot[1]] = cache[key]
            cached += 1
        else:
            pending[slot] = (text, target)

    provider_parts = []
    if enabled and pending and os.getenv('OPENAI_API_KEY', '').strip():
        ai_values = _openai_translate_jobs(pending)
        if ai_values:
            provider_parts.append('OpenAI')
        for slot, value in ai_values.items():
            text, target = pending.pop(slot)
            events[slot[0]][slot[1]] = value
            cache[_cache_key(text, target)] = value
            translated += 1

    if enabled and pending:
        workers = max(1, min(8, int(os.getenv('TRANSLATION_WORKERS', '6'))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {pool.submit(_translate_one, text, target, cache): slot for slot, (text, target) in pending.items()}
            for fut in as_completed(future_map):
                slot = future_map[fut]
                try:
                    key, value, source = fut.result()
                except Exception:
                    key, value, source = '', '', 'failed'
                if value:
                    events[slot[0]][slot[1]] = value
                    if key:
                        cache[key] = value
                    translated += 1
                    if 'free-fallback' not in provider_parts:
                        provider_parts.append('free-fallback')
                else:
                    failed += 1

    # 无论在线翻译是否可用，都保证重点卡片至少有中英双语的“关联说明”。
    for i, e in enumerate(events[:title_limit]):
        if not e.get('reason_en'):
            e['reason_en'] = reason_en_fallback(e)
        if not e.get('title_zh'):
            e['title_zh'] = e.get('title_original') or e.get('title') or ''
        if not e.get('title_en'):
            e['title_en'] = e.get('title_original') or e.get('title') or ''

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
    return {
        'enabled': enabled,
        'title_limit': title_limit,
        'reason_limit': reason_limit,
        'translated': translated,
        'cache_hits': cached,
        'failed': failed,
        'provider': ' + '.join(provider_parts) if provider_parts else 'deterministic fallback',
    }
