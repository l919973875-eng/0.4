from __future__ import annotations

import ast
import importlib.util
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "run_mediacrawler.py"

spec = importlib.util.spec_from_file_location("run_mediacrawler", MODULE_PATH)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def main() -> None:
    # Regression: GitHub secrets can contain line breaks, quotes and backslashes.
    # repr() must survive regex replacement literally; otherwise base_config.py
    # becomes an unterminated Python string before MediaCrawler even starts.
    cookie = "a1=abc; web_session=hello\\world; note=it's ok;\nsecond_line=value"
    keywords = "中国员工 遇袭,南海 中国海警 碰撞"

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        config_dir = repo / "config"
        config_dir.mkdir(parents=True)
        base = config_dir / "base_config.py"
        base.write_text(
            "PLATFORM = 'xhs'\n"
            "KEYWORDS = 'old'\n"
            "LOGIN_TYPE = 'qrcode'\n"
            "COOKIES = ''\n"
            "CRAWLER_TYPE = 'search'\n",
            encoding="utf-8",
        )

        mod.patch_config(repo, "xhs", cookie, keywords, 12, cdp=False)
        patched = base.read_text(encoding="utf-8")
        ast.parse(patched)

        ns: dict[str, object] = {}
        exec(compile(patched, str(base), "exec"), ns)
        assert ns["COOKIES"] == cookie
        assert ns["KEYWORDS"] == keywords
        assert ns["PLATFORM"] == "xhs"

    print("ok: MediaCrawler config escaping regression test passed")




def test_batch_helpers():
    kws = mod.split_keywords(" A ,B,A,, C ")
    assert kws == ["A", "B", "C"]
    assert mod.chunked(kws, 2) == [["A", "B"], ["C"]]


def test_suppress_noisy_upstream_logs():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        core = repo / "media_platform" / "xhs" / "core.py"
        core.parent.mkdir(parents=True)
        core.write_text('utils.logger.info(f"[XiaoHongShuCrawler.search] Note details: {note_details}")\n', encoding="utf-8")
        changed = mod.suppress_noisy_upstream_logs(repo, "xhs")
        text = core.read_text(encoding="utf-8")
        assert changed == 1
        assert "Note details fetched" in text
        assert "Note details: {note_details}" not in text


if __name__ == "__main__":
    main()
    test_batch_helpers()
    test_suppress_noisy_upstream_logs()
    print("run_mediacrawler batching/log patch tests: ok")
