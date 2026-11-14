"""渡した Python を Resolve に接続して実行するスクリプト (scripts/rconsole.py) のテスト。接続は模擬に置き換えるので、Resolve は要らない。"""
import json
import os
import sys

import pytest

from conftest import patch_common

import rconsole



def _no_resolve(*a, **k):
    raise AssertionError("Resolve に接続しようとした")


def test_help_exits_zero_without_connecting(monkeypatch, capsys):
    # --help は説明を出して 0 で終わる。接続しないので排他のロックも見ない
    patch_common(monkeypatch, "connect_or_exit", _no_resolve)
    with pytest.raises(SystemExit) as e:
        rconsole.main(["--help"])
    assert e.value.code == 0
    assert "実行する .py" in capsys.readouterr().out


def test_no_script_is_usage_error(monkeypatch):
    patch_common(monkeypatch, "connect_or_exit", _no_resolve)
    with pytest.raises(SystemExit) as e:
        rconsole.main([])
    assert e.value.code == 2


class Timeline:
    def GetName(self):
        return "tl"


class Project:
    def GetName(self):
        return "test-x"

    def GetMediaPool(self):
        return "mp"

    def GetCurrentTimeline(self):
        return Timeline()


class PM:
    def __init__(self, proj):
        self.proj = proj

    def GetCurrentProject(self):
        return self.proj


class Resolve:
    def __init__(self, proj):
        self.pm = PM(proj)

    def GetProjectManager(self):
        return self.pm


def _run(monkeypatch, tmp_path, proj, args):
    """渡す .py が見た sys.argv・__file__・用意された名前を JSON に書かせて返す。"""
    out = tmp_path / "seen.json"
    target = tmp_path / "look.py"
    target.write_text(
        "import json, os, sys\n"
        "with open(os.path.join(os.path.dirname(__file__), 'seen.json'), 'w', encoding='utf-8') as fh:\n"
        "    json.dump({'argv': sys.argv, 'file': __file__, 'name': __name__,\n"
        "               'proj': proj.GetName() if proj else None, 'mp': mp, 'tl': tl.GetName() if tl else None,\n"
        "               'has': sorted(k for k in ('C', 'cfg', 'resolve', 'pm') if k in globals())}, fh)\n",
        encoding="utf-8")
    monkeypatch.setattr(sys, "argv", list(sys.argv))   # rconsole が書き換える sys.argv をテストの後に戻す
    patch_common(monkeypatch, "connect_or_exit", lambda tries=1: Resolve(proj))
    rconsole.main([str(target), *args])
    return json.loads(out.read_text(encoding="utf-8")), str(target)


def test_runs_script_with_args(monkeypatch, tmp_path):
    # 渡した .py の後ろの引数は、--help や - で始まるものも含めてその .py の sys.argv[1:] になる
    seen, target = _run(monkeypatch, tmp_path, Project(), ["a", "-v", "--help"])
    assert seen["argv"] == [target, "a", "-v", "--help"]
    assert seen["file"] == os.path.abspath(target)
    assert seen["name"] == "__main__"
    assert (seen["proj"], seen["mp"], seen["tl"]) == ("test-x", "mp", "tl")
    assert seen["has"] == ["C", "cfg", "pm", "resolve"]


def test_no_project(monkeypatch, tmp_path):
    seen, _ = _run(monkeypatch, tmp_path, None, [])
    assert (seen["proj"], seen["mp"], seen["tl"]) == (None, None, None)
