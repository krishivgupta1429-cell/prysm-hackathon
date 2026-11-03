"""
app/routers/billing.py
Razorpay billing — subscriptions, top-ups, webhooks.

Endpoints:
  POST /billing/subscribe          — create Razorpay subscription
  POST /billing/verify             — verify payment after checkout
  POST /billing/topup              — buy a credit top-up pack
  POST /billing/topup/verify       — verify top-up payment
  GET  /billing/status             — current plan, credits, usage
  POST /billing/cancel             — cancel subscription
  POST /billing/webhook            — Razorpay webhook handler
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import razorpay
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.cache import redis_client
from app.models.user import User, PlanTier
from app.models.prysm_analysis import PrysmAnalysis
from app.models.chat import ChatSession, ChatMessage
from sqlalchemy import select, and_

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Razorpay client ──────────────────────────────────────────────────────────

def _get_razorpay() -> razorpay.Client:
    return razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )


# ── Plan config ──────────────────────────────────────────────────────────────

PLAN_CONFIG = {
    "plus": {
        "credits": settings.PLAN_PLUS_MONTHLY_CREDITS,
        "rollover_cap": settings.ROLLOVER_CAP_PLUS,
        "monthly_inr": settings.PRICE_PLUS_MONTHLY_INR,
        "annual_inr": settings.PRICE_PLUS_ANNUAL_INR,
        "monthly_usd": settings.PRICE_PLUS_MONTHLY_USD,
        "annual_usd": settings.PRICE_PLUS_ANNUAL_USD,
    },
    "creator": {
        "credits": settings.PLAN_CREATOR_MONTHLY_CREDITS,
        "rollover_cap": settings.ROLLOVER_CAP_CREATOR,
        "monthly_inr": settings.PRICE_CREATOR_MONTHLY_INR,
        "annual_inr": settings.PRICE_CREATOR_ANNUAL_INR,
        "monthly_usd": settings.PRICE_CREATOR_MONTHLY_USD,
        "annual_usd": settings.PRICE_CREATOR_ANNUAL_USD,
    },
}

TOPUP_PACKS = {
    "starter": {"credits": settings.TOPUP_STARTER_CREDITS, "amount_inr": settings.TOPUP_STARTER_INR},
    "popular": {"credits": settings.TOPUP_POPULAR_CREDITS, "amount_inr": settings.TOPUP_POPULAR_INR},
    "power":   {"credits": settings.TOPUP_POWER_CREDITS,   "amount_inr": settings.TOPUP_POWER_INR},
}


# ── Request models ───────────────────────────────────────────────────────────

class SubscribeRequest(BaseModel):
    plan: str          # "pro", "plus", "creator"
    cycle: str = "monthly"  # "monthly" or "annual"


class VerifyRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    plan: str
    cycle: str = "monthly"


class TopupRequest(BaseModel):
    pack: str  # "starter", "popular", "power"


class TopupVerifyRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    pack: str


# ── Subscribe ────────────────────────────────────────────────────────────────

@router.post("/subscribe")
async def create_subscription(
    req: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Razorpay order for plan subscription."""
    if req.plan not in PLAN_CONFIG:
        raise HTTPException(status_code=400, detail="Invalid plan")
    if req.cycle not in ("monthly", "annual"):
        raise HTTPException(status_code=400, detail="Invalid billing cycle")

    plan = PLAN_CONFIG[req.plan]
    amount = plan[f"{req.cycle}_inr"]

    try:
        client = _get_razorpay()
        order = client.order.create({
            "amount": amount,
            "currency": "INR",
            "receipt": f"sub_{str(current_user.id)[:8]}_{req.plan}_{req.cycle}",
            "notes": {
                "user_id": str(current_user.id),
                "plan": req.plan,
                "cycle": req.cycle,
                "type": "subscription",
            },
        })
    except Exception as e:
        logger.error(f"[Billing] Razorpay order creation failed: {e}")
        raise HTTPException(status_code=502, detail=f"Payment gateway error: {str(e)}")

    return {
        "order_id": order["id"],
        "amount": amount,
        "currency": "INR",
        "key_id": settings.RAZORPAY_KEY_ID,
        "plan": req.plan,
        "cycle": req.cycle,
        "user_email": current_user.email,
        "user_name": current_user.full_name or current_user.username,
    }


