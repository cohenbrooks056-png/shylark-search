# Shylark Search Engine (starter MVP)

This project is a starter search engine that maintains its **own local page index** in SQLite. It does not send searches to DuckDuckGo, Google, or another search provider. Search results come only from pages Shylark has crawled.

## Run locally

Requires Python 3.10+.

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 and visit http://127.0.0.1:5000/admin to add starting URLs.

## How it works

- `/admin` accepts seed URLs and starts a bounded crawler.
- The crawler checks each domain's `robots.txt`, stays on each seed domain, uses a delay, and limits each run to 500 pages.
- Crawled page text and metadata are stored in `shylark.db`.
- Search ranks matching pages from Shylark's own database, with extra weight for title matches.

## Before public launch

This is a learning MVP, not yet a production web-scale engine. It starts with an empty index and has no distributed crawling, robust ranking, duplicate detection, safe-search pipeline, malware filtering, admin authentication, or abuse protection. Do not expose `/admin` to the public without authentication and rate limits. Set a real contact URL and descriptive user agent, review each site's policies, and obey applicable law and robots directives. Keep crawl volume modest.

For deployment, use a production WSGI server and persistent disk for `shylark.db`. SQLite data on ephemeral hosting may be lost after restart.


## Publish it online (managed hosting)

The project now includes a `render.yaml` blueprint for Render.

1. Create a GitHub account and make a new repository named `shylark-search`.
2. Upload the files inside this project folder to that repository (keep the `templates` and `static` folders).
3. In Render, choose **New → Blueprint** and connect the GitHub repository.
4. Review the service and deploy it. Render will provide a public `onrender.com` address.
5. Anyone with that address can open Shylark without your Chromebook running.

Hosting caveats:
- A free Render web service can sleep after 15 minutes without traffic and take around a minute to wake.
- Its local filesystem is temporary, so the SQLite index can be lost after restarts/redeploys. For a lasting shared index, switch SQLite to a persistent database such as managed PostgreSQL and schedule crawling as a separate, controlled job.
- The current crawler is intentionally limited and will not crawl pages that disallow it or sites that restrict automated access. Do not try to bypass those restrictions. For YouTube-specific video search, use the official YouTube Data API and follow its policies; that is a separate integration and requires an API key.
- Before making the crawler publicly accessible, add authentication to `/admin`, use a real contact URL in the crawler user-agent, and configure abuse controls.
