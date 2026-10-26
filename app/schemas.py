from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, HttpUrl


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: int
    email: EmailStr
    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LinkCreate(BaseModel):
    target_url: HttpUrl
    custom_alias: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{3,16}$")
    expires_in_days: int | None = Field(default=None, ge=1, le=365)
    title: str | None = Field(default=None, max_length=120)


class LinkUpdate(BaseModel):
    target_url: HttpUrl | None = None
    title: str | None = Field(default=None, max_length=120)


class LinkOut(BaseModel):
    code: str
    short_url: str
    target_url: str
    title: str | None
    created_at: datetime
    expires_at: datetime | None
    total_clicks: int = 0


class DayCount(BaseModel):
    date: str
    clicks: int


class NameCount(BaseModel):
    name: str
    clicks: int


class LinkStats(BaseModel):
    code: str
    title: str | None
    target_url: str
    total_clicks: int
    last_7_days: list[DayCount]
    browsers: list[NameCount]
    devices: list[NameCount]
    referrers: list[NameCount]