@router.post("/verify")
async def verify_subscription(
    req: VerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify Razorpay payment signature and activate subscription."""
    # Verify signature
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        f"{req.razorpay_order_id}|{req.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, req.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    # Idempotency — reject duplicate payment verification
    idem_key = f"payment:verified:{req.razorpay_payment_id}"
    if await redis_client.exists(idem_key):
        raise HTTPException(status_code=409, detail="Payment already processed")

    if req.plan not in PLAN_CONFIG:
        raise HTTPException(status_code=400, detail="Invalid plan")

    # Verify the order amount matches the expected plan price (prevents plan spoofing)
    # This must happen BEFORE setting the idempotency key so a bad request can be retried.
    try:
        client = _get_razorpay()
        order = client.order.fetch(req.razorpay_order_id)
        expected_amount = PLAN_CONFIG[req.plan][f"{req.cycle}_inr"]
        if order.get("amount") != expected_amount:
            logger.warning(
                "[Billing] Plan spoofing attempt",
                extra={"user_id": str(current_user.id), "claimed_plan": req.plan,
                       "order_amount": order.get("amount"), "expected": expected_amount},
            )
            raise HTTPException(status_code=400, detail="Payment amount does not match plan")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Billing] Order verification failed: {e}")
        raise HTTPException(status_code=502, detail="Could not verify order")

    # Set idempotency key only after all validation passes
    await redis_client.setex(idem_key, 86400 * 7, "1")  # remember for 7 days

    plan_enum = {
        "pro": PlanTier.PRO,
        "plus": PlanTier.PLUS,
        "creator": PlanTier.CREATOR,
    }[req.plan]

    plan_credits = PLAN_CONFIG[req.plan]["credits"]
    rollover_cap = PLAN_CONFIG[req.plan]["rollover_cap"]

    # Cap carryover credits to prevent subscribe→cancel→resubscribe rollover abuse
    # Existing credits carry over, but capped at the plan's monthly allocation
    existing_credits = current_user.credits
    capped_carryover = min(existing_credits, plan_credits)

    now = datetime.now(timezone.utc)

    current_user.plan = plan_enum
    current_user.credits = round(plan_credits + capped_carryover, 1)
    current_user.rollover_credits = round(capped_carryover, 1)
    current_user.billing_cycle = req.cycle
    current_user.razorpay_subscription_id = req.razorpay_payment_id
    current_user.plan_started_at = now

    await db.commit()

    logger.info(
        "[Billing] Subscription activated",
        extra={
            "audit": True,
            "action": "subscription_activated",
            "user_id": str(current_user.id),
            "email": current_user.email,
            "plan": req.plan,
            "cycle": req.cycle,
            "payment_id": req.razorpay_payment_id,
            "credits_total": current_user.credits,
            "credits_carried": capped_carryover,
            "credits_capped_from": existing_credits,
        },
    )

    return {
        "status": "active",
        "plan": req.plan,
        "cycle": req.cycle,
        "credits": current_user.credits,
        "existing_credits_carried": capped_carryover,
    }


# ── Top-up ───────────────────────────────────────────────────────────────────

@router.post("/topup")
async def create_topup(
    req: TopupRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Razorpay order for a credit top-up pack."""
    if current_user.plan == PlanTier.FREE:
        raise HTTPException(status_code=403, detail="Upgrade to a paid plan to purchase top-ups")

    if req.pack not in TOPUP_PACKS:
        raise HTTPException(status_code=400, detail="Invalid top-up pack")

    pack = TOPUP_PACKS[req.pack]
    client = _get_razorpay()

    order = client.order.create({
        "amount": pack["amount_inr"],
        "currency": "INR",
        "receipt": f"top_{str(current_user.id)[:8]}_{req.pack}",
        "notes": {
            "user_id": str(current_user.id),
            "pack": req.pack,
            "credits": pack["credits"],
            "type": "topup",
        },
    })

    return {
        "order_id": order["id"],
        "amount": pack["amount_inr"],
        "currency": "INR",
        "key_id": settings.RAZORPAY_KEY_ID,
        "credits": pack["credits"],
        "pack": req.pack,
    }


@router.post("/topup/verify")
async def verify_topup(
    req: TopupVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify top-up payment and add credits."""
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        f"{req.razorpay_order_id}|{req.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, req.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    # Idempotency — reject duplicate top-up verification
    idem_key = f"payment:verified:{req.razorpay_payment_id}"
    if await redis_client.exists(idem_key):
        raise HTTPException(status_code=409, detail="Payment already processed")

    if req.pack not in TOPUP_PACKS:
        raise HTTPException(status_code=400, detail="Invalid top-up pack")

    # Verify the order amount matches expected pack price (prevents pack spoofing)
    # Must happen BEFORE setting the idempotency key so a bad request can be retried.
    try:
        client = _get_razorpay()
        order = client.order.fetch(req.razorpay_order_id)
        expected_amount = TOPUP_PACKS[req.pack]["amount_inr"]
        if order.get("amount") != expected_amount:
            logger.warning(
                "[Billing] Top-up spoofing attempt",
                extra={"user_id": str(current_user.id), "claimed_pack": req.pack,
                       "order_amount": order.get("amount"), "expected": expected_amount},
            )
            raise HTTPException(status_code=400, detail="Payment amount does not match pack")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Billing] Top-up order verification failed: {e}")
        raise HTTPException(status_code=502, detail="Could not verify order")

    # Set idempotency key only after all validation passes
    await redis_client.setex(idem_key, 86400 * 7, "1")

    credits_to_add = TOPUP_PACKS[req.pack]["credits"]
    current_user.credits = round(current_user.credits + credits_to_add, 1)
    await db.commit()

    logger.info(
        "[Billing] Top-up credited",
        extra={
            "audit": True,
            "action": "topup_credited",
            "user_id": str(current_user.id),
            "email": current_user.email,
            "pack": req.pack,
            "credits_added": credits_to_add,
            "credits_total": current_user.credits,
            "payment_id": req.razorpay_payment_id,
        },
    )

    return {
        "status": "success",
        "credits_added": credits_to_add,
        "total_credits": current_user.credits,
    }


# ── Plan status ──────────────────────────────────────────────────────────────

@router.get("/status")
async def billing_status(
    current_user: User = Depends(get_current_user),
):
    """Get current billing status — plan, credits, usage."""
    plan = current_user.plan.value if hasattr(current_user.plan, 'value') else str(current_user.plan)
    plan_config = PLAN_CONFIG.get(plan, {})

    return {
        "plan": plan,
        "credits": round(current_user.credits, 1),
        "monthly_credits": plan_config.get("credits", 0),
        "rollover_credits": round(current_user.rollover_credits, 1),
        "billing_cycle": current_user.billing_cycle or "monthly",
        "plan_started_at": current_user.plan_started_at.isoformat() if current_user.plan_started_at else None,
        "credit_costs": {
            "analysis": settings.CREDITS_PER_ANALYSIS,
            "aria_chat": settings.CREDITS_PER_ARIA_CHAT,
        },
        "topup_packs": {
            name: {"credits": p["credits"], "amount_inr": p["amount_inr"]}
            for name, p in TOPUP_PACKS.items()
        },
    }


# ── Usage log ────────────────────────────────────────────────────────────────

CREDITS_PER_USD = 31.44

@router.get("/usage")
async def get_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return per-event credit usage log for the current billing cycle."""
    plan = current_user.plan.value if hasattr(current_user.plan, "value") else str(current_user.plan)
    plan_config = PLAN_CONFIG.get(plan, {})
    monthly_credits = plan_config.get("credits", 0)

    # Cycle start — fall back to start of current month if not set
    cycle_start = current_user.plan_started_at
    if not cycle_start:
        now = datetime.now(timezone.utc)
        cycle_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    events = []

    # ── Analyses ──────────────────────────────────────────────────────────────
    analysis_result = await db.execute(
        select(PrysmAnalysis)
        .where(
            PrysmAnalysis.user_id == current_user.id,
            PrysmAnalysis.created_at >= cycle_start,
            PrysmAnalysis.total_cost_usd != None,
        )
        .order_by(PrysmAnalysis.created_at.desc())
    )
    analyses = analysis_result.scalars().all()

    for a in analyses:
        credits_used = round((a.total_cost_usd or 0) * CREDITS_PER_USD, 1)
        if credits_used <= 0:
            continue
        events.append({
            "id": str(a.id),
            "type": "analysis",
            "label": f"{a.intent == 'competitor' and 'Reel Breakdown' or 'Video Analysis'}",
            "detail": a.brief or a.niche or "",
            "niche": a.niche or "",
            "score": a.overall_score,
            "source_type": a.source_type or "upload",
            "credits": credits_used,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })

    # ── ARIA chat messages ────────────────────────────────────────────────────
    chat_result = await db.execute(
        select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(
            ChatSession.user_id == current_user.id,
            ChatMessage.created_at >= cycle_start,
            ChatMessage.cost_usd != None,
            ChatMessage.role == "assistant",
        )
        .order_by(ChatMessage.created_at.desc())
    )
    chat_msgs = chat_result.scalars().all()

    for msg in chat_msgs:
        credits_used = round((msg.cost_usd or 0) * CREDITS_PER_USD, 1)
        if credits_used <= 0:
            continue
        events.append({
            "id": str(msg.id),
            "type": "chat",
            "label": "ARIA Chat",
            "detail": (msg.content or "")[:80],
            "niche": "",
            "score": None,
            "source_type": None,
            "credits": credits_used,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        })

    # Sort all events newest first
    events.sort(key=lambda e: e["created_at"] or "", reverse=True)

    # Credits consumed this cycle = monthly allocation - current balance (capped at 0)
    credits_consumed = max(0.0, round(monthly_credits - (current_user.credits or 0), 1))
    # For free users who started with a one-time grant, use sum of events instead
    if plan == "free":
        credits_consumed = round(sum(e["credits"] for e in events), 1)

    return {
        "plan": plan,
        "monthly_credits": monthly_credits,
        "credits_remaining": round(current_user.credits or 0, 1),
        "credits_consumed": credits_consumed,
        "cycle_start": cycle_start.isoformat(),
        "plan_expires_at": current_user.plan_expires_at.isoformat() if current_user.plan_expires_at else None,
        "events": events,
    }


# ── Cancel ───────────────────────────────────────────────────────────────────

@router.post("/cancel")
async def cancel_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel subscription — user keeps credits until they run out, plan downgrades to free."""
    if current_user.plan == PlanTier.FREE:
        raise HTTPException(status_code=400, detail="No active subscription to cancel")

    current_user.plan = PlanTier.FREE
    current_user.razorpay_subscription_id = None
    current_user.billing_cycle = None
    current_user.rollover_credits = 0.0
    # Keep remaining credits — they just won't refresh
    await db.commit()

    logger.info(
        "[Billing] Subscription cancelled",
        extra={
            "audit": True,
            "action": "subscription_cancelled",
            "user_id": str(current_user.id),
            "email": current_user.email,
            "remaining_credits": round(current_user.credits, 1),
        },
    )

    return {
        "status": "cancelled",
        "remaining_credits": round(current_user.credits, 1),
        "message": "Your remaining credits are still available. Plan downgraded to Free.",
    }


# ── Webhook ──────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def razorpay_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Razorpay webhook handler — handles payment events.
    Verify signature, then process event.
    """
    body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    # Verify webhook signature
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        logger.warning("[Billing] Invalid webhook signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body)
    event = payload.get("event", "")

    logger.info(f"[Billing] Webhook received: {event}")

    if event == "payment.captured":
        logger.info(f"[Billing] Payment captured: {payload.get('payload', {}).get('payment', {}).get('entity', {}).get('id')}")

    elif event == "payment.failed":
        logger.warning(f"[Billing] Payment failed: {payload}")

    elif event in ("payment.refunded", "payment.dispute.created"):
        # Revoke credits on refund or chargeback
        try:
            payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
            notes = payment_entity.get("notes", {})
            user_id = notes.get("user_id")
            if user_id:
                from sqlalchemy import select as _sel
                result = await db.execute(_sel(User).where(User.id == user_id))
                user = result.scalar_one_or_none()
                if user:
                    old_credits = user.credits
                    user.credits = 0.0
                    user.plan = PlanTier.FREE
                    user.razorpay_subscription_id = None
                    user.billing_cycle = None
                    user.rollover_credits = 0.0
                    await db.commit()
                    logger.warning(
                        "[Billing] Credits revoked due to refund/chargeback",
                        extra={
                            "audit": True,
                            "action": "credits_revoked",
                            "event": event,
                            "user_id": user_id,
                            "credits_revoked": old_credits,
                            "payment_id": payment_entity.get("id"),
                        },
                    )
        except Exception as e:
            logger.error(f"[Billing] Failed to process refund webhook: {e}")

    return {"status": "ok"}
