from flask import Flask, request, render_template, redirect, url_for, flash
import sqlite3
import threading
import time
import os
from urllib.parse import urlparse, urljoin, urldefrag
from urllib.robotparser import RobotFileParser
from collections import deque
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
app.secret_key = "change-this-before-deploying"
DB_PATH = "shylark.db"
USER_AGENT = "ShylarkBot/0.1 (+https://example.com/bot-info)"
MAX_PAGE_BYTES = 1_500_000
CRAWL_DELAY_SECONDS = 1.0
crawl_lock = threading.Lock()
stop_crawl = threading.Event()
crawl_status = {"running": False, "indexed": 0, "message": "Ready to crawl."}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            url TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            body TEXT NOT NULL,
            crawled_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_url ON pages(url)")

def clean_url(url):
    url, _ = urldefrag(url)
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    if p.username or p.password:
        return None
    return p._replace(fragment="", query="").geturl().rstrip("/")

def robots_allowed(url):
    p = urlparse(url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        response = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=5)
        if response.status_code == 404:
            return True
        if response.status_code >= 400:
            return False
        parser.parse(response.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except requests.RequestException:
        # Fail closed when robots.txt cannot be checked.
        return False

def save_page(url, title, description, body):
    with db() as conn:
        conn.execute("""
        INSERT INTO pages(url, title, description, body, crawled_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(url) DO UPDATE SET
          title=excluded.title, description=excluded.description,
          body=excluded.body, crawled_at=CURRENT_TIMESTAMP
        """, (url, title[:500], description[:1000], body[:200000]))

def crawl(seeds, max_pages=100):
    if not crawl_lock.acquire(blocking=False):
        return
    stop_crawl.clear()
    crawl_status.update({"running": True, "indexed": 0, "message": "Crawling started."})
    try:
        queue = deque((u, 0) for u in seeds)
        seen = set()
        while queue and len(seen) < max_pages and not stop_crawl.is_set():
            raw_url, depth = queue.popleft()
            url = clean_url(raw_url)
            if not url or url in seen:
                continue
            seen.add(url)
            if not robots_allowed(url):
                crawl_status["message"] = f"Skipped disallowed or unverifiable robots policy: {url}"
                continue
            try:
                response = requests.get(
                    url, headers={"User-Agent": USER_AGENT},
                    timeout=10, stream=True, allow_redirects=True
                )
                content_type = response.headers.get("Content-Type", "").lower()
                if response.status_code != 200 or "text/html" not in content_type:
                    response.close()
                    continue
                data = response.raw.read(MAX_PAGE_BYTES + 1, decode_content=True)
                response.close()
                if len(data) > MAX_PAGE_BYTES:
                    continue
                soup = BeautifulSoup(data, "html.parser")
                for tag in soup(["script", "style", "noscript", "svg", "header", "footer"]):
                    tag.decompose()
                title = soup.title.get_text(" ", strip=True) if soup.title else urlparse(url).netloc
                desc_tag = soup.find("meta", attrs={"name": "description"})
                description = desc_tag.get("content", "").strip() if desc_tag else ""
                body = " ".join(soup.get_text(" ", strip=True).split())
                if body:
                    save_page(response.url, title, description, body)
                    crawl_status["indexed"] += 1
                    crawl_status["message"] = f"Indexed {crawl_status['indexed']} page(s); last: {response.url}"
                if depth < 2:
                    for a in soup.find_all("a", href=True):
                        nxt = clean_url(urljoin(response.url, a["href"]))
                        if nxt and urlparse(nxt).netloc == urlparse(url).netloc and nxt not in seen:
                            queue.append((nxt, depth + 1))
            except (requests.RequestException, ValueError):
                pass
            time.sleep(CRAWL_DELAY_SECONDS)
        crawl_status["message"] = f"Finished. Indexed {crawl_status['indexed']} page(s) in this run."
    finally:
        crawl_status["running"] = False
        crawl_lock.release()

def search_pages(query, limit=20):
    terms = [t.lower() for t in query.split() if len(t) > 1][:12]
    if not terms:
        return []
    rows = []
    with db() as conn:
        all_pages = conn.execute("SELECT url, title, description, body, crawled_at FROM pages").fetchall()
    for row in all_pages:
        title = row["title"] or ""
        desc = row["description"] or ""
        body = row["body"] or ""
        hay = (title + " " + desc + " " + body).lower()
        score = sum(hay.count(term) for term in terms)
        title_score = sum(title.lower().count(term) for term in terms)
        if score:
            rows.append({
                "url": row["url"], "title": title or row["url"],
                "description": desc or make_snippet(body, terms),
                "score": score + title_score * 5,
                "crawled_at": row["crawled_at"]
            })
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:limit]

def make_snippet(body, terms):
    text = body or ""
    lower = text.lower()
    positions = [lower.find(t) for t in terms if lower.find(t) >= 0]
    start = max(0, (min(positions) if positions else 0) - 90)
    return ("…" if start else "") + text[start:start+260] + ("…" if start+260 < len(text) else "")

@app.route("/")
def home():
    query = request.args.get("q", "").strip()[:200]
    results = search_pages(query) if query else []
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    return render_template("index.html", query=query, results=results, page_count=count)

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        seeds = [line.strip() for line in request.form.get("seeds", "").splitlines() if line.strip()]
        try:
            max_pages = max(1, min(500, int(request.form.get("max_pages", "50"))))
        except ValueError:
            max_pages = 50
        valid = [clean_url(u) for u in seeds]
        valid = [u for u in valid if u]
        if not valid:
            flash("Enter at least one valid http:// or https:// URL.")
        elif crawl_status["running"]:
            flash("A crawl is already running.")
        else:
            threading.Thread(target=crawl, args=(valid, max_pages), daemon=True).start()
            flash("Crawl started. Refresh this page to see its status.")
        return redirect(url_for("admin"))
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    return render_template("admin.html", status=crawl_status, page_count=count)

@app.route("/admin/stop", methods=["POST"])
def stop():
    stop_crawl.set()
    flash("Stop requested; the crawler will stop after its current request.")
    return redirect(url_for("admin"))

@app.route("/health")
def health():
    return {"ok": True, "indexed_pages": crawl_status["indexed"], "running": crawl_status["running"]}

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
