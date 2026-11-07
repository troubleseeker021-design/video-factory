"""Agent 9 - The Publisher (Instagram Reels + Facebook Page Reels) via the official Meta Graph API.
Needs: META_PAGE_ID, META_PAGE_TOKEN (long-lived Page token) and IG_USER_ID
(see CLOUD SETUP GUIDE.txt, part 5 - META_SETUP.bat prints all three for you)."""
import os
import time

import requests

from .common import log

VER = os.environ.get("META_GRAPH_VERSION", "v24.0")
G = f"https://graph.facebook.com/{VER}"


def _check(r):
    try:
        data = r.json()
    except ValueError:
        r.raise_for_status()
        return {}
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Meta API error: {data.get('error', data)}")
    return data


def _instagram_once(video_path, caption, ig_user_id, token):
    size = os.path.getsize(video_path)
    c = _check(requests.post(f"{G}/{ig_user_id}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
        "share_to_feed": "true", "access_token": token}, timeout=60))
    cid = c["id"]
    with open(video_path, "rb") as f:
        _check(requests.post(f"https://rupload.facebook.com/ig-api-upload/{VER}/{cid}", data=f, timeout=600, headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)}))
    for _ in range(60):  # wait for Instagram to process (up to ~5 min)
        st = _check(requests.get(f"{G}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=30))
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram processing failed: {st.get('status')}")
        time.sleep(5)
    pub = _check(requests.post(f"{G}/{ig_user_id}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=60))
    link = _check(requests.get(f"{G}/{pub['id']}", params={"fields": "permalink", "access_token": token}, timeout=30))
    url = link.get("permalink", f"instagram media {pub['id']}")
    log(f"  posted to Instagram: {url}")
    return url


def facebook_reel(video_path, description, page_id, token):
    size = os.path.getsize(video_path)
    start = _check(requests.post(f"{G}/{page_id}/video_reels", data={"upload_phase": "start", "access_token": token}, timeout=60))
    vid = start["video_id"]
    with open(video_path, "rb") as f:
        _check(requests.post(f"https://rupload.facebook.com/video-upload/{VER}/{vid}", data=f, timeout=600, headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)}))
    _check(requests.post(f"{G}/{page_id}/video_reels", data={
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
        "description": description[:2200], "access_token": token}, timeout=60))
    url = f"https://www.facebook.com/reel/{vid}"
    log(f"  posted to Facebook: {url}")
    return url


def _wait_and_publish(cid, ig_user_id, token):
    for _ in range(60):
        st = _check(requests.get(f"{G}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=30))
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram processing failed: {st.get('status')}")
        time.sleep(5)
    pub = _check(requests.post(f"{G}/{ig_user_id}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=60))
    link = _check(requests.get(f"{G}/{pub['id']}", params={"fields": "permalink", "access_token": token}, timeout=30))
    url = link.get("permalink", f"instagram media {pub['id']}")
    log(f"  posted to Instagram: {url}")
    return url


def facebook_video_source(fb_url, token):
    """The public MP4 link of a video already posted to the Facebook Page (Meta's own CDN)."""
    vid = fb_url.rstrip("/").split("/")[-1]
    for _ in range(36):  # Facebook needs a little time to process the reel
        d = requests.get(f"{G}/{vid}", params={"fields": "source", "access_token": token}, timeout=30).json()
        if d.get("source"):
            return d["source"]
        time.sleep(5)
    raise RuntimeError("Facebook video link not ready")


def _instagram_from_url(video_url, caption, ig_user_id, token):
    c = _check(requests.post(f"{G}/{ig_user_id}/media", data={
        "media_type": "REELS", "video_url": video_url, "caption": caption[:2200],
        "share_to_feed": "true", "access_token": token}, timeout=60))
    return _wait_and_publish(c["id"], ig_user_id, token)


def instagram_reel(video_path, caption, ig_user_id, token, tries=2, fallback_url=None):
    """Upload a Reel. Instagram's direct upload sometimes fails with "ProcessingFailedError";
    then we hand Instagram the same video from Facebook's servers instead (fallback_url)."""
    last = None
    for i in range(tries):
        try:
            return _instagram_once(video_path, caption, ig_user_id, token)
        except Exception as e:
            last = e
            log(f"  Instagram direct upload {i + 1}/{tries} failed: {str(e)[:160]}")
            time.sleep(15)
    if fallback_url:
        try:
            url = fallback_url() if callable(fallback_url) else fallback_url
            log("  trying Instagram via the Facebook copy of the video")
            return _instagram_from_url(url, caption, ig_user_id, token)
        except Exception as e:
            log(f"  Instagram via Facebook copy failed: {str(e)[:160]}")
            last = e
    raise last
