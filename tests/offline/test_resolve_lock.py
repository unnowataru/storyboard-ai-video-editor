"""Resolve を使う順番のロック (scripts/resolve_lock.py) のテスト。作業フォルダーはテスト用の場所 (pytest の tmp_path) にする。

本物の作業フォルダーの resolve.lock には触らない: 関数には work= を、コマンドには環境変数 RESOLVE_WORK を必ず渡す。
"""
import json
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from conftest import patch_common
import resolve_lock as L

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts",
                      "resolve_lock.py")


def env_for(work):
    """ロックの場所をテスト用の作業フォルダーにした環境 (コマンドの子も同じ場所を見る)。
    個人の設定 (config/local.json) を読まないよう、RESOLVE_CONFIG は無いファイルを指す。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", RESOLVE_WORK=str(work),
               RESOLVE_CONFIG=os.path.join(os.path.abspath(str(work)), "no-local.json"))
    env.pop(L.ENV_OWNER, None)
    return env


def cli(work, *args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True,
                          encoding="utf-8", env=env_for(work), timeout=60, check=False)


RACER = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, sys.argv[1])
    import resolve_lock as L
    work, name, holder, n, start = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], float(sys.argv[6])
    while time.time() < start:
        pass
    rc = L.run(name, [sys.executable, holder, work, name, n], work)
    open(os.path.join(work, "done-" + name), "w").close()   # この run が終わった合図
    sys.exit(rc)
""")

# 勝ったコマンド: 流れたことを示すファイルを書き、ほかの run が全部終わるまでロックを持ち続ける (遅れて起きた run に外したロックを取らせない)
HOLDER = textwrap.dedent("""
    import glob, os, sys, time
    work, name, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    open(os.path.join(work, "ran-" + name), "w").close()
    deadline = time.time() + 60
    while len(glob.glob(os.path.join(work, "done-*"))) < n - 1 and time.time() < deadline:
        time.sleep(0.05)
""")


def test_only_one_of_simultaneous_runs_runs(tmp_path):
    scripts = os.path.dirname(SCRIPT)
    holder = tmp_path / "holder.py"
    holder.write_text(HOLDER, encoding="utf-8")
    n = 6
    for rnd in range(3):
        work = tmp_path / f"round{rnd}"
        work.mkdir()
        start = time.time() + 1.5
        procs = [subprocess.Popen([sys.executable, "-c", RACER, scripts, str(work), f"p{i}", str(holder), str(n),
                                   str(start)], stderr=subprocess.PIPE, text=True, encoding="utf-8") for i in range(n)]
        errs = [p.communicate(timeout=60)[1] for p in procs]
        ran = sorted(n for n in os.listdir(work) if n.startswith("ran-"))
        assert len(ran) == 1, errs
        assert [p.returncode for p in procs].count(0) == 1
        assert sum("使用中" in e for e in errs) == 5
        assert L.read_state(str(work))["state"] == "free"


def test_run_returns_command_code_and_releases(tmp_path):
    r = cli(tmp_path, "run", "--owner", "runner", "--", sys.executable, "-c",
            "import os, sys; print(os.environ['RESOLVE_LOCK_OWNER']); sys.exit(7)")
    assert r.returncode == 7, r.stderr
    assert r.stdout.strip() == "runner"   # ロックのことは標準エラーに出し、コマンドの標準出力は汚さない
    assert L.read_state(str(tmp_path))["state"] == "free"


def test_run_releases_after_exception_in_command(tmp_path):
    r = cli(tmp_path, "run", "--owner", "runner", "--", sys.executable, "-c", "raise RuntimeError('boom')")
    assert r.returncode == 1
    assert L.read_state(str(tmp_path))["state"] == "free"


def test_run_releases_when_command_cannot_start(tmp_path):
    r = cli(tmp_path, "run", "--owner", "runner", "--", "no-such-command-xyz")
    assert r.returncode == 127
    assert L.read_state(str(tmp_path))["state"] == "free"


def test_run_while_busy_exits_at_once(tmp_path):
    assert L.take("other", str(tmp_path))
    flag = tmp_path / "ran.txt"
    t0 = time.monotonic()
    r = cli(tmp_path, "run", "--owner", "runner", "--", sys.executable, "-c", f"open(r'{flag}', 'w').close()")
    assert r.returncode == 1
    assert time.monotonic() - t0 < 20
    assert "使用中: other" in r.stderr
    assert not flag.exists()
    assert L.read_state(str(tmp_path))["owner"] == "other"


