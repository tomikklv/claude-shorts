#!/usr/bin/env python3
"""Upload finished shorts to YouTube as PRIVATE videos (YouTube Data API v3).

Standard library only. Uploads are always private. Making a video public is a
separate, explicit step (`publish VIDEO_ID --confirm`) that is only run when
the user asks for that specific video to go public.

Files (all outside the repository, never committed):
  ~/.config/shorts/client_secret.json   OAuth client (Desktop app) from Google Cloud
  ~/.config/shorts/token.json           created by `auth`, chmod 600
  ~/.config/shorts/youtube.json         optional {"account": "you@example.com"}
  ~/.config/shorts/uploads.jsonl        log of uploads (prevents double uploads)

Usage:
  youtube_upload.py auth [--account EMAIL] [--no-browser]
  youtube_upload.py whoami
  youtube_upload.py status VIDEO_ID
  youtube_upload.py publish VIDEO_ID [--confirm]
  youtube_upload.py upload --metadata upload.json [--confirm] [--force]
  youtube_upload.py upload --file short_01_yt.mp4 --title "..." \
        [--description "..."] [--hashtags "streamer,gta"] [--confirm]

Without --confirm `upload` is a dry run: it prints exactly what would be sent
and contacts nothing.

upload.json: [{"file": "...mp4", "title": "...", "description": "...",
               "hashtags": ["streamer", "gta"]}, ...]
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CFG = os.path.expanduser(os.environ.get("SHORTS_YT_CONFIG", "~/.config/shorts"))
CLIENT = os.path.join(CFG, "client_secret.json")
TOKEN = os.path.join(CFG, "token.json")
SETTINGS = os.path.join(CFG, "youtube.json")
UPLOADS = os.path.join(CFG, "uploads.jsonl")

MANAGE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"  # read status, change visibility
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", MANAGE_SCOPE, "openid", "email"]
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status")
CHUNK = 8 * 1024 * 1024
PRIVACY = "private"  # deliberately not configurable
CATEGORY_GAMING = "20"


def out(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def fail(msg, **extra):
    out({"error": msg, **extra})
    sys.exit(1)


def load_client():
    if not os.path.isfile(CLIENT):
        fail(f"OAuth client file not found: {CLIENT}")
    with open(CLIENT) as f:
        data = json.load(f)
    c = data.get("installed") or data.get("web")
    if not c:
        fail("client_secret.json is not a Desktop-app OAuth client")
    return c


def expected_account(cli_value=None):
    if cli_value:
        return cli_value.lower()
    if os.path.isfile(SETTINGS):
        with open(SETTINGS) as f:
            return (json.load(f).get("account") or "").lower() or None
    return None


def save_private(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2)
    os.chmod(path, 0o600)


def post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            body = json.loads(body)
            body = {k: body[k] for k in ("error", "error_description") if k in body}
        except ValueError:
            body = body[:300]
        fail("Google rejected the token request", status=e.code, detail=body)


def id_token_email(id_token):
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "").lower()
    except (IndexError, ValueError):
        return ""


def open_browser(url):
    for cmd in (["wslview", url], ["powershell.exe", "-NoProfile", "-Command",
                                   f"Start-Process '{url}'"], ["xdg-open", url]):
        try:
            if subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=20).returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    return False


def cmd_auth(args):
    client = load_client()
    account = expected_account(args.account)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "state" in q or "error" in q:
                result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Hotovo, toto okno môžeš zavrieť.</h2>".encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", args.port), Handler)
    redirect = f"http://localhost:{server.server_address[1]}"
    params = {"client_id": client["client_id"], "redirect_uri": redirect,
              "response_type": "code", "scope": " ".join(SCOPES), "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256",
              "access_type": "offline", "prompt": "consent select_account"}
    if account:
        params["login_hint"] = account
    url = client["auth_uri"] + "?" + urllib.parse.urlencode(params)

    opened = False if args.no_browser else open_browser(url)
    print(("Browser opened. " if opened else "") + "Sign in"
          + (f" as {account}" if account else "") + " at:\n\n" + url + "\n", file=sys.stderr)
    server.timeout = 5
    deadline = time.time() + args.timeout
    while "state" not in result and "error" not in result and time.time() < deadline:
        server.handle_request()
    server.server_close()

    if "error" in result:
        fail("Sign-in was refused", detail=result["error"])
    if result.get("state") != state or "code" not in result:
        fail("No sign-in received before the timeout (or state mismatch)")

    tok = post_form(client["token_uri"], {
        "code": result["code"], "client_id": client["client_id"],
        "client_secret": client["client_secret"], "redirect_uri": redirect,
        "grant_type": "authorization_code", "code_verifier": verifier})
    email = id_token_email(tok.get("id_token", ""))
    if account and email != account:
        fail(f"Signed in as {email or 'an unknown account'}, expected {account}. Token NOT saved.")
    if SCOPES[0] not in (tok.get("scope") or "").split():
        fail("Signed in, but the permission to upload videos to YouTube was not granted. "
             "Run auth again and tick the YouTube checkbox on Google's consent screen. Token NOT saved.")
    if "refresh_token" not in tok:
        fail("Google returned no refresh token. Remove the app's access at "
             "https://myaccount.google.com/permissions and run auth again.")
    save_private(TOKEN, {"refresh_token": tok["refresh_token"],
                         "access_token": tok["access_token"],
                         "expires_at": int(time.time()) + int(tok.get("expires_in", 0)) - 60,
                         "scope": tok.get("scope"), "account": email})
    out({"action": "auth", "account": email, "token_file": TOKEN, "status": "ok"})


def access_token():
    if not os.path.isfile(TOKEN):
        fail("Not signed in. Run: youtube_upload.py auth")
    with open(TOKEN) as f:
        tok = json.load(f)
    account = expected_account()
    if account and tok.get("account") != account:
        fail(f"Token belongs to {tok.get('account')}, expected {account}. Run auth again.")
    if SCOPES[0] not in (tok.get("scope") or "").split():
        fail("Saved token has no permission to upload to YouTube. Run auth again and tick "
             "the YouTube checkbox on Google's consent screen.")
    if tok.get("expires_at", 0) > time.time():
        return tok["access_token"], tok
    client = load_client()
    new = post_form(client["token_uri"], {
        "client_id": client["client_id"], "client_secret": client["client_secret"],
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    tok["access_token"] = new["access_token"]
    tok["expires_at"] = int(time.time()) + int(new.get("expires_in", 0)) - 60
    save_private(TOKEN, tok)
    return tok["access_token"], tok


def api_json(method, url, token, body=None):
    req = urllib.request.Request(url, method=method, headers={"Authorization": f"Bearer {token}"},
                                 data=json.dumps(body).encode() if body is not None else None)
    if body is not None:
        req.add_header("Content-Type", "application/json; charset=UTF-8")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, api_error(e)


def need_manage(tok):
    if MANAGE_SCOPE not in (tok.get("scope") or "").split():
        fail("The saved sign-in can only upload. Run auth again and tick every YouTube "
             "checkbox to allow reading the status and changing the visibility of videos.")


def fetch_video(video_id, token):
    data, err = api_json("GET", f"{API}/videos?part=snippet,status&id={urllib.parse.quote(video_id)}", token)
    if err:
        fail("YouTube refused the request", detail=err)
    if not data.get("items"):
        fail(f"Video {video_id} was not found on the channels of this account "
             "(deleted, or it belongs to another channel).")
    return data["items"][0]


def describe(item):
    sn, st = item["snippet"], item["status"]
    return {"video_id": item["id"], "title": sn.get("title"), "channel": sn.get("channelTitle"),
            "channel_id": sn.get("channelId"), "privacy": st.get("privacyStatus"),
            "upload_status": st.get("uploadStatus"), "rejection_reason": st.get("rejectionReason"),
            "failure_reason": st.get("failureReason"),
            "watch_url": f"https://youtu.be/{item['id']}",
            "studio_url": f"https://studio.youtube.com/video/{item['id']}/edit"}


def cmd_status(args):
    token, tok = access_token()
    need_manage(tok)
    out({"action": "status", "account": tok.get("account"), **describe(fetch_video(args.video_id, token))})


def cmd_publish(args):
    token, tok = access_token()
    need_manage(tok)
    item = fetch_video(args.video_id, token)
    before = describe(item)
    if not args.confirm:
        out({"action": "publish", "dry_run": True, "note": "Nothing changed. Re-run with --confirm "
             "to make this video PUBLIC.", "video": before})
        return
    st = item["status"]
    # videos.update replaces the whole part: send back the other mutable fields unchanged
    status = {k: st[k] for k in ("embeddable", "license", "publicStatsViewable",
                                 "selfDeclaredMadeForKids") if k in st}
    status["privacyStatus"] = "public"
    _, err = api_json("PUT", f"{API}/videos?part=status", token, {"id": item["id"], "status": status})
    if err:
        fail("YouTube refused to change the visibility", detail=err, video=before)
    after = describe(fetch_video(args.video_id, token))  # trust what YouTube reports, not the request
    out({"action": "publish", "dry_run": False, "account": tok.get("account"),
         "privacy_before": before["privacy"], "video": after,
         "status": "ok" if after["privacy"] == "public" else "not_public"})
    sys.exit(0 if after["privacy"] == "public" else 1)


def cmd_whoami(_):
    if not os.path.isfile(TOKEN):
        out({"signed_in": False, "token_file": TOKEN})
        return
    with open(TOKEN) as f:
        tok = json.load(f)
    out({"signed_in": True, "account": tok.get("account"), "scope": tok.get("scope"),
         "expected_account": expected_account(), "token_file": TOKEN})


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def already_uploaded():
    done = {}
    if os.path.isfile(UPLOADS):
        with open(UPLOADS) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    done[r["sha256"]] = r
    return done


def build_body(item):
    tags = [t.strip().lstrip("#").replace(" ", "") for t in item.get("hashtags") or [] if t.strip()]
    title = " ".join(item["title"].split())
    if len(title) > 100:
        title = title[:97].rstrip() + "..."
    desc = (item.get("description") or "").strip()
    hashline = " ".join("#" + t for t in dict.fromkeys(tags + ["Shorts"]))
    desc = (desc + "\n\n" + hashline).strip()[:5000]
    if "<" in title or ">" in title or "<" in desc or ">" in desc:
        fail("Title/description must not contain < or >", file=item["file"])
    return {"snippet": {"title": title, "description": desc, "tags": tags,
                        "categoryId": item.get("category_id", CATEGORY_GAMING)},
            "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False}}


def api_error(e):
    body = e.read().decode(errors="replace")
    try:
        err = json.loads(body).get("error", {})
        return {"status": e.code, "message": err.get("message"),
                "reason": (err.get("errors") or [{}])[0].get("reason")}
    except ValueError:
        return {"status": e.code, "message": body[:300]}


def upload_one(path, body, token):
    size = os.path.getsize(path)
    req = urllib.request.Request(UPLOAD_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            session = r.headers["Location"]
    except urllib.error.HTTPError as e:
        return None, api_error(e)

    sent = 0
    with open(path, "rb") as f:
        while sent < size:
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            put = urllib.request.Request(session, data=chunk, method="PUT", headers={
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {sent}-{end}/{size}"})
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(put, timeout=300) as r:
                        return json.load(r), None  # 200/201: finished
                except urllib.error.HTTPError as e:
                    if e.code == 308:  # chunk accepted, continue
                        break
                    if e.code >= 500 and attempt < 3:
                        time.sleep(2 ** attempt)
                        continue
                    return None, api_error(e)
                except urllib.error.URLError as e:
                    if attempt < 3:
                        time.sleep(2 ** attempt)
                        continue
                    return None, {"message": str(e.reason)}
            sent = end + 1
            print(json.dumps({"action": "upload", "file": os.path.basename(path),
                              "percent": round(100 * sent / size)}), file=sys.stderr, flush=True)
    return None, {"message": "upload ended without a response"}


def cmd_upload(args):
    if args.metadata:
        with open(args.metadata, encoding="utf-8") as f:
            items = json.load(f)
        items = items.get("videos", items) if isinstance(items, dict) else items
    elif args.file and args.title:
        items = [{"file": args.file, "title": args.title, "description": args.description or "",
                  "hashtags": [t for t in (args.hashtags or "").split(",") if t.strip()]}]
    else:
        fail("Give --metadata FILE, or --file and --title")

    plan = []
    done = already_uploaded()
    for it in items:
        path = os.path.expanduser(it["file"])
        if not os.path.isfile(path):
            fail(f"File not found: {path}")
        it["file"] = path
        digest = sha256_file(path)
        prev = done.get(digest)
        plan.append({"file": path, "size_mb": round(os.path.getsize(path) / 1048576, 1),
                     "sha256": digest, "body": build_body(it),
                     "already_uploaded": prev["video_id"] if prev else None})

    if not args.confirm:
        out({"action": "upload", "dry_run": True, "privacy": PRIVACY,
             "note": "Nothing was sent. Re-run with --confirm to upload.",
             "videos": [{k: p[k] for k in ("file", "size_mb", "already_uploaded", "body")} for p in plan]})
        return

    token, tok = access_token()
    results, failed = [], 0
    for p in plan:
        if p["already_uploaded"] and not args.force:
            results.append({"file": p["file"], "status": "skipped",
                            "reason": "already uploaded", "video_id": p["already_uploaded"]})
            continue
        res, err = upload_one(p["file"], p["body"], token)
        if err:
            failed += 1
            results.append({"file": p["file"], "status": "failed", "error": err})
            if err.get("reason") in ("quotaExceeded", "uploadLimitExceeded"):
                break  # no point trying the rest today
            continue
        rec = {"date": time.strftime("%Y-%m-%dT%H:%M:%S"), "file": p["file"], "sha256": p["sha256"],
               "video_id": res["id"], "title": res["snippet"]["title"],
               "privacy": res["status"]["privacyStatus"], "account": tok.get("account"),
               "channel": res["snippet"].get("channelTitle"),
               "channel_id": res["snippet"].get("channelId")}
        with open(UPLOADS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        results.append({"file": p["file"], "status": "uploaded", "video_id": res["id"],
                        "privacy": res["status"]["privacyStatus"],
                        "channel": res["snippet"].get("channelTitle"),
                        "studio_url": f"https://studio.youtube.com/video/{res['id']}/edit"})
    out({"action": "upload", "dry_run": False, "account": tok.get("account"),
         "uploaded": sum(r["status"] == "uploaded" for r in results), "failed": failed,
         "results": results})
    sys.exit(1 if failed else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("auth", help="sign in in the browser and save the token")
    a.add_argument("--account", help="Google account that must be used")
    a.add_argument("--no-browser", action="store_true", help="only print the link")
    a.add_argument("--port", type=int, default=0)
    a.add_argument("--timeout", type=int, default=300)
    sub.add_parser("whoami", help="show which account the saved token belongs to")
    st = sub.add_parser("status", help="show channel, visibility and processing state of a video")
    st.add_argument("video_id")
    pb = sub.add_parser("publish", help="make ONE uploaded video public (dry run without --confirm)")
    pb.add_argument("video_id")
    pb.add_argument("--confirm", action="store_true", help="really make it public")
    u = sub.add_parser("upload", help="upload as private (dry run without --confirm)")
    u.add_argument("--metadata")
    u.add_argument("--file")
    u.add_argument("--title")
    u.add_argument("--description")
    u.add_argument("--hashtags", help="comma separated, without #")
    u.add_argument("--confirm", action="store_true", help="really upload")
    u.add_argument("--force", action="store_true", help="upload even if this file was uploaded before")
    args = ap.parse_args()
    {"auth": cmd_auth, "whoami": cmd_whoami, "upload": cmd_upload,
     "status": cmd_status, "publish": cmd_publish}[args.cmd](args)


if __name__ == "__main__":
    main()
