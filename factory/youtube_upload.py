"""Agent 6 - The Publisher (YouTube).
Uploads videos with the free YouTube Data API, posts a question comment under each
video, and reads view/like counts so the factory can learn what works.
Run YOUTUBE_SIGNIN.bat once on your PC to sign in (it prints the value for the
GitHub secret YT_REFRESH_TOKEN)."""
import json
import os

from .common import DATA, log

# upload = post videos; force-ssl = read stats + post comments
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
CLIENT_SECRET = os.path.join(DATA, "client_secret.json")
TOKEN = os.path.join(DATA, "youtube_token.json")
_svc = None


def prepare_token():
    """Cloud: writes data/youtube_token.json from the YOUTUBE_TOKEN_JSON secret."""
    tok = os.environ.get("YOUTUBE_TOKEN_JSON", "").strip()
    if not tok:
        return False
    data = json.loads(tok, strict=False)
    # GitHub secrets pasted with a stray line break or space would break the token - clean them
    data = {k: ("".join(v.split()) if isinstance(v, str) else v) for k, v in data.items()}
    data.pop("scopes", None)  # keep whatever access was granted at sign-in
    with open(TOKEN, "w") as f:
        json.dump(data, f)
    return True


def _service():
    global _svc
    if _svc:
        return _svc
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not os.path.exists(TOKEN):
        raise FileNotFoundError("YouTube is not signed in yet (run YOUTUBE_SIGNIN.bat).")
    # no scopes passed: the token keeps whatever access you granted when you signed in
    creds = Credentials.from_authorized_user_file(TOKEN)
    if creds.refresh_token or not creds.valid:  # always start with a fresh access token
        creds.refresh(Request())
    _svc = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return _svc


def video_id(url):
    url = str(url)
    for marker in ("/shorts/", "watch?v=", "youtu.be/"):
        if marker in url:
            return url.split(marker, 1)[1].split("?")[0].split("&")[0].strip("/")
    return None


def upload(video_path, post, privacy="private", short=True):
    from googleapiclient.http import MediaFileUpload
    yt = _service()
    body = {
        "snippet": {
            "title": post["youtube"]["title"][:100],
            "description": post["youtube"]["description"][:4900],
            "tags": post["youtube"]["tags"][:15],
            "categoryId": "27",  # Education
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": True},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    url = f"https://youtube.com/shorts/{resp['id']}" if short else f"https://youtube.com/watch?v={resp['id']}"
    log(f"  uploaded to YouTube: {url}")
    return url


def comment(vid, text):
    """Posts a comment from your channel under the video (needs the newer sign-in)."""
    _service().commentThreads().insert(part="snippet", body={
        "snippet": {"videoId": vid, "topLevelComment": {"snippet": {"textOriginal": text[:900]}}}}).execute()


def set_thumbnail(vid, path):
    from googleapiclient.http import MediaFileUpload
    _service().thumbnails().set(videoId=vid, media_body=MediaFileUpload(path, mimetype="image/jpeg")).execute()


def stats(ids):
    """{video_id: {"views": n, "likes": n, "comments": n}} for up to 50 ids per call."""
    out, ids = {}, [i for i in ids if i]
    for k in range(0, len(ids), 50):
        r = _service().videos().list(part="statistics", id=",".join(ids[k:k + 50])).execute()
        for it in r.get("items", []):
            s = it.get("statistics", {})
            out[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0))}
    return out


def top_comments(vid, n=10):
    """Newest top-level comments under a video: [{"id","author","text","mine"}]."""
    r = _service().commentThreads().list(part="snippet", videoId=vid, maxResults=n, order="time",
                                         textFormat="plainText").execute()
    out = []
    for it in r.get("items", []):
        c = it["snippet"]["topLevelComment"]["snippet"]
        out.append({"id": it["id"], "author": c.get("authorDisplayName", ""), "text": c.get("textDisplay", ""),
                    "mine": c.get("authorChannelId", {}).get("value") == it["snippet"].get("channelId")})
    return out


def channel_stats():
    r = _service().channels().list(part="statistics", mine=True).execute()
    s = (r.get("items") or [{}])[0].get("statistics", {})
    return {"subscribers": int(s.get("subscriberCount", 0)), "views": int(s.get("viewCount", 0))}


CLIENT_ID = "244014506799-m1ovk750oa1fbdus1l27oj3hoolt22e2.apps.googleusercontent.com"
PORT = 8080  # must match "http://localhost:8080/" in the Google client's Authorized redirect URIs


def sign_in():
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    print("\n=== YouTube sign-in for Hidden Facts HQ ===\n")
    secret = "".join(input("Paste your Google Client secret (starts with GOCSPX-) and press Enter: ").split())
    flow = InstalledAppFlow.from_client_config({"installed": {
        "client_id": CLIENT_ID, "client_secret": secret, "redirect_uris": [f"http://localhost:{PORT}/"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}, SCOPES)
    print("\nA browser window is opening. Choose the Google account of your YouTube channel,")
    print("click 'Advanced > Go to ... (unsafe)' if asked (it is your own app), tick BOTH boxes, then Continue.\n")
    creds = flow.run_local_server(port=PORT, prompt="consent", access_type="offline", open_browser=True)
    # test it right away, exactly like the cloud factory will use it
    test = Credentials(None, refresh_token=creds.refresh_token, client_id=CLIENT_ID, client_secret=secret,
                       token_uri="https://oauth2.googleapis.com/token")
    from google.auth.transport.requests import Request
    test.refresh(Request())
    ch = build("youtube", "v3", credentials=test, cache_discovery=False).channels().list(part="snippet", mine=True).execute()
    name = (ch.get("items") or [{}])[0].get("snippet", {}).get("title", "(no channel found on this account!)")
    print("\n" + "=" * 70)
    print(f"  TEST PASSED - signed in to YouTube channel: {name}")
    print("=" * 70)
    print("\nCopy the line below into the GitHub secret  YT_REFRESH_TOKEN  (pencil > paste > Update secret):\n")
    print(creds.refresh_token)
    print("\nAlso make sure the GitHub secret YT_CLIENT_SECRET is the same secret you pasted above.")
    print("Do not share these codes with anyone.")


if __name__ == "__main__":
    sign_in()
