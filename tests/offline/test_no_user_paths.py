"""リポジトリの .py / .ps1 / .json / .comp (Fusion のテンプレート) に、利用者や PC に固有のパスが書かれていないことを確認する。

見るもの: ドライブ文字で始まる絶対パスと、Users フォルダーの下のパス。
場所は 環境変数 → config/local.json → config/defaults.json の既定の探し方 で決める (docs/setup.md)。
"""
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKIP_DIRS = {".git", "out", "__pycache__", ".pytest_cache", ".venv", "node_modules", ".worktrees", "worktrees"}
EXTS = (".py", ".ps1", ".json", ".comp")

# 見つかってもよいファイルと理由。理由の無い例外は作らない
ALLOW = {
    "config/local.json": "個人の設定ファイル。git 管理外で、各自の PC の場所を書く場所",
}

SEP = "[" + re.escape("\\/") + "]"
PATTERNS = [
    ("ドライブ文字で始まる絶対パス", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]" + ":" + SEP + r"(?!/)")),
    ("Users の下のパス", re.compile("Users" + SEP, re.IGNORECASE)),
]


def files():
    for d, dirs, names in os.walk(REPO):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for n in names:
            if n.endswith(EXTS):
                yield os.path.relpath(os.path.join(d, n), REPO).replace(os.sep, "/")


@pytest.mark.parametrize("rel", sorted(files()))
def test_no_user_specific_paths(rel):
    if rel in ALLOW:
        pytest.skip(ALLOW[rel])
    with open(os.path.join(REPO, rel), encoding="utf-8", errors="replace") as f:
        text = f.read()
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for label, pat in PATTERNS:
            if pat.search(line):
                hits.append(f"{rel}:{n}: {label}: {line.strip()[:120]}")
    assert not hits, "利用者に固有のパスがある (環境変数か config で指定する形にする):\n" + "\n".join(hits)


def test_patterns_catch_examples():
    """検査の式そのものの確認。例はここで組み立てる (このファイルに直接書くと自分が引っかかる)。"""
    drive, users, bs = "C" + ":", "Us" + "ers", chr(92)
    bad = [drive + bs + users + bs + "someone" + bs + "x.mp4", drive + "/" + users + "/someone/x.mp4",
           "D" + ":" + bs + "tools" + bs + "ffmpeg",
           users + "/someone"]
    good = ["%PROGRAMFILES%/Blackmagic Design", "{repo}/out", "https" + "://example.com/a",
            "%USERPROFILE%/Videos/storyboard-work"]
    assert all(any(p.search(s) for _, p in PATTERNS) for s in bad)
    assert not any(p.search(s) for s in good for _, p in PATTERNS)
