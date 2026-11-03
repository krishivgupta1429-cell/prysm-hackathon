"""
app/routers/admin.py
Read-only admin endpoints. All routes require the X-Debug-Token header
(same credential used by /debug/queue — set DEBUG_TOKEN in Railway env).
"""

from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.config import settings
from app.models.user import User
from app.models.prysm_analysis import PrysmAnalysis

router = APIRouter()

SHARE_BASE = "https://theprysm.io/share"


def _require_admin(x_debug_token: str = Header(default="")):
    token = settings.DEBUG_TOKEN
    if not token or x_debug_token != token:
        raise HTTPException(status_code=404, detail="Not found")


@router.get(
    "/analyses-overview",
    dependencies=[Depends(_require_admin)],
    summary="Per-analysis overview joined with user data",
)
async def analyses_overview(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: Optional[str] = Query(default=None, description="Filter to a single user UUID"),
):
    """
    Returns one row per analysis with:
    - user_email, user_id
    - user_total_analyses  (window count — no extra query)
    - source_type, created_at
    - share_url            (constructed at response time, not stored)

    Sorted newest-first. Paginated via limit/offset.
    """
    user_count = (
        func.count(PrysmAnalysis.id)
        .over(partition_by=PrysmAnalysis.user_id)
        .label("user_total_analyses")
    )

    stmt = (
        select(
            PrysmAnalysis.id,
            PrysmAnalysis.user_id,
            PrysmAnalysis.source_type,
            PrysmAnalysis.created_at,
            PrysmAnalysis.share_id,
            PrysmAnalysis.is_public,
            PrysmAnalysis.overall_score,
            PrysmAnalysis.niche,
            PrysmAnalysis.platform,
            User.email,
            user_count,
        )
        .join(User, User.id == PrysmAnalysis.user_id, isouter=True)
        .order_by(PrysmAnalysis.created_at.desc())
    )

    if user_id:
        stmt = stmt.where(PrysmAnalysis.user_id == user_id)

    # Total count (for pagination metadata) — one lightweight query
    count_stmt = select(func.count()).select_from(PrysmAnalysis)
    if user_id:
        count_stmt = count_stmt.where(PrysmAnalysis.user_id == user_id)
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (await db.execute(stmt.offset(offset).limit(limit))).all()

    items = [
        {
            "analysis_id":        str(r.id),
            "user_id":            str(r.user_id) if r.user_id else None,
            "user_email":         r.email,
            "user_total_analyses": r.user_total_analyses,
            "source_type":        r.source_type,
            "niche":              r.niche,
            "platform":           r.platform,
            "overall_score":      r.overall_score,
            "created_at":         r.created_at.isoformat() if r.created_at else None,
            "is_public":          r.is_public,
            "share_url":          f"{SHARE_BASE}/{r.share_id}" if r.is_public and r.share_id else None,
        }
        for r in rows
    ]

    return {
        "total":  total,
        "limit":  limit,
        "offset": offset,
        "items":  items,
    }
