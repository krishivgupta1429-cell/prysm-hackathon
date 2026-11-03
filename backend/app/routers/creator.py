"""
app/routers/creator.py
Creator Intelligence endpoints:
  GET  /creator/profile        – full creator intelligence profile
  GET  /creator/trends         – niche & topic performance trends
  GET  /creator/benchmark      – how this creator ranks vs niche
  POST /creator/stripe/webhook – Stripe billing webhook
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.chat import CreatorProfile
from app.services.memory_service import get_or_create_profile, get_creator_context

router = APIRouter()


@router.get("/profile")
async def get_creator_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full aggregated creator intelligence profile."""
    from app.core.config import settings
    from app.studio.safety import unlimited_view
    ctx = await get_creator_context(str(current_user.id), db)
    plan, credits = unlimited_view(settings.STUDIO_UNLIMITED, current_user.plan, current_user.credits)
    return {
        "user_id": str(current_user.id),
        "username": current_user.username,
        "plan": plan,
        "credits": credits,
        **ctx,
    }


@router.get("/trends")
async def get_creator_trends(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return virality and hook score trends over time."""
    profile = await get_or_create_profile(str(current_user.id), db)
    return {
        "virality_trend":   profile.virality_trend or [],
        "hook_score_trend": profile.hook_score_trend or [],
        "total_videos":     profile.total_videos_analyzed or 0,
        "avg_virality":     profile.avg_virality_score,
        "avg_hook_score":   profile.avg_hook_score,
    }


@router.get("/benchmark")
async def get_creator_benchmark(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compare creator performance against niche averages."""
    profile = await get_or_create_profile(str(current_user.id), db)
    return {
        "primary_niche":          profile.primary_niche,
        "avg_virality_score":     profile.avg_virality_score,
        "avg_hook_score":         profile.avg_hook_score,
        "avg_value_density":      profile.avg_value_density,
        "avg_pacing_score":       profile.avg_pacing_score,
        "top_performing_topics":  profile.top_performing_topics or [],
        "platform_performance":   profile.platform_performance or {},
        "recommendations":        profile.recommendations or [],
    }


# ── Stripe Billing ────────────────────────────────────────────────────────────

@router.post("/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Handle Stripe webhook events for subscription lifecycle management.
    """
    import stripe
    from app.core.config import settings
    from app.models.user import PlanTier
    from sqlalchemy import select, update

    payload   = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature.")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "customer.subscription.created":
        # Legacy Stripe — kept for backwards compat, new billing uses Razorpay
        plan_map = {
            "price_pro":     (PlanTier.PRO,     settings.PLAN_PRO_MONTHLY_CREDITS),
            "price_plus":    (PlanTier.PLUS,    settings.PLAN_PLUS_MONTHLY_CREDITS),
            "price_creator": (PlanTier.CREATOR, settings.PLAN_CREATOR_MONTHLY_CREDITS),
        }
        price_id = data["items"]["data"][0]["price"]["id"]
        plan, monthly_credits = plan_map.get(price_id, (PlanTier.FREE, settings.FREE_TIER_CREDITS))

        await db.execute(
            update(User)
            .where(User.stripe_customer_id == data["customer"])
            .values(
                plan=plan,
                stripe_subscription_id=data["id"],
                credits=monthly_credits,
            )
        )
        await db.commit()

    elif event_type == "customer.subscription.deleted":
        await db.execute(
            update(User)
            .where(User.stripe_customer_id == data["customer"])
            .values(plan=PlanTier.FREE, stripe_subscription_id=None)
        )
        await db.commit()

    elif event_type == "invoice.payment_succeeded":
        # Monthly credit refresh
        plan_credits = {
            PlanTier.PRO: settings.PLAN_PRO_MONTHLY_CREDITS,
            PlanTier.PLUS: settings.PLAN_PLUS_MONTHLY_CREDITS,
            PlanTier.CREATOR: settings.PLAN_CREATOR_MONTHLY_CREDITS,
            PlanTier.FREE: 0,
        }
        result = await db.execute(
            select(User).where(User.stripe_customer_id == data["customer"])
        )
        user = result.scalar_one_or_none()
        if user:
            user.credits = plan_credits.get(user.plan, 0)
            await db.commit()

    return {"received": True}
