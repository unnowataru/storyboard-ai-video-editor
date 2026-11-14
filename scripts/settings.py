"""設定の読み込み・場所の決定・結果の出し方 (scripts/common.py から読み込み直す部品)。

設定は次の順に決める (上が優先)。
- 場所 (paths): 環境変数 → 個人の設定ファイル (config/local.json) → 既定の探し方 (config/defaults.json の search)
- 見た目と出力 (storyboard): 構成表の値 → config/local.json → config/defaults.json

個人の設定ファイルの場所は環境変数 RESOLVE_CONFIG で変えられる。
どの値がどこから来たかは `python scripts/common.py --show-config [構成表.json]` で表示する。
標準出力を UTF-8 にする (下の for) のと、設定の読み込み (config()。最初に呼ばれたとき 1 回) はここでだけ起きる。
"""
import copy
import glob
import json
import os
import shutil
import sys


SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SCRIPTS)
DEFAULTS = os.path.join(REPO, "config", "defaults.json")
LOCAL_DEFAULT = os.path.join(REPO, "config", "local.json")
EXE = ".exe" if os.name == "nt" else ""


# Git Bash やパイプ越しのときは UTF-8 で出す (Windows のコンソールに直接出すときはそのまま)
for _s in (sys.stdout, sys.stderr):
    try:
        if _s and not _s.isatty() and (_s.encoding or "").lower() not in ("utf-8", "utf8"):
            _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _merge(base, over, src, sources, prefix=""):
    """over を base に重ね、葉の値ごとに出どころを sources に記録する。"""
    for k, v in over.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v, src, sources, key + ".")
        else:
            base[k] = copy.deepcopy(v)
            for leaf in _leaves(v, key):
                sources[leaf] = src
    return base


def _leaves(v, key):
    if isinstance(v, dict):
        out = []
        for k, x in v.items():
            out += _leaves(x, f"{key}.{k}")
        return out
    return [key]


