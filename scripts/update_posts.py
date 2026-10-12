"""Keep the LinkedIn column on portfolio.html current.

1. If APIFY_TOKEN is set, run the Apify LinkedIn posts actor and merge any
   new posts into data/posts.json.
2. Rebuild the post cards in portfolio.html (between the POSTS markers)
   from data/posts.json.

data/posts.json is the source of truth. Per post you can edit by hand:
  "title":  replaces the automatic headline (the post's first sentence)
  "hidden": true keeps a post off the site
Your edits are kept on every run.

Settings (environment variables, all optional except APIFY_TOKEN for fetching):
  APIFY_TOKEN           Apify API token (GitHub secret)
  LINKEDIN_PROFILE_URL  e.g. https://www.linkedin.com/in/yourname/
  APIFY_ACTOR           default harvestapi~linkedin-profile-posts
  APIFY_INPUT           full JSON input for the actor, overrides the default
  SINCE                 earliest post date to add, default 2026-07-28
  SHOW                  how many posts to list on the page, newest first, default 50
"""
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "posts.json"
PAGE = ROOT / "portfolio.html"
START, END = "<!-- POSTS:START -->", "<!-- POSTS:END -->"

SINCE = os.environ.get("SINCE", "2026-07-28")
SHOW = int(os.environ.get("SHOW", "50"))
ACTOR = os.environ.get("APIFY_ACTOR", "harvestapi~linkedin-profile-posts")


def post_id(url, fallback=""):
    m = re.search(r"(?:activity|share|ugcPost)[:%3A-]+(\d{15,})", url or "")
    return m.group(1) if m else str(fallback or url)


def date_from_id(pid):
    try:
        ms = int(pid) >> 22
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return ""


def pick_date(item, pid):
    for key in ("postedAt", "postedAtISO", "postedDate", "date", "createdAt"):
        v = item.get(key)
        if isinstance(v, dict):
            v = v.get("date") or v.get("timestamp")
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / (1000 if v > 1e11 else 1), tz=timezone.utc).strftime("%Y-%m-%d")
        if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
            return v[:10]
    return date_from_id(pid)


def is_repost(item):
    if item.get("isRepost") or item.get("repostedBy") or item.get("type") == "repost":
        return True
    header = item.get("header")
    return isinstance(header, dict) and "repost" in json.dumps(header).lower()


def fetch():
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("No APIFY_TOKEN; skipping fetch, rebuilding page only.")
        return []
    if os.environ.get("APIFY_INPUT"):
        payload = json.loads(os.environ["APIFY_INPUT"])
    else:
        profile = os.environ.get("LINKEDIN_PROFILE_URL")
        if not profile:
            sys.exit("Set LINKEDIN_PROFILE_URL (or APIFY_INPUT).")
        payload = {"targetUrls": [profile], "maxPosts": 30}
    url = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?token={token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        items = json.load(r)
    print(f"Apify returned {len(items)} items.")
    return items


def normalize(item):
    url = item.get("linkedinUrl") or item.get("postUrl") or item.get("url") or ""
    text = (item.get("content") or item.get("text") or item.get("commentary") or "").strip()
    if not url or not text or is_repost(item):
        return None
    pid = post_id(url, item.get("id") or item.get("entityId"))
    return {"id": pid, "date": pick_date(item, pid), "url": url, "text": text}


def key(text):
    return re.sub(r"\W+", "", text.lower())[:80]


def merge(posts, items):
    seen_ids = {p["id"] for p in posts}
    seen_text = {key(p["text"]) for p in posts}
    added = 0
    for item in items:
        p = normalize(item)
        if not p or p["date"] < SINCE:
            continue
        if p["id"] in seen_ids or key(p["text"]) in seen_text:
            continue
        posts.append(p)
        seen_ids.add(p["id"])
        seen_text.add(key(p["text"]))
        added += 1
    print(f"Added {added} new post(s).")
    return posts


def headline(post):
    if post.get("title"):
        return post["title"]
    text = re.sub(r"#\w+", "", post["text"]).strip()
    first = re.split(r"(?<=[.!?])\s+|\n", text, maxsplit=1)[0].strip()
    return first or text[:160]


def nice_date(d):
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%b %-d, %Y")
    except ValueError:
        return ""


def card(post):
    label = "LinkedIn Post"
    if nice_date(post["date"]):
        label += " · " + nice_date(post["date"])
    return f'''<div class="bg-white rounded-2xl border border-slate-200 border-l-4 border-l-red-700 p-5 shadow-sm hover:shadow-md transition-shadow flex flex-col gap-3">
<div>
<span class="text-xs font-bold tracking-widest text-red-700 uppercase block mb-1">{html.escape(label)}</span>
<h3 class="text-lg font-bold text-blue-900 leading-snug">{html.escape(headline(post))}</h3>
</div>
<a href="{html.escape(post["url"], quote=True)}"
target="_blank" rel="noopener noreferrer"
class="self-start inline-flex items-center gap-2 bg-slate-900 text-white text-sm font-semibold px-4 py-2 rounded-lg hover:bg-red-700 transition-colors">
Read on LinkedIn <span>→</span>
</a>
</div>'''


def render(posts):
    visible = sorted((p for p in posts if not p.get("hidden")),
                     key=lambda p: p["date"], reverse=True)[:SHOW]
    block = "\n".join(card(p) for p in visible)
    page = PAGE.read_text(encoding="utf-8")
    a, b = page.index(START) + len(START), page.index(END)
    PAGE.write_text(page[:a] + "\n" + block + "\n" + page[b:], encoding="utf-8")
    print(f"Page shows {len(visible)} post(s).")


def main():
    posts = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
    posts = merge(posts, fetch())
    posts.sort(key=lambda p: p["date"], reverse=True)
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text(json.dumps(posts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    render(posts)


if __name__ == "__main__":
    main()
