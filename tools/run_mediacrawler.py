from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def replace_assignment(text: str, name: str, value_repr: str) -> str:
    multi = re.compile(rf'(?ms)^{re.escape(name)}\s*=\s*\(.*?^\s*\)\s*(?:#.*)?$')
    if multi.search(text):
        return multi.sub(f'{name} = {value_repr}', text, count=1)
    pat = re.compile(rf'(?m)^{re.escape(name)}\s*=.*$')
    if pat.search(text):
        return pat.sub(f'{name} = {value_repr}', text, count=1)
    return text + f'\n{name} = {value_repr}\n'


def patch_config(repo: Path, platform: str, cookie: str, keywords: str, max_notes: int, cdp: bool = False):
    path = repo / 'config' / 'base_config.py'
    text = path.read_text(encoding='utf-8')
    values = {
        'PLATFORM': repr(platform),
        'KEYWORDS': repr(keywords),
        'LOGIN_TYPE': repr('cookie'),
        'COOKIES': repr(cookie),
        'CRAWLER_TYPE': repr('search'),
        'SAVE_DATA_OPTION': repr('json'),
        'CRAWLER_MAX_NOTES_COUNT': str(max_notes),
        'MAX_CONCURRENCY_NUM': '1',
        'CRAWLER_MAX_SLEEP_SEC': '2',
        'ENABLE_GET_COMMENTS': 'False',
        'ENABLE_GET_SUB_COMMENTS': 'False',
        'ENABLE_GET_MEIDAS': 'False',
        'HEADLESS': 'True',
        'ENABLE_CDP_MODE': 'True' if cdp else 'False',
        'CDP_HEADLESS': 'True',
        'CDP_CONNECT_EXISTING': 'False',
        'AUTO_CLOSE_BROWSER': 'True',
        # MediaCrawler search crawlers read START_PAGE directly. Keep this explicit
        # because upstream main has changed config layout several times.
        'START_PAGE': '1',
        'CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES': '0',
        'DISABLE_SSL_VERIFY': 'False',
        'SAVE_DATA_PATH': repr(''),
        'XHS_INTERNATIONAL': 'False',
        'START_DAY': repr('2024-01-01'),
        'END_DAY': repr('2024-01-01'),
        'ENABLE_IP_PROXY': 'False',
        'SAVE_LOGIN_STATE': 'False',
        'ENABLE_GET_WORDCLOUD': 'False',
    }
    for k, v in values.items():
        text = replace_assignment(text, k, v)
    path.write_text(text, encoding='utf-8')


def content_files(repo: Path, platform: str) -> set[str]:
    base = repo / 'data' / platform
    if not base.exists():
        return set()
    out = set()
    for p in base.rglob('*'):
        if not p.is_file():
            continue
        name = p.name.lower()
        if any(x in name for x in ('comment', 'creator', 'wordcloud')):
            continue
        if p.suffix.lower() in {'.json', '.jsonl', '.csv', '.xlsx'}:
            out.add(str(p.resolve()))
    return out


def count_rough_rows(paths: set[str]) -> int:
    total = 0
    for raw in paths:
        p = Path(raw)
        try:
            if p.suffix.lower() == '.json':
                obj = json.loads(p.read_text(encoding='utf-8'))
                total += len(obj) if isinstance(obj, list) else (1 if isinstance(obj, dict) else 0)
            elif p.suffix.lower() == '.jsonl':
                total += sum(1 for line in p.read_text(encoding='utf-8').splitlines() if line.strip())
            elif p.suffix.lower() == '.csv':
                total += max(0, sum(1 for _ in p.open('r', encoding='utf-8-sig', errors='ignore')) - 1)
            elif p.suffix.lower() == '.xlsx':
                # 仅用于诊断，真正导入器会读取。这里避免强依赖 openpyxl。
                total += 1
        except Exception:
            pass
    return total


