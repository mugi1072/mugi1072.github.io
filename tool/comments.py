"""コメントを集めて返信案のファイルを作る／オーナーがOKした返信だけを送る。

1. 集める:  py pipeline/comments.py fetch
   → comments/返信案_<日付>.json を作る（まだ返信していないコメントだけ）。
     Claude が コメント返信ガイド.md に沿って "reply" 欄に返信案を書く。返信しないものは "reply" を空のままにする。
2. オーナーが返信案を確認して、送ってよいものの "approved" を true にする（「全部OK」なら Claude がまとめて true にする）
3. 送る:  py pipeline/comments.py post comments/返信案_<日付>.json
   → approved が true で reply が入っているものだけ送る。送ったものは "sent" に記録する
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from googleapiclient.discovery import build  # noqa: E402
from youtube_upload import credentials  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "comments"


def fetch(yt):
    ch = yt.channels().list(part="id", mine=True).execute()["items"][0]["id"]
    items, token = [], None
    while True:
        r = yt.commentThreads().list(part="snippet,replies", allThreadsRelatedToChannelId=ch,
                                     maxResults=100, order="time", textFormat="plainText", pageToken=token).execute()
        items += r["items"]
        token = r.get("nextPageToken")
        if not token or len(items) >= 300:
            break
    out = []
    for t in items:
        top = t["snippet"]["topLevelComment"]
        replies = t.get("replies", {}).get("comments", [])
        # すでにチャンネルが返信しているスレッドは飛ばす
        if any(c["snippet"].get("authorChannelId", {}).get("value") == ch for c in replies):
            continue
        if top["snippet"].get("authorChannelId", {}).get("value") == ch:
            continue
        out.append({
            "comment_id": top["id"], "video_id": t["snippet"]["videoId"],
            "author": top["snippet"]["authorDisplayName"], "text": top["snippet"]["textDisplay"],
            "likes": top["snippet"]["likeCount"], "published": top["snippet"]["publishedAt"],
            "reply": "", "approved": False, "sent": None,
        })
    DIR.mkdir(exist_ok=True)
    path = DIR / f"返信案_{datetime.date.today()}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"未返信のコメント {len(out)} 件 → {path.relative_to(ROOT)}")


def post(yt, path):
    p = Path(path)
    items = json.loads(p.read_text(encoding="utf-8"))
    n = 0
    for it in items:
        if not it.get("approved") or not it.get("reply") or it.get("sent"):
            continue
        r = yt.comments().insert(part="snippet", body={"snippet": {"parentId": it["comment_id"], "textOriginal": it["reply"]}}).execute()
        it["sent"] = r["id"]
        n += 1
        print(f"  ✔ 返信：{it['text'][:20]}… → {it['reply']}")
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{n} 件送りました")


if __name__ == "__main__":
    yt = build("youtube", "v3", credentials=credentials())
    if sys.argv[1:2] == ["fetch"]:
        fetch(yt)
    elif sys.argv[1:2] == ["post"] and len(sys.argv) > 2:
        post(yt, sys.argv[2])
    else:
        print(__doc__)
