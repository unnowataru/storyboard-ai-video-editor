"""このリポジトリの作業に要るものがそろっているかを確認する。結果を 1 つの JSON に書き、画面には要約を出す。

使い方: python scripts/check_env.py [--json]
        python scripts/resolve_lock.py run --owner <名前> -- python scripts/check_env.py --connect [--json]
  --connect  起動中の Resolve に外から接続して、製品名・版・Studio かを確認する (既定では接続しない)。resolve_lock.py run で包む
             版が config の resolve.verified_version (docs/scripting-api.md の表を確認した版) と違えば
             「確認し直しが要る」と出す
  --json     要約の代わりに JSON を全部出す
画面: 1 行目に件数と JSON のパス。続けて、欠けている必須のものと、注意 (確認し直しが要る、作業フォルダーが AppData の下など) だけを出す。
結果の JSON: 出力先 (config の out_dir) の check-env.json。全項目と、場所の値ごとの出どころ (環境変数・設定ファイル・既定の探し方) が入る。
終了コード: 必須のものが欠けていれば 1。Resolve のインストール先・スクリプト API・fusionscript.dll は --connect のときだけ必須
(簡易版 sb2ffmpeg.py は FFmpeg だけで動くため、--connect を付けなければ任意の項目として出す)。
"""
import argparse
import ctypes
import importlib
import json
import os
import platform
import subprocess
import sys

import common as C

RESULTS = []
NOTICES = []   # 合否には入れないが、画面に出す注意 (確認し直しが要る、作業フォルダーが AppData の下など)


def add(group, name, ok, value="", note="", required=True):
    RESULTS.append({"group": group, "name": name, "ok": bool(ok), "required": required, "value": value, "note": note})


def version_differs(actual, verified):
    """GetVersionString() の版 (major.minor.patch[suffix].build、例 21.1.1.10) が確認した版と違うか。
    suffix (ベータを示す文字など) も比べるので、文字列のまま比べる。"""
    return str(actual or "").strip() != str(verified or "").strip()


def file_version(path):
    """Windows の実行ファイルの版 (例 21.1.1.10)。取れなければ None。"""
    if os.name != "nt" or not os.path.isfile(path):
        return None
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        ver.GetFileVersionInfoW(path, 0, size, buf)
        p, n = ctypes.c_void_p(), ctypes.c_uint()
        ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n))
        ffi = ctypes.cast(p, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        ms, ls = ffi[2], ffi[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def process_running(name):
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="replace").stdout
        return f'"{name}.exe"'.lower() in out.lower()
    except Exception:
        return None


def ocr_available(cfg):
    """Windows 標準の文字認識が使えるか (ocr_windows.ps1 -Check)。(使えるか, 言語のタグ) を返す。"""
    if os.name != "nt":
        return False, None
    ps = cfg.section("apps").get("windows_powershell", "")
    ps1 = os.path.join(C.SCRIPTS, "ocr_windows.ps1")
    try:
        out = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, "-Check"],
                             capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL, timeout=60).stdout
        j = json.loads(out.strip().splitlines()[-1])
        return bool(j.get("ok")), j.get("language")
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return False, None


