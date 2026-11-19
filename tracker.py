#!/usr/bin/env python3
"""GTA VI live newsroom.

Polls gaming-news RSS feeds and Google News every minute, keeps every GTA VI
story it finds in data/feed.json, flags likely record-breaking headlines, and
serves a live dashboard at http://localhost:8765/live.html.

No installs needed: Python 3.8+ standard library only.
Run:  python3 tracker.py          (Ctrl+C to stop)
"""
import email.utils
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FEED_FILE = os.path.join(DATA, "feed.json")
PORT = int(os.environ.get("PORT", "8765"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
NOTIFY = os.environ.get("NOTIFY", "1") == "1"   # macOS notification on new record headlines
AIPIPE_TOKEN = os.environ.get("AIPIPE_TOKEN", "").strip()
AIPIPE_URL = "https://aipipe.org/openai/v1/chat/completions"
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "gpt-5-nano")
SUMMARY_BATCH = 25
SUMMARY_MAX = int(os.environ.get("SUMMARY_MAX", "100"))   # stories summarised per poll
SUMMARY_PROMPT = (
    "You write summaries for a GTA VI news dashboard. For each story, write one plain sentence of at most "
    "30 words saying what the story reports. Use only facts in its headline and snippet. Never add numbers, "
    "names, dates or claims that are not in the input. If there is no snippet, restate the headline's claim "
    "in plain words. Reply with JSON: {\"summaries\": [{\"n\": <story number>, \"summary\": \"...\"}]}."
)

def gnews(q):
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q + " when:1d", "hl": "en-US", "gl": "US", "ceid": "US:en"})

SOURCES = [
    ("Google News", gnews('"GTA 6" OR "GTA VI" OR "Grand Theft Auto VI"')),
    ("Google News", gnews('"GTA 6" record')),
    ("Google News", gnews('"GTA 6" sales OR viewers OR Twitch OR Metacritic')),
    ("IGN", "https://feeds.feedburner.com/ign/all"),
    ("VGC", "https://www.videogameschronicle.com/feed/"),
    ("Eurogamer", "https://www.eurogamer.net/feed"),
    ("GamesRadar+", "https://www.gamesradar.com/feeds/all/"),
    ("Dexerto", "https://www.dexerto.com/feed/"),
    ("Insider Gaming", "https://insider-gaming.com/feed/"),
    ("Push Square", "https://www.pushsquare.com/feeds/latest"),
    ("Pure Xbox", "https://www.purexbox.com/feeds/latest"),
    ("Dot Esports", "https://dotesports.com/feed"),
    ("The Verge", "https://www.theverge.com/rss/games/index.xml"),
    ("r/GTA6", "https://www.reddit.com/r/GTA6/new/.rss"),
]

GTA = re.compile(r"\b(gta\s?(6|vi)\b|grand theft auto\s?(6|vi)\b|leonida|vice city|rockstar|take-two)", re.I)
RECORD = re.compile(r"\b(record|records|all[- ]time|highest|ever|biggest|largest|fastest|most[- ](watched|played|pre-?ordered|viewed|sold)|"
                    r"history|historic|shatter\w*|smash\w*|break[s]?|broke|beats?|surpass\w*|tops?|first[- ]ever|milestone|peak)\b", re.I)
TOPICS = [
    ("Streaming", re.compile(r"twitch|kick|youtube|stream|viewer|ccv|concurrent", re.I)),
    ("Sales", re.compile(r"sales|sold|copies|units|revenue|pre-?order|billion|\$\d|take-two|stock|shares|sensor tower|circana", re.I)),
    ("Reviews", re.compile(r"review|metacritic|opencritic|score|/10|stars", re.I)),
    ("Outage", re.compile(r"psn|xbox live|outage|down\b|servers?|crash|error", re.I)),
    ("Players", re.compile(r"players|playing|player count|million people", re.I)),
]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) GTA6-Live-Tracker/1.0"

lock = threading.Lock()
state = {"items": {}, "sources": {}, "started": None, "polls": 0}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load():
    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(FEED_FILE):
        try:
            with open(FEED_FILE) as f:
                saved = json.load(f)
            state["items"] = {i["id"]: i for i in saved.get("items", [])}
        except Exception as e:
            print("Could not read saved feed, starting fresh:", e)


def save():
    tmp = FEED_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snapshot(), f, ensure_ascii=False, indent=1)
    os.replace(tmp, FEED_FILE)


