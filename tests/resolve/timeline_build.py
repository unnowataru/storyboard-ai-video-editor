"""タイムラインにクリップを並べる方法の違い: CreateTimelineFromClips と AppendToTimeline。

期待 (Studio 21.1.1 の実機):
- CreateTimelineFromClips(名前, [3 件]) は先頭の 1 件しか並べない (.pyi では 3 件並ぶはず)
- CreateEmptyTimeline の後に AppendToTimeline([3 件]) をまとめて 1 回でも、1 件ずつ 3 回でも、3 件並ぶ
- endFrame はそのフレームの手前まで (含まない)。並んだクリップの長さは endFrame - startFrame
- mediaType 1 (映像だけ) と recordFrame を付けると、指定の位置に映像だけが並ぶ (音声トラックは 0 件)
- AppendToTimeline が返す TimelineItem はそのまま使える。GetTrackTypeAndIndex() は置いたトラック (["video", 1] など)、
  GetUniqueId() は GetItemListInTrack で得た項目と同じ。まとめて渡しても 1 件ずつでも、mediaType・recordFrame・trackIndex の有無によらない
- trackIndex 2 は、映像トラック 2 が無いと映像トラック 1 に置かれる。AddTrack の後なら ["video", 2]
- mediaType を付けないと映像と音声の両方が並ぶが、戻り値は映像の項目だけ。音声は GetLinkedItems() で引ける。
  trackIndex 2 でも、音声トラック 2 が無ければ音声は音声トラック 1 に入る
- 戻り値の項目に SetSpeed した後も、同じ項目で GetStart / GetSpeed が使え、つながった音声の速さも変わる

使い方: python scripts/resolve_lock.py run --owner timeline_build -- python tests/resolve/timeline_build.py [--source 素材]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_helpers import Trial  # noqa: E402

t = Trial("timeline_build")
clip = t.clip()
fps = t.fps
rows = [(0, 5 * fps), (5 * fps, 25 * fps), (30 * fps, 36 * fps)]   # tests/trial-storyboard.json の 3 行と同じ範囲
infos = [{"mediaPoolItem": clip, "startFrame": s, "endFrame": e} for s, e in rows]


def items(tl, kind="video"):
    return tl.GetItemListInTrack(kind, 1) or []


def spans(tl):
    return [(i.GetStart() - tl.GetStartFrame(), i.GetEnd() - tl.GetStartFrame()) for i in items(tl)]


tl = t.mp.CreateTimelineFromClips("A-create", infos)
n = len(items(tl)) if tl else None
t.check("CreateTimelineFromClips に 3 件渡すと 1 件しか並ばない", n == 1, n, 1)

tl = t.timeline("B-append-list")
got = t.mp.AppendToTimeline(infos) or []
t.check("AppendToTimeline をまとめて 1 回: 3 件並ぶ", len(items(tl)) == 3, len(items(tl)), 3)
lens = [e - s for s, e in spans(tl)]
t.check("endFrame は含まない (長さ = endFrame - startFrame)", lens == [e - s for s, e in rows], lens, [e - s for s, e in rows])
tt = [g.GetTrackTypeAndIndex() for g in got]
t.check("戻り値の GetTrackTypeAndIndex() は [\"video\", 1]", tt == [["video", 1]] * 3, tt, [["video", 1]] * 3)
ids = [g.GetUniqueId() for g in got]
t.check("戻り値の項目はトラックの項目と同じ (GetUniqueId)", ids == [i.GetUniqueId() for i in items(tl)], ids,
        "GetItemListInTrack と同じ並び")

tl = t.timeline("C-append-each")
for info in infos:
    t.mp.AppendToTimeline([info])
t.check("AppendToTimeline を 1 件ずつ 3 回: 3 件並ぶ", len(items(tl)) == 3, len(items(tl)), 3)

tl = t.timeline("D-video-record")
rec, got = tl.GetStartFrame(), []
for s, e in rows:   # 1 件ずつ、mediaType・trackIndex・recordFrame を付けて渡す
    got += t.mp.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": s, "endFrame": e, "mediaType": 1, "trackIndex": 1,
                                   "recordFrame": rec}]) or []
    rec += e - s
want, pos = [], 0
for s, e in rows:
    want.append((pos, pos + e - s))
    pos += e - s
t.check("mediaType 1 + recordFrame: 指定の位置に並ぶ", spans(tl) == want, spans(tl), want)
na = len(items(tl, "audio"))
t.check("mediaType 1: 音声は並ばない", na == 0, na, 0)
got_spans = [(g.GetStart() - tl.GetStartFrame(), g.GetEnd() - tl.GetStartFrame()) for g in got]
t.check("1 件ずつ + mediaType 1 + recordFrame: 戻り値の項目の位置が並んだ位置と同じ", got_spans == want, got_spans, want)
tt = [g.GetTrackTypeAndIndex() for g in got]
t.check("1 件ずつ + mediaType 1 + recordFrame: 戻り値のトラックは [\"video\", 1]", tt == [["video", 1]] * 3, tt,
        [["video", 1]] * 3)

tl = t.timeline("E-track2")
one = {"mediaPoolItem": clip, "startFrame": 0, "endFrame": 2 * fps, "mediaType": 1, "trackIndex": 2,
       "recordFrame": tl.GetStartFrame()}
g = (t.mp.AppendToTimeline([one]) or [None])[0]
tt = g.GetTrackTypeAndIndex() if g else None
t.check("trackIndex 2 で映像トラック 2 が無い: 映像トラック 1 に置かれる", tt == ["video", 1], tt, ["video", 1])
tl.AddTrack("video")
g = (t.mp.AppendToTimeline([dict(one, recordFrame=tl.GetStartFrame() + 5 * fps)]) or [None])[0]
tt = g.GetTrackTypeAndIndex() if g else None
t.check("AddTrack の後の trackIndex 2: 映像トラック 2", tt == ["video", 2], tt, ["video", 2])
g = (t.mp.AppendToTimeline([dict(one, mediaType=2, trackIndex=1, recordFrame=tl.GetStartFrame() + 10 * fps)]) or [None])[0]
tt = g.GetTrackTypeAndIndex() if g else None
t.check("mediaType 2: 戻り値のトラックは [\"audio\", 1]", tt == ["audio", 1], tt, ["audio", 1])

tl = t.timeline("F-linked-speed")
tl.AddTrack("video")
got = t.mp.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": 5 * fps, "endFrame": 15 * fps, "trackIndex": 2,
                              "recordFrame": tl.GetStartFrame()}]) or []
tt = [g.GetTrackTypeAndIndex() for g in got]
t.check("mediaType なし: 戻り値は映像の項目 1 件だけ", tt == [["video", 2]], tt, [["video", 2]])
na = len(items(tl, "audio"))
t.check("mediaType なし + trackIndex 2: 音声は音声トラック 1 に入る", na == 1, na, 1)
g = got[0] if got else None
linked = [x.GetTrackTypeAndIndex() for x in (g.GetLinkedItems() or [])] if g else None
t.check("つながった音声は戻り値の GetLinkedItems() で引ける", linked == [["audio", 1]], linked, [["audio", 1]])
ok = g.SetSpeed({"Percentage": 200.0, "RippleTimeline": False}) if g else None
after = (g.GetStart() - tl.GetStartFrame(), g.GetSpeed().get("Percentage"), g.GetTrackTypeAndIndex()) if g else None
t.check("SetSpeed の後も戻り値の項目が使える (位置・速さ・トラック)", ok and after == (0, 200.0, ["video", 2]),
        [ok, after], [True, (0, 200.0, ["video", 2])])
asp = [x.GetSpeed().get("Percentage") for x in (g.GetLinkedItems() or [])] if g else None
t.check("SetSpeed の後、つながった音声の速さも 200%", asp == [200.0], asp, [200.0])
t.finish()