@pytest.mark.skipif(os.name != "nt", reason="Ctrl+Break を送るのは Windows だけ")
def test_run_keeps_lock_after_interrupt_even_if_child_ends(tmp_path):
    # 中断の合図 (Ctrl+Break) はコマンドにも届き、コマンドはすぐ終わる。それでもロックは残し、130 で終わる
    # (子が終わっても、その先の孫が Resolve を使い続けているかは分からないため)
    p = subprocess.Popen([sys.executable, SCRIPT, "run", "--owner", "runner", "--",
                          sys.executable, "-c", "import time; time.sleep(60)"], stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", env=env_for(tmp_path), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    deadline = time.time() + 20
    while L.read_state(str(tmp_path))["state"] == "free" and time.time() < deadline:
        time.sleep(0.1)
    time.sleep(1)
    t0 = time.monotonic()
    p.send_signal(signal.CTRL_BREAK_EVENT)
    _, err = p.communicate(timeout=60)
    assert p.returncode == L.EXIT_INTERRUPTED, err
    assert time.monotonic() - t0 < L.CHILD_EXIT_WAIT   # 子が終われば 15 秒を待たずに返る
    assert "中断したので resolve.lock を残した" in err and "status" in err
    assert L.read_state(str(tmp_path))["owner"] == "runner"


IGNORER = textwrap.dedent("""
    import os, signal, sys, time
    for s in (signal.SIGINT, signal.SIGBREAK):
        signal.signal(s, signal.SIG_IGN)
    with open(sys.argv[1], "w") as f:
        f.write(str(os.getpid()))
    time.sleep(120)
""")


@pytest.mark.skipif(os.name != "nt", reason="Ctrl+Break を送るのは Windows だけ")
def test_run_returns_when_command_ignores_interrupt(tmp_path):
    # 中断の合図を無視するコマンドでも、CHILD_EXIT_WAIT (15 秒) で待つのをやめ、ロックを残して 130 で終わる
    work = tmp_path / "w"
    ignorer, ready = tmp_path / "ignorer.py", tmp_path / "ready"
    ignorer.write_text(IGNORER, encoding="utf-8")
    # 標準エラーはファイルに受ける (残った子がパイプを持ち続けるので、パイプでは読み終わらない)
    log = tmp_path / "stderr.txt"
    with open(log, "w", encoding="utf-8") as f:
        p = subprocess.Popen([sys.executable, SCRIPT, "run", "--owner", "runner", "--",
                              sys.executable, str(ignorer), str(ready)], stderr=f, env=env_for(work),
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    child = None
    try:
        deadline = time.time() + 30
        while not (ready.exists() and ready.read_text()) and time.time() < deadline:
            time.sleep(0.1)
        child = int(ready.read_text())
        t0 = time.monotonic()
        p.send_signal(signal.CTRL_BREAK_EVENT)
        p.wait(timeout=L.CHILD_EXIT_WAIT + 20)
        took = time.monotonic() - t0
        err = log.read_text(encoding="utf-8")
        assert p.returncode == L.EXIT_INTERRUPTED, err
        assert L.CHILD_EXIT_WAIT - 1 <= took < L.CHILD_EXIT_WAIT + 10
        assert "中断したので resolve.lock を残した" in err
        assert L.read_state(str(work))["owner"] == "runner"
    finally:
        if p.poll() is None:
            p.kill()
        if child:
            subprocess.run(["taskkill", "/F", "/PID", str(child)], capture_output=True, check=False)


def test_owner_write_failure_does_not_run_command(tmp_path, monkeypatch, capsys):
    def broken(path, text):
        raise OSError("書けない (テスト)")

    monkeypatch.setattr(L, "_write_owner", broken)
    flag = tmp_path / "ran.txt"
    rc = L.run("runner", [sys.executable, "-c", f"open(r'{flag}', 'w').close()"], str(tmp_path))
    assert rc == 1
    assert not flag.exists()
    assert L.read_state(str(tmp_path))["state"] == "free"   # 中身の無いロックを残さない
    assert "片付けた" in capsys.readouterr().err


def no_rmtree(monkeypatch):
    """ロックを消せない場面を作る (resolve_lock の中の shutil.rmtree を、例外を出すものに置き換える)。"""
    def fail(*a, **k):
        raise PermissionError("消せない (テスト)")

    monkeypatch.setattr(L, "shutil", type("NoRmtree", (), {"rmtree": staticmethod(fail)}))


def test_owner_write_failure_reports_left_lock(tmp_path, monkeypatch, capsys):
    def broken(path, text):
        raise OSError("書けない (テスト)")

    monkeypatch.setattr(L, "_write_owner", broken)
    no_rmtree(monkeypatch)
    assert L.run("runner", [sys.executable, "-c", "pass"], str(tmp_path)) == 1
    assert (tmp_path / L.LOCK_NAME).is_dir()
    err = capsys.readouterr().err
    assert "消せずに残った" in err and "消せない (テスト)" in err and "片付けた" not in err


def test_give_back_failure_reports_left_lock(tmp_path, monkeypatch, capsys):
    no_rmtree(monkeypatch)
    assert L.run("runner", [sys.executable, "-c", "pass"], str(tmp_path)) == 0
    assert L.read_state(str(tmp_path))["owner"] == "runner"
    err = capsys.readouterr().err
    assert "消せずに残った" in err and "消せない (テスト)" in err and "返した" not in err


def test_wrapped_resolve_test_sees_same_lock(tmp_path):
    # ロックの場所は RESOLVE_WORK だけで決まり、包んだコマンド (tests/resolve の require_lock) も同じロックを見る
    helpers = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resolve")
    code = f"import sys; sys.path.insert(0, r'{helpers}'); from resolve_helpers import require_lock; require_lock(); print('ok')"
    r = cli(tmp_path, "run", "--owner", "runner", "--", sys.executable, "-c", code)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                       env=env_for(tmp_path), check=False)
    assert r.returncode == 0 and r.stdout.strip() == "ok"   # 包まなくても、既定では警告を出して先へ進む
    assert "警告" in r.stderr and "resolve_lock.py run で包んで" in r.stderr
    local = tmp_path / "local.json"
    local.write_text(json.dumps({"resolve": {"require_lock": True}}), encoding="utf-8")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                       env=dict(env_for(tmp_path), RESOLVE_CONFIG=str(local)), check=False)
    assert r.returncode == 1 and "resolve_lock.py run で包んで" in r.stderr   # require_lock が true なら接続の前に終わる


