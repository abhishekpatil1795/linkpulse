from sqlalchemy import inspect, text

from .database import engine

# Lightweight "add column if missing" migration so existing databases keep working.
# (Alembic is the proper long-term tool; this keeps the demo dependency-free.)
ADDITIONS = {
    "links": {"title": "VARCHAR(120)"},
    "clicks": {"browser": "VARCHAR(32)", "device": "VARCHAR(16)", "referrer": "VARCHAR(255)"},
}


def run() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in ADDITIONS.items():
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
