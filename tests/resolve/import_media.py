"""MediaPool.ImportMedia に渡す形の違い。

期待 (Studio 21.1.1 の実機):
- 辞書の list ([{"FilePath": パス}]) を渡すと None か空が返る (.pyi の記載とは違う)
- パス文字列の list なら取り込める。区切りは / でも \\ でもよい
- 同じファイルを何度取り込んでも、メディアプールの項目は 1 つ
- MediaStorage.AddItemListToMediaPool([パス]) でも取り込める (結果を記録するだけ)

使い方: python scripts/resolve_lock.py run --owner import_media -- python tests/resolve/import_media.py [--source 素材]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_helpers import Trial  # noqa: E402

t = Trial("import_media")
fwd = t.source.replace(os.sep, "/")
back = t.source.replace("/", os.sep)
root = t.mp.GetRootFolder()


def names(r):
    return [c.GetName() for c in r] if r else r


r = t.mp.ImportMedia([{"FilePath": fwd}])
t.check("辞書の list (/ 区切り) は取り込めない", not r, names(r), "None か空")
r = t.mp.ImportMedia([{"FilePath": back}])
t.check("辞書の list (\\ 区切り) は取り込めない", not r, names(r), "None か空")
r = t.mp.ImportMedia([fwd])
t.check("パス文字列 (/ 区切り) は取り込める", bool(r) and len(r) == 1, names(r), "1 件")
r = t.mp.ImportMedia([back])
t.check("パス文字列 (\\ 区切り) は取り込める", bool(r) and len(r) == 1, names(r), "1 件")
try:
    r = t.resolve.GetMediaStorage().AddItemListToMediaPool([back])
    t.note("MediaStorage.AddItemListToMediaPool", names(r))
except Exception as e:  # noqa: BLE001
    t.note("MediaStorage.AddItemListToMediaPool", f"例外 {e}")
n = len(root.GetClipList() or [])
t.check("同じファイルはメディアプールに 1 つだけ", n == 1, n, 1)
t.finish()