def diagnose(output: str, returncode: int, new_rows: int) -> tuple[str, str]:
    low = output.lower()
    patterns = [
        (("attributeerror: module 'config' has no attribute",), 'config_mismatch', 'MediaCrawler 上游配置字段不一致；兼容层未覆盖到该字段'),
        (('captcha appeared', 'verifytype', '滑块', '验证码'), 'captcha', '平台触发验证码/滑块，GitHub 云端无法人工处理'),
        (('没有权限访问', 'code=-104'), 'account_permission', '登录账号当前无搜索接口权限（常见于小红书 -104）'),
        (('ipblock', 'ip block', 'ip_error', 'ip被封', 'ip blocked'), 'ip_block', 'GitHub Runner 云 IP 被平台风控/封锁'),
        (('cookie expired', 'cookie失效', '登录过期', 'login expired'), 'cookie_expired', 'Cookie 可能已失效'),
        (('账号也许被风控', '风控'), 'risk_control', '账号或云端出口触发平台风控'),
        (('retryerror', 'datafetcherror'), 'fetch_error', '平台接口请求失败/重试耗尽'),
        (('targetclosederror',), 'browser_closed', '浏览器进程异常关闭'),
        (('timeout', 'timed out'), 'timeout', '平台响应超时'),
    ]
    for needles, code, detail in patterns:
        if any(n in low for n in needles):
            return code, detail
    if returncode != 0:
        return 'process_failed', f'MediaCrawler 退出码 {returncode}'
    if new_rows <= 0:
        return 'empty', '程序执行完成但没有产生内容数据；通常是 Cookie、账号权限、平台风控或 GitHub 云 IP 限制'
    return 'ok', f'成功产生约 {new_rows} 条内容记录'


def write_status(path: Path | None, payload: dict):
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def scrub(text: str, cookie: str) -> str:
    if cookie:
        text = text.replace(cookie, '[COOKIE_REDACTED]')
    # 避免把常见 cookie 键值完整打印到 artifact。
    text = re.sub(r'(?i)(web_session|a1|sessionid|sid_guard|passport_csrf_token|SUB|SUBP)=([^;\s]+)', r'\1=[REDACTED]', text)
    return text


def write_platform_bootstrap(repo: Path, platform: str) -> Path:
    """Run only the requested MediaCrawler platform with a compatibility shim.

    MediaCrawler's main branch evolves quickly. Some crawler modules access
    config attributes directly, so a transient upstream config mismatch can
    crash before any platform request is sent. We inject conservative defaults
    *before* importing the selected platform module.
    """
    mapping = {
        "xhs": ("media_platform.xhs", "XiaoHongShuCrawler"),
        "dy": ("media_platform.douyin", "DouYinCrawler"),
        "wb": ("media_platform.weibo", "WeiboCrawler"),
    }
    module, cls = mapping[platform]
    path = repo / "_gcn_platform_runner.py"

    # Defaults mirror the small subset of MediaCrawler config used by our
    # low-volume search mode. Existing upstream values are never overwritten.
    compat_defaults = {
        "START_PAGE": 1,
        "START_DAY": "2024-01-01",
        "END_DAY": "2024-01-01",
        "XHS_INTERNATIONAL": False,
        "SORT_TYPE": "popularity_descending",
        "PUBLISH_TIME_TYPE": 0,
        "WEIBO_SEARCH_TYPE": "default",
        "CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES": 0,
        "ENABLE_GET_COMMENTS": False,
        "ENABLE_GET_SUB_COMMENTS": False,
        "ENABLE_GET_MEIDAS": False,
        "ENABLE_GET_WORDCLOUD": False,
        "ENABLE_IP_PROXY": False,
        "IP_PROXY_POOL_COUNT": 2,
        "IP_PROXY_PROVIDER_NAME": "kuaidaili",
        "STATIC_PROXY_URL": "",
        "SAVE_LOGIN_STATE": False,
        "USER_DATA_DIR": "%s_user_data_dir",
        "SAVE_DATA_PATH": "",
        "DISABLE_SSL_VERIFY": False,
        "CDP_DEBUG_PORT": 9222,
        "CUSTOM_BROWSER_PATH": "",
        "BROWSER_LAUNCH_TIMEOUT": 60,
    }

    code = (
        "from __future__ import annotations\n"
        "import asyncio\n"
        "import config\n\n"
        f"_COMPAT_DEFAULTS = {compat_defaults!r}\n"
        "for _name, _value in _COMPAT_DEFAULTS.items():\n"
        "    if not hasattr(config, _name):\n"
        "        setattr(config, _name, _value)\n"
        "print('[compat] START_PAGE=', getattr(config, 'START_PAGE', None), 'CRAWLER_MAX_NOTES_COUNT=', getattr(config, 'CRAWLER_MAX_NOTES_COUNT', None))\n"
        f"from {module} import {cls}\n\n"
        "async def _main():\n"
        f"    crawler = {cls}()\n"
        "    await crawler.start()\n\n"
        "if __name__ == '__main__':\n"
        "    asyncio.run(_main())\n"
    )
    path.write_text(code, encoding="utf-8")
    return path


def summarize_exception(output: str) -> str:
    lines = [x.strip() for x in output.splitlines() if x.strip()]
    for line in reversed(lines):
        if line.startswith(("AttributeError:", "ModuleNotFoundError:", "ImportError:", "RuntimeError:", "ValueError:", "TypeError:", "Exception:")):
            return line[:700]
    return lines[-1][:700] if lines else ""


