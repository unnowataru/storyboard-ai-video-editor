"""このリポジトリのスクリプトが共通で使う部品の入口。中身は責務ごとのモジュールにあり、ここは名前を読み込み直すだけ。

- settings: 設定の読み込み (Config・config)、場所の決定、結果の出し方 (report・write_json・run_dir・publish_run)
- resolve: Resolve への接続 (connect_resolve) と時刻 (tc_to_sec・row_clips・hold_*・comp_frame)
- schema: 構成表の書式の点検 (mask_problems・callout_problems など) と構成表に書ける項目 (ROW_KEYS・unknown_keys)
- layout: 配置の計算 (safe_area・fit_caption・badge_box・callout_values・apply_layout・point_values・side_values)
- fusion: Fusion の値 (scene_values・hold_values) と組み立て (build_fusion)
- video: FFmpeg (run_tool・probe_video) と FrameReader (frame_reader)

スクリプトとテストは `import common as C` のまま、`C.xxx` で全部の名前を使う。
テストで名前を差し替えるときは、common と、その名前を持つモジュールの両方を差し替える (tests/conftest.py の patch_common)。

設定の決め方は settings の docstring。どの値がどこから来たかは `python scripts/common.py --show-config [構成表.json]` で表示する。
"""
import json

import fusion
import layout
import resolve
import schema
import settings
import video
from fusion import *  # noqa: F401,F403
from fusion import _animate  # noqa: F401  (tests/resolve/native_annotation.py・native_fade.py が使う)
from layout import *  # noqa: F401,F403
from resolve import *  # noqa: F401,F403
from schema import *  # noqa: F401,F403
from settings import *  # noqa: F401,F403
from settings import _read_json  # noqa: F401  (テストと features.py が使う)
from video import *  # noqa: F401,F403

# 分けたモジュール (tests/offline/test_features_catalog.py が、単独で使うスクリプトではなく部品として扱う。tests/conftest.py の patch_common も使う)
PARTS = (settings, resolve, schema, layout, fusion, video)


def _main():
    import argparse
    ap = argparse.ArgumentParser(description="設定の値と出どころを表示する")
    ap.add_argument("--show-config", action="store_true")
    ap.add_argument("--json", action="store_true", help="JSON で出す")
    ap.add_argument("storyboard", nargs="?")
    a = ap.parse_args()
    sb = _read_json(a.storyboard) if a.storyboard else None
    d = config().describe(sb)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return
    print("設定ファイル:", d["config_files"]["defaults"])
    print("個人の設定:", d["config_files"]["local"], "(あり)" if d["config_files"]["local_exists"] else "(なし)")
    print("\n[場所]")
    for k, v in d["paths"].items():
        mark = "OK  " if v["exists"] else "なし"
        print(f"{mark} {k:20} {v['value']}  ← {v['source']}")
    print("\n[見た目と出力]")
    for k, v in d["storyboard"].items():
        print(f"{k:28} {json.dumps(v['value'], ensure_ascii=False)}  ← {v['source']}")


if __name__ == "__main__":
    _main()