class Config:
    def __init__(self):
        self.defaults = _read_json(DEFAULTS)
        self.local_path = os.environ.get("RESOLVE_CONFIG") or LOCAL_DEFAULT
        self.local = _read_json(self.local_path) if os.path.isfile(self.local_path) else {}
        self._paths = {}
        self._resolving = set()

    # ---------- 場所 ----------
    def _expand(self, s):
        s = s.replace("{repo}", REPO).replace("{exe}", EXE)
        for k in self.defaults["paths"]:
            token = "{" + k + "}"
            if token in s:
                v = self.path(k)
                if v is None:
                    return None
                s = s.replace(token, v)
        return os.path.normpath(os.path.expanduser(os.path.expandvars(s)))

    def _ok(self, p, spec):
        if p is None or not os.path.exists(p):
            return False
        chk = spec.get("check")
        return not chk or os.path.exists(os.path.join(p, chk.replace("{exe}", EXE)))

    def path_info(self, key):
        """場所 1 つを決め、{"value", "source", "exists"} を返す。"""
        if key in self._paths:
            return self._paths[key]
        if key in self._resolving:
            raise ValueError(f"設定の参照が循環している: {key}")
        self._resolving.add(key)
        try:
            info = self._resolve_path(key)
        finally:
            self._resolving.discard(key)
        self._paths[key] = info
        return info

    def _resolve_path(self, key):
        spec = self.defaults["paths"][key]
        many = spec.get("all", False)

        def done(value, source):
            if many:
                vals = value if isinstance(value, list) else [value]
                return {"value": vals, "source": source, "exists": all(os.path.exists(v) for v in vals)}
            return {"value": value, "source": source, "exists": value is not None and os.path.exists(value)}

        env = spec.get("env")
        if env and os.environ.get(env):
            raw = os.environ[env]
            vals = [self._expand(x) for x in raw.split(os.pathsep) if x] if many else self._expand(raw)
            return done(vals, f"環境変数 {env}")
        local = self.local.get("paths", {}).get(key)
        if local:
            vals = [self._expand(x) for x in local] if many else self._expand(local)
            return done(vals, f"設定ファイル {os.path.basename(self.local_path)}")
        hits = []
        for pat in spec.get("search", []):
            if pat.startswith("PATH:"):
                w = shutil.which(pat[5:])
                cands = [os.path.dirname(w) if (w and spec.get("check")) else w] if w else []
            else:
                p = self._expand(pat)
                if p is None:
                    continue
                cands = sorted(glob.glob(p))[::-1] if any(c in p for c in "*?") else [p]
                if spec.get("create") and not many:
                    return done(cands[0] if cands else p, f"既定の探し方 (defaults.json): {pat}")
            for c in cands:
                if self._ok(c, spec):
                    if not many:
                        return done(c, f"既定の探し方 (defaults.json): {pat}")
                    hits.append(c)
        if many and hits:
            return done(hits, "既定の探し方 (defaults.json)")
        return {"value": [] if many else None, "source": "見つからない", "exists": False}

    def path(self, key):
        return self.path_info(key)["value"]

    def require(self, key, hint=""):
        info = self.path_info(key)
        if not info["value"] or not info["exists"]:
            spec = self.defaults["paths"][key]
            sys.exit(f"{key} が見つからない ({info['source']}: {info['value']})。環境変数 {spec.get('env')} か "
                     f"config/local.json の paths.{key} で場所を指定する。{hint}")
        return info["value"]

    @staticmethod
    def _made(p):
        """フォルダーを作り、実際の場所を返す。

        パッケージ化されたアプリ (Microsoft Store から入れた Python など) から動かしたプログラムが %LOCALAPPDATA% の下に
        書いたものは、%LOCALAPPDATA%\\Packages\\<アプリ>\\LocalCache\\Local\\ へ振り替えられ、Resolve などからは
        見えない。このため作業フォルダーの既定 (config の work_dir。既定は %USERPROFILE%/Videos/storyboard-work) は AppData の外にしてある。
        環境変数などで AppData の下を指定されたときに備え、realpath で実際の場所を渡す。
        """
        os.makedirs(p, exist_ok=True)
        return os.path.realpath(p)

    def out_dir(self, *sub):
        return self._made(os.path.join(self.path("out_dir"), *sub))

    def work_dir(self, *sub):
        return self._made(os.path.join(self.path("work_dir"), *sub))

    def tool(self, name):
        """ffmpeg / ffprobe の実行ファイル。"""
        d = self.require("ffmpeg_dir", "FFmpeg は winget install Gyan.FFmpeg で入る (docs/setup.md)")
        p = os.path.join(d, name + EXE)
        if not os.path.isfile(p):
            sys.exit(f"{p} が無い")
        return p

    def section(self, key):
        """storyboard と paths 以外の項目 (track、contact_sheet、apps など) を、defaults.json に local.json を重ねて返す。"""
        sources = {}
        base = _merge({}, self.defaults.get(key, {}), "defaults.json", sources)
        return _merge(base, self.local.get(key, {}), os.path.basename(self.local_path), sources)

    def font_file(self, family, style=None):
        """書体のファイル。style (Bold など) があれば「名前 太さ」(fonts の "Meiryo Bold") を先に探し、無ければ名前だけで探す。"""
        for key in ([f"{family} {style}"] if style else []) + [family]:
            for d in self.path("fonts_dirs") or []:
                for name in self.defaults.get("fonts", {}).get(key, []) + self.local.get("fonts", {}).get(key, []):
                    p = os.path.join(d, name)
                    if os.path.isfile(p):
                        return p
        return None

    # ---------- 見た目と出力 ----------
    def storyboard_values(self, sb=None):
        """config の storyboard に、local.json、構成表の順に重ねた値と、葉ごとの出どころ。"""
        sources = {}
        base = _merge({}, self.defaults.get("storyboard", {}), "defaults.json", sources)
        _merge(base, self.local.get("storyboard", {}), os.path.basename(self.local_path), sources)
        if sb:
            over = {k: v for k, v in sb.items() if k in base}
            _merge(base, over, "構成表", sources)
        return base, sources

    def load_storyboard(self, path, source=None):
        """構成表を読み、素材のパスを決めて、見た目の値を重ねた dict を返す。

        素材: 引数 source → 構成表の source。構成表の source に {trial_source} などの名前を書くと、
        場所の設定 (環境変数 → local.json) で置き換える。相対パスは構成表のフォルダーから。
        書き出し (output) の相対パスも構成表のフォルダーから。構成表の書式は docs/storyboard.md。
        """
        sb = _read_json(path)
        base = os.path.dirname(os.path.abspath(path))

        def fix(s):
            if not s:
                return s
            p = self._expand(s)
            if p is None:
                key = s.strip("{}")
                spec = self.defaults["paths"].get(key, {})
                sys.exit(f"素材の場所 {s} が決まらない。引数 --source か環境変数 {spec.get('env', key)} で指定する")
            return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))

        sb["source"] = fix(source) if source else fix(sb.get("source"))
        if sb.get("output"):
            sb["output"] = fix(sb["output"])
        for r in sb.get("rows", []):
            if r.get("source"):
                r["source"] = fix(r["source"])
        look, _ = self.storyboard_values(sb)
        sb.update(look)
        per = sb.get("seconds_per_row")
        if per != "source" and not (isinstance(per, (int, float)) and not isinstance(per, bool) and per > 0):
            sys.exit(f"seconds_per_row は 0 より大きい秒か \"source\" (等倍): {per!r}")
        from layout import apply_layout   # layout が settings を読むので、ここで読む
        return apply_layout(sb)

    # ---------- 表示 ----------
    def describe(self, sb=None):
        paths = {k: self.path_info(k) for k in self.defaults["paths"]}
        values, sources = self.storyboard_values(sb)
        flat = {}
        for leaf, src in sources.items():
            v = values
            for part in leaf.split("."):
                v = v[part]
            flat[leaf] = {"value": v, "source": src}
        return {"config_files": {"defaults": DEFAULTS, "local": self.local_path,
                                 "local_exists": os.path.isfile(self.local_path)},
                "paths": paths, "storyboard": dict(sorted(flat.items()))}


