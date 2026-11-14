"""外部プロセスから Resolve に接続できるかを確認する。

使い方 (Resolve を使うので resolve_lock.py run で包む):
  python scripts/resolve_lock.py run --owner <名前> -- python scripts/probe.py [試す回数]   (5 秒おきに試す)
API と fusionscript.dll の場所は scripts/common.py が決める (環境変数 RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB が優先)。
"""
import argparse
import sys

import common as C


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tries", nargs="?", type=int, default=1, help="接続を試す回数 (既定 1。5 秒おきに試す)")
    a = ap.parse_args(argv)
    dvr = C.import_dvr()
    print("python", sys.version.split()[0], sys.executable)
    print("module", dvr)
    resolve = C.connect_resolve(a.tries)
    print("->", resolve)
    if resolve:
        print("product", resolve.GetProductName(), resolve.GetVersionString(), "studio", resolve.IsStudio())
        pm = resolve.GetProjectManager()
        print("project", pm.GetCurrentProject().GetName() if pm.GetCurrentProject() else None)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
