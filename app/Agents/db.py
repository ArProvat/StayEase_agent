
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Generator

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)


_pool: ThreadedConnectionPool | None = None


def init_pool(dsn: str | None = None, min_conn: int = 2, max_conn: int = 10) -> None:
    """
    Initialise the global connection pool. Safe to call multiple times —
    subsequent calls are no-ops if the pool is already open.
    """
    global _pool
    if _pool is not None:
        return
    dsn = dsn or os.environ["DATABASE_URL"]
    _pool = ThreadedConnectionPool(
        minconn=min_conn,
        maxconn=max_conn,
        dsn=dsn,
        cursor_factory=psycopg2.extras.RealDictCursor,  # rows as dicts everywhere
    )
    logger.info("psycopg2 pool created (min=%d max=%d)", min_conn, max_conn)


def close_pool() -> None:
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
        logger.info("psycopg2 pool closed")


@contextmanager
def get_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Yield a connection from the pool. Commits on clean exit, rolls back on
    exception, and always returns the connection to the pool.
    """
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_pool() at startup")
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


#
def search_properties(
    location: str,
    check_in: date,
    check_out: date,
    num_guests: int,
    bedrooms: int | None = None,
) -> list[dict[str, Any]]:
    """
    Return active listings in `location` that can accommodate `num_guests`
    and have no confirmed booking overlapping [check_in, check_out).
    """
    sql = """
        SELECT
            l.id,
            l.title,
            l.location,
            l.district,
            l.price_per_night,
            l.max_guests,
            l.bedrooms,
            l.bathrooms,
            l.amenities,
            l.cancellation_policy
        FROM listings l
        WHERE
            LOWER(l.district) LIKE LOWER(%(location)s)
            AND l.max_guests   >= %(num_guests)s
            AND (%(bedrooms)s IS NULL OR l.bedrooms >= %(bedrooms)s)
            AND l.is_active    = TRUE
            AND NOT EXISTS (
                SELECT 1
                FROM   bookings b
                WHERE  b.listing_id = l.id
                AND    b.status     = 'confirmed'
                AND    daterange(b.check_in, b.check_out, '[)')
                    && daterange(%(check_in)s, %(check_out)s, '[)')
            )
        ORDER BY l.price_per_night ASC
        LIMIT 10
    """
    params = {
        "location":   f"%{location}%",
        "num_guests": num_guests,
        "bedrooms":   bedrooms,
        "check_in":   check_in,
        "check_out":  check_out,
    }
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [_serialize_row(dict(r)) for r in rows]


def get_listing(listing_id: str) -> dict[str, Any] | None:
    """
    Return full listing details by ID, or None if not found / inactive.
    """
    sql = """
        SELECT
            id, title, location, district, description,
            price_per_night, max_guests, bedrooms, bathrooms,
            amenities, images, house_rules, cancellation_policy
        FROM listings
        WHERE id = %(id)s AND is_active = TRUE
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"id": listing_id})
            row = cur.fetchone()

    return _serialize_row(dict(row)) if row else None




def insert_booking(
    listing_id: str,
    conversation_id: str | None,
    guest_name: str,
    guest_contact: str,
    check_in: date,
    check_out: date,
    num_guests: int,
) -> dict[str, Any]:
    """
    Insert a confirmed booking. Calculates total_price from the listing rate.
    Raises psycopg2.errors.ExclusionViolation on double-booking.
    Returns the full booking row.
    """
    # Fetch price first (inside same transaction context is fine here)
    price_sql = "SELECT price_per_night, title FROM listings WHERE id = %(id)s AND is_active = TRUE"
    insert_sql = """
        INSERT INTO bookings
            (listing_id, conversation_id, guest_name, guest_contact,
             check_in, check_out, num_guests, total_price, status)
        VALUES
            (%(listing_id)s, %(conversation_id)s, %(guest_name)s, %(guest_contact)s,
             %(check_in)s, %(check_out)s, %(num_guests)s, %(total_price)s, 'confirmed')
        RETURNING id, listing_id, guest_name, guest_contact,
                  check_in, check_out, num_guests, total_price, status, created_at
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(price_sql, {"id": listing_id})
            listing = cur.fetchone()
            if not listing:
                raise ValueError(f"Listing '{listing_id}' not found or inactive")

            nights = (check_out - check_in).days
            total_price = nights * listing["price_per_night"]

            cur.execute(insert_sql, {
                "listing_id":      listing_id,
                "conversation_id": conversation_id,
                "guest_name":      guest_name,
                "guest_contact":   guest_contact,
                "check_in":        check_in,
                "check_out":       check_out,
                "num_guests":      num_guests,
                "total_price":     total_price,
            })
            booking = dict(cur.fetchone())

    booking["nights"]            = nights
    booking["currency"]          = "BDT"
    booking["listing_title"]     = listing["title"]
    booking["price_per_night"]   = listing["price_per_night"]
    booking["confirmation_message"] = (
        f"Booking confirmed! Your reference is {booking['id'].upper()[-8:]}. "
        f"Property: {listing['title']}. "
        f"Total: ৳{total_price:,} for {nights} night(s). "
        "You will receive an SMS confirmation shortly."
    )
    return _serialize_row(booking)



def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    """Return the full conversations row as a dict, or None."""
    sql = "SELECT * FROM conversations WHERE id = %(id)s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"id": conversation_id})
            row = cur.fetchone()
    return _serialize_row(dict(row)) if row else None


def get_or_create_conversation(conversation_id: str) -> dict[str, Any]:
    """
    Fetch an existing conversation or create a fresh one.
    Returns the conversation row.
    """
    sql = """
        INSERT INTO conversations (id, messages, status, escalated, created_at, updated_at)
        VALUES (%(id)s, '[]'::jsonb, 'active', FALSE, NOW(), NOW())
        ON CONFLICT (id) DO NOTHING
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"id": conversation_id})
            cur.execute("SELECT * FROM conversations WHERE id = %(id)s", {"id": conversation_id})
            row = cur.fetchone()
    return _serialize_row(dict(row))


def append_messages(
    conversation_id: str,
    new_messages: list[dict[str, Any]],
    escalated: bool = False,
) -> None:
    """
    Append message dicts to the JSONB messages column atomically.
    Sets status = 'escalated' and escalated = TRUE when flagged.
    """
    sql = """
        UPDATE conversations
        SET
            messages   = messages || %(msgs)s::jsonb,
            escalated  = escalated OR %(escalated)s,
            status     = CASE
                           WHEN escalated OR %(escalated)s THEN 'escalated'
                           ELSE status
                         END,
            updated_at = NOW()
        WHERE id = %(id)s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {
                "id":        conversation_id,
                "msgs":      json.dumps(new_messages, default=str),
                "escalated": escalated,
            })



def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    """
    Convert psycopg2 row types that aren't JSON-serializable:
    - datetime / date → ISO string
    - memoryview / bytes → str
    - JSONB already arrives as dict/list from RealDictCursor
    """
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, (datetime,)):
            out[k] = v.isoformat()
        elif isinstance(v, date):
            out[k] = v.isoformat()
        elif isinstance(v, memoryview):
            out[k] = bytes(v).decode("utf-8")
        else:
            out[k] = v
    return out