def snapshot():
    with lock:
        items = sorted(state["items"].values(), key=lambda i: i["published"], reverse=True)
        return {"updated": now_iso(), "started": state["started"], "polls": state["polls"],
                "poll_seconds": POLL_SECONDS, "sources": state["sources"], "items": items}


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_date(s):
    if not s:
        return now_iso()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return now_iso()
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def text(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None:
            if found.text and found.text.strip():
                return found.text.strip()
            if found.get("href"):
                return found.get("href")
    return ""


def parse_feed(raw):
    root = ET.fromstring(raw)
    atom = "{http://www.w3.org/2005/Atom}"
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        out.append({"title": text(it, "title"), "link": text(it, "link"),
                    "published": text(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date"),
                    "summary": text(it, "description"),
                    "outlet": src.text.strip() if src is not None and src.text else ""})
    for it in root.iter(atom + "entry"):
        link = it.find(atom + "link")
        out.append({"title": text(it, atom + "title"), "link": link.get("href") if link is not None else "",
                    "published": text(it, atom + "published", atom + "updated"),
                    "summary": text(it, atom + "summary", atom + "content"), "outlet": ""})
    return out


def norm(title):
    t = re.sub(r"\s+-\s+[^-]+$", "", title.lower())  # drop " - Outlet" suffix from Google News
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def classify(title, summary):
    blob = title + " " + summary
    topic = next((name for name, rx in TOPICS if rx.search(blob)), "News")
    return topic, bool(RECORD.search(title))


def notify(item):
    if not NOTIFY or sys.platform != "darwin":
        return
    t = item["title"].replace('"', "'")[:180]
    try:
        subprocess.run(["osascript", "-e", f'display notification "{t}" with title "GTA VI record alert" sound name "Glass"'],
                       timeout=5, check=False)
    except Exception:
        pass


def poll_once(first=False):
    new_records = []
    added = 0
    for name, url in SOURCES:
        key = name + "|" + url
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml"})
            with urllib.request.urlopen(req, timeout=20) as r:
                entries = parse_feed(r.read())
            ok, err = True, ""
        except Exception as e:
            entries, ok, err = [], False, str(e)[:120]
        hits = 0
        for e in entries:
            title = strip_tags(e["title"])
            summary = strip_tags(e["summary"])[:400]
            if not title or not GTA.search(title + " " + summary):
                continue
            if name != "Google News" and not GTA.search(title) and name != "r/GTA6":
                continue
            hits += 1
            iid = norm(title)[:140]
            with lock:
                if iid in state["items"]:
                    continue
                topic, rec = classify(title, summary)
                outlet = e["outlet"] or name
                if name == "Google News":
                    m = re.search(r"\s+-\s+([^-]+)$", title)
                    if m:
                        outlet = m.group(1).strip()
                        title = title[: m.start()].strip()
                item = {"id": iid, "title": title, "link": e["link"], "outlet": outlet, "via": name,
                        "published": parse_date(e["published"]), "seen": now_iso(),
                        "summary": summary if name not in ("Google News",) else "",
                        "topic": topic, "record": rec}
                state["items"][iid] = item
                added += 1
                if rec and not first:
                    new_records.append(item)
        with lock:
            state["sources"][key] = {"name": name, "ok": ok, "error": err, "hits": hits, "checked": now_iso()}
    with lock:
        state["polls"] += 1
    save()
    for it in new_records[:3]:
        notify(it)
    stamp = datetime.now().strftime("%H:%M:%S")
    ok_count = sum(1 for s in state["sources"].values() if s["ok"])
    print(f"[{stamp}] poll {state['polls']}: +{added} new stories ({len(new_records)} record headlines) · "
          f"{ok_count}/{len(SOURCES)} feeds OK · {len(state['items'])} total", flush=True)


def summarise_batch(batch):
    stories = "\n\n".join(
        f"Story {n}\nHeadline: {it['title']}\nOutlet: {it['outlet']}\nSnippet: {it['summary'] or '(none)'}"
        for n, it in enumerate(batch, 1))
    body = json.dumps({"model": SUMMARY_MODEL, "reasoning_effort": "minimal",
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": SUMMARY_PROMPT},
                                    {"role": "user", "content": stories}]}).encode()
    req = urllib.request.Request(AIPIPE_URL, data=body, headers={
        "Authorization": "Bearer " + AIPIPE_TOKEN, "Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        reply = json.load(r)["choices"][0]["message"]["content"]
    out = {}
    for s in json.loads(reply).get("summaries", []):
        try:
            n = int(s.get("n"))
        except (TypeError, ValueError):
            continue
        text_ = str(s.get("summary", "")).strip()
        if 1 <= n <= len(batch) and text_:
            out[batch[n - 1]["id"]] = text_[:300]
    return out


def summarise():
    if not AIPIPE_TOKEN:
        return
    with lock:
        todo = sorted((i for i in state["items"].values() if not i.get("ai_summary")),
                      key=lambda i: i["published"], reverse=True)[:SUMMARY_MAX]
    done = 0
    for k in range(0, len(todo), SUMMARY_BATCH):
        try:
            got = summarise_batch(todo[k:k + SUMMARY_BATCH])
        except Exception as e:
            print("summary error:", str(e)[:200], flush=True)
            break
        with lock:
            for iid, s in got.items():
                if iid in state["items"]:
                    state["items"][iid]["ai_summary"] = s
                    done += 1
    if done:
        save()
    print(f"summaries: +{done} ({len(todo) - done} still waiting)", flush=True)


def poller():
    first = not state["items"]
    while True:
        try:
            poll_once(first=first)
            summarise()
        except Exception as e:
            print("poll error:", e, flush=True)
        first = False
        time.sleep(POLL_SECONDS)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def do_GET(self):
        if self.path.startswith("/api/feed"):
            body = json.dumps(snapshot(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in ("/", ""):
            self.path = "/live.html"
        return super().do_GET()

    def log_message(self, *a):
        pass


def main():
    load()
    state["started"] = now_iso()
    if "--once" in sys.argv[1:]:
        poll_once(first=not state["items"])
        summarise()
        return
    threading.Thread(target=poller, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"GTA VI live newsroom running.\n  Live dashboard: http://localhost:{PORT}/live.html\n"
          f"  Analysis page:  http://localhost:{PORT}/index.html\n  Polling {len(SOURCES)} feeds every {POLL_SECONDS}s. Ctrl+C to stop.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Stories are saved in data/feed.json")


if __name__ == "__main__":
    main()
