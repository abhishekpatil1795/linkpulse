# LinkPulse

URL shortener with per-link click analytics. Built with FastAPI, SQLAlchemy 2.0, PostgreSQL, JWT auth, Docker and GitHub Actions CI.

## Features
- JWT authentication (bcrypt password hashing)
- Create short links with random 7-char codes or custom aliases, optional expiry
- 307 redirect that records every click (timestamp, user agent)
- Per-link stats: total clicks and daily clicks for the last 7 days
- Per-user sliding-window rate limiting
- Click analytics by browser, device and referrer; QR code per link; link titles and editing
- One-click guest sessions (no sign-up needed to try it)
- Single-page web UI served by the API itself (no build step)
- Ownership checks: users can only view or delete their own links
- 9 automated tests, run on every push via GitHub Actions

## Run
```bash
docker compose up --build        # API on http://localhost:8000, docs at /docs
# or locally with SQLite:
pip install -r requirements.txt && uvicorn app.main:app --reload
pytest -q
```

## Design decisions
- Random codes from `secrets` (62^7 ≈ 3.5 trillion), with retry on unique-constraint collision
- Indexes on `links.code` (hot redirect path) and `clicks.link_id` (analytics)
- Rate limiter is in-memory; would move to Redis for multiple instances
- Click write happens on the redirect path; at scale I'd move it to a queue

## Roadmap
Alembic migrations, Redis cache for redirects, async click logging, deploy to Render/Railway.
