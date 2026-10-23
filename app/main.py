import secrets
import string
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import schemas
from .config import BASE_URL, RATE_LIMIT_PER_MIN
from .database import Base, engine, get_db
from .models import Click, Link, User
from .security import create_token, get_current_user, hash_password, verify_password

Base.metadata.create_all(bind=engine)

app = FastAPI(title="LinkPulse", description="URL shortener with click analytics", version="1.0.0")

ALPHABET = string.ascii_letters + string.digits
RESERVED = {"docs", "redoc", "openapi.json", "health", "auth", "links"}

# --- simple in-memory sliding-window rate limiter (use Redis for multi-instance) ---
_hits: dict[int, deque] = defaultdict(deque)


def rate_limit(user: User = Depends(get_current_user)) -> User:
    now, q = time.time(), _hits[user.id]
    while q and q[0] < now - 60:
        q.popleft()
    if len(q) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")
    q.append(now)
    return user


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _to_out(link: Link) -> schemas.LinkOut:
    return schemas.LinkOut(code=link.code, short_url=f"{BASE_URL}/{link.code}",
                           target_url=link.target_url, created_at=link.created_at,
                           expires_at=link.expires_at)


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


@app.post("/links", response_model=schemas.LinkOut, status_code=201)
def create_link(body: schemas.LinkCreate, user: User = Depends(rate_limit), db: Session = Depends(get_db)):
    expires = (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
               if body.expires_in_days else None)
    if body.custom_alias:
        if body.custom_alias.lower() in RESERVED:
            raise HTTPException(400, "Alias is reserved")
        link = Link(code=body.custom_alias, target_url=str(body.target_url),
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
            link = Link(code=code, target_url=str(body.target_url),
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
    return _to_out(link)


@app.get("/links", response_model=list[schemas.LinkOut])
def list_links(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    links = db.scalars(select(Link).where(Link.owner_id == user.id).order_by(Link.created_at.desc()))
    return [_to_out(l) for l in links]


def _owned_link(code: str, user: User, db: Session) -> Link:
    link = db.scalar(select(Link).where(Link.code == code, Link.owner_id == user.id))
    if not link:
        raise HTTPException(404, "Link not found")
    return link


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
    return schemas.LinkStats(code=code, total_clicks=total,
                             last_7_days=[schemas.DayCount(date=str(d), clicks=c) for d, c in rows])


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
    db.add(Click(link_id=link.id, user_agent=(request.headers.get("user-agent") or "")[:512]))
    db.commit()
    return RedirectResponse(link.target_url, status_code=307)
