
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2.errors
from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from . import db

logger = logging.getLogger(__name__)

Bangladesh_TZ = ZoneInfo("Asia/Dhaka")


def _now_in_bangladesh() -> datetime:
    return datetime.now(Bangladesh_TZ)


def _today_in_bangladesh() -> date:
    return _now_in_bangladesh().date()



class SearchPropertiesInput(BaseModel):
    location: str = Field(
        ...,
        description=(
            "City, district, or area in Bangladesh. "
            'Examples: "Cox\'s Bazar", "Sylhet", "Bandarban", "Dhaka", "Rangamati"'
        ),
    )
    check_in: date = Field(..., description="Check-in date — YYYY-MM-DD")
    check_out: date = Field(..., description="Check-out date — YYYY-MM-DD")
    num_guests: int = Field(..., ge=1, le=20, description="Number of guests")
    bedrooms: int | None = Field(default=None, ge=1, le=20, description="Minimum number of bedrooms")

    @model_validator(mode="after")
    def dates_are_valid(self) -> "SearchPropertiesInput":
        today = _today_in_bangladesh()
        if self.check_in < today:
            raise ValueError(
                f"check_in cannot be in the past. Today in Bangladesh is {today.isoformat()}"
            )
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be strictly after check_in")
        return self


class ListingDetailsInput(BaseModel):
    listing_id: str = Field(
        ...,
        description='Listing ID from search results, e.g. "lst-cxb-001"',
    )


class CreateBookingInput(BaseModel):
    listing_id: str = Field(..., description="ID of the listing to book")
    guest_name: str = Field(..., min_length=2, description="Full name of the primary guest")
    guest_contact: str = Field(
        ...,
        description="Guest phone number (+880…) or email address",
    )
    check_in: date = Field(..., description="Check-in date — YYYY-MM-DD")
    check_out: date = Field(..., description="Check-out date — YYYY-MM-DD")
    num_guests: int = Field(..., ge=1, le=20, description="Number of guests")
    conversation_id: str | None = Field(
        default=None,
        description="Conversation ID — injected automatically, do not ask the guest",
    )

    @model_validator(mode="after")
    def dates_are_valid(self) -> "CreateBookingInput":
        today = _today_in_bangladesh()
        if self.check_in < today:
            raise ValueError(
                f"check_in cannot be in the past. Today in Bangladesh is {today.isoformat()}"
            )
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be strictly after check_in")
        return self


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def get_current_datetime() -> dict[str, str]:
    """
    Return the current date and time in Bangladesh.

    Use this when resolving relative dates like today, tomorrow, next weekend,
    or when validating whether a requested booking date is already in the past.
    """
    now = _now_in_bangladesh()
    return {
        "timezone": "Asia/Dhaka",
        "current_date": now.date().isoformat(),
        "current_time": now.strftime("%H:%M:%S"),
        "current_datetime": now.isoformat(),
        "weekday": now.strftime("%A"),
    }


@tool(args_schema=SearchPropertiesInput)
def search_available_properties(
    location: str,
    check_in: date,
    check_out: date,
    num_guests: int,
    bedrooms: int | None = None,
) -> list[dict[str, Any]]:
    """
    Search for available StayEase listings in a given location for the
    specified dates and guest count.

    Returns a list of matching properties with title, location, price per
    night (BDT), max guests, bedrooms, and key amenities. Returns an empty
    list when nothing is available.

    Use this tool whenever the guest provides a location, travel dates, and
    number of guests and wants to see what is available.
    """
    results = db.search_properties(location, check_in, check_out, num_guests, bedrooms)

    if not results:
        return []

    return [
        {
            "id":               r["id"],
            "title":            r["title"],
            "location":         r["location"],
            "price_per_night":  r["price_per_night"],
            "max_guests":       r["max_guests"],
            "bedrooms":         r["bedrooms"],
            "amenities":        r["amenities"],
            "cancellation_policy": r.get("cancellation_policy", ""),
        }
        for r in results
    ]


@tool(args_schema=ListingDetailsInput)
def get_listing_details(listing_id: str) -> dict[str, Any]:
    """
    Retrieve full details for a specific listing by its ID.

    Returns description, amenities, house rules, cancellation policy,
    and pricing. Raises ValueError if the listing does not exist or
    has been deactivated.

    Use this tool when the guest asks "tell me more about property X"
    or references a specific listing they saw in search results.
    """
    listing = db.get_listing(listing_id)

    if listing is None:
        raise ValueError(
            f"Listing '{listing_id}' was not found or is no longer available. "
            "Please search again to see current listings."
        )

    return listing


@tool(args_schema=CreateBookingInput)
def create_booking(
    listing_id: str,
    guest_name: str,
    guest_contact: str,
    check_in: date,
    check_out: date,
    num_guests: int,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """
    Create a confirmed booking for the specified listing and guest.

    Calculates total price automatically from the listing nightly rate.
    Returns booking reference, total price (BDT), and a confirmation message.

    Only call this tool when the guest has explicitly confirmed they want to
    book and you have collected all required details: listing_id, guest_name,
    guest_contact, check_in, check_out, and num_guests.

    Raises an error if the dates are no longer available (someone else just
    booked) — inform the guest and offer to search again.
    """
    try:
        booking = db.insert_booking(
            listing_id=listing_id,
            conversation_id=conversation_id,
            guest_name=guest_name,
            guest_contact=guest_contact,
            check_in=check_in,
            check_out=check_out,
            num_guests=num_guests,
        )
    except psycopg2.errors.ExclusionViolation:
      
        raise RuntimeError(
            f"exclusion constraint: dates {check_in} to {check_out} for listing "
            f"'{listing_id}' conflict with an existing confirmed booking."
        )
    except ValueError:
        raise  

    return booking




TOOLS = [
    get_current_datetime,
    search_available_properties,
    get_listing_details,
    create_booking,
]