"""Resolve のプロジェクトのスクリプト (scripts/projects.py) のテスト。Resolve の API は模擬のオブジェクトで置き換えるので、Resolve は要らない。"""
import os

import pytest

from conftest import patch_common

import common as C
import projects as P


# ---------- 模擬の Resolve ----------
class Clip:
    def __init__(self, name, path):
        self.name, self.path = name, path

    def GetName(self):
        return self.name

    def GetClipProperty(self, key):
        assert key == "File Path"
        return self.path


class Bin:
    def __init__(self, name, clips=(), subs=()):
        self.name, self.clips, self.subs = name, list(clips), list(subs)

    def GetName(self):
        return self.name

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.subs


class Item:
    def __init__(self, mpi):
        self.mpi = mpi

    def GetMediaPoolItem(self):
        return self.mpi


class Timeline:
    def __init__(self, name, tracks):
        self.name, self.tracks = name, tracks   # {"video": [[項目, ...], ...], "audio": [...]}

    def GetName(self):
        return self.name

    def GetTrackCount(self, kind):
        return len(self.tracks.get(kind, []))

    def GetItemListInTrack(self, kind, n):
        return self.tracks[kind][n - 1]


class Pool:
    def __init__(self, root):
        self.root = root

    def GetRootFolder(self):
        return self.root


class Project:
    def __init__(self, name, root=None, timelines=()):
        self.name, self.pool, self.timelines = name, Pool(root or Bin("Master")), list(timelines)

    def GetName(self):
        return self.name

    def GetMediaPool(self):
        return self.pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1]


class PM:
    """プロジェクトマネージャーの模擬。tree は {"projects": {名前: Project}, "folders": {名前: tree}}。"""

    def __init__(self, tree, current=None):
        self.tree, self.stack, self.current = tree, [], current
        self.loaded, self.exported, self.saved = [], [], []
        self.can_save = True

    def SaveProject(self):
        self.saved.append(self.current.GetName() if self.current else None)
        return self.can_save

    def _here(self):
        t = self.tree
        for f in self.stack:
            t = t["folders"][f]
        return t

    def GetCurrentDatabase(self):
        return {"DbType": "Disk", "DbName": "Local Database"}

    def GetCurrentProject(self):
        return self.current

    def GotoRootFolder(self):
        self.stack = []
        return True

    def GotoParentFolder(self):
        self.stack.pop()
        return True

    def OpenFolder(self, name):
        if name not in self._here().get("folders", {}):
            return False
        self.stack.append(name)
        return True

    def GetProjectListInCurrentFolder(self):
        return list(self._here().get("projects", {}))

    def GetFolderListInCurrentFolder(self):
        return list(self._here().get("folders", {}))

    def LoadProject(self, name):
        p = self._here().get("projects", {}).get(name)
        if p:
            self.current = p
            self.loaded.append("/".join(self.stack + [name]))
        return p

    def ExportProject(self, name, path, with_stills):
        if name not in self._here().get("projects", {}):
            return False
        self.exported.append((name, path, with_stills))
        with open(path, "wb") as f:
            f.write(b"drp")
        return True


class Resolve:
    def __init__(self, pm):
        self.pm = pm

    def GetProjectManager(self):
        return self.pm


@pytest.fixture
def out(tmp_path, monkeypatch):
    """check の JSON を pytest の一時フォルダーに書かせる。"""
    monkeypatch.setenv("RESOLVE_OUT_DIR", str(tmp_path / "out"))
    patch_common(monkeypatch, "_CFG", None)
    return tmp_path


def connect(monkeypatch, pm):
    patch_common(monkeypatch, "connect_or_exit", lambda tries=1: Resolve(pm))


def sample(tmp_path):
    """ルートに A、フォルダー Case の下に B。A の素材は全部ある。B はビンの下の 1 つとタイムラインの音声の 1 つが無い。"""
    have = tmp_path / "have.mp4"
    have.write_bytes(b"x")
    gone, gone2 = str(tmp_path / "gone.mp4"), str(tmp_path / "gone.wav")
    a = Project("A", Bin("Master", [Clip("have", str(have)), Clip("Timeline 1", "")]),
                [Timeline("T", {"video": [[Item(Clip("have", str(have)))]], "audio": [[]]})])
    b = Project("B", Bin("Master", [Clip("have", str(have))], [Bin("rec", [Clip("gone", gone)])]),
                [Timeline("T1", {"video": [[Item(Clip("have", str(have))), Item(None)]]}),
                 Timeline("T2", {"video": [[]], "audio": [[Item(Clip("gone2", gone2))], [Item(Clip("have", str(have)))]]})])
    return {"projects": {"A": a}, "folders": {"Case": {"projects": {"B": b}, "folders": {}}}}, a, b


