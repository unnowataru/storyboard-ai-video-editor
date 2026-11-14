"""テスト用の構成表 (tests/trial-storyboard.json) を、sb2resolve.py と同じ部品で組み、段階ごとの成否を記録するテスト。

使い方 (Resolve を使うので resolve_lock.py run で包む。Resolve Studio を起動しておく):
  python scripts/resolve_lock.py run --owner <名前> -- python scripts/api_trial.py [--storyboard 構成表] [--source 素材]
結果: 出力先 (config の out_dir) の api-trial.json と api-trial.mp4。各段階の成否と戻り値を JSON に残す。
画面: 1 行目に ok の段階の数と JSON のパス。続けて、NG の段階 (例外なら最後の 1 行) と警告だけを出す。
段階の成否は、例外が出ないことに加えて、その段階に要る API の戻り値 (書き出しは作業の状態が Complete) で決める。
NG の段階があれば終了コード 1。build が NG なら後続の段階は動かさない。
組み方 (カット・倍速・Fusion・書き出し) は sb2resolve.py の部品。ここだけにあるのは、マーカーと Dynamic Zoom の段階。
"""
import argparse
import os
import sys
import time
import traceback

import common as C
import sb2resolve as S

log = {"steps": {}}
OUT = None   # 出力先。実行したときに決める
ctx = {}
WARNINGS = []   # 画面に出す警告 (JSON には各段階の detail に入る)


def save():
    if OUT:
        C.write_json(os.path.join(OUT, "api-trial.json"), log)


def step(name, fn):
    """段階を 1 つ動かす。fn は (成否, 詳細) を返す。例外が出たときと、成否が偽のときは NG。"""
    try:
        ok, detail = fn()
        log["steps"][name] = {"ok": bool(ok), "detail": detail}
    except Exception:
        log["steps"][name] = {"ok": False, "error": traceback.format_exc()}
    save()
    return log["steps"][name]["ok"]


def failure_lines():
    """画面に出す、NG の段階 (例外なら最後の 1 行) と警告。全文は api-trial.json にある。"""
    lines = []
    for name, s in log["steps"].items():
        if not s["ok"]:
            err = (s.get("error") or "").strip().splitlines()
            lines.append(f"NG {name}: " + (err[-1] if err else "API の戻り値が偽"))
    if "stopped_after" in log:
        lines.append(f"{log['stopped_after']} が NG なので後続の段階は動かしていない")
    return lines + WARNINGS


def run_steps(steps, required=("build",)):
    """段階を順に動かす。required の段階が NG なら後続は動かさない。全部 ok なら 0、そうでなければ 1 を返す。"""
    for name, fn in steps:
        if not step(name, fn) and name in required:
            log["stopped_after"] = name
            break
    return 0 if "stopped_after" not in log and all(s["ok"] for s in log["steps"].values()) else 1


def build():
    """素材を取り込み、全部の行を置いて速さを変える (sb2resolve.import_media / build_timeline)。"""
    rows = S.plan(SB)
    media = S.import_media(mp, rows, FPS)
    if any(v["item"] is None for v in media.values()):
        return False, {"imported": {k: v["item"] is not None for k, v in media.items()}}
    tl, items, _, res = S.build_timeline(proj, mp, SB, rows, media, name)
    ctx.update(rows=rows, media=media, tl=tl, items=items)
    return tl is not None and all(r.get("match") for r in res), {"rows": res}


def markers():
    res = S.add_markers(ctx["items"], SB)
    res["tl_marker"] = ctx["tl"].AddMarker(0, SB["marker"]["head_color"], "先頭", "タイムラインのマーカー", 1, "head")
    return all(res.values()), res


def dynzoom():
    it = ctx["items"][2]
    ok = it.SetProperties({"DynamicZoomEnabled": True, "DynamicZoomEase": 0})
    return ok, {"set": ok,
                "get": {k: it.GetProperties().get(k) for k in ("DynamicZoomEnabled", "DynamicZoomEase", "ZoomX", "Pan")}}


def fusion_effects():
    """行ごとに、書いてあるものだけを Fusion で組む (sb2resolve.add_fusion)。"""
    for it in ctx["items"]:
        it.SetProperties({"DynamicZoomEnabled": False})   # dynzoom の段階で入れた Dynamic Zoom を Fusion のズームと重ねない
    ok, res, warns = S.add_fusion(ctx["items"], SB, ctx["rows"], ctx["media"], FONT_FILE)
    WARNINGS.extend("警告 " + w for w in warns)
    return ok and bool(res), res


def render():
    r = S.render(proj, SB, OUT, "api-trial", limit=600)
    return r["done"], r


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", default=os.path.join(C.REPO, "tests", "trial-storyboard.json"))
    ap.add_argument("--source", help="素材 (構成表の source より優先)")
    args = ap.parse_args()

    cfg = C.config()
    OUT = cfg.out_dir()
    SB = cfg.load_storyboard(args.storyboard, args.source)
    FPS = int(SB["fps"])
    FONT_FILE = C.caption_font(SB)
    log["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # 見た目と出力は実効値だけ残す (出どころは python scripts/common.py --show-config 構成表 で見る)
    log["config"] = {k: SB[k] for k in cfg.storyboard_values()[0]}
    path = os.path.join(OUT, "api-trial.json")

    name = f"api-trial-{int(time.time())}"
    try:   # 接続とプロジェクトの作成で例外が出ても (DaVinciResolveScript が読めないなど)、1 行と記録の JSON にする
        resolve = C.connect_resolve(quiet=True)
        proj = resolve.GetProjectManager().CreateProject(name) if resolve else None
    except (Exception, SystemExit) as e:
        resolve = proj = None
        log["exception"] = traceback.format_exc()
        log["error"] = f"接続かプロジェクトの作成で止まった: {(str(e) or type(e).__name__).strip().splitlines()[-1]}"
    if not proj:
        log.setdefault("error", "Resolve に接続できない" if not resolve else f"プロジェクト {name} を作れない")
        save()
        sys.exit(C.report(False, f"api_trial: {log['error']}", path))
    pm = resolve.GetProjectManager()
    log["settings"] = S.project_settings(proj, SB["timeline"]["width"], SB["timeline"]["height"], FPS)
    WARNINGS.extend("警告 " + w for w in log["settings"]["warnings"])
    mp = proj.GetMediaPool()
    log["project"] = name
    log["is_studio"] = resolve.IsStudio()

    steps = [("build", build), ("markers", markers), ("dynamic_zoom", dynzoom),
             ("fusion_effects", fusion_effects), ("render", render)]
    code = run_steps(steps)
    pm.SaveProject()
    log["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()
    n_ok = sum(1 for s in log["steps"].values() if s["ok"])
    C.report(code == 0, f"api_trial: {n_ok}/{len(steps)} 段階 ok (プロジェクト {name})", path, failure_lines())
    sys.exit(code)