def run_once(repo: Path, platform: str, cookie: str, keywords: str, max_notes: int, cdp: bool, log_path: Path) -> tuple[int, int, str]:
    before = content_files(repo, platform)
    before_rows = count_rough_rows(before)
    patch_config(repo, platform, cookie, keywords, max_notes, cdp=cdp)
    bootstrap = write_platform_bootstrap(repo, platform)
    cmd = ['uv', 'run', 'python', bootstrap.name]
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, errors='replace')
    combined = scrub((proc.stdout or '') + '\n' + (proc.stderr or ''), cookie)
    after = content_files(repo, platform)
    after_rows = count_rough_rows(after)
    new_rows = max(0, after_rows - before_rows)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(combined[-120000:], encoding='utf-8')
    return proc.returncode, new_rows, combined


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--platform', choices=['xhs', 'dy', 'wb'], required=True)
    ap.add_argument('--cookie-env', required=True)
    ap.add_argument('--keywords', required=True)
    ap.add_argument('--max-notes', type=int, default=20)
    ap.add_argument('--status-file', default='')
    ap.add_argument('--log-file', default='')
    ap.add_argument('--retry-cdp', action='store_true')
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    status_path = Path(args.status_file).resolve() if args.status_file else None
    log_path = Path(args.log_file).resolve() if args.log_file else (Path.cwd() / 'data' / f'mediacrawler_{args.platform}.log')
    cookie = os.getenv(args.cookie_env, '').strip()
    now = datetime.now(timezone.utc).isoformat()

    if not cookie:
        payload = {
            'platform_code': args.platform, 'status': 'not_configured', 'items': 0,
            'reason_code': 'cookie_missing', 'detail': f'未配置 GitHub Secret: {args.cookie_env}',
            'attempts': 0, 'updated_at': now, 'keywords': args.keywords,
        }
        write_status(status_path, payload)
        print(f'[skip] {args.platform}: {args.cookie_env} 未配置')
        return 3

    max_notes = max(5, min(50, args.max_notes))
    print(f'[run] MediaCrawler {args.platform}: cookie=present, max_notes={max_notes}, keywords={args.keywords}')
    attempt1_log = log_path.with_name(log_path.stem + '_attempt1' + log_path.suffix)
    rc, rows, output = run_once(repo, args.platform, cookie, args.keywords, max_notes, False, attempt1_log)
    attempts = 1
    reason_code, detail = diagnose(output, rc, rows)
    short_error = summarize_exception(output)
    if short_error and rc != 0:
        print(f'[attempt1-error] {args.platform}: {short_error}')

    if args.retry_cdp and args.platform in {'xhs', 'dy'} and rows <= 0 and reason_code not in {'captcha', 'account_permission', 'cookie_expired', 'config_mismatch'}:
        print(f'[retry] {args.platform}: 标准模式未产出，尝试 CDP 模式')
        attempt2_log = log_path.with_name(log_path.stem + '_attempt2_cdp' + log_path.suffix)
        rc2, rows2, output2 = run_once(repo, args.platform, cookie, args.keywords, max_notes, True, attempt2_log)
        attempts += 1
        short_error2 = summarize_exception(output2)
        if short_error2 and rc2 != 0:
            print(f'[attempt2-error] {args.platform}: {short_error2}')
        if rows2 > rows or (rc != 0 and rc2 == 0):
            rc, rows, output = rc2, rows2, output2
        reason_code, detail = diagnose(output, rc, rows)
        short_error = summarize_exception(output)

    log_path.write_text(output[-120000:], encoding='utf-8')

    status = 'ok' if rows > 0 else ('blocked' if reason_code in {'captcha', 'account_permission', 'ip_block', 'risk_control'} else 'empty')
    payload = {
        'platform_code': args.platform, 'status': status, 'items': rows,
        'reason_code': reason_code, 'detail': detail, 'exit_code': rc,
        'attempts': attempts, 'updated_at': datetime.now(timezone.utc).isoformat(),
        'keywords': args.keywords, 'diagnostic_log': str(log_path),
        'exception': short_error,
    }
    write_status(status_path, payload)
    print(f'[result] {args.platform}: status={status}, items={rows}, reason={reason_code} - {detail}')
    # 让 GitHub step 真正显示失败/警告；workflow 已 continue-on-error，不会阻断其他平台。
    return 0 if rows > 0 else (rc or 2)


if __name__ == '__main__':
    raise SystemExit(main())
