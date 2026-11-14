"""機密の無いテスト用の素材を FFmpeg のテストパターン (testsrc2) と音 (sine) から作る。

使い方: python scripts/make_test_source.py [--seconds 40] [--out パス]
既定の置き場所は 作業フォルダー (config の work_dir) の test-media\\testsrc.mp4。
解像度とフレームレートは config の timeline と fps (config/local.json で変えられる)。
作ったパスを表示するので、環境変数 RESOLVE_TRIAL_SOURCE か config/local.json の paths.trial_source に入れる。
"""
import argparse
import os
from pathlib import Path

import common as C


def make(out, seconds):
    cfg = C.config()
    look, _ = cfg.storyboard_values()
    W, H, fps = look["timeline"]["width"], look["timeline"]["height"], look["fps"]
    Path(out).parent.mkdir(parents=True, exist_ok=True)   # ファイル名だけの指定 (親が空) でも動く
    C.run_tool("ffmpeg", ["-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={fps}:duration={seconds}",
                          "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
                          "-vf", C.BT709_VF, *C.BT709_TAGS,   # 色は BT.709 (Resolve と check_final の読み方に合わせる)
                          "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-shortest", out])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=int, default=40, help="長さ (秒)。tests/trial-storyboard.json は 36 秒まで使う")
    ap.add_argument("--out")
    a = ap.parse_args()
    out = a.out or os.path.join(C.config().work_dir("test-media"), "testsrc.mp4")
    print(make(out, a.seconds))


if __name__ == "__main__":
    main()
