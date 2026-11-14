"""Resolve を使う順番を守るロック (作業フォルダーの resolve.lock) を取ってコマンドを流す・ロックを見る。

使い方:
  python scripts/resolve_lock.py run --owner 名前 -- コマンド...
      ロックを取ってからコマンドを前景で流し、コマンドが終わったら (失敗・例外でも) その run が取ったロックを外す。
      終了コードはコマンドのもの。ロックが既にあれば「使用中」と持ち主を出し、コマンドを流さずにすぐ 1 で終わる (待たない)。
      コマンドには環境変数 RESOLVE_LOCK_OWNER=名前 を渡す。common.connect_resolve と tests/resolve のテストは、
      これとロックの owner.txt の持ち主が同じでなければ警告を出して接続する。config の resolve.require_lock が
      true なら、警告ではなく接続する前に終わる (require_lock)
  python scripts/resolve_lock.py status [--json]
      空いている (ロックのフォルダーが無い) なら 0。ロックがあれば「使用中」と持ち主・時刻を出して 1。
      owner.txt が無いか読めないロックも「使用中・持ち主不明」

ロックの形 (このロックを使うほかの処理も同じ形で読み書きする):
- 作業フォルダー (config の work_dir、環境変数 RESOLVE_WORK が優先) に resolve.lock フォルダーがあれば使用中
- 取るときは mkdir を 1 回だけ行い、成功したらすぐに中の owner.txt に「名前 YYYY-MM-DD HH:MM:SS」の 1 行を書く。
  書けなければコマンドを流さず、ロックを片付けて終わる
- 外すときはフォルダーごと消す。消すのは、owner.txt が自分の書いた中身のままのときだけ

コマンドを起動した後に Ctrl+C などで中断されたら、ロックは残して 130 で終わる (コマンドが終わるのは最大 15 秒待つ)。
コマンドの子や孫が Resolve を使い続けているかは分からないため。起動する前の中断ならロックを外す。
ロックを消しきれなかったときは「残った」と出す。
持ち主不明のロックや、止まった処理のロックは、このスクリプトでは消さない。Resolve を使う処理と空くのを待つ処理がすべて止まっていることを確認してから、人が消す。
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

import common as C

LOCK_NAME = "resolve.lock"
OWNER_FILE = "owner.txt"
TIME_FMT = "%Y-%m-%d %H:%M:%S"
ENV_OWNER = "RESOLVE_LOCK_OWNER"
EXIT_INTERRUPTED = 130
CHILD_EXIT_WAIT = 15            # 中断のあと、コマンドのプロセスが終わるのを待つ秒数
WAIT_STEP = 0.2                 # コマンドを待つ間に中断の合図を見る間隔 (秒)


def work_dir(work=None):
    """ロックを置く作業フォルダー。省略すると config の work_dir。"""
    if work:
        os.makedirs(work, exist_ok=True)
        return os.path.realpath(work)
    return C.config().work_dir()


def lock_path(work=None):
    return os.path.join(work_dir(work), LOCK_NAME)


def check_owner(owner):
    owner = (owner or "").strip()
    if not owner or "\n" in owner or "\r" in owner:
        raise ValueError("持ち主の名前は空でない 1 行の文字列")
    return owner


def parse_owner(text):
    """owner.txt の 1 行目「名前 YYYY-MM-DD HH:MM:SS」から (名前, 時刻の文字列か None)。読めなければ (None, None)。"""
    first = text.splitlines()[0].strip() if text and text.strip() else ""
    if not first:
        return None, None
    head, _, tail = first.rpartition(" ")
    name, _, day = head.rpartition(" ")
    try:
        time.strptime(f"{day} {tail}", TIME_FMT)
        if name.strip():
            return name.strip(), f"{day} {tail}"
    except ValueError:
        pass
    return first, None


def _minutes(sec):
    return f"{int(sec)} 秒" if sec < 60 else f"{int(sec // 60)} 分"


def read_state(work=None):
    """ロックの状態。state は free (ロックのフォルダーが無い) か busy (ある)。busy で owner が None なら持ち主不明。"""
    path = lock_path(work)
    out = {"state": "free", "path": path, "owner": None, "since": None, "age_seconds": None}
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return out
    except OSError:
        out["state"] = "busy"   # 見えているが調べられない。空いているとはみなさない
        return out
    out["state"] = "busy"
    text = None
    try:
        with open(os.path.join(path, OWNER_FILE), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        pass
    owner, since = parse_owner(text)
    if owner and since:
        t = time.mktime(time.strptime(since, TIME_FMT))   # PC の時刻 (ロックを書くほかの処理と同じ)
    else:
        t = getattr(st, "st_birthtime", None) or (st.st_ctime if os.name == "nt" else st.st_mtime)
        since = time.strftime(TIME_FMT, time.localtime(t))
    out.update(owner=owner, since=since, age_seconds=max(0.0, round(time.time() - t, 1)))
    return out


def describe(st):
    if st["state"] == "free":
        return "空いている"
    if st["age_seconds"] is None:
        return "使用中・持ち主不明 (ロックを調べられない)"
    if st["owner"]:
        return f"使用中: {st['owner']} ({st['since']} から {_minutes(st['age_seconds'])})"
    return f"使用中・持ち主不明 (owner.txt が無いか読めない。作られてから {_minutes(st['age_seconds'])})"


def lock_problem(work=None):
    """この処理が run で包まれていて、ロックの持ち主が run の渡した RESOLVE_LOCK_OWNER と同じか。
    同じなら None、違えば (理由の文, read_state の値)。環境変数だけでは信じず、ロックの owner.txt と照らす。"""
    owner = os.environ.get(ENV_OWNER)
    st = read_state(work)
    if st["state"] != "busy":
        return "resolve.lock が無い", st
    if not st["owner"]:
        return "resolve.lock の持ち主が分からない", st
    if not owner:
        return f"run で包まれていない ({ENV_OWNER} が無い)", st
    if st["owner"] != owner:
        return f"resolve.lock の持ち主 ({st['owner']}) が、この処理の {ENV_OWNER} ({owner}) と違う", st
    return None


def lock_required():
    """config の resolve.require_lock (既定 false)。true なら、run で包まれていない処理は Resolve に接続しない。"""
    return C.config().section("resolve").get("require_lock") is True


def require_lock():
    """run で包まれていなければ (lock_problem)、警告を出す。ロックは消さない・取らない。
    config の resolve.require_lock が true なら、警告ではなく Resolve に接続する前に終わる (SystemExit)。
    common.connect_resolve と tests/resolve のテストが呼ぶ。"""
    bad = lock_problem()
    if not bad:
        return
    why, st = bad
    try:
        script = os.path.relpath(sys.argv[0])
    except ValueError:   # 今のフォルダーとスクリプトが別のドライブにある
        script = sys.argv[0]
    hint = (f"Resolve に接続する処理は resolve_lock.py run で包んで動かす: python scripts/resolve_lock.py run "
            f"--owner 名前 -- python {script} ...  (今の resolve.lock: {describe(st)})")
    if lock_required():
        sys.exit(f"NG {why}。{hint}")
    print(f"警告 {why}。ほかの処理が同時に Resolve を使うと、操作が混ざる。{hint}。"
          f"包まれていないときに接続させないなら、config の resolve.require_lock を true にする", file=sys.stderr)


def _write_owner(path, text):
    with open(os.path.join(path, OWNER_FILE), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def take(owner, work=None):
    """ロックを 1 回だけ取ってみる。取れたら owner.txt に書いた中身 (外すときの照合に使う)、使用中なら None。
    owner.txt を書けなければロックを片付けて OSError (片付けで例外が出たら、その例外を lock_left に入れる)。
    中身は mkdir の前に作り、mkdir と書き込みの間に処理を挟まない。"""
    owner = check_owner(owner)
    path = lock_path(work)
    text = f"{owner} {time.strftime(TIME_FMT)}\n"
    try:
        os.mkdir(path)
    except FileExistsError:
        return None
    try:
        _write_owner(path, text)
    except BaseException as e:
        try:
            shutil.rmtree(path)
        except OSError as rm:
            e.lock_left = rm
        raise
    return text


def give_back(text, work=None):
    """take が返した中身のままのロックだけを消す。released (消した) か not_mine (無いか、中身が違う他人のロック) を返す。
    消している途中で失敗したら OSError (ロックは残っている)。"""
    path = lock_path(work)
    try:
        with open(os.path.join(path, OWNER_FILE), encoding="utf-8") as f:
            if f.read() != text:
                return "not_mine"
    except OSError:
        return "not_mine"
    shutil.rmtree(path)
    return "released"


class _Signals:
    """run の間だけ中断の合図 (Ctrl+C、Ctrl+Break、終了の合図) を受け持つ。受けたら数えるだけで、例外は投げない。
    そのため、ロックを取る・書く・外す途中で処理が止まることはない。合図を見るのは、コマンドを待つ合間だけ。"""

    NAMES = ("SIGINT", "SIGTERM", "SIGBREAK")

    def __init__(self):
        self.old = {}
        self.hits = 0

    def _handle(self, signum, frame):
        self.hits += 1

    def install(self):
        for name in self.NAMES:
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    self.old[sig] = signal.signal(sig, self._handle)
                except (ValueError, OSError):   # 主スレッドでないときなど。受け持てない
                    pass
        return self

    def restore(self):
        for sig, h in self.old.items():
            signal.signal(sig, h)
        self.old = {}


LEFT_BY_INTERRUPT = ("NG 中断したので resolve.lock を残した。コマンドとその子 (Resolve を使う処理) が止まったことを"
                     "確認してから人が消す。持ち主は status で見る")


def run(owner, cmd, work=None, err=None):
    """ロックを取って cmd を前景で流し、終了コードを返す。使用中・ロックを書けないときは 1 で、cmd は流さない。

    ロックを外すのは、コマンドが終わったとき、起動に失敗したとき、起動する前に中断されたときだけ。
    コマンドを起動した後に中断されたら、ロックは残して 130 で終わる (子が終わっても、その先の孫が Resolve を使い
    続けているかは分からないため)。中断の後はコマンドが終わるのを最大 CHILD_EXIT_WAIT 秒待つ。
    """
    err = sys.stderr if err is None else err
    owner = check_owner(owner)

    def say(msg):
        print(msg, file=err, flush=True)

    sigs = _Signals().install()
    text = None
    proc = None
    release = True   # ロックを外してよいか。コマンドを起動してから、正常に終わるか起動に失敗するまでは False
    try:
        try:
            text = take(owner, work)
        except OSError as e:
            left = getattr(e, "lock_left", None)
            if left is not None:
                say(f"NG resolve.lock の owner.txt を書けず、ロックを消せずに残った。人が消す: {e} / 消せない理由: {left}")
            else:
                say(f"NG resolve.lock の owner.txt を書けないので、コマンドを流さずにロックを片付けた: {e}")
            return 1
        if text is None:
            say(f"NG resolve.lock は{describe(read_state(work))}。待たずに終わる。空いてから run を呼び直す")
            return 1
        say(f"resolve.lock を取った ({owner})")
        if sigs.hits:
            return EXIT_INTERRUPTED   # 起動する前に中断された。子はいないのでロックは外す
        release = False
        try:
            # 作業フォルダーは絶対パスで渡す (相対の RESOLVE_WORK のまま渡すと、子が cd したときに別のロックを見る)
            proc = subprocess.Popen(cmd, env=dict(os.environ, **{ENV_OWNER: owner, "RESOLVE_WORK": work_dir(work)}))
        except OSError as e:
            release = True   # 起動に失敗した。子はいない
            say(f"コマンドを起動できない: {e}")
            return 127
        # 時間を決めない wait は Windows では合図で戻らないので、短い時間の wait をくり返して合図を見る
        while True:
            try:
                rc = proc.wait(timeout=WAIT_STEP)
                break
            except subprocess.TimeoutExpired:
                pass
            if sigs.hits:
                # 中断の合図はコマンドにも届いている。終わるのを少し待つ (もう一度中断されたらやめる)。ロックは残す
                seen, deadline = sigs.hits, time.monotonic() + CHILD_EXIT_WAIT
                while proc.poll() is None and sigs.hits == seen and time.monotonic() < deadline:
                    time.sleep(WAIT_STEP)
                break
        if sigs.hits:
            return EXIT_INTERRUPTED
        release = True
        return rc
    finally:
        try:
            if text is not None:
                if not release:
                    if sigs.hits:
                        say(LEFT_BY_INTERRUPT)
                    else:
                        say("NG コマンドの起動か待つ途中で止まったので resolve.lock を残した。コマンドが止まったことを"
                            "確認してから人が消す")
                else:
                    try:
                        r = give_back(text, work)
                    except OSError as e:
                        r = None
                        say(f"NG resolve.lock を消せずに残った。人が消す: {e}")
                    if r == "released":
                        say(f"resolve.lock を返した ({owner})")
                    elif r == "not_mine":
                        say("resolve.lock が無いか、中身が自分の書いたものと違うので消さなかった")
        finally:
            sigs.restore()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true", help="状態を JSON で出す")
    p = sub.add_parser("run")
    p.add_argument("--owner", required=True, help="持ち主の名前 (同時に動く処理ごとに違う名前)")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- の後に流すコマンド")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        st = read_state()
        if a.json:
            print(json.dumps(st, ensure_ascii=False, indent=1))
            return 0 if st["state"] == "free" else 1
        return C.report(st["state"] == "free", f"resolve.lock: {describe(st)}", None,
                        [st["path"]] if st["state"] != "free" else [])
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        ap.error("run の後に -- コマンド... を書く")
    try:
        owner = check_owner(a.owner)
    except ValueError as e:
        ap.error(str(e))
    try:
        return run(owner, cmd)
    except KeyboardInterrupt:   # 合図を受け持てなかったとき。ロックの扱いは run の finally で済んでいる
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
