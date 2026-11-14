"""引数を読むスクリプトの --help が、説明を出して 0 で終わるか (コマンドとして流す)。
作業フォルダーに Resolve の排他のロックが無くても止まらず、Resolve に接続しない。"""
import os
import subprocess
import sys

import pytest

from conftest import REPO


@pytest.mark.parametrize("script", ["probe.py", "rconsole.py", "wallclock_to_tc.py"])
def test_help_command_exits_zero(tmp_path, script):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", RESOLVE_WORK=str(tmp_path))
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", script), "--help"],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    assert "usage:" in r.stdout
