"""チャンネルと各動画の数字を YouTube Analytics API で集めて、分析レポートを作る。

使い方: py pipeline/analytics.py            … 公開から今日までの全期間
        py pipeline/analytics.py --days 7   … 直近7日
出力:   reports/<日付>_分析データ.json（生の数字）と reports/<日付>_分析.md（読みやすいまとめ）

※ YouTube Analytics の数字は1〜2日遅れて反映される（Studio のリアルタイムの数字とはずれる）。
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from googleapiclient.discovery import build  # noqa: E402
from youtube_upload import credentials  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
START = "2026-11-20"  # チャンネルの最初の公開日


def q(ya, **kw):
    try:
        return ya.reports().query(ids="channel==MINE", **kw).execute()
    except Exception as e:  # データ不足などで取れない指標は飛ばす
        return {"error": str(e)[:200]}


def rows(resp):
    if not resp or "rows" not in resp:
        return []
    names = [h["name"] for h in resp["columnHeaders"]]
    return [dict(zip(names, r)) for r in resp["rows"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0)
    a = ap.parse_args()
    today = datetime.date.today()
    start = (today - datetime.timedelta(days=a.days)).isoformat() if a.days else START
    end = today.isoformat()

    creds = credentials()
    yt = build("youtube", "v3", credentials=creds)
    ya = build("youtubeAnalytics", "v2", credentials=creds)

    # 公開済みの動画一覧（タイトルを付けるため）
    ch = yt.channels().list(part="contentDetails,statistics", mine=True).execute()["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    vids, token = [], None
    while True:
        pl = yt.playlistItems().list(part="snippet,status", playlistId=uploads, maxResults=50, pageToken=token).execute()
        vids += pl["items"]
        token = pl.get("nextPageToken")
        if not token:
            break
    ids = [v["snippet"]["resourceId"]["videoId"] for v in vids]
    stats = {}
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,statistics,status,contentDetails", id=",".join(ids[i:i + 50])).execute()
        for v in r["items"]:
            stats[v["id"]] = v

    metrics = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained"
    data = {
        "period": [start, end],
        "channel_now": ch["statistics"],
        "daily": rows(q(ya, startDate=start, endDate=end, metrics=metrics, dimensions="day", sort="day")),
        "by_video": rows(q(ya, startDate=start, endDate=end, metrics=metrics, dimensions="video", sort="-views", maxResults=200)),
        "traffic": rows(q(ya, startDate=start, endDate=end, metrics="views,estimatedMinutesWatched", dimensions="insightTrafficSourceType", sort="-views")),
        "country": rows(q(ya, startDate=start, endDate=end, metrics="views,averageViewPercentage", dimensions="country", sort="-views", maxResults=10)),
        "device": rows(q(ya, startDate=start, endDate=end, metrics="views", dimensions="deviceType", sort="-views")),
        "age_gender": rows(q(ya, startDate=start, endDate=end, metrics="viewerPercentage", dimensions="ageGroup,gender")),
        "retention": {},
    }
    # 視聴維持率のグラフ（動画のどの位置で何%残っているか）
    for v in data["by_video"][:20]:
        r = rows(q(ya, startDate=start, endDate=end, metrics="audienceWatchRatio,relativeRetentionPerformance",
                   dimensions="elapsedVideoTimeRatio", filters=f"video=={v['video']}"))
        data["retention"][v["video"]] = r

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"{end}_分析データ.json").write_text(json.dumps({"data": data, "videos": {
        k: {"title": s["snippet"]["title"], "published": s["snippet"]["publishedAt"],
            "privacy": s["status"]["privacyStatus"], "publishAt": s["status"].get("publishAt"),
            "duration": s["contentDetails"]["duration"], "stats": s.get("statistics", {})}
        for k, s in stats.items()}}, ensure_ascii=False, indent=2), encoding="utf-8")

    # 読みやすいまとめ（細かい考察は Claude がこのデータを読んで書き足す）
    lines = [f"# 分析レポート（{start} 〜 {end}）", "",
             f"- 登録者：{ch['statistics'].get('subscriberCount')} 人 / 総再生：{ch['statistics'].get('viewCount')} 回 / 動画：{ch['statistics'].get('videoCount')} 本", "",
             "## 動画ごと（再生数の多い順）", "",
             "| 動画 | 再生 | 平均視聴(秒) | 平均視聴率 | いいね | コメント | 共有 | 登録+ |", "|---|---|---|---|---|---|---|---|"]
    for v in data["by_video"]:
        t = stats.get(v["video"], {}).get("snippet", {}).get("title", v["video"])
        lines.append(f"| {t} | {v['views']} | {v['averageViewDuration']} | {v['averageViewPercentage']:.1f}% | {v['likes']} | {v['comments']} | {v['shares']} | {v['subscribersGained']} |")
    lines += ["", "## どこから見られたか", ""] + [f"- {r['insightTrafficSourceType']}：{r['views']} 回" for r in data["traffic"]]
    lines += ["", "## 国", ""] + [f"- {r['country']}：{r['views']} 回（平均視聴率 {r['averageViewPercentage']:.1f}%）" for r in data["country"]]
    lines += ["", "## 端末", ""] + [f"- {r['deviceType']}：{r['views']} 回" for r in data["device"]]
    lines += ["", "## 年齢・性別", ""] + [f"- {r['ageGroup']} {r['gender']}：{r['viewerPercentage']:.1f}%" for r in data["age_gender"]]
    lines += ["", "## Claude の考察", "", "（ここに考察を書く）"]
    (REPORTS / f"{end}_分析.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"✔ reports/{end}_分析.md")


if __name__ == "__main__":
    main()
