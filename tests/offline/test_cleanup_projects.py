"""テストで作ったプロジェクトを消すスクリプト (scripts/cleanup_projects.py) のテスト。Resolve の API は模擬のオブジェクトで置き換えるので、Resolve は要らない。"""
import pytest

from conftest import patch_common

import common as C
import cleanup_projects as CP


class Project:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


class PM:
    """ルートのフォルダーだけの模擬。開いているプロジェクトは消せない (Resolve と同じ)。"""

    def __init__(self, names, current=None):
        self.names, self.current = list(names), Project(current) if current else None
        self.loaded, self.deleted, self.saved = [], [], []
        self.ghost = set()   # 消せたと返すのに残る名前

    def GotoRootFolder(self):
        return True

    def GetProjectListInCurrentFolder(self):
        return list(self.names)

    def GetCurrentProject(self):
        return self.current

    def LoadProject(self, name):
        if name not in self.names:
            return None
        self.current = Project(name)
        self.loaded.append(name)
        return self.current

    def SaveProject(self):
        self.saved.append(self.current.GetName())
        return True

    def DeleteProject(self, name):
        if name not in self.names or (self.current and self.current.GetName() == name):
            return False
        self.deleted.append(name)
        if name not in self.ghost:
            self.names.remove(name)
        return True


class Resolve:
    def __init__(self, pm):
        self.pm = pm

    def GetProjectManager(self):
        return self.pm


NAMES = ["my-project", "test-keep", "test-sample", "test-sample-20261025-1000",
         "test-other-20261024-100356"]


def run(monkeypatch, capsys, pm, argv):
    patch_common(monkeypatch, "connect_or_exit", lambda tries=1: Resolve(pm))
    code = CP.main(argv)
    return code, capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("argv", [[], ["my-project"], ["test-a", "*"], ["Test-a"]])
def test_names_without_prefix_stop_before_connecting(argv, monkeypatch):
    patch_common(monkeypatch, "connect_or_exit", lambda tries=1: pytest.fail("接続してはいけない"))
    with pytest.raises(SystemExit) as e:
        CP.main(argv)
    assert e.value.code == 2


def test_deletes_matches_and_keeps_others(monkeypatch, capsys):
    pm = PM(NAMES, current="test-keep")
    code, out = run(monkeypatch, capsys, pm, ["test-sample*"])
    assert code == 0 and out[0].startswith("OK cleanup_projects: 2/2 件を消した")
    assert pm.deleted == ["test-sample", "test-sample-20261025-1000"]
    assert pm.names == ["my-project", "test-keep", "test-other-20261024-100356"]
    assert pm.loaded == [] and pm.saved == []   # 開いているのが対象でなければ切り替えない
    assert "  開いている: test-keep" in out


def test_open_target_is_closed_by_reopening(monkeypatch, capsys):
    pm = PM(NAMES, current="test-sample")
    code, out = run(monkeypatch, capsys, pm, ["test-sample", "--reopen", "test-keep"])
    assert code == 0 and pm.loaded == ["test-keep"] and pm.saved == []
    assert pm.deleted == ["test-sample"]
    assert "  開いている: test-keep" in out


def test_reopen_target_is_never_deleted(monkeypatch, capsys):
    pm = PM(NAMES, current="test-sample")
    code, _ = run(monkeypatch, capsys, pm, ["test-*", "--reopen", "test-keep"])
    assert code == 0
    assert "test-keep" in pm.names and pm.current.GetName() == "test-keep"
    assert pm.names == ["my-project", "test-keep"]


def test_open_target_without_reopen_deletes_nothing(monkeypatch, capsys):
    pm = PM(NAMES, current="test-sample")
    code, out = run(monkeypatch, capsys, pm, ["test-sample*"])
    assert code == 1 and out[0].startswith("NG cleanup_projects: 何も消さずに止めた")
    assert pm.deleted == [] and pm.loaded == []
    assert any("--reopen" in x for x in out)


def test_missing_reopen_deletes_nothing(monkeypatch, capsys):
    pm = PM(NAMES, current="test-sample")
    code, out = run(monkeypatch, capsys, pm, ["test-sample*", "--reopen", "test-nothing"])
    assert code == 1 and out[0].startswith("NG cleanup_projects: 何も消さずに止めた")
    assert pm.deleted == [] and pm.loaded == []


def test_dry_run_changes_nothing(monkeypatch, capsys):
    pm = PM(NAMES, current="test-sample")
    code, out = run(monkeypatch, capsys, pm, ["test-sample*", "test-gone", "--dry-run"])
    assert code == 0 and pm.deleted == [] and pm.loaded == []
    assert out[0].startswith("OK cleanup_projects --dry-run: 2 件を消す")
    assert "  当たらない: test-gone" in out and "  消す: test-sample" in out


def test_no_match_is_not_a_failure(monkeypatch, capsys):
    pm = PM(NAMES)
    code, out = run(monkeypatch, capsys, pm, ["test-gone"])
    assert code == 0 and out[0].startswith("OK cleanup_projects: 0/0 件を消した") and "  当たらない: test-gone" in out


def test_project_left_after_delete_is_reported(monkeypatch, capsys):
    pm = PM(NAMES)
    pm.ghost = {"test-sample"}
    code, out = run(monkeypatch, capsys, pm, ["test-sample"])
    assert code == 1 and "  NG 消せない: test-sample" in out
