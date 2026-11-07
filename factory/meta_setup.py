"""One-time helper: turns a short-lived token from Meta's Graph API Explorer into the
permanent values the cloud factory needs (META_PAGE_ID, META_PAGE_TOKEN, IG_USER_ID).
Run META_SETUP.bat and follow the prompts. Nothing is sent anywhere except Meta."""
import requests

VER = "v24.0"
G = f"https://graph.facebook.com/{VER}"


def main():
    print("\n=== Meta (Instagram + Facebook) setup helper ===\n")
    app_id = input("1) Paste your Meta App ID: ").strip()
    app_secret = input("2) Paste your Meta App Secret: ").strip()
    short = input("3) Paste the User Access Token from Graph API Explorer: ").strip()
    r = requests.get(f"{G}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
        "fb_exchange_token": short}, timeout=30).json()
    if "access_token" not in r:
        print("\nCould not exchange the token:", r.get("error", r))
        return
    long_user = r["access_token"]
    pages = requests.get(f"{G}/me/accounts", params={"access_token": long_user, "fields": "id,name,access_token"}, timeout=30).json()
    data = pages.get("data", [])
    if not data:
        print("\nNo Facebook Pages found. Make sure you selected your Page when generating the token.", pages)
        return
    for i, p in enumerate(data):
        print(f"   [{i}] {p['name']}")
    idx = int(input("4) Type the number of the Page to post to: ").strip() or 0)
    page = data[idx]
    ig = requests.get(f"{G}/{page['id']}", params={"fields": "instagram_business_account", "access_token": page["access_token"]}, timeout=30).json()
    ig_id = ig.get("instagram_business_account", {}).get("id", "")
    print("\n================ COPY THESE INTO GITHUB SECRETS ================\n")
    print("META_PAGE_ID     =", page["id"])
    print("META_PAGE_TOKEN  =", page["access_token"])
    print("IG_USER_ID       =", ig_id or "(not found - is your Instagram a Business/Creator account linked to this Page?)")
    print("\n(The Page token above does not expire.)")


if __name__ == "__main__":
    main()