def check(cfg, connect):
    # Python
    add("Python", "実行中の Python", sys.version_info >= (3, 10), sys.version.split()[0], sys.executable)
    for mod, req, note in (("PIL", True, "pip install -r requirements.txt"),
                           ("pytest", False, "tests/offline を動かすとき"),
                           ("numpy", False, "track.py (隠す位置を見つける)"),
                           ("cv2", False, "track.py (opencv-python)")):
        try:
            m = importlib.import_module(mod)
            add("Python", f"パッケージ {mod}", True, getattr(m, "__version__", ""), required=req)
        except ImportError:
            add("Python", f"パッケージ {mod}", False, "", note, required=req)

    # Resolve (完成版だけで使う。--connect のときだけ必須)
    inst = cfg.path_info("resolve_install_dir")
    add("Resolve", "インストール先", inst["exists"], inst["value"], inst["source"], required=connect)
    exe = os.path.join(inst["value"], "Resolve.exe") if inst["value"] else None
    ver = file_version(exe) if exe else None
    verified = cfg.section("resolve").get("verified_version", "")
    add("Resolve", "Resolve.exe の版", ver is not None, ver or "", f"docs/scripting-api.md の表を確認した版は {verified}",
        required=connect)
    for key, label in (("resolve_script_api", "スクリプト API のフォルダー"), ("resolve_script_lib", "fusionscript.dll")):
        i = cfg.path_info(key)
        add("Resolve", label, i["exists"], i["value"], i["source"], required=connect)
    running = process_running(cfg.defaults.get("apps", {}).get("resolve_process", ""))
    add("Resolve", "起動しているか", bool(running), running, "接続のテストには起動が要る", required=False)
    if connect:
        try:
            r = C.connect_resolve(tries=1, quiet=True)
        except BaseException as e:  # noqa: BLE001  require の sys.exit と DLL の読み込みの失敗も表に出す
            r = None
            add("Resolve", "外からの接続", False, "", f"{type(e).__name__}: {e}")
        else:
            if r:
                live = r.GetVersionString()
                add("Resolve", "外からの接続", True, f"{r.GetProductName()} {live}")
                same = not version_differs(live, verified)
                add("Resolve", "確認した版と同じか", same, live, f"確認した版は {verified} (config の resolve.verified_version)",
                    required=False)
                if not same:
                    NOTICES.append(f"確認し直しが要る: Resolve の版 {live} は、docs/scripting-api.md の表を確認した版 "
                                   f"{verified} と違う。表の項目は tests/resolve のテストで確認し直す")
                add("Resolve", "Studio か (IsStudio)", bool(r.IsStudio()), r.IsStudio(), "外からの接続は Studio だけ")
            else:
                add("Resolve", "外からの接続", False, "", "起動しているか、環境設定 > システム > 一般 > 「外部スクリプトに使用」を確認する")
    else:
        add("Resolve", "外からの接続", True, "確認していない", "--connect を付けると確認する", required=False)

    # FFmpeg
    ff = cfg.path_info("ffmpeg_dir")
    add("FFmpeg", "FFmpeg の場所", ff["exists"], ff["value"], ff["source"])
    if ff["exists"]:
        f = os.path.join(ff["value"], "ffmpeg" + C.EXE)
        try:
            v = subprocess.run([f, "-version"], capture_output=True, text=True, errors="replace").stdout.splitlines()[0]
            add("FFmpeg", "版", True, v[:70])
            enc = subprocess.run([f, "-hide_banner", "-encoders"], capture_output=True, text=True, errors="replace").stdout
            flt = subprocess.run([f, "-hide_banner", "-filters"], capture_output=True, text=True, errors="replace").stdout
            add("FFmpeg", "libx264", " libx264 " in enc, "", "full build を入れる")
            add("FFmpeg", "drawtext", " drawtext " in flt, "", "full build を入れる (contact_sheet.py が使う)")
        except OSError as e:
            add("FFmpeg", "実行", False, "", str(e))
        add("FFmpeg", "ffprobe", os.path.isfile(os.path.join(ff["value"], "ffprobe" + C.EXE)), "")

    # 書体
    look, _ = cfg.storyboard_values()
    for fam in sorted({look["caption"]["font_name"], look["badge"]["font_name"]}):
        p = cfg.font_file(fam)
        note = ""
        if p:
            try:
                from PIL import Image, ImageDraw, ImageFont
                im = Image.new("L", (60, 60))
                ImageDraw.Draw(im).text((5, 5), "あ", font=ImageFont.truetype(p, 40), fill=255)
                note = "日本語を描けた" if im.getbbox() else "日本語を描けない"
            except ImportError:
                note = "Pillow が無いので描画は確認していない"
        add("書体", fam, bool(p), p or "", note or "config の fonts に書いたファイル名で探す")

    # 文字認識 (track.py)
    ok, lang = ocr_available(cfg)
    add("文字認識", "Windows の文字認識 (track.py)", ok, lang or "",
        "設定 > 時刻と言語 > 言語 で、文字認識に使える言語が入っているか" if not ok else "ocr_windows.ps1 -Check", required=False)

    # 作業フォルダーと出力先
    for key, label in (("work_dir", "作業フォルダー"), ("out_dir", "出力先")):
        i = cfg.path_info(key)
        warn = C.appdata_warning(i["value"])
        real = os.path.realpath(i["value"]) if i["value"] and os.path.exists(i["value"]) else None
        note = i["source"]
        if real and os.path.normcase(real) != os.path.normcase(os.path.abspath(i["value"])):
            note += f"。実際の場所は {real}"
        add("場所", label, bool(i["value"]), i["value"], (warn + "。" if warn else "") + note, required=(key == "work_dir"))
        if warn:
            NOTICES.append(f"注意: {label} {warn}")
    ts = cfg.path_info("trial_source")
    add("場所", "テスト用の素材 (RESOLVE_TRIAL_SOURCE)", ts["exists"], ts["value"] or "",
        "Resolve のテストと tests/trial-storyboard.json で使う。無ければ scripts/make_test_source.py で作る", required=False)

    # 設定
    add("設定", "個人の設定ファイル", True, cfg.local_path if os.path.isfile(cfg.local_path) else "なし (既定値だけ)",
        "", required=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--connect", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = C.config()
    check(cfg, a.connect)
    missing = [r for r in RESULTS if r["required"] and not r["ok"]]
    # 場所と出どころだけを残す。見た目と出力の値は環境の確認に関係しない (python scripts/common.py --show-config で見る)
    d = cfg.describe()
    report = {"os": platform.platform(), "python": sys.executable, "ok": not missing,
              "checks": RESULTS, "notices": NOTICES, "config": {"config_files": d["config_files"], "paths": d["paths"]}}
    path = os.path.join(cfg.out_dir(), "check-env.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return 1 if missing else 0
    optional = sum(1 for r in RESULTS if not r["required"] and not r["ok"])
    summary = (f"check_env: {len(RESULTS)} 項目、必須の欠け {len(missing)} 件、任意で無いもの {optional} 件"
               + ("。直し方は docs/setup.md" if missing else ""))
    lines = [f"なし {r['group']} {r['name']} {r['value']}" + (f" ({r['note']})" if r["note"] else "") for r in missing]
    return C.report(not missing, summary, path, lines + NOTICES)


if __name__ == "__main__":
    sys.exit(main())
