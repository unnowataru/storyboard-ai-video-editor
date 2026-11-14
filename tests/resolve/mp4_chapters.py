"""MP4 のチャプターが、読み込むだけで Resolve のマーカーになるか。

素材はチャプター付きの MP4 を --source で渡す (ffprobe -show_chapters で確認できるもの。録画ツールが録画中に
付けた印をチャプターとして書くものや、FFmpeg の -map_metadata で ffmetadata のチャプターを付けたもの)。
メディアプールのクリップ (MediaPoolItem.GetMarkers)、タイムラインに置いたクリップ (TimelineItem.GetMarkers)、
タイムライン (Timeline.GetMarkers) の 3 か所を読み、チャプターと見比べる。

期待 (Studio 21.1.1 の実機):
- チャプターごとに、クリップに青のマーカーが 1 つ付く。位置は 秒 × フレームレート に最も近いフレーム
  (ちょうど半分のときの丸め方は未確認なので、差 0.5 フレームまでを許す)、
  名前はチャプターの題、長さ 1 フレーム、メモと customData は空。0 秒のチャプターもマーカーになる
- タイムラインに置いたクリップ (TimelineItem.GetMarkers) にも同じマーカーが出る
- タイムライン自体のマーカー (Timeline.GetMarkers) は増えない
画面 (メディアプール・ソースビューア・タイムライン) は、残した試し用のプロジェクトを開いて人が見る。

使い方: python scripts/resolve_lock.py run --owner mp4_chapters -- python tests/resolve/mp4_chapters.py --source chapters.mp4
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_helpers import Trial, C  # noqa: E402

t = Trial("mp4_chapters")
r = C.run_tool("ffprobe", ["-v", "error", "-show_chapters", "-of", "json", t.source])
chapters = [(float(c["start_time"]), c.get("tags", {}).get("title", "")) for c in json.loads(r.stdout).get("chapters", [])]
t.note("chapters (ffprobe)", chapters)
clip = t.clip()
t.note("clip FPS", clip.GetClipProperty("FPS"))
t.note("clip Start TC", clip.GetClipProperty("Start TC"))
tl = t.timeline("chapters")
t.mp.AppendToTimeline([{"mediaPoolItem": clip}])
items = tl.GetItemListInTrack("video", 1) or []
fps = float(clip.GetClipProperty("FPS"))
want = [(sec * fps, {"color": "Blue", "duration": 1, "note": "", "name": name, "customData": ""}) for sec, name in chapters]


def same(markers):
    """マーカーがチャプターと 1 対 1 で、位置の差が 0.5 フレーム以内、中身が同じか。"""
    got = sorted((int(k), v) for k, v in (markers or {}).items())
    return len(got) == len(want) and all(abs(f - w) <= 0.5 and v == wv for (f, v), (w, wv) in zip(got, want))


t.check("チャプターがある素材", len(chapters) > 0, len(chapters), "1 件以上")
t.check("クリップのマーカー = チャプター", same(clip.GetMarkers()), clip.GetMarkers(), want)
got = items[0].GetMarkers() if items else None
t.check("タイムラインのクリップにも同じマーカー", got is not None and same(got), got, want)
t.check("タイムライン自体のマーカーは増えない", not tl.GetMarkers(), tl.GetMarkers(), "空")
t.resolve.OpenPage("edit")
t.finish()
