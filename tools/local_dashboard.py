from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'site'
DATA = ROOT / 'data'
PORT = 8765
_lock = threading.Lock()
_job: dict[str, object] = {'process': None, 'mode': None, 'started_at': None, 'log': None}


def job_state() -> dict:
    with _lock:
        proc = _job.get('process')
        running = bool(proc and proc.poll() is None)
        return {
            'running': running,
            'mode': _job.get('mode'),
            'started_at': _job.get('started_at'),
            'exit_code': None if running or not proc else proc.poll(),
            'log': _job.get('log'),
        }


def start_job(mode: str) -> tuple[bool, str]:
    if mode not in {'all', 'social', 'rebuild'}:
        return False, 'Unsupported mode'
    with _lock:
        proc = _job.get('process')
        if proc and proc.poll() is None:
            return False, 'A scan is already running'
        DATA.mkdir(parents=True, exist_ok=True)
        log = DATA / 'local_dashboard_runner.log'
        if mode == 'social':
            command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'tools' / 'run-manual-social-scan.ps1')]
        else:
            command = [sys.executable, str(ROOT / 'cloud_runner.py'), '--mode', mode]
        stream = log.open('ab')
        proc = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        _job.update(process=proc, mode=mode, started_at=time.strftime('%Y-%m-%d %H:%M:%S'), log=str(log))
        return True, 'Started'


PANEL = '''
<style>#localCtl{position:fixed;right:18px;bottom:18px;z-index:99;background:#121b2f;border:1px solid #3c5685;border-radius:14px;padding:12px;box-shadow:0 14px 34px #0009;font:13px system-ui;color:#eaf1ff;max-width:320px}#localCtl b{display:block;margin-bottom:8px}#localCtl button{background:#2864c7;color:#fff;border:0;border-radius:7px;padding:8px 10px;margin:3px;cursor:pointer}#localCtl button:disabled{opacity:.55;cursor:wait}#localCtl small{display:block;color:#b7c5db;line-height:1.45;margin-top:6px}</style>
<div id="localCtl"><b>本机控制 / Local control</b><div><button data-mode="all">运行全量巡航</button><button data-mode="social">运行社交扫描</button><button data-mode="rebuild">刷新页面</button></div><small id="localCtlState">读取状态…</small></div>
<script>(()=>{const state=document.getElementById('localCtlState'),buttons=[...document.querySelectorAll('#localCtl button')];async function refresh(){try{const d=await fetch('/api/status').then(r=>r.json());state.textContent=d.running?`正在运行：${d.mode}（完成后自动刷新页面）`:'空闲。可直接查看当前数据。';buttons.forEach(b=>b.disabled=d.running)}catch(e){state.textContent='控制服务状态不可用'}}async function run(mode){const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});const d=await r.json();state.textContent=d.message||'已提交';refresh()}buttons.forEach(b=>b.onclick=()=>run(b.dataset.mode));refresh();setInterval(refresh,4000)})()</script>
'''


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITE), **kwargs)

    def log_message(self, fmt, *args):
        return

    def send_json(self, obj, status=HTTPStatus.OK):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/status':
            return self.send_json(job_state())
        if path in {'/', '/index.html'}:
            page = SITE / 'index.html'
            if not page.exists():
                return self.send_error(HTTPStatus.NOT_FOUND, 'Run a rebuild first')
            html = page.read_text(encoding='utf-8').replace('</body>', PANEL + '</body>')
            encoded = html.encode('utf-8')
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            return self.wfile.write(encoded)
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != '/api/run':
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            payload = json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, TypeError):
            return self.send_json({'message': 'Invalid request'}, HTTPStatus.BAD_REQUEST)
        ok, message = start_job(str(payload.get('mode', '')))
        return self.send_json({'message': message, **job_state()}, HTTPStatus.ACCEPTED if ok else HTTPStatus.CONFLICT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://127.0.0.1:{args.port}/'
    print(f'Local dashboard: {url}', flush=True)
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == '__main__':
    main()