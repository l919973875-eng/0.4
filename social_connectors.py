from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import httpx


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(text, limit=1600) -> str:
    return re.sub(r'\s+', ' ', html.unescape(str(text or ''))).strip()[:limit]


def _sid(platform: str, key: str) -> str:
    return hashlib.sha256(f'{platform}:{key}'.encode('utf-8', errors='ignore')).hexdigest()[:20]


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        from dateutil import parser as dtparser
        dt = dtparser.parse(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


async def collect_x_official(queries: list[str], max_per_query: int) -> tuple[list[dict], dict]:
    token = os.getenv('X_BEARER_TOKEN', '').strip()
    if not token:
        return [], {'platform': 'x', 'status': 'disabled', 'detail': '未配置 X_BEARER_TOKEN'}
    rows: list[dict] = []
    errors: list[str] = []
    headers = {'Authorization': f'Bearer {token}', 'User-Agent': 'GlobalChinaSignals/0.3'}
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        for q in queries:
            query = q.strip()
            if not query:
                continue
            if '-is:retweet' not in query:
                query = f'({query}) -is:retweet'
            params = {
                'query': query,
                'max_results': max(10, min(100, max_per_query)),
                'tweet.fields': 'created_at,public_metrics,author_id,lang,attachments',
                'expansions': 'author_id',
                'user.fields': 'username,name,verified',
            }
            try:
                r = await client.get('https://api.x.com/2/tweets/search/recent', params=params)
                r.raise_for_status()
                payload = r.json()
                users = {u.get('id'): u for u in payload.get('includes', {}).get('users', [])}
                for t in (payload.get('data') or [])[:max_per_query]:
                    user = users.get(t.get('author_id'), {})
                    username = user.get('username') or t.get('author_id') or 'unknown'
                    tid = str(t.get('id') or '')
                    if not tid:
                        continue
                    rows.append({
                        'id': _sid('x', tid),
                        'platform': 'x',
                        'author': username,
                        'author_name': user.get('name') or username,
                        'verified': bool(user.get('verified')),
                        'text': _clean(t.get('text')),
                        'url': f'https://x.com/{username}/status/{tid}',
                        'published_at': t.get('created_at'),
                        'collected_at': _now_iso(),
                        'query': q,
                        'engagement': t.get('public_metrics') or {},
                        'media_count': len((t.get('attachments') or {}).get('media_keys') or []),
                        'collector': 'x_api_v2',
                    })
            except Exception as e:
                errors.append(f'{type(e).__name__}: {_clean(e, 180)}')
    status = {'platform': 'x', 'status': 'ok' if rows else ('error' if errors else 'empty'), 'items': len(rows)}
    if errors:
        status['detail'] = errors[:3]
    return rows, status


async def collect_x_twikit(queries: list[str], max_per_query: int) -> tuple[list[dict], dict]:
    cookie_json = os.getenv('X_COOKIES_JSON', '').strip()
    if not cookie_json:
        return [], {'platform': 'x', 'status': 'disabled', 'detail': '未配置 X_COOKIES_JSON（Twikit实验模式）'}
    try:
        from twikit import Client
    except Exception as e:
        return [], {'platform': 'x', 'status': 'error', 'detail': f'Twikit 未安装或导入失败: {type(e).__name__}'}

    rows: list[dict] = []
    errors: list[str] = []
    try:
        cookies = json.loads(cookie_json)
        client = Client('en-US')
        # Twikit 的 set_cookies 接受键值字典；也兼容常见浏览器导出数组。
        if isinstance(cookies, list):
            cookies = {str(x.get('name')): str(x.get('value')) for x in cookies if x.get('name')}
        if not isinstance(cookies, dict) or not cookies:
            raise ValueError('X_COOKIES_JSON 需要是 Cookie JSON 字典或浏览器 Cookie 数组')
        client.set_cookies(cookies)
        for q in queries:
            try:
                result = await client.search_tweet(q, 'Latest', count=max(1, min(20, max_per_query)))
                count = 0
                for tweet in result:
                    if count >= max_per_query:
                        break
                    count += 1
                    user = getattr(tweet, 'user', None)
                    username = getattr(user, 'screen_name', None) or getattr(user, 'name', None) or 'unknown'
                    tid = str(getattr(tweet, 'id', '') or '')
                    text = getattr(tweet, 'full_text', None) or getattr(tweet, 'text', '')
                    created = getattr(tweet, 'created_at', None)
                    metrics = {
                        'like_count': getattr(tweet, 'favorite_count', 0) or 0,
                        'retweet_count': getattr(tweet, 'retweet_count', 0) or 0,
                        'reply_count': getattr(tweet, 'reply_count', 0) or 0,
                        'quote_count': getattr(tweet, 'quote_count', 0) or 0,
                    }
                    rows.append({
                        'id': _sid('x', tid or f'{username}:{text[:80]}'),
                        'platform': 'x', 'author': username, 'text': _clean(text),
                        'url': f'https://x.com/{username}/status/{tid}' if tid else '',
                        'published_at': str(created) if created else None,
                        'collected_at': _now_iso(), 'query': q, 'engagement': metrics,
                        'media_count': len(getattr(tweet, 'media', None) or []), 'collector': 'twikit',
                    })
                await asyncio.sleep(1.2)
            except Exception as e:
                errors.append(f'{type(e).__name__}: {_clean(e, 180)}')
    except Exception as e:
        errors.append(f'{type(e).__name__}: {_clean(e, 180)}')
    status = {'platform': 'x', 'status': 'ok' if rows else 'error', 'items': len(rows), 'collector': 'twikit'}
    if errors:
        status['detail'] = errors[:3]
    return rows, status


async def collect_x(queries: list[str], max_per_query: int) -> tuple[list[dict], dict]:
    # 正规 API 优先；没有 Bearer Token 时才尝试免费的 Twikit 实验模式。
    if os.getenv('X_BEARER_TOKEN', '').strip():
        return await collect_x_official(queries, max_per_query)
    return await collect_x_twikit(queries, max_per_query)


async def collect_telegram(channels: list[str], lookback_hours: int = 36, max_per_channel: int = 80) -> tuple[list[dict], dict]:
    api_id = os.getenv('TG_API_ID', '').strip()
    api_hash = os.getenv('TG_API_HASH', '').strip()
    session_string = os.getenv('TG_SESSION_STRING', '').strip()
    if not (api_id and api_hash and session_string and channels):
        detail = '需要 TG_API_ID / TG_API_HASH / TG_SESSION_STRING，且 config/social_sources.yaml 填写频道'
        return [], {'platform': 'telegram', 'status': 'disabled', 'detail': detail}
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except Exception as e:
        return [], {'platform': 'telegram', 'status': 'error', 'detail': f'Telethon 导入失败: {type(e).__name__}'}

    rows: list[dict] = []
    errors: list[str] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    client = TelegramClient(StringSession(session_string), int(api_id), api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError('TG_SESSION_STRING 已失效或未授权')
        for channel in channels:
            ch = str(channel).strip().lstrip('@')
            if not ch:
                continue
            try:
                count = 0
                async for msg in client.iter_messages(ch, limit=max_per_channel):
                    dt = msg.date.astimezone(timezone.utc) if msg.date else None
                    if dt and dt < cutoff:
                        break
                    text = _clean(msg.message)
                    if not text:
                        continue
                    count += 1
                    rows.append({
                        'id': _sid('telegram', f'{ch}:{msg.id}'),
                        'platform': 'telegram', 'author': ch, 'text': text,
                        'url': f'https://t.me/{ch}/{msg.id}',
                        'published_at': dt.isoformat() if dt else None,
                        'collected_at': _now_iso(), 'query': f'channel:{ch}',
                        'engagement': {'views': int(msg.views or 0), 'forwards': int(msg.forwards or 0)},
                        'media_count': 1 if msg.media else 0, 'collector': 'telethon',
                    })
                    if count >= max_per_channel:
                        break
            except Exception as e:
                errors.append(f'{ch}: {type(e).__name__}: {_clean(e, 160)}')
    except Exception as e:
        errors.append(f'{type(e).__name__}: {_clean(e, 180)}')
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    status = {'platform': 'telegram', 'status': 'ok' if rows else ('error' if errors else 'empty'), 'items': len(rows)}
    if errors:
        status['detail'] = errors[:5]
    return rows, status


async def collect_rss_social(feeds: list[dict], max_per_feed: int = 30) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    statuses: list[dict] = []
    if not feeds:
        return rows, statuses
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={'User-Agent': 'GlobalChinaSignals/0.3'}) as client:
        for feed in feeds:
            url = str(feed.get('url') or '').strip()
            platform = str(feed.get('platform') or 'social-rss').strip().lower()
            name = str(feed.get('name') or platform)
            if not url:
                continue
            try:
                r = await client.get(url)
                r.raise_for_status()
                parsed = feedparser.parse(r.content)
                count = 0
                for entry in parsed.entries[:max_per_feed]:
                    title = _clean(getattr(entry, 'title', ''))
                    summary = _clean(getattr(entry, 'summary', '') or getattr(entry, 'description', ''))
                    text = _clean(f'{title} {summary}')
                    link = getattr(entry, 'link', '') or ''
                    if not text:
                        continue
                    count += 1
                    key = link or f'{name}:{title}'
                    rows.append({
                        'id': _sid(platform, key), 'platform': platform, 'author': name,
                        'text': text, 'url': link,
                        'published_at': getattr(entry, 'published', None) or getattr(entry, 'updated', None),
                        'collected_at': _now_iso(), 'query': f'rss:{name}', 'engagement': {}, 'media_count': 0,
                        'collector': 'rss_bridge',
                    })
                statuses.append({'platform': platform, 'source': name, 'status': 'ok', 'items': count})
            except Exception as e:
                statuses.append({'platform': platform, 'source': name, 'status': 'error', 'detail': f'{type(e).__name__}: {_clean(e, 180)}'})
    return rows, statuses


async def collect_social(config: dict, manual_query: str = '') -> tuple[list[dict], list[dict]]:
    max_per_query = int(config.get('max_per_query') or 20)
    queries = [str(x).strip() for x in (config.get('queries') or []) if str(x).strip()]
    if manual_query.strip():
        # 手动关键词放第一位，并限制在一次运行内；不写回配置。
        queries.insert(0, manual_query.strip())
    # 去重并防止一次手动运行无限请求。
    queries = list(dict.fromkeys(queries))[:12]
    channels = [str(x).strip() for x in (config.get('telegram_channels') or []) if str(x).strip()]

    rows: list[dict] = []
    statuses: list[dict] = []
    x_rows, x_status = await collect_x(queries, max_per_query)
    rows.extend(x_rows); statuses.append(x_status)
    tg_rows, tg_status = await collect_telegram(channels)
    rows.extend(tg_rows); statuses.append(tg_status)
    rss_rows, rss_statuses = await collect_rss_social(config.get('rss_feeds') or [])
    rows.extend(rss_rows); statuses.extend(rss_statuses)

    # 当前轮次 URL/ID 去重。
    unique = {}
    for row in rows:
        unique[row.get('id') or _sid(row.get('platform', 'social'), row.get('url') or row.get('text', ''))] = row
    return list(unique.values()), statuses