# ---------- 引数 ----------
@pytest.mark.parametrize("argv", [[], ["export", "A"], ["export", "--out", "x"], ["check"], ["rename", "A"]])
def test_bad_arguments_stop_before_connecting(argv, monkeypatch):
    patch_common(monkeypatch, "connect_or_exit", lambda tries=1: pytest.fail("接続してはいけない"))
    with pytest.raises(SystemExit) as e:
        P.main(argv)
    assert e.value.code == 2


# ---------- list ----------
def test_list_walks_folders_and_marks_current(tmp_path, monkeypatch, capsys):
    tree, a, _ = sample(tmp_path)
    pm = PM(tree, current=a)
    connect(monkeypatch, pm)
    assert P.main(["list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("OK projects list: 2 件")
    assert lines[1:] == ["  A (開いている)", "  Case/B"]
    assert pm.stack == []


# ---------- export ----------
STAMP = "20261019-0930"


@pytest.fixture
def stamp(monkeypatch):
    """書き出しの名前に付ける日時を決めておく。"""
    monkeypatch.setattr(P.time, "strftime", lambda fmt: STAMP if fmt == "%Y%m%d-%H%M" else pytest.fail(fmt))


def test_export_writes_drp_and_checks_size(tmp_path, monkeypatch, capsys, stamp):
    """名前は <プロジェクト名>-<YYYYMMDD-HHMM>.drp。終わるとフォルダーの位置は一番上に戻る。"""
    tree, _, _ = sample(tmp_path)
    pm = PM(tree)
    connect(monkeypatch, pm)
    dest = tmp_path / "drp"
    assert P.main(["export", "A", "Case/B", "--out", str(dest)]) == 0
    assert pm.exported == [("A", str(dest / f"A-{STAMP}.drp"), True), ("B", str(dest / f"B-{STAMP}.drp"), True)]
    assert capsys.readouterr().out.startswith("OK projects export: 2/2 件")
    assert pm.stack == []


def test_export_stops_when_file_exists(tmp_path, monkeypatch, capsys, stamp):
    tree, _, _ = sample(tmp_path)
    pm = PM(tree)
    connect(monkeypatch, pm)
    (tmp_path / f"B-{STAMP}.drp").write_bytes(b"old")
    (tmp_path / "A.drp").write_bytes(b"older")   # 日時の無い古い名前は重ならない
    assert P.main(["export", "A", "Case/B", "--out", str(tmp_path)]) == 1
    assert pm.exported == []   # A も書き出さない
    assert (tmp_path / f"B-{STAMP}.drp").read_bytes() == b"old" and not (tmp_path / f"A-{STAMP}.drp").exists()
    out = capsys.readouterr().out
    assert out.startswith("NG projects export: 書き出さずに止めた") and "既にある" in out


def test_export_ng_when_file_is_empty_or_project_missing(tmp_path, monkeypatch, capsys, stamp):
    tree, _, _ = sample(tmp_path)
    pm = PM(tree)
    pm.ExportProject = lambda name, path, s: open(path, "wb").close() or True   # 戻り値は True でも中身が空
    connect(monkeypatch, pm)
    assert P.main(["export", "A", "Nope/X", "--out", str(tmp_path / "d")]) == 1
    out = capsys.readouterr().out
    assert out.startswith("NG projects export: 0/2 件") and "大きさ 0" in out and "フォルダーが無い" in out


# ---------- check ----------
def test_check_counts_pool_and_timelines(out, monkeypatch, capsys):
    tree, a, b = sample(out)
    pm = PM(tree, current=a)
    r = P.check_project(b)
    assert r["media"] == {"total": 2, "missing": 1} and r["timeline"] == {"total": 3, "missing": 1}
    assert [x["where"] for x in r["missing"]] == ["メディアプール rec/gone", "タイムライン T2 audio 1"]
    ra = P.check_project(a)
    assert ra["media"] == {"total": 1, "missing": 0} and ra["timeline"] == {"total": 1, "missing": 0}

    connect(monkeypatch, pm)
    assert P.main(["check", "A"]) == 0
    capsys.readouterr()
    assert P.main(["check", "A", "Case/B"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("NG projects check: 1/2 件") and "check.json" in lines[0]
    assert "  OK A: メディアプール 1/1、タイムラインのクリップ 1/1" in lines
    assert "  NG Case/B: メディアプール 1/2、タイムラインのクリップ 2/3" in lines
    assert sum("見つからない" in x for x in lines) == 2
    assert pm.loaded[-1] == "A" and pm.current is a   # 始めに開いていたものを開き直す
    assert pm.saved == ["A", "A"]   # 切り替える前に、開いていたものを保存する
    assert os.path.isfile(out / "out" / "projects" / "check.json")


def test_check_stops_before_switching_when_save_fails(out, monkeypatch, capsys):
    tree, a, _ = sample(out)
    pm = PM(tree, current=a)
    pm.can_save = False
    connect(monkeypatch, pm)
    assert P.main(["check", "Case/B"]) == 1
    assert pm.saved == ["A"] and pm.loaded == [] and pm.current is a and pm.stack == []
    assert "保存できないので、切り替えずに止めた" in capsys.readouterr().out


def test_check_is_ng_when_reopened_project_has_another_name(out, monkeypatch, capsys):
    """LoadProject が何かを返しても、開いたプロジェクトの名前が始めのものと違えば開き直せていない。"""
    tree, a, _ = sample(out)
    pm = PM(tree, current=a)
    load = pm.LoadProject
    pm.LoadProject = lambda name: Project("other") if name == "A" else load(name)
    connect(monkeypatch, pm)
    assert P.main(["check", "Case/B"]) == 1
    assert "NG 始めに開いていた A を開き直せなかった" in capsys.readouterr().out


def test_check_unknown_project_is_ng(out, monkeypatch, capsys):
    tree, a, _ = sample(out)
    connect(monkeypatch, PM(tree, current=a))
    assert P.main(["check", "B"]) == 1   # B はルートの直下には無い
    assert "NG B: 開けない" in capsys.readouterr().out


def test_export_stops_when_names_collide_ignoring_case(tmp_path, monkeypatch, capsys, stamp):
    tree, _, _ = sample(tmp_path)
    tree["folders"]["Other"] = {"projects": {"b": Project("b")}, "folders": {}}
    monkeypatch.setattr(os.path, "normcase", lambda p: p.lower())   # Windows の扱い (大文字と小文字を区別しない)
    pm = PM(tree)
    connect(monkeypatch, pm)
    assert P.main(["export", "Case/B", "Other/b", "--out", str(tmp_path / "d")]) == 1
    assert pm.exported == []
    out = capsys.readouterr().out
    assert "重なる: Case/B, Other/b" in out


def same_name_tree(tmp_path):
    tree, a, _ = sample(tmp_path)
    a2 = Project("A")
    tree["folders"]["Case"]["projects"]["A"] = a2
    return tree, a, a2


def test_list_does_not_mark_current_when_name_is_not_unique(tmp_path, monkeypatch, capsys):
    tree, a, _ = same_name_tree(tmp_path)
    connect(monkeypatch, PM(tree, current=a))
    assert P.main(["list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert not any("(開いている)" in x for x in lines)
    assert "  A" in lines and "  Case/A" in lines and any("同じ名前が 2 つ" in x for x in lines)


def test_check_stops_before_switching_when_current_name_is_not_unique(out, monkeypatch, capsys):
    tree, a, _ = same_name_tree(out)
    pm = PM(tree, current=a)
    connect(monkeypatch, pm)
    assert P.main(["check", "Case/B"]) == 1
    assert pm.loaded == [] and pm.current is a
    assert "切り替えずに止めた" in capsys.readouterr().out


def test_check_reopens_first_project_even_after_error(out, monkeypatch):
    tree, a, _ = sample(out)
    pm = PM(tree, current=a)
    connect(monkeypatch, pm)

    def broken(p):
        raise RuntimeError("途中で止まった")
    monkeypatch.setattr(P, "check_project", broken)
    with pytest.raises(RuntimeError):
        P.main(["check", "Case/B"])
    assert pm.loaded == ["Case/B", "A"] and pm.current is a and pm.stack == []


def test_check_is_ng_when_first_project_cannot_be_reopened(out, monkeypatch, capsys):
    import json
    tree, a, _ = sample(out)
    tree["folders"]["Case"]["projects"]["B"] = Project("B")   # 素材がそろったプロジェクトでも、戻せなければ 1
    pm = PM(tree, current=a)
    load = pm.LoadProject
    pm.LoadProject = lambda name: None if name == "A" else load(name)   # A に戻せない
    connect(monkeypatch, pm)
    assert P.main(["check", "Case/B"]) == 1
    assert "NG 始めに開いていた A を開き直せなかった" in capsys.readouterr().out
    with open(out / "out" / "projects" / "check.json", encoding="utf-8") as f:
        rec = json.load(f)
    assert rec["reopened"] is None and rec["reopen_failed"] == "A"
    assert rec["projects"]["Case/B"]["media"] == {"total": 0, "missing": 0}
