"""仮組み (--render-anyway) の書き出し先を、完成版から離すテスト。Resolve には接続しない。

模擬の render は、渡されたディレクトリ (仮組みでは一時ディレクトリ) にバイトを書く。
公開できていれば、そのバイトは仮組みの書き出し先にあり、一時ファイルは残らない。
接続前の拒否ができていれば、connect_resolve は呼ばれず、守るバイトは残る。
"""
import json
import os
import tempfile

import pytest

from conftest import patch_common

import common as C
import sb2resolve as S
from test_sb2resolve import MediaPool, Proj, RunPM, RunProj, sb_rows

ORIGINAL = b"original-final"
RENDERED = b"rendered-by-mock"


def resolve_write_path(path, fmt="mp4"):
    """テスト側の見本。Resolve は拡張子を形式にし、CustomName は拡張子を除いた名前。"""
    path = os.path.abspath(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(os.path.dirname(path), f"{stem}.{fmt}")


@pytest.fixture
def preview_env(tmp_path, monkeypatch):
    """Resolve を模擬に置き換えた run。書き出しの模擬は、呼ばれたらファイルを上書きする。"""
    monkeypatch.setenv("RESOLVE_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("RESOLVE_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("RESOLVE_PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("RESOLVE_CONFIG", str(tmp_path / "no-local.json"))
    patch_common(monkeypatch, "_CFG", None)
    out_root = C.config().path("out_dir")
    preview_root = C.config().path("preview_dir")
    connects, renders = [], []
    plant = {"during": None, "fail": None, "write": True}
    pm = RunPM({"test-gui30": RunProj("test-gui30", timelines=["sample"]), "test-open": RunProj("test-open")},
               current="test-open")

    def connect(*a, **k):
        connects.append(1)
        return type("R", (), {"GetProjectManager": lambda self: pm})()

    def fake_render(proj, sb, d, n, limit, marks=None):
        fmt = (sb.get("render") or {}).get("format") or "mp4"
        path = os.path.join(d, f"{n}.{fmt}")
        os.makedirs(d, exist_ok=True)
        if plant.get("write", True):
            with open(path, "wb") as f:   # 渡されたパス (一時ディレクトリ) に書く
                f.write(RENDERED)
        during = plant.get("during")
        if during:
            os.makedirs(os.path.dirname(during["path"]) or ".", exist_ok=True)
            with open(during["path"], "xb") as f:
                f.write(during["data"])
        renders.append((d, n, path))
        env.marks.append(marks)
        if plant.get("fail"):
            return {"done": False, "status": plant["fail"], "path": path}
        return {"done": True, "status": "Complete", "path": path}

    patch_common(monkeypatch, "connect_resolve", connect)
    monkeypatch.setattr(S, "probe_sources", lambda rows: {rows[0]["source"]: {"width": 1920, "height": 1080,
                                                                               "fps": 30.0, "duration": 9999.0}})
    monkeypatch.setattr(S, "add_fusion", lambda *a, **k: (True, {}, []))
    monkeypatch.setattr(S, "add_markers", lambda *a, **k: {})
    patch_common(monkeypatch, "caption_font", lambda look: None)
    monkeypatch.setattr(S, "render", fake_render)

    def go(output_name="sample.mp4", plant_during=None, fail_render=None, write=True, **kw):
        connects.clear()
        renders.clear()
        plant["during"] = plant_during
        plant["fail"] = fail_render
        plant["write"] = write
        sb = sb_rows(2, tmp_path)
        path = tmp_path / "sample.json"
        data = {k: sb[k] for k in ("source", "rows", "fps")}
        final = None
        if output_name is not None:
            final = str(tmp_path / output_name)
            data["output"] = final
        C.write_json(str(path), data)
        import argparse
        fields = dict(storyboard=str(path), source=None, name=None, project=None, timeline=None, output=None,
                      render=False, render_anyway=False, overwrite=False, rows=None, dry_run=False)
        fields.update(kw)
        log = {}
        ok, summary, lines = S.run(argparse.Namespace(**fields), log)
        return ok, summary, lines, log, final

    env = type("Env", (), {})()
    env.tmp_path, env.out_root, env.preview_root = tmp_path, out_root, preview_root
    env.connects, env.renders, env.go, env.pm = connects, renders, go, pm
    env.marks = []
    return env


def _refused(env, summary):
    assert env.connects == [], summary
    assert env.renders == []


@pytest.mark.parametrize("kind", [
    "same",
    "relative",
    "dotdot",
    "format_extension",
    "declared_extension",
    "symlink",
    "casefold",
])
def test_render_anyway_refuses_path_that_resolves_to_the_final(preview_env, monkeypatch, kind):
    """--output が完成版と同じファイルになる仮組みは、Resolve に触る前に拒否する。

    変更前は render が呼ばれ、模擬が完成版のバイトを rendered-by-mock に変える。
    """
    env, tmp = preview_env, preview_env.tmp_path
    output_name, output = "sample.mp4", str(tmp / "sample.mp4")
    protect = tmp / "sample.mp4"
    if kind == "relative":
        monkeypatch.chdir(tmp)
        output = "sample.mp4"
    elif kind == "dotdot":
        nested = tmp / "nested"
        nested.mkdir()
        output = str(nested / ".." / "sample.mp4")
    elif kind == "format_extension":
        # 構成表は .mov。形式が mp4 なので、Resolve が書くのは .mp4
        output_name, output = "sample.mov", str(tmp / "sample.mp4")
    elif kind == "declared_extension":
        output_name, output = "sample.mov", str(tmp / "sample.mov")
    elif kind == "symlink":
        protect.write_bytes(ORIGINAL)
        link = tmp / "alias.mp4"
        try:
            link.symlink_to(protect)
        except OSError as e:
            if getattr(e, "winerror", None) == 1314:
                pytest.skip("シンボリックリンクを作れない (winerror 1314)")
            raise
        output = str(link)
    elif kind == "casefold":
        monkeypatch.setattr(os.path, "normcase", lambda p: str(p).lower())
        output_name, output = "Sample.mp4", str(tmp / "sample.mp4")
    if kind != "symlink":
        protect.write_bytes(ORIGINAL)
    ok, summary, lines, log, _ = env.go(output_name=output_name, render_anyway=True, output=output,
                                        project="test-gui30")
    assert ok is False
    _refused(env, summary)
    assert "render" not in log
    assert "完成版と同じ" in summary
    assert protect.read_bytes() == ORIGINAL
    assert any("完成版" in x for x in lines)


def test_existing_preview_file_is_not_overwritten(preview_env):
    """完成版とは別の仮組みファイルが既にあるときは、上書きせず、Resolve に触らない。

    変更前は render が呼ばれ、バイトが rendered-by-mock になる。
    """
    env, tmp = preview_env, preview_env.tmp_path
    preview = tmp / "elsewhere" / "draft.mp4"
    preview.parent.mkdir()
    preview.write_bytes(b"keep-me")
    final = tmp / "sample.mp4"
    final.write_bytes(ORIGINAL)
    ok, summary, lines, log, _ = env.go(render_anyway=True, output=str(preview), project="test-gui30")
    assert ok is False
    _refused(env, summary)
    assert "render" not in log
    assert "上書きしない" in summary
    assert preview.read_bytes() == b"keep-me"
    assert final.read_bytes() == ORIGINAL
    assert str(preview) in "\n".join(lines)


def _main_record(env, monkeypatch, capsys, *extra):
    """main を通して記録の JSON と画面の出力を返す (構成表は go と同じものを書く)。"""
    env.go(output_name="sample.mp4", dry_run=True)   # 構成表 sample.json を書くため (接続しない)
    sb_path = env.tmp_path / "sample.json"
    capsys.readouterr()
    monkeypatch.setattr("sys.argv", ["sb2resolve.py", str(sb_path), *extra])
    code = S.main()
    out = capsys.readouterr().out
    with open(os.path.join(C.config().out_dir("sb2resolve"), "sample.json"), encoding="utf-8") as f:
        rec = json.load(f)
    return code, out, rec


@pytest.mark.parametrize("case", ["existing_preview", "same_as_final", "project_name_taken", "existing_final"])
def test_refusal_reason_is_in_the_record(preview_env, monkeypatch, capsys, case):
    """拒否の理由 (要約と行) が、画面の出力だけでなく記録の JSON にも残る。

    変更前は記録に summary が無く、仮組みの書き出し先が既にあるときの理由が JSON から分からなかった。
    """
    env, tmp = preview_env, preview_env.tmp_path
    if case == "existing_preview":
        preview = tmp / "elsewhere" / "draft.mp4"
        preview.parent.mkdir()
        preview.write_bytes(b"keep-me")
        extra = ["--render-anyway", "--output", str(preview), "--project", "test-gui30"]
        want = "上書きしない"
    elif case == "same_as_final":
        extra = ["--render-anyway", "--output", str(tmp / "sample.mp4"), "--project", "test-gui30"]
        want = "完成版と同じ"
    elif case == "existing_final":
        (tmp / "sample.mp4").write_bytes(ORIGINAL)
        extra = ["--render", "--project", "test-gui30"]
        want = "完成版の書き出し先が既にある"
    else:
        extra = ["--name", "test-gui30"]
        want = "既にある"
    code, out, rec = _main_record(env, monkeypatch, capsys, *extra)
    assert code == 1 and rec["ok"] is False
    first = out.splitlines()[0]
    assert want in rec["summary"]
    assert first.startswith("NG " + rec["summary"])
    for line in rec["lines"]:
        assert line in out
    if case == "existing_preview":
        assert str(preview) in rec["lines"]
        assert preview.read_bytes() == b"keep-me"


def test_existing_final_is_refused_before_connecting(preview_env):
    """--render も、完成版の書き出し先が既にあれば Resolve に接続する前に止め、ファイルを変えない。

    変更前は render が完成版のパスへそのまま書き、模擬が中身を rendered-by-mock に変えた。
    0 バイトのファイルも守る (Resolve は空のファイルも確認なしで消して書く)。
    """
    env, tmp = preview_env, preview_env.tmp_path
    final = tmp / "sample.mp4"
    for data in (ORIGINAL, b""):
        final.write_bytes(data)
        ok, summary, lines, log, final_path = env.go(render=True, project="test-gui30")
        assert ok is False
        _refused(env, summary)
        assert "render" not in log
        assert "完成版の書き出し先が既にあるので上書きしない" in summary
        assert resolve_write_path(final_path) in lines
        assert any("--overwrite" in x for x in lines)
        assert final.read_bytes() == data
    # 構成表の output の拡張子が形式と違っても、Resolve が書くファイル (.mp4) で見る
    final.write_bytes(ORIGINAL)
    ok, summary, _, _, _ = env.go(output_name="sample.mov", render=True, project="test-gui30")
    assert ok is False and env.connects == [] and final.read_bytes() == ORIGINAL


def test_render_writes_the_final_through_a_temp_dir(preview_env):
    """--render は完成版を宛先の親の一時ディレクトリへ書いてから置く。宛先が無ければ置ける。"""
    env, tmp = preview_env, preview_env.tmp_path
    ok, summary, lines, log, final_path = env.go(render=True, project="test-gui30")
    assert ok, (summary, lines)
    assert env.connects == [1]
    asked = env.renders[0][0]
    assert os.path.normpath(os.path.dirname(asked)) == os.path.normpath(str(tmp))
    assert os.path.basename(asked).startswith("sb2render-")
    assert not os.path.exists(env.renders[0][2]) and not os.path.exists(asked)
    assert log["render"]["mode"] == "final"
    assert log["render"]["ignored_ng"] == []
    assert "仮組み" not in summary
    assert "書き出し " + resolve_write_path(final_path) in summary
    assert log["output"] == final_path
    assert os.path.normpath(log["render"]["path"]) == os.path.normpath(resolve_write_path(final_path))
    assert os.path.normpath(log["render"]["dest"]) == os.path.normpath(resolve_write_path(final_path))
    assert (tmp / "sample.mp4").read_bytes() == RENDERED

    other = tmp / "renders" / "sample-v5.mp4"
    ok, summary, _, log, _ = env.go(render=True, output=str(other), project="test-gui30")
    assert ok
    assert log["output"] == str(other)
    assert log["render"]["mode"] == "final"
    assert os.path.normpath(log["render"]["path"]) == os.path.normpath(str(other))
    assert other.read_bytes() == RENDERED


@pytest.mark.parametrize("mode", ["render", "render_anyway"])
def test_overwrite_replaces_an_existing_destination(preview_env, mode):
    """--overwrite を付けると、既にある書き出し先 (完成版・仮組み) を置き換える。書き出しは一時ディレクトリから。"""
    env, tmp = preview_env, preview_env.tmp_path
    dest = tmp / ("sample.mp4" if mode == "render" else "elsewhere/draft.mp4")
    dest.parent.mkdir(exist_ok=True)
    dest.write_bytes(b"old-bytes")
    kw = {mode: True} | ({} if mode == "render" else {"output": str(dest)})
    ok, summary, lines, log, _ = env.go(project="test-gui30", overwrite=True, **kw)
    assert ok, (summary, lines)
    assert env.connects == [1]
    assert dest.read_bytes() == RENDERED
    assert os.path.normpath(log["render"]["path"]) == os.path.normpath(str(dest))
    asked = env.renders[0][0]
    assert os.path.basename(asked).startswith("sb2render-") and not os.path.exists(asked)


def test_overwrite_does_not_write_into_a_hardlinked_final(preview_env):
    """仮組みの --output が完成版のハードリンクなら、--overwrite があっても完成版と同じとして拒否する。"""
    env, tmp = preview_env, preview_env.tmp_path
    final = tmp / "sample.mp4"
    final.write_bytes(ORIGINAL)
    alias = tmp / "elsewhere" / "alias.mp4"
    alias.parent.mkdir()
    os.link(final, alias)
    ok, summary, _, log, _ = env.go(render_anyway=True, output=str(alias), project="test-gui30", overwrite=True)
    assert ok is False
    _refused(env, summary)
    assert "完成版と同じ" in summary
    assert final.read_bytes() == ORIGINAL and alias.read_bytes() == ORIGINAL


def test_publish_overwrite_replaces_the_name_only(tmp_path):
    """publish の overwrite は宛先の名前を差し替える。宛先がハードリンクでも、ほかの名前の中身は変わらない。"""
    parent, src, dest = _preview_pair(tmp_path, payload=b"new-bytes")
    real = parent / "real.mp4"
    real.write_bytes(b"keep-hardlink")
    os.link(real, dest)
    assert S.publish(src, dest, overwrite=True) == dest
    assert open(dest, "rb").read() == b"new-bytes"
    assert real.read_bytes() == b"keep-hardlink"
    assert not os.path.exists(src)


@pytest.mark.parametrize("mode", ["render", "render_anyway"])
def test_failed_render_records_the_destination(preview_env, mode):
    """書き出しが失敗したら、記録の render.dest と画面の行に本来の宛先を残す。
    何も書けなかったときは一時ファイルを報告せず (path は None)、空の一時ディレクトリも残さない。"""
    env, tmp = preview_env, preview_env.tmp_path
    dest = tmp / ("sample.mp4" if mode == "render" else "elsewhere/draft.mp4")
    kw = {mode: True} | ({} if mode == "render" else {"output": str(dest)})
    ok, summary, lines, log, _ = env.go(project="test-gui30", fail_render="Failed", write=False, **kw)
    assert ok is False
    rr = log["render"]
    assert rr["done"] is False and rr["path"] is None
    assert os.path.normpath(rr["dest"]) == os.path.normpath(str(dest))
    assert any(str(dest) in x for x in lines)
    assert "一時ファイル" not in summary and not any("一時ファイル" in x for x in lines)
    assert not os.path.exists(env.renders[0][0])
    assert not dest.exists()
    # 書けたが失敗扱いのときは、一時ファイルと宛先の両方を出す
    ok, summary, lines, log, _ = env.go(project="test-gui30", fail_render="Failed", **kw)
    rr = log["render"]
    assert ok is False and rr["path"] and os.path.isfile(rr["path"])
    assert os.path.normpath(rr["dest"]) == os.path.normpath(str(dest))
    assert rr["path"] in summary and "成功ではない" in summary
    assert any(str(dest) in x and rr["path"] in x for x in lines)


def test_render_anyway_default_path_differs_from_the_final(preview_env):
    """--output が無い仮組みは、設定の仮組みの書き出し先 (preview_dir) の直下で、完成版とは別のファイル。"""
    env = preview_env
    final = env.tmp_path / "sample.mp4"
    final.write_bytes(ORIGINAL)
    ok, summary, lines, log, final_path = env.go(render_anyway=True, project="test-gui30")
    assert ok, (summary, lines)
    got = log["render"]["path"]
    expected = os.path.join(env.preview_root, "sample-preview.mp4")
    assert os.path.normpath(got) == os.path.normpath(expected)
    assert os.path.normpath(os.path.dirname(got)) == os.path.normpath(env.preview_root)
    assert not os.path.normpath(got).startswith(os.path.normpath(env.out_root) + os.sep)
    assert os.path.basename(got) == "sample-preview.mp4"
    assert os.path.normpath(got) != os.path.normpath(resolve_write_path(final_path))
    assert log["output"] == got
    assert log["render"]["mode"] == "preview"
    assert log["render"]["ignored_ng"] == []
    assert "仮組み" in summary and "完成版ではない" in summary
    assert final.read_bytes() == ORIGINAL
    assert open(got, "rb").read() == RENDERED
    assert env.connects == [1]
    asked = env.renders[0][0]
    assert os.path.normpath(asked) != os.path.normpath(os.path.dirname(got))
    assert os.path.normpath(os.path.dirname(asked)) == os.path.normpath(os.path.dirname(got))
    assert os.path.basename(asked).startswith("sb2render-")
    assert not os.path.exists(env.renders[0][2])


def test_explicit_preview_output_different_from_the_final_is_used(preview_env):
    """完成版と違う --output は仮組みとして使う。--output を当てた後のパスと比べると、全部拒否してしまう。"""
    env, tmp = preview_env, preview_env.tmp_path
    other = tmp / "elsewhere" / "draft.mov"
    final = tmp / "sample.mp4"
    final.write_bytes(ORIGINAL)
    ok, summary, lines, log, _ = env.go(render_anyway=True, output=str(other), project="test-gui30")
    assert ok, (summary, lines)
    assert env.connects == [1]
    assert os.path.normpath(log["render"]["path"]) == os.path.normpath(resolve_write_path(other))
    assert log["render"]["mode"] == "preview"
    assert "仮組み" in summary
    assert final.read_bytes() == ORIGINAL
    assert other.with_suffix(".mp4").read_bytes() == RENDERED
    dest_dir = os.path.dirname(resolve_write_path(other))
    assert os.path.normpath(env.renders[0][0]) != os.path.normpath(dest_dir)
    assert not os.path.exists(env.renders[0][2])


def test_default_preview_is_refused_when_it_resolves_to_the_final(preview_env, monkeypatch):
    """既定の仮組み先を完成版と同じ場所にすると、--output が無くても拒否する。"""
    def collide(cfg, final_output, storyboard, fmt, suffix="preview"):
        return final_output

    monkeypatch.setattr(S, "default_preview_path", collide)
    env = preview_env
    final = env.tmp_path / "sample.mp4"
    final.write_bytes(ORIGINAL)
    ok, summary, lines, log, _ = env.go(render_anyway=True, project="test-gui30")
    assert ok is False
    _refused(env, summary)
    assert "完成版と同じ" in summary
    assert final.read_bytes() == ORIGINAL
    assert "render" not in log


def test_preview_project_name_stays_on_the_final_stem(preview_env):
    """仮組みのファイル名で、新しく作るプロジェクトの名前は変えない。"""
    env = preview_env
    ok, summary, lines, log, _ = env.go(render_anyway=True)
    assert ok, (summary, lines)
    assert log["mode"] == "new"
    assert log["project"] == "sample"
    assert log["render"]["path"].endswith(os.path.join("previews", "sample-preview.mp4"))


def test_preview_that_ignored_ng_is_not_a_successful_final(preview_env, monkeypatch, capsys):
    """NG を無視した仮組みは、成功した完成版の 1 行に見えない。JSON に mode・パス・ignored_ng が残る。"""
    env = preview_env
    real = S.build_timeline

    def broken(*a, **k):
        tl, items, holds, res = real(*a, **k)
        res[1]["match"] = False
        return tl, items, holds, res

    monkeypatch.setattr(S, "build_timeline", broken)
    final = env.tmp_path / "sample.mp4"
    final.write_bytes(ORIGINAL)
    ok, summary, lines, log, final_path = env.go(render_anyway=True, project="test-gui30")
    assert ok is False
    assert log["render"]["mode"] == "preview"
    assert len(log["render"]["ignored_ng"]) == 1
    assert log["render"]["ignored_ng"][0].startswith("NG 2 行目")
    assert os.path.normpath(log["render"]["path"]) != os.path.normpath(resolve_write_path(final_path))
    assert log["output"] == log["render"]["path"]
    assert "仮組み" in summary and "完成版ではない" in summary
    assert "NG 1 件を無視して書き出した" in summary
    assert final.read_bytes() == ORIGINAL
    code = C.report(ok, summary, None, lines)
    printed = capsys.readouterr().out
    first = printed.splitlines()[0]
    assert code == 1 and first.startswith("NG ")
    assert "仮組み" in first and "完成版ではない" in first
    dest = env.tmp_path / "log.json"
    C.write_json(str(dest), log)
    saved = json.loads(dest.read_text(encoding="utf-8"))
    assert saved["render"]["mode"] == "preview"
    assert saved["render"]["path"] == log["render"]["path"]
    assert saved["render"]["ignored_ng"] == log["render"]["ignored_ng"]


def test_dry_run_does_not_connect(preview_env):
    """--dry-run は Resolve に触らない。仮組みの出力先が完成版と同じでも、dry-run では拒否しない。"""
    env = preview_env
    ok, summary, lines, log, _ = env.go(dry_run=True)
    assert env.connects == []
    assert "render" not in log
    assert "dry-run" in summary and "Resolve には触っていない" in summary
    assert ok, (summary, lines)
    final = str(env.tmp_path / "sample.mp4")
    ok, summary, lines, log, _ = env.go(dry_run=True, render_anyway=True, output=final)
    assert env.connects == []
    assert "render" not in log
    assert "dry-run" in summary
    assert "完成版と同じ" not in summary
    assert ok, (summary, lines)
    ok, summary, _, log, _ = env.go(dry_run=True, render=True)
    assert env.connects == []
    assert "render" not in log


def test_effective_render_path_replaces_extension(tmp_path):
    p = str(tmp_path / "a" / "clip.mov")
    assert S.effective_render_path(p, "mp4") == os.path.abspath(str(tmp_path / "a" / "clip.mp4"))


def test_same_output_file_collapses_relative_and_dotdot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = S.effective_render_path("clip.mov", "mp4")
    b = S.effective_render_path(str(tmp_path / "sub" / ".." / "clip.mp4"), "mp4")
    assert S.same_output_file(a, b)
    assert not S.same_output_file(a, S.effective_render_path(str(tmp_path / "other" / "clip.mp4"), "mp4"))


def test_same_output_file_uses_normcase(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "normcase", lambda p: str(p).lower())
    assert S.same_output_file(str(tmp_path / "Clip.MP4"), str(tmp_path / "clip.mp4"))


def test_same_output_file_strips_extended_prefix(tmp_path):
    a = str(tmp_path / "clip.mp4")
    assert S.same_output_file("\\\\?\\" + a, a)
    assert S.same_output_file("\\\\?\\UNC\\server\\share\\clip.mp4", "\\\\server\\share\\clip.mp4")


def test_dry_run_render_anyway_does_not_need_storyboard_output(preview_env):
    """--render-anyway --dry-run は構成表の output が無くても点検し、Resolve に接続しない。"""
    env = preview_env
    ok, summary, lines, log, final = env.go(output_name=None, dry_run=True, render_anyway=True)
    assert final is None
    assert env.connects == []
    assert "render" not in log
    assert ok, (summary, lines)
    assert "書き出し先が無い" not in summary
    assert "書き出し先が無い" not in "\n".join(lines)
    assert "dry-run" in summary and "Resolve には触っていない" in summary
    # --render の dry-run は、構成表の output が無いと止める
    ok, summary, lines, log, _ = env.go(output_name=None, dry_run=True, render=True)
    assert env.connects == []
    assert "書き出し先が無い" in summary


def test_dest_created_during_render_is_not_replaced(preview_env):
    """接続前の確認の後に宛先ができると、既存のバイトは残し、一時ファイルを報告する。done は false。"""
    env, tmp = preview_env, preview_env.tmp_path
    preview = tmp / "elsewhere" / "draft.mp4"
    kept = b"appeared-after-precheck"
    final = tmp / "sample.mp4"
    final.write_bytes(ORIGINAL)
    ok, summary, lines, log, _ = env.go(
        render_anyway=True, output=str(preview), project="test-gui30",
        plant_during={"path": str(preview), "data": kept})
    assert ok is False
    assert env.connects == [1]
    assert log["render"]["done"] is False
    assert log["render"]["mode"] == "preview"
    assert preview.read_bytes() == kept
    assert final.read_bytes() == ORIGINAL
    temp = log["render"]["path"]
    assert os.path.normpath(temp) != os.path.normpath(str(preview))
    assert open(temp, "rb").read() == RENDERED
    assert os.path.basename(os.path.dirname(temp)).startswith("sb2render-")
    assert os.path.normpath(env.renders[0][0]) != os.path.normpath(str(preview.parent))
    assert temp in summary
    assert "成功ではない" in summary
    assert "成功ではない" in "\n".join(lines)
    assert log["output"] == temp


def test_failed_render_is_not_published(preview_env):
    """書き出しが失敗したら宛先へ置かない。途中でできた既存のファイルも、一時ファイルも残す。"""
    env, tmp = preview_env, preview_env.tmp_path
    preview = tmp / "elsewhere" / "draft.mp4"
    kept = b"created-while-render-failed"
    ok, summary, lines, log, _ = env.go(
        render_anyway=True, output=str(preview), project="test-gui30", fail_render="Failed",
        plant_during={"path": str(preview), "data": kept})
    assert ok is False
    assert log["render"]["done"] is False
    assert preview.read_bytes() == kept
    temp = log["render"]["path"]
    assert open(temp, "rb").read() == RENDERED
    assert temp in summary
    assert "成功ではない" in summary
    assert os.path.normpath(env.renders[0][0]) != os.path.normpath(str(preview.parent))

    preview2 = tmp / "elsewhere" / "other.mp4"
    ok, summary, _, log, _ = env.go(
        render_anyway=True, output=str(preview2), project="test-gui30", fail_render="Failed")
    assert ok is False
    assert not preview2.exists()
    assert os.path.isfile(log["render"]["path"])
    assert log["render"]["done"] is False
    assert log["render"]["path"] in summary


def test_existing_hardlink_at_preview_path_is_not_overwritten(preview_env):
    """宛先が既存ファイルのハードリンクでも、接続前に拒否し、どちらも書き換えない。"""
    env, tmp = preview_env, preview_env.tmp_path
    real = tmp / "kept.mp4"
    real.write_bytes(b"keep-hardlink")
    preview = tmp / "elsewhere" / "draft.mp4"
    preview.parent.mkdir()
    os.link(real, preview)
    final = tmp / "sample.mp4"
    final.write_bytes(ORIGINAL)
    ok, summary, lines, log, _ = env.go(render_anyway=True, output=str(preview), project="test-gui30")
    assert ok is False
    _refused(env, summary)
    assert "render" not in log
    assert preview.read_bytes() == b"keep-hardlink"
    assert real.read_bytes() == b"keep-hardlink"
    assert final.read_bytes() == ORIGINAL


def _preview_pair(tmp_path, name="clip.mp4", payload=b"new-bytes"):
    parent = tmp_path / "preview"
    parent.mkdir(exist_ok=True)
    temp = tempfile.mkdtemp(prefix="sb2render-", dir=parent)
    src = os.path.join(temp, name)
    with open(src, "wb") as f:
        f.write(payload)
    return parent, src, str(parent / name)


def test_publish_links_without_rename(tmp_path, monkeypatch):
    """成功時は os.link で置き、os.rename は呼ばない。宛先が既にあれば既存のバイトを残す。"""
    def no_rename(*a, **k):
        raise AssertionError("rename で置き換えた")

    monkeypatch.setattr(os, "rename", no_rename)
    parent, src, dest = _preview_pair(tmp_path)
    assert S.publish(src, dest) == dest
    assert open(dest, "rb").read() == b"new-bytes"
    assert not os.path.exists(src)
    os.rmdir(os.path.dirname(src))

    parent, src, dest = _preview_pair(tmp_path, name="other.mp4", payload=b"second")
    with open(dest, "wb") as f:
        f.write(b"keep")
    with pytest.raises(FileExistsError):
        S.publish(src, dest)
    assert open(dest, "rb").read() == b"keep"
    assert open(src, "rb").read() == b"second"


def test_publish_does_not_overwrite_hardlink(tmp_path):
    parent, src, dest = _preview_pair(tmp_path, payload=b"new-bytes")
    real = parent / "real.mp4"
    real.write_bytes(b"keep-hardlink")
    os.link(real, dest)
    with pytest.raises(FileExistsError):
        S.publish(src, dest)
    assert open(dest, "rb").read() == b"keep-hardlink"
    assert real.read_bytes() == b"keep-hardlink"
    assert open(src, "rb").read() == b"new-bytes"


def test_publish_fallback_renames_only_when_dest_is_absent(tmp_path, monkeypatch):
    """link できないときは、宛先が無ければ rename する。宛先があれば置き換えない。"""
    def no_link(*a, **k):
        raise OSError("hard link is not supported")

    monkeypatch.setattr(os, "link", no_link)
    parent, src, dest = _preview_pair(tmp_path)
    assert S.publish(src, dest) == dest
    assert open(dest, "rb").read() == b"new-bytes"
    assert not os.path.exists(src)

    def no_rename(*a, **k):
        raise AssertionError("rename で置き換えた")

    monkeypatch.setattr(os, "rename", no_rename)
    parent, src, dest = _preview_pair(tmp_path, name="other.mp4", payload=b"second")
    with open(dest, "wb") as f:
        f.write(b"keep")
    with pytest.raises(FileExistsError):
        S.publish(src, dest)
    assert open(dest, "rb").read() == b"keep"
    assert open(src, "rb").read() == b"second"


# ---------- --rows (部分の書き出し) ----------
def test_parse_rows():
    assert S.parse_rows("4", 5) == ((4, 4), None)
    assert S.parse_rows(" 3-5 ", 5) == ((3, 5), None)
    for text, word in (("5-3", "より後"), ("0-2", "1〜5"), ("3-9", "1〜5"), ("6", "1〜5"),
                       ("a", "形で書く"), ("1-2-3", "形で書く"), ("", "形で書く"), ("-2", "形で書く")):
        sel, err = S.parse_rows(text, 5)
        assert sel is None and word in err, (text, err)


def test_rows_frames_include_the_hold_of_the_last_row():
    """範囲は始まりの行の最初のフレームから、終わりの行の止めたクリップの最後のフレームまで (両端を含む)。"""
    rows = [{"rec": 0, "frames": 60, "hold_frames": 30}, {"rec": 90, "frames": 60, "hold_frames": 0},
            {"rec": 150, "frames": 30, "hold_frames": 0}]
    assert S.rows_frames(rows, (1, 1)) == (0, 89)
    assert S.rows_frames(rows, (1, 2)) == (0, 149)
    assert S.rows_frames(rows, (2, 3)) == (90, 179)
    assert S.rows_label((3, 5)) == "rows3-5" and S.rows_label((4, 4)) == "rows4"


def test_render_with_marks_selects_the_range(tmp_path):
    """marks を渡すと SelectAllFrames を偽にし、MarkIn・MarkOut をそのまま入れる。渡さなければ全編。"""
    class Proj:
        def __init__(self):
            self.settings = []

        def SetCurrentRenderFormatAndCodec(self, f, c):
            return True

        def SetRenderSettings(self, d):
            self.settings.append(dict(d))
            return True

        def AddRenderJob(self):
            return "job"

        def StartRendering(self, jobs, isInteractiveMode=False):
            return True

        def IsRenderingInProgress(self):
            return False

        def GetRenderJobStatus(self, j):
            return {"JobStatus": "Complete"}

    look = {"render": {"format": "mp4", "codec": "H264"}}
    proj = Proj()
    S.render(proj, look, str(tmp_path), "x", 5, (108010, 108020))
    S.render(proj, look, str(tmp_path), "y", 5)
    assert proj.settings[0] | {} == {"SelectAllFrames": False, "MarkIn": 108010, "MarkOut": 108020,
                                     "TargetDir": str(tmp_path), "CustomName": "x"}
    assert proj.settings[1]["SelectAllFrames"] is True and "MarkIn" not in proj.settings[1]


@pytest.mark.parametrize("spec,sel,name", [("1-2", (1, 2), "sample-rows1-2-preview.mp4"),
                                           ("2", (2, 2), "sample-rows2-preview.mp4")])
def test_rows_renders_the_rows_to_the_preview_dir(preview_env, spec, sel, name):
    """--rows は全部を組んでから、行の範囲を MarkIn・MarkOut (GetStartFrame を足した値) で書き出し、preview_dir に置く。
    完成版は変えない。記録の range に行・フレーム・MarkIn・MarkOut が残り、要約は完成版の成功に見えない。"""
    env = preview_env
    final = env.tmp_path / "sample.mp4"
    final.write_bytes(ORIGINAL)
    ok, summary, lines, log, _ = env.go(rows=spec, project="test-gui30")
    assert ok, (summary, lines)
    rows = log["rows"]
    a = rows[sel[0] - 1]["rec"]
    last = rows[sel[1] - 1]
    b = last["rec"] + last["frames"] + last["hold_frames"] - 1
    assert env.marks == [(108000 + a, 108000 + b)]
    assert log["range"] == {"rows": list(sel), "frames": [a, b], "count": b - a + 1,
                            "mark_in": 108000 + a, "mark_out": 108000 + b}
    rr = log["render"]
    assert rr["mode"] == "rows" and rr["done"]
    assert os.path.normpath(rr["path"]) == os.path.normpath(os.path.join(env.preview_root, name))
    assert log["output"] == rr["path"] and open(rr["path"], "rb").read() == RENDERED
    assert os.path.basename(env.renders[0][0]).startswith("sb2render-")
    assert "部分の書き出し" in summary and "完成版ではない" in summary
    assert f"{sel[0]}〜{sel[1]} 行目" in summary
    assert final.read_bytes() == ORIGINAL


@pytest.mark.parametrize("spec", ["2-1", "1-3", "0", "x", "1-", ""])
def test_rows_outside_the_storyboard_is_refused_before_connecting(preview_env, spec):
    """--rows の a > b と行の外は、構成表の行の数で Resolve に接続する前に止める (Resolve は止めない)。--dry-run でも同じ。"""
    env = preview_env
    for kw in ({}, {"dry_run": True}):
        ok, summary, lines, log, _ = env.go(rows=spec, project="test-gui30", **kw)
        assert ok is False
        _refused(env, summary)
        assert "--rows" in summary and "Resolve には触っていない" in summary
        assert "render" not in log


def test_render_with_rows_is_refused_before_connecting(preview_env):
    """--render と --rows は一緒に使えない (--render を黙って無視して完成版を書かないことにしない)。接続する前に止める。"""
    env = preview_env
    ok, summary, lines, log, _ = env.go(rows="1", render=True)
    assert ok is False and "--render と --rows" in summary
    _refused(env, summary)
    assert "render" not in log


def test_build_and_render_without_preview_target_does_not_use_the_final_path(tmp_path, monkeypatch):
    """run を通らずに部分の書き出しを呼んでも、決まっていない書き出し先を完成版のパスにしない。"""
    import argparse
    monkeypatch.setattr(S, "add_fusion", lambda *a, **k: (True, {}, []))
    monkeypatch.setattr(S, "render_via_temp", lambda *a, **k: pytest.fail("書き出してはいけない"))
    sb = dict(sb_rows(2, tmp_path), render={"format": "mp4", "codec": "H264"})
    rows = S.plan(sb)
    args = argparse.Namespace(render=False, render_anyway=False, rows_sel=(1, 1), overwrite=False)
    log = {"settings": {"warnings": []}, "range": {"rows": [1, 1], "frames": [0, 149], "count": 150}}
    pm = type("PM", (), {"SaveProject": lambda self: True})()
    ok, summary, _ = S.build_and_render(args, log, pm, Proj(), MediaPool(), sb, rows, "p", "t")
    assert ok is False and "書き出し先が決まっていない" in summary and "render" not in log


def test_rows_existing_destination_needs_overwrite(preview_env):
    """部分の書き出し先が既にあれば接続する前に止め、--overwrite なら置き換える。完成版と同じ --output は拒否する。"""
    env = preview_env
    dest = os.path.join(env.preview_root, "sample-rows1-preview.mp4")
    os.makedirs(env.preview_root, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"keep-me")
    ok, summary, lines, log, _ = env.go(rows="1", project="test-gui30")
    assert ok is False
    _refused(env, summary)
    assert "部分の書き出しの書き出し先が既にある" in summary and dest in lines
    assert open(dest, "rb").read() == b"keep-me"
    ok, summary, lines, log, _ = env.go(rows="1", project="test-gui30", overwrite=True)
    assert ok, (summary, lines)
    assert open(dest, "rb").read() == RENDERED
    final = env.tmp_path / "sample.mp4"
    final.write_bytes(ORIGINAL)
    ok, summary, _, _, _ = env.go(rows="1", project="test-gui30", output=str(final), overwrite=True)
    assert ok is False and env.connects == [] and "完成版と同じ" in summary
    assert final.read_bytes() == ORIGINAL


def test_rows_with_ng_needs_render_anyway(preview_env, monkeypatch):
    """NG の行があれば --rows は書き出さない。--render-anyway も付ければ書き出し、無視した NG を残す。"""
    env = preview_env
    real = S.build_timeline

    def broken(*a, **k):
        tl, items, holds, res = real(*a, **k)
        res[1]["match"] = False
        return tl, items, holds, res

    monkeypatch.setattr(S, "build_timeline", broken)
    ok, summary, lines, log, _ = env.go(rows="1", project="test-gui30")
    assert ok is False and env.renders == [] and "render" not in log
    assert any("--render-anyway" in x for x in lines)
    ok, summary, lines, log, _ = env.go(rows="1", render_anyway=True, project="test-gui30")
    assert ok is False and len(env.renders) == 1
    assert log["render"]["mode"] == "rows" and len(log["render"]["ignored_ng"]) == 1
    assert log["render"]["path"].endswith("sample-rows1-preview.mp4")
    assert "NG 1 件を無視して書き出した" in summary


def test_rows_dry_run_reports_the_range_without_connecting(preview_env):
    env = preview_env
    ok, summary, lines, log, _ = env.go(output_name=None, rows="1-2", dry_run=True)
    assert ok, (summary, lines)
    assert env.connects == [] and "render" not in log
    assert "1〜2 行目" in summary and log["range"]["rows"] == [1, 2]