_CFG = None


# ---------- 結果の表示 ----------
def report(ok, summary, path=None, lines=(), max_lines=None):
    """スクリプトの結果を標準出力に出すルール。終了コード (ok なら 0、そうでなければ 1) を返す。

    1 行目は OK か NG、要約、詳しい結果のファイルのパス。lines には期待と違う項目や警告など、
    見るべき箇所だけを渡す (1 項目 1 行で字下げして出す)。正常で見るものが無ければ 1 行で終わる。
    lines は先頭から max_lines 行 (省略すると config の report.max_lines) まで出し、残りは「ほか N 件」の 1 行にまとめる
    (行の多い構成表で画面の出力が膨らまないように。全部は path の JSON に残す)。
    """
    print(("OK " if ok else "NG ") + summary + (f"  詳細: {path}" if path else ""))
    lines = list(lines)
    if max_lines is None:
        max_lines = config().section("report").get("max_lines", 10)
    max_lines = max(int(max_lines), 0)
    for x in lines[:max_lines]:
        print("  " + " ".join(str(x).split("\n")))
    if len(lines) > max_lines:
        print(f"  ほか {len(lines) - max_lines} 件" + (" (詳細は JSON)" if path else ""))
    return 0 if ok else 1


def write_json(path, obj):
    """記録の JSON を書く (BOM なし UTF-8、LF、日本語はそのまま)。書けない値は文字列にする。"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)


def run_dir(dest):
    """dest の中に、この実行だけが書く一時のフォルダー (.run-...) を作って返す。

    照らし合わせの JSON と画像 (check_final・area_sheet) は、ここに書いてから publish_run で dest へ移す。
    途中で止まった・失敗したときは discard_run で消すので、dest の前回の結果は残る。
    dest の中に作るので同じドライブになり、os.replace で名前を差し替えられる。
    """
    import tempfile
    os.makedirs(dest, exist_ok=True)
    return tempfile.mkdtemp(prefix=".run-", dir=dest)


def publish_run(temp, dest, stale=(), last=None):
    """run_dir で作った temp のファイルを dest へ移し、temp を消す。処理が最後まで済んだときだけ呼ぶ。

    前回のファイル (今回と同じ名前と、stale に挙げた dest の中の前回の結果。大文字小文字は区別しない) は、
    先に dest の中の退避のフォルダー (.old-...) へ移してから、今回のファイルを移す。last (記録の JSON の名前) は最後に移す。
    全部移せたら退避したものと temp を消す (消せなくても失敗にしない)。途中で失敗・中断したら、移した今回のファイルを
    temp へ戻し、退避のフォルダーの中身を元の場所へ戻して例外を出す (temp は呼ぶ側が discard_run で消す)。
    戻すものは記録ではなくフォルダーの実際の中身で決める (移した直後の中断でも漏らさない)。戻せなかったファイルは
    退避のフォルダーに残し、消すのは空になった退避のフォルダーだけ。
    """
    import tempfile
    new = sorted(os.listdir(temp), key=lambda f: f == last)
    want = {f.casefold() for f in new} | {f.casefold() for f in stale}
    old = [f for f in os.listdir(dest) if f.casefold() in want and os.path.isfile(os.path.join(dest, f))]
    keep = tempfile.mkdtemp(prefix=".old-", dir=dest)
    try:
        for f in old:
            os.replace(os.path.join(dest, f), os.path.join(keep, f))
        for f in new:
            os.replace(os.path.join(temp, f), os.path.join(dest, f))
    except BaseException:
        for f in new:   # temp に無く dest にある今回の名前は、移したもの
            if not os.path.exists(os.path.join(temp, f)) and os.path.exists(os.path.join(dest, f)):
                try:
                    os.replace(os.path.join(dest, f), os.path.join(temp, f))
                except OSError:
                    pass
        for f in os.listdir(keep):
            try:
                os.replace(os.path.join(keep, f), os.path.join(dest, f))
            except OSError:
                pass
        try:
            os.rmdir(keep)   # 空のときだけ消える
        except OSError:
            pass
        raise
    shutil.rmtree(keep, ignore_errors=True)
    discard_run(temp)


def discard_run(temp):
    """途中で止まった・失敗した実行の temp を消す。dest の前回の結果には触らない。"""
    shutil.rmtree(temp, ignore_errors=True)


def config():
    global _CFG
    if _CFG is None:
        _CFG = Config()
    return _CFG


def path_key(p):
    """同じファイルかを比べるためのパスの形 (絶対パスにして、Windows では大文字小文字と区切りをそろえる)。
    シンボリックリンクやハードリンクは見ない (それも見る sb2resolve.same_output_file とは別)。"""
    return os.path.normcase(os.path.abspath(p))


def same_path(p, q):
    """2 つのパスが同じファイルを指すか (path_key が同じ)。track2masks.py が構成表の素材と追跡の動画を比べるのに使う。"""
    return path_key(p) == path_key(q)


def appdata_warning(path):
    """作業フォルダーなどが AppData の下なら警告の文を返す。"""
    if not path:
        return None
    root = os.environ.get("APPDATA") and os.path.dirname(os.path.normcase(os.environ["APPDATA"]))
    p = os.path.normcase(os.path.abspath(path))
    if root and (p == root or p.startswith(root + os.sep)):
        return (f"{path} は AppData の下にある。パッケージ化されたアプリ (Microsoft Store から入れた Python など) から書いたファイルは "
                "アプリ用の別の場所に振り替えられ、Resolve から見えないことがある。環境変数 RESOLVE_WORK などで AppData の外を指定する")
    return None