def test_relative_work_dir_is_passed_as_absolute(tmp_path):
    # RESOLVE_WORK が相対パスでも、子にはロックを取った作業フォルダーの絶対パスが渡り、子が cd しても同じロックを見る
    helpers = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resolve")
    other = tmp_path / "other"
    other.mkdir()
    code = (f"import os, sys; print(os.environ['RESOLVE_WORK']); os.chdir(r'{other}'); "
            f"sys.path.insert(0, r'{helpers}'); from resolve_helpers import require_lock; require_lock(); print('ok')")
    r = subprocess.run([sys.executable, SCRIPT, "run", "--owner", "runner", "--", sys.executable, "-c", code],
                       capture_output=True, text=True, encoding="utf-8", env=env_for("w"), cwd=tmp_path,
                       timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    passed, ok = r.stdout.strip().splitlines()
    assert os.path.isabs(passed) and os.path.samefile(passed, tmp_path / "w")
    assert ok == "ok"
    assert L.read_state(str(tmp_path / "w"))["state"] == "free"


# ---------- 中断の時機 (合図は signal.raise_signal で、このプロセスに送る) ----------
class FakeProc:
    """コマンドのプロセスの代わり。wait と poll で中断の合図を送れる。"""

    def __init__(self, interrupt_on=(), done=True):
        self.pid = 4242
        self.interrupt_on = set(interrupt_on)
        self.done = done

    def _maybe(self, name):
        if name in self.interrupt_on:
            self.interrupt_on.discard(name)
            signal.raise_signal(signal.SIGINT)

    def wait(self, timeout=None):
        self._maybe("wait")
        if not self.done:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def poll(self):
        self._maybe("poll")
        return 0 if self.done else None


def fake_popen(monkeypatch, proc, interrupt_in_start=False, start_error=None):
    def popen(*a, **k):
        if interrupt_in_start:
            signal.raise_signal(signal.SIGINT)
        if start_error:
            raise start_error
        return proc

    monkeypatch.setattr(L.subprocess, "Popen", popen)


def test_interrupt_before_start_releases(tmp_path, monkeypatch):
    # ロックを取った直後 (コマンドを起動する前) の中断は、子がいないのでロックを外し、コマンドは流さない
    real = L._write_owner

    def write_then_interrupt(path, text):
        real(path, text)
        signal.raise_signal(signal.SIGINT)

    monkeypatch.setattr(L, "_write_owner", write_then_interrupt)
    flag = tmp_path / "ran.txt"
    rc = L.run("runner", [sys.executable, "-c", f"open(r'{flag}', 'w').close()"], str(tmp_path))
    assert rc == L.EXIT_INTERRUPTED
    assert not flag.exists()
    assert L.read_state(str(tmp_path))["state"] == "free"


def test_interrupt_during_start_keeps_lock(tmp_path, monkeypatch, capsys):
    # 起動の途中の中断: 起動した子がすぐ終わっても、ロックは残す
    fake_popen(monkeypatch, FakeProc(), interrupt_in_start=True)
    assert L.run("runner", ["x"], str(tmp_path)) == L.EXIT_INTERRUPTED
    assert L.read_state(str(tmp_path))["owner"] == "runner"
    assert "中断したので resolve.lock を残した" in capsys.readouterr().err


def test_interrupt_while_running_keeps_lock_even_if_child_ends(tmp_path, monkeypatch, capsys):
    proc = FakeProc(interrupt_on=("wait",), done=False)
    calls = {"n": 0}

    def poll():
        calls["n"] += 1
        return 0   # 中断の後、子はすぐ終わる

    proc.poll = poll
    fake_popen(monkeypatch, proc)
    assert L.run("runner", ["x"], str(tmp_path)) == L.EXIT_INTERRUPTED
    assert calls["n"] >= 1
    assert L.read_state(str(tmp_path))["owner"] == "runner"
    assert "中断したので resolve.lock を残した" in capsys.readouterr().err


def test_second_interrupt_stops_waiting_and_keeps_lock(tmp_path, monkeypatch, capsys):
    # 1 回目の中断 (wait の中) の後、子の終わりを待っている間 (poll) にもう一度中断されたら、待つのをやめる
    t0 = time.monotonic()
    fake_popen(monkeypatch, FakeProc(interrupt_on=("wait", "poll"), done=False))
    assert L.run("runner", ["x"], str(tmp_path)) == L.EXIT_INTERRUPTED
    assert time.monotonic() - t0 < L.CHILD_EXIT_WAIT
    assert L.read_state(str(tmp_path))["owner"] == "runner"
    assert "残した" in capsys.readouterr().err


def test_child_still_running_after_interrupt_keeps_lock(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(L, "CHILD_EXIT_WAIT", 0.2)
    fake_popen(monkeypatch, FakeProc(interrupt_on=("wait",), done=False))
    assert L.run("runner", ["x"], str(tmp_path)) == L.EXIT_INTERRUPTED
    assert L.read_state(str(tmp_path))["owner"] == "runner"
    assert "残した" in capsys.readouterr().err


def test_unknown_error_in_start_keeps_lock(tmp_path, monkeypatch, capsys):
    # 起動が OSError 以外で止まったら、子があるか分からないのでロックを残す
    fake_popen(monkeypatch, None, start_error=RuntimeError("起動の途中で止まった (テスト)"))
    with pytest.raises(RuntimeError):
        L.run("runner", ["x"], str(tmp_path))
    assert L.read_state(str(tmp_path))["owner"] == "runner"
    assert "起動か待つ途中で止まった" in capsys.readouterr().err


def test_signal_handlers_restored(tmp_path):
    before = signal.getsignal(signal.SIGINT)
    assert L.run("runner", [sys.executable, "-c", "pass"], str(tmp_path)) == 0
    assert signal.getsignal(signal.SIGINT) is before


def test_interrupt_during_give_back_finishes_cleanup(tmp_path, monkeypatch):
    # 正常に終わった後、ロックを消している最中の合図では片付けが止まらず、最後まで消してから合図の扱いが元に戻る
    before = signal.getsignal(signal.SIGINT)
    real = L.give_back
    seen = {}

    def give_back_with_interrupt(text, work=None):
        signal.raise_signal(signal.SIGINT)
        seen["handler_during"] = signal.getsignal(signal.SIGINT)
        return real(text, work)

    monkeypatch.setattr(L, "give_back", give_back_with_interrupt)
    assert L.run("runner", [sys.executable, "-c", "pass"], str(tmp_path)) == 0
    assert seen["handler_during"] is not before   # 片付けの間は run が受け持っている
    assert L.read_state(str(tmp_path))["state"] == "free"
    assert signal.getsignal(signal.SIGINT) is before


def test_give_back_only_own(tmp_path):
    mine = L.take("me", str(tmp_path))
    assert mine and L.take("you", str(tmp_path)) is None
    assert L.give_back("you 2026-10-15 10:00:00\n", str(tmp_path)) == "not_mine"
    assert L.read_state(str(tmp_path))["owner"] == "me"
    assert L.give_back(mine, str(tmp_path)) == "released"
    assert L.read_state(str(tmp_path))["state"] == "free"


def test_run_keeps_lock_replaced_by_someone_else(tmp_path):
    # コマンドの中でロックが他人のものに置き換わったら、それは消さない
    script = ("import os, shutil, sys; p = os.path.join(sys.argv[1], 'resolve.lock'); shutil.rmtree(p); os.mkdir(p); "
              "open(os.path.join(p, 'owner.txt'), 'w').write('other 2026-10-15 10:00:00\\n')")
    r = cli(tmp_path, "run", "--owner", "runner", "--", sys.executable, "-c", script, str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert L.read_state(str(tmp_path))["owner"] == "other"


def test_status(tmp_path):
    r = cli(tmp_path, "status")
    assert r.returncode == 0 and "空いている" in r.stdout
    os.mkdir(tmp_path / L.LOCK_NAME)
    r = cli(tmp_path, "status")
    assert r.returncode == 1 and "使用中・持ち主不明" in r.stdout and "作られてから" in r.stdout
    (tmp_path / L.LOCK_NAME / L.OWNER_FILE).write_text("", encoding="utf-8")
    assert "使用中・持ち主不明" in cli(tmp_path, "status").stdout
    (tmp_path / L.LOCK_NAME / L.OWNER_FILE).write_text("test-lock 2026-10-15 10:40:47\n", encoding="utf-8")
    r = cli(tmp_path, "status")
    assert r.returncode == 1 and "使用中: test-lock (2026-10-15 10:40:47 から" in r.stdout
    st = json.loads(cli(tmp_path, "status", "--json").stdout)
    assert st["state"] == "busy" and st["owner"] == "test-lock"


def test_parse_owner():
    assert L.parse_owner("test-lock 2026-10-15 10:40:47\n") == ("test-lock", "2026-10-15 10:40:47")
    assert L.parse_owner("名前 に 空白 2026-10-15 10:40:47") == ("名前 に 空白", "2026-10-15 10:40:47")
    assert L.parse_owner("名前だけ") == ("名前だけ", None)
    assert L.parse_owner("") == (None, None)


# ---------- Resolve に接続する前の検査 (common.connect_resolve) ----------
def test_require_lock_is_off_by_default():
    import common as C
    assert C._read_json(C.DEFAULTS)["resolve"]["require_lock"] is False


def test_connect_resolve_warns_without_lock(tmp_path, monkeypatch, capsys):
    """resolve.require_lock が false (既定) なら、run で包まれていなくても警告を出して接続する。ロックは取らない・消さない。"""
    import common as C
    monkeypatch.setattr(L, "lock_path", lambda work=None: str(tmp_path / L.LOCK_NAME))
    monkeypatch.setattr(L, "lock_required", lambda: False)
    resolve = object()
    patch_common(monkeypatch, "import_dvr", lambda: type("DVR", (), {"scriptapp": staticmethod(lambda name: resolve)}))
    monkeypatch.delenv(L.ENV_OWNER, raising=False)
    assert C.connect_resolve(quiet=True) is resolve
    err = capsys.readouterr().err
    assert "警告 resolve.lock が無い" in err and "resolve.require_lock" in err
    assert L.read_state(str(tmp_path))["state"] == "free"
    lock = tmp_path / L.LOCK_NAME
    lock.mkdir()
    (lock / L.OWNER_FILE).write_text("other 2026-10-19 10:00:00\n", encoding="utf-8")
    monkeypatch.setenv(L.ENV_OWNER, "me")
    assert C.connect_resolve(quiet=True) is resolve
    assert "持ち主 (other)" in capsys.readouterr().err
    assert (lock / L.OWNER_FILE).read_text(encoding="utf-8").startswith("other ")


def test_connect_resolve_needs_own_lock(tmp_path, monkeypatch):
    """resolve.require_lock が true なら、common.connect_resolve は run で包まれロックの持ち主がこの処理
    (RESOLVE_LOCK_OWNER) のときだけ接続する。ロックなし・持ち主不明・持ち主違い・包まれていないときは、
    DaVinciResolveScript を読む前に終わり、ロックは消さない。"""
    import common as C
    monkeypatch.setattr(L, "lock_path", lambda work=None: str(tmp_path / L.LOCK_NAME))
    monkeypatch.setattr(L, "lock_required", lambda: True)
    resolve, calls = object(), []

    def fake_dvr():
        calls.append(1)
        return type("DVR", (), {"scriptapp": staticmethod(lambda name: resolve)})
    patch_common(monkeypatch, "import_dvr", fake_dvr)
    monkeypatch.setenv(L.ENV_OWNER, "me")

    def refused():
        with pytest.raises(SystemExit) as e:
            C.connect_resolve(quiet=True)
        return str(e.value.code)
    assert "resolve.lock が無い" in refused()
    # 今のフォルダーとスクリプトが別のドライブにあると relpath が ValueError になる。そのときも案内で止まる
    real_relpath = os.path.relpath

    def other_drive(p, *a):
        raise ValueError("path is on mount 'C:', start on mount 'D:'")
    monkeypatch.setattr(os.path, "relpath", other_drive)
    assert "resolve.lock が無い" in refused()
    monkeypatch.setattr(os.path, "relpath", real_relpath)
    lock = tmp_path / L.LOCK_NAME
    lock.mkdir()
    assert "持ち主が分からない" in refused()                     # owner.txt が無い
    (lock / L.OWNER_FILE).write_text("", encoding="utf-8")
    assert "持ち主が分からない" in refused()                     # owner.txt が空
    (lock / L.OWNER_FILE).write_text("other 2026-10-19 10:00:00\n", encoding="utf-8")
    assert "持ち主 (other)" in refused()                         # 持ち主違い
    monkeypatch.delenv(L.ENV_OWNER)
    assert "包まれていない" in refused()
    assert calls == [] and (lock / L.OWNER_FILE).is_file()
    monkeypatch.setenv(L.ENV_OWNER, "me")
    (lock / L.OWNER_FILE).write_text("me 2026-10-19 10:00:00\n", encoding="utf-8")
    assert C.connect_resolve(quiet=True) is resolve and calls == [1]   # 一致
    assert C.connect_or_exit() is resolve


def test_check_final_no_resolve_needs_no_lock(tmp_path, monkeypatch):
    """check_final.py --no-resolve は Resolve に接続しないので、ロックが無くても今までどおり動く (sb2resolve.py --dry-run は
    test_sb2resolve.py で、接続しないことを確認している)。"""
    import check_final as CF
    import common as C
    monkeypatch.setattr(L, "lock_path", lambda work=None: str(tmp_path / L.LOCK_NAME))
    monkeypatch.delenv(L.ENV_OWNER, raising=False)
    patch_common(monkeypatch, "import_dvr", lambda: pytest.fail("接続しようとした"))
    monkeypatch.setattr(CF, "write_report", lambda cfg, report, name: "failed=0")
    sb = os.path.join(C.REPO, "tests", "trial-storyboard.json")
    monkeypatch.setattr(sys, "argv", ["check_final.py", sb, "--source", str(tmp_path / "none.mp4"), "--no-resolve"])
    with pytest.raises(SystemExit) as e:
        CF.main()
    assert e.value.code == 1   # 動画もタイムラインも無いので unknown (ロックが無いことでは止まらない)
