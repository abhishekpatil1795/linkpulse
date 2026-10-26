import io
import secrets
import string
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import segno
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import migrate, schemas
from .config import BASE_URL, RATE_LIMIT_PER_MIN
from .database import Base, engine, get_db
from .models import Click, Link, User
from .security import create_token, get_current_user, hash_password, verify_password
from .useragent import parse_ua, referrer_host

Base.metadata.create_all(bind=engine)
migrate.run()

app = FastAPI(title="LinkPulse", description="URL shortener with click analytics", version="2.0.0")

ALPHABET = string.ascii_letters + string.digits
RESERVED = {"docs", "redoc", "openapi.json", "health", "auth", "links", "static"}
STATIC_DIR = Path(__file__).parent / "static"

# --- simple in-memory sliding-window rate limiters (use Redis for multi-instance) ---
_hits: dict[int, deque] = defaultdict(deque)
_demo_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(user: User = Depends(get_current_user)) -> User:
    now, q = time.time(), _hits[user.id]
    while q and q[0] < now - 60:
        q.popleft()
    if len(q) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")
    q.append(now)
    return user


def public_base(request: Request) -> str:
    if BASE_URL:
        return BASE_URL.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "localhost")
    return f"{proto}://{host}"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _to_out(link: Link, base: str, clicks: int = 0) -> schemas.LinkOut:
    return schemas.LinkOut(code=link.code, short_url=f"{base}/{link.code}", target_url=link.target_url,
                           title=link.title, created_at=link.created_at, expires_at=link.expires_at,
                           total_clicks=clicks)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/register", response_model=schemas.UserOut, status_code=201)
def register(body: schemas.UserCreate, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "Email already registered")
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/login", response_model=schemas.Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == form.username.lower()))
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    return schemas.Token(access_token=create_token(user.id))


@app.post("/auth/demo", response_model=schemas.Token)
def demo_session(request: Request, db: Session = Depends(get_db)):
    """Creates a throwaway guest account so visitors can try the app without signing up."""
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown")
    now, q = time.time(), _demo_hits[ip]
    while q and q[0] < now - 3600:
        q.popleft()
    if len(q) >= 10:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many guest sessions. Try again later.")
    q.append(now)
    user = User(email=f"guest-{secrets.token_hex(5)}@guest.linkpulse.app",
                password_hash=hash_password(secrets.token_urlsafe(16)))
    db.add(user)
    db.commit()
    db.refresh(user)
    return schemas.Token(access_token=create_token(user.id))


@app.post("/links", response_model=schemas.LinkOut, status_code=201)
def create_link(body: schemas.LinkCreate, request: Request, user: User = Depends(rate_limit),
                db: Session = Depends(get_db)):
    expires = (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
               if body.expires_in_days else None)
    title = (body.title or "").strip() or None
    if body.custom_alias:
        if body.custom_alias.lower() in RESERVED:
            raise HTTPException(400, "Alias is reserved")
        link = Link(code=body.custom_alias, target_url=str(body.target_url), title=title,
                    owner_id=user.id, expires_at=expires)
        db.add(link)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Alias already taken")
    else:
        for _ in range(5):  # retry on the (very rare) random collision
            code = "".join(secrets.choice(ALPHABET) for _ in range(7))
            link = Link(code=code, target_url=str(body.target_url), title=title,
                        owner_id=user.id, expires_at=expires)
            db.add(link)
            try:
                db.commit()
                break
            except IntegrityError:
                db.rollback()
        else:
            raise HTTPException(500, "Could not generate a unique code")
    db.refresh(link)
    return _to_out(link, public_base(request))


@app.get("/links", response_model=list[schemas.LinkOut])
def list_links(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Link, func.count(Click.id))
        .outerjoin(Click, Click.link_id == Link.id)
        .where(Link.owner_id == user.id)
        .group_by(Link.id)
        .order_by(Link.created_at.desc())
    ).all()
    base = public_base(request)
    return [_to_out(link, base, n) for link, n in rows]


def _owned_link(code: str, user: User, db: Session) -> Link:
    link = db.scalar(select(Link).where(Link.code == code, Link.owner_id == user.id))
    if not link:
        raise HTTPException(404, "Link not found")
    return link


def _breakdown(db: Session, link_id: int, col) -> list[schemas.NameCount]:
    n = func.count(Click.id)
    rows = db.execute(select(col, n).where(Click.link_id == link_id)
                      .group_by(col).order_by(n.desc()).limit(6)).all()
    return [schemas.NameCount(name=name or "Unknown", clicks=c) for name, c in rows]


@app.get("/links/{code}/stats", response_model=schemas.LinkStats)
def link_stats(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    link = _owned_link(code, user, db)
    total = db.scalar(select(func.count(Click.id)).where(Click.link_id == link.id)) or 0
    since = datetime.now(timezone.utc) - timedelta(days=7)
    day = func.date(Click.clicked_at)
    rows = db.execute(
        select(day, func.count(Click.id))
        .where(Click.link_id == link.id, Click.clicked_at >= since)
        .group_by(day).order_by(day)
    ).all()
    return schemas.LinkStats(
        code=code, title=link.title, target_url=link.target_url, total_clicks=total,
        last_7_days=[schemas.DayCount(date=str(d), clicks=c) for d, c in rows],
        browsers=_breakdown(db, link.id, Click.browser),
        devices=_breakdown(db, link.id, Click.device),
        referrers=_breakdown(db, link.id, Click.referrer),
    )


@app.get("/links/{code}/qr", include_in_schema=True)
def link_qr(code: str, request: Request, db: Session = Depends(get_db)):
    if not db.scalar(select(Link.id).where(Link.code == code)):
        raise HTTPException(404, "Link not found")
    buf = io.BytesIO()
    segno.make(f"{public_base(request)}/{code}", error="m").save(buf, kind="svg", scale=8, border=2)
    return Response(buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.patch("/links/{code}", response_model=schemas.LinkOut)
def update_link(code: str, body: schemas.LinkUpdate, request: Request,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    link = _owned_link(code, user, db)
    if body.target_url is not None:
        link.target_url = str(body.target_url)
    if "title" in body.model_fields_set:
        link.title = (body.title or "").strip() or None
    db.commit()
    db.refresh(link)
    return _to_out(link, public_base(request))


@app.delete("/links/{code}", status_code=204)
def delete_link(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_owned_link(code, user, db))
    db.commit()


@app.get("/{code}", include_in_schema=False)
def redirect(code: str, request: Request, db: Session = Depends(get_db)):
    link = db.scalar(select(Link).where(Link.code == code))
    if not link:
        raise HTTPException(404, "Short link not found")
    if link.expires_at and _aware(link.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(410, "Link has expired")
    ua = request.headers.get("user-agent") or ""
    browser, device = parse_ua(ua)
    db.add(Click(link_id=link.id, user_agent=ua[:512], browser=browser, device=device,
                 referrer=referrer_host(request.headers.get("referer"))))
    db.commit()
    return RedirectResponse(link.target_url, status_code=307)
