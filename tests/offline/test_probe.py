"""Resolve への接続を確認するスクリプト (scripts/probe.py) のテスト。接続は模擬に置き換えるので、Resolve は要らない。"""

import pytest

from conftest import patch_common

import probe



def _no_resolve(*a, **k):
    raise AssertionError("Resolve に接続しようとした")


def test_help_exits_zero_without_connecting(monkeypatch, capsys):
    # --help は説明を出して 0 で終わる。Resolve の API も読み込まない
    patch_common(monkeypatch, "import_dvr", _no_resolve)
    patch_common(monkeypatch, "connect_resolve", _no_resolve)
    with pytest.raises(SystemExit) as e:
        probe.main(["--help"])
    assert e.value.code == 0
    assert "試す回数" in capsys.readouterr().out


class Project:
    def GetName(self):
        return "test-x"


class PM:
    def GetCurrentProject(self):
        return Project()


class Resolve:
    def GetProductName(self):
        return "DaVinci Resolve Studio"

    def GetVersionString(self):
        return "21.1.1"

    def IsStudio(self):
        return True

    def GetProjectManager(self):
        return PM()


@pytest.mark.parametrize("argv, tries", [([], 1), (["3"], 3)])
def test_tries_and_output(monkeypatch, capsys, argv, tries):
    # 試す回数は位置の引数 (既定 1)。接続できたら製品とプロジェクトを出す
    got = []
    patch_common(monkeypatch, "import_dvr", lambda: "dvr")
    patch_common(monkeypatch, "connect_resolve", lambda n: got.append(n) or Resolve())
    probe.main(argv)
    assert got == [tries]
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "module dvr"
    assert out[3] == "product DaVinci Resolve Studio 21.1.1 studio True"
    assert out[4] == "project test-x"


def test_no_resolve_exits_one(monkeypatch, capsys):
    patch_common(monkeypatch, "import_dvr", lambda: "dvr")
    patch_common(monkeypatch, "connect_resolve", lambda n: None)
    with pytest.raises(SystemExit) as e:
        probe.main([])
    assert e.value.code == 1
    assert "-> None" in capsys.readouterr().out


def test_bad_tries_is_usage_error(monkeypatch):
    # 数でない回数は、接続の前に引数の誤り (2) で終わる
    patch_common(monkeypatch, "import_dvr", _no_resolve)
    patch_common(monkeypatch, "connect_resolve", _no_resolve)
    with pytest.raises(SystemExit) as e:
        probe.main(["abc"])
    assert e.value.code == 2
