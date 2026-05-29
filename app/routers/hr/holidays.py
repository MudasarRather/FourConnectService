"""HR Holiday calendar — SKELETON for Phase 2.X."""
from __future__ import annotations

from datetime import date
from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.holiday import Holiday
from app.schemas.hr.attendance import HolidayCreate, HolidayUpdate, HolidayResponse, HolidayListResponse
from app.utils.dependencies import get_current_superuser, get_current_user

router = APIRouter(prefix="/hr/holidays", tags=["HR — Holidays"])


@router.get("/me", response_model=HolidayListResponse)
def list_holidays_for_user(
    year: Optional[int] = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """User-accessible read-only holiday calendar.

    The self-service attendance page uses this to render government and
    company holidays on the month grid. Read-only and lightweight — admins
    still own the master list via the existing `/` endpoint.
    """
    q = db.query(Holiday).filter(Holiday.is_deleted == False)  # noqa: E712
    q = q.filter(Holiday.is_active == True)  # noqa: E712
    if year is not None:
        from datetime import date as _date
        q = q.filter(Holiday.date >= _date(year, 1, 1), Holiday.date <= _date(year, 12, 31))
    rows = q.order_by(Holiday.date.asc()).limit(500).all()
    return HolidayListResponse(
        items=[_to_response(r) for r in rows],
        total=len(rows), page=1, limit=500, total_pages=1,
    )


def _to_response(h: Holiday) -> HolidayResponse:
    return HolidayResponse(
        id=h.id, name=h.name, date=h.date, holiday_type=h.holiday_type,
        location_id=h.location_id, description=h.description,
        is_active=bool(h.is_active), created_at=h.created_at,
    )


@router.get("/", response_model=HolidayListResponse)
def list_holidays(
    year: Optional[int] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(Holiday).filter(Holiday.is_deleted == False)  # noqa: E712
    if year is not None:
        from datetime import date as _date
        q = q.filter(Holiday.date >= _date(year, 1, 1), Holiday.date <= _date(year, 12, 31))
    total = q.count()
    rows = q.order_by(Holiday.date.asc()).offset((page - 1) * limit).limit(limit).all()
    return HolidayListResponse(
        items=[_to_response(r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/", response_model=HolidayResponse, status_code=http_status.HTTP_201_CREATED)
def create_holiday(
    payload: HolidayCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    h = Holiday(**payload.model_dump(), created_by_id=admin.id)
    db.add(h)
    db.commit()
    db.refresh(h)
    return _to_response(h)


@router.patch("/{holiday_id}", response_model=HolidayResponse)
def update_holiday(
    holiday_id: UUID,
    payload: HolidayUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Update a single holiday. Used by the curation flow when admin clicks
    a draft, tweaks anything (name/type/date/description), and either saves
    or activates it."""
    h = db.query(Holiday).filter(Holiday.id == holiday_id, Holiday.is_deleted == False).first()  # noqa: E712
    if not h:
        raise HTTPException(404, "Holiday not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(h, k, v)
    db.commit()
    db.refresh(h)
    return _to_response(h)


@router.post("/{holiday_id}/activate", response_model=HolidayResponse)
def activate_holiday(
    holiday_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Flip is_active=True on a single draft holiday. Once active the row
    starts short-circuiting the daily rollup and appears in the user's
    self-service calendar."""
    h = db.query(Holiday).filter(Holiday.id == holiday_id, Holiday.is_deleted == False).first()  # noqa: E712
    if not h:
        raise HTTPException(404, "Holiday not found")
    h.is_active = True
    db.commit()
    db.refresh(h)
    return _to_response(h)


@router.post("/bulk-activate", response_model=dict)
def bulk_activate_holidays(
    year: Optional[int] = Query(None, description="Only activate drafts in this year"),
    holiday_type: Optional[str] = Query(None, description="Only activate this type"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Bulk-activate every DRAFT (is_active=False) holiday matching the
    filter. Returns how many were activated so the toast can confirm."""
    q = db.query(Holiday).filter(
        Holiday.is_deleted == False,  # noqa: E712
        Holiday.is_active == False,   # noqa: E712
    )
    if year is not None:
        from datetime import date as _date
        q = q.filter(Holiday.date >= _date(year, 1, 1), Holiday.date <= _date(year, 12, 31))
    if holiday_type:
        q = q.filter(Holiday.holiday_type == holiday_type)
    rows = q.all()
    for h in rows:
        h.is_active = True
    db.commit()
    return {"activated": len(rows), "year": year, "holiday_type": holiday_type}


@router.delete("/{holiday_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_holiday(
    holiday_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    h = db.query(Holiday).filter(Holiday.id == holiday_id).first()
    if not h:
        raise HTTPException(404, "Holiday not found")
    h.is_deleted = True
    db.commit()


@router.delete("/", response_model=dict)
def bulk_delete_holidays(
    year: Optional[int] = Query(None, description="If set, only delete holidays in this calendar year"),
    holiday_type: Optional[str] = Query(None, description="If set, only delete this HolidayType"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Soft-delete multiple holidays at once.

    Filters are AND-ed:
      - `year` restricts to a specific calendar year (otherwise all years)
      - `holiday_type` restricts to NATIONAL / COMPANY / REGIONAL / RESTRICTED
    Always returns the count actually deleted so the UI can confirm.
    """
    q = db.query(Holiday).filter(Holiday.is_deleted == False)  # noqa: E712
    if year is not None:
        from datetime import date as _date
        q = q.filter(Holiday.date >= _date(year, 1, 1), Holiday.date <= _date(year, 12, 31))
    if holiday_type:
        q = q.filter(Holiday.holiday_type == holiday_type)
    rows = q.all()
    for h in rows:
        h.is_deleted = True
    db.commit()
    return {"deleted": len(rows), "year": year, "holiday_type": holiday_type}


# ── Real-world holiday import ───────────────────────────────────────────────
# Reads {year, country} from query params and bulk-creates Holiday rows.
# Idempotent — re-running for the same (year, country) is safe; rows with a
# matching (date, name) are skipped instead of duplicated.
#
# Data source strategy:
#   1. For India, the free public APIs (Nager.Date, OpenHolidays) return zero
#      data — both yield HTTP 204 / empty arrays for every year. We carry a
#      curated India calendar for 2025–2027 covering the Central Government
#      gazette holidays.
#   2. For every other country, we hit Nager.Date and gracefully handle a 204
#      / empty response (no more 500 from `.json()` on an empty body).

# Curated India calendar — Central Government gazette + well-established
# religious dates. Updated when new years are gazetted. Variable lunar dates
# may shift ±1 day by tradition; admins can edit individual rows after import.
_INDIA_HOLIDAYS = {
    2025: [
        ("2025-01-26", "Republic Day"),
        ("2025-03-14", "Holi"),
        ("2025-03-31", "Eid-ul-Fitr"),
        ("2025-04-10", "Mahavir Jayanti"),
        ("2025-04-14", "Ambedkar Jayanti"),
        ("2025-04-18", "Good Friday"),
        ("2025-05-12", "Buddha Purnima"),
        ("2025-06-07", "Eid-ul-Zuha (Bakrid)"),
        ("2025-07-06", "Muharram"),
        ("2025-08-15", "Independence Day"),
        ("2025-08-16", "Janmashtami"),
        ("2025-09-05", "Milad-un-Nabi"),
        ("2025-10-02", "Gandhi Jayanti"),
        ("2025-10-02", "Dussehra"),
        ("2025-10-20", "Diwali"),
        ("2025-11-05", "Guru Nanak Jayanti"),
        ("2025-12-25", "Christmas Day"),
    ],
    2026: [
        ("2026-01-26", "Republic Day"),
        ("2026-02-17", "Maha Shivratri"),
        ("2026-03-04", "Holi"),
        ("2026-03-20", "Eid-ul-Fitr"),
        ("2026-03-31", "Mahavir Jayanti"),
        ("2026-04-03", "Good Friday"),
        ("2026-04-14", "Ambedkar Jayanti"),
        ("2026-05-01", "Buddha Purnima"),
        ("2026-05-27", "Eid-ul-Zuha (Bakrid)"),
        ("2026-06-26", "Muharram"),
        ("2026-08-15", "Independence Day"),
        ("2026-08-25", "Milad-un-Nabi"),
        ("2026-09-04", "Janmashtami"),
        ("2026-10-02", "Gandhi Jayanti"),
        ("2026-10-20", "Dussehra"),
        ("2026-11-08", "Diwali"),
        ("2026-11-24", "Guru Nanak Jayanti"),
        ("2026-12-25", "Christmas Day"),
    ],
    2027: [
        ("2027-01-26", "Republic Day"),
        ("2027-02-06", "Maha Shivratri"),
        ("2027-03-22", "Holi"),
        ("2027-03-10", "Eid-ul-Fitr"),
        ("2027-04-19", "Mahavir Jayanti"),
        ("2027-04-14", "Ambedkar Jayanti"),
        ("2027-03-26", "Good Friday"),
        ("2027-05-20", "Buddha Purnima"),
        ("2027-05-17", "Eid-ul-Zuha (Bakrid)"),
        ("2027-06-16", "Muharram"),
        ("2027-08-15", "Independence Day"),
        ("2027-08-25", "Janmashtami"),
        ("2027-08-15", "Milad-un-Nabi"),
        ("2027-10-02", "Gandhi Jayanti"),
        ("2027-10-09", "Dussehra"),
        ("2027-11-08", "Diwali"),  # approximate
        ("2027-11-13", "Guru Nanak Jayanti"),
        ("2027-12-25", "Christmas Day"),
    ],
}


def _fetch_external_holidays(year: int, country: str) -> list[dict]:
    """Fetch holidays from Nager.Date. Returns `[]` on 204 / empty body.
    Raises HTTPException only on hard provider failures (network / 5xx)."""
    import httpx
    url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country.upper()}"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
    except Exception as exc:
        raise HTTPException(502, f"Could not reach holiday provider: {exc}")
    # 204 No Content → provider has nothing for this (year, country).
    if resp.status_code == 204 or not (resp.content or b"").strip():
        return []
    if resp.status_code == 404:
        return []
    if resp.status_code >= 400:
        raise HTTPException(502, f"Holiday provider returned {resp.status_code}")
    try:
        payload = resp.json()
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


@router.post("/import", response_model=dict)
def import_public_holidays(
    year: int = Query(..., ge=1900, le=2100),
    country: str = Query("IN", min_length=2, max_length=2),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    from app.models.hr.holiday import HolidayType
    from datetime import date as _date

    cc = country.upper()
    source = "date.nager.at"
    entries: list[dict] = []

    # India: hit the curated calendar first — no free API covers India.
    if cc == "IN":
        if year in _INDIA_HOLIDAYS:
            entries = [{"date": iso, "name": name, "localName": name}
                       for iso, name in _INDIA_HOLIDAYS[year]]
            source = "curated:in"
        else:
            # Fall back to public API in case it grows India coverage one day.
            entries = _fetch_external_holidays(year, cc)
            if entries:
                source = "date.nager.at"
    else:
        entries = _fetch_external_holidays(year, cc)

    if not entries:
        raise HTTPException(
            404,
            f"No public holidays available for {cc} in {year}. "
            f"For India we keep a curated calendar for 2025–2027 — for other years "
            f"or countries the upstream provider had no data. You can add holidays "
            f"manually with the New holiday button.",
        )

    imported = 0
    skipped = 0
    for entry in entries:
        try:
            iso = entry.get("date") or ""
            name = entry.get("localName") or entry.get("name") or "Public holiday"
            hdate = _date.fromisoformat(iso)
        except Exception:
            skipped += 1
            continue
        exists = (
            db.query(Holiday.id)
            .filter(Holiday.date == hdate, Holiday.name == name, Holiday.is_deleted == False)  # noqa: E712
            .first()
        )
        if exists:
            skipped += 1
            continue
        db.add(Holiday(
            name=name,
            date=hdate,
            holiday_type=HolidayType.NATIONAL,
            description=entry.get("name") or None,
            # Imports land as DRAFT so they don't silently affect the daily
            # rollup. Admin reviews each one (or bulk-applies) before they
            # go live.
            is_active=False,
            created_by_id=admin.id,
        ))
        imported += 1
    db.commit()
    return {
        "imported": imported,
        "skipped": skipped,
        "year": year,
        "country": cc,
        "source": source,
        "status": "DRAFT",
    }
