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


def channel_stats():
    r = _service().channels().list(part="statistics", mine=True).execute()
    s = (r.get("items") or [{}])[0].get("statistics", {})
    return {"subscribers": int(s.get("subscriberCount", 0)), "views": int(s.get("viewCount", 0))}


def sign_in():
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not os.path.exists(CLIENT_SECRET):
        raise SystemExit("data/client_secret.json is missing. See Step 4 in START HERE.txt.")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    with open(TOKEN, "w") as f:
        f.write(creds.to_json())
    tok = json.loads(creds.to_json())
    print("\nYouTube sign-in complete.\n")
    print("Copy the line below into the GitHub secret  YT_REFRESH_TOKEN  (pencil icon > paste > Update secret):\n")
    print(tok.get("refresh_token", "(no refresh token returned - run this again)"))
    print("\nDo not share this value with anyone.")


if __name__ == "__main__":
    sign_in()
