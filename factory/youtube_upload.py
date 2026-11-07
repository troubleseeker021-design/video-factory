"""Agent 6 - The Publisher (YouTube).
Uploads approved videos as YouTube Shorts using the free YouTube Data API.
One-time setup is explained in START HERE.txt (Step 4). Run this file
directly once to sign in:  python -m factory.youtube_upload"""
import os

from .common import DATA, log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET = os.path.join(DATA, "client_secret.json")
TOKEN = os.path.join(DATA, "youtube_token.json")


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET):
                raise FileNotFoundError("data/client_secret.json is missing. See Step 4 in START HERE.txt.")
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload(video_path, post, privacy="private"):
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
    url = f"https://youtube.com/shorts/{resp['id']}"
    log(f"  uploaded to YouTube: {url}")
    return url


if __name__ == "__main__":
    _service()
    print("YouTube sign-in complete. Uploads will now work from the review page.")
