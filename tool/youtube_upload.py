"""YouTube Data API で動画をアップロードし、予約公開まで設定する。

準備（1回だけ）:
  1. Google Cloud で OAuth クライアント（デスクトップ アプリ）を作り、JSON を
     プロジェクト直下に client_secret.json として置く（git には入らない）
  2. py pipeline/youtube_upload.py --login
     → ブラウザが開くので「へぇチーン」のチャンネルを選んで許可する。token.json が保存される

使い方:
  py pipeline/youtube_upload.py --test 13_tower_bridge
      … 非公開でテストアップロード（予約なし）
  py pipeline/youtube_upload.py 13_tower_bridge
      … output/upload_queue.json の日時で予約アップロード
  py pipeline/youtube_upload.py --queue
      … upload_queue.json のうち、まだ上げていないものを全部予約アップロード

注意:
  Google の審査（監査）を通るまで、API で上げた動画は「非公開」に固定される（予約公開されない）。
  審査が通ったら、このプログラムのままで予約公開まで自動になる。
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).resolve().parent.parent
CLIENT_SECRET = ROOT / "client_secret.json"
TOKEN = ROOT / "token.json"
QUEUE = ROOT / "output" / "upload_queue.json"
LOG = ROOT / "投稿記録.md"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    # 分析（再生数・視聴維持率など）を読むため
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    # コメントを読んで、オーナーがOKした返信を送るため
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
CATEGORY_EDUCATION = "27"
JST = datetime.timezone(datetime.timedelta(hours=9))


def credentials(interactive=False):
    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not interactive:
            sys.exit("ログインが必要です： py pipeline/youtube_upload.py --login")
        if not CLIENT_SECRET.exists():
            sys.exit("client_secret.json がありません（Google Cloud でダウンロードしてプロジェクト直下に置く）")
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


def youtube(interactive=False):
    return build("youtube", "v3", credentials=credentials(interactive))


def to_utc_iso(jst_text):
    """'2026-11-24 12:00'（日本時間）→ '2026-11-24T03:00:00Z'"""
    t = datetime.datetime.strptime(jst_text, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upload(yt, item, test=False):
    body = {
        "snippet": {
            "title": item["title"],
            "description": item["description"],
            "tags": item.get("tags", []),
            "categoryId": CATEGORY_EDUCATION,
            "defaultLanguage": "ja",
            "defaultAudioLanguage": "ja",
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
            # 生成AIのリアルな映像が混ざる可能性があるので「改変・合成コンテンツ」を申告する
            "containsSyntheticMedia": True,
        },
    }
    if not test:
        body["status"]["publishAt"] = to_utc_iso(item["publish"])
    media = MediaFileUpload(str(ROOT / item["file"]), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"    {int(status.progress() * 100)}%")
    return resp


def log_row(item, video_id, test):
    state = "API テスト（非公開）" if test else "予約済み（API）"
    row = f"| {item['publish']} | {item['id']} | {item['title']} | {state} https://youtu.be/{video_id} |\n"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--queue", action="store_true")
    a = ap.parse_args()

    if a.login:
        yt = youtube(interactive=True)
        ch = yt.channels().list(part="snippet", mine=True).execute()
        for c in ch.get("items", []):
            print(f"ログインOK：{c['snippet']['title']}（{c['id']}）")
        return

    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    targets = [q for q in queue if (a.queue and not q.get("video_id")) or q["id"] in a.ids]
    if not targets:
        sys.exit("対象がありません")
    yt = youtube()
    for item in targets:
        print(f"▶ {item['id']}（{'テスト' if a.test else item['publish'] + ' に予約'}）")
        resp = upload(yt, item, test=a.test)
        vid = resp["id"]
        print(f"  ✔ https://youtu.be/{vid}  状態: {resp['status'].get('privacyStatus')} {resp['status'].get('publishAt', '')}")
        if not a.test:
            item["video_id"] = vid
        log_row(item, vid, a.test)
    QUEUE.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
