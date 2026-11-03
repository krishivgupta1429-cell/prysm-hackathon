"""
app/routers/chat.py
ARIA conversational assistant endpoints.
  POST /chat/sessions         – create a new chat session
  POST /chat/sessions/{id}    – send a message (returns full response)
  GET  /chat/sessions/{id}/stream – SSE streaming response
  GET  /chat/sessions/        – list sessions
  GET  /chat/ideas            – generate video ideas
"""

import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.chat import ChatSession, ChatMessage
from app.services import chatbot_service

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class NewSessionRequest(BaseModel):
    title: Optional[str] = "New Chat"
    context_video_id: Optional[str] = None


class ChatMessageRequest(BaseModel):
    content: str
    context_video_id: Optional[str] = None


class MessageResponse(BaseModel):
    session_id: str
    message_id: str
    role: str
    content: str
    tokens_used: Optional[int]


class VideoIdeasRequest(BaseModel):
    count: int = 5
    theme: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/sessions", status_code=201)
async def create_session(
    body: NewSessionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = ChatSession(
        user_id=current_user.id,
        title=body.title,
        context={"video_id": body.context_video_id} if body.context_video_id else {},
    )
    db.add(session)
    await db.flush()
    await db.commit()
    return {"session_id": str(session.id), "title": session.title}


@router.post("/sessions/{session_id}", response_model=MessageResponse)
async def send_message(
    session_id: str,
    body: ChatMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify session ownership
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found.")

    # Load history
    history_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    history = history_result.scalars().all()
    messages = [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": body.content})

    # Optionally load analysis context
    analysis_context = None
    video_id = body.context_video_id or (session.context or {}).get("video_id")
    if video_id:
        from app.core.cache import cache_get, CacheKeys
        analysis_context = await cache_get(CacheKeys.ANALYSIS_RESULT.format(video_id=video_id))

    # Save user message
    user_msg = ChatMessage(
        session_id=session_id,
        role="user",
        content=body.content,
    )
    db.add(user_msg)
    await db.flush()

    # Call ARIA
    response = await chatbot_service.chat(
        user_id=str(current_user.id),
        messages=messages,
        db=db,
        analysis_context=analysis_context,
        stream=False,
    )

    # Save assistant reply
    assistant_msg = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=response["content"],
        tokens_used=response.get("tokens_used"),
    )
    db.add(assistant_msg)
    await db.commit()

    return MessageResponse(
        session_id=session_id,
        message_id=str(assistant_msg.id),
        role="assistant",
        content=response["content"],
        tokens_used=response.get("tokens_used"),
    )


@router.get("/sessions/{session_id}/stream")
async def stream_message(
    session_id: str,
    message: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE streaming endpoint for ARIA responses."""
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    history_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    history = history_result.scalars().all()
    messages = [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": message})

    async def event_stream():
        async for chunk in await chatbot_service.chat(
            user_id=str(current_user.id),
            messages=messages,
            db=db,
            stream=True,
        ):
            yield f"data: {json.dumps({'delta': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/sessions/", response_model=List[dict])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id, ChatSession.is_active == True)
        .order_by(desc(ChatSession.updated_at))
        .limit(50)
    )
    sessions = result.scalars().all()
    return [
        {"session_id": str(s.id), "title": s.title, "updated_at": s.updated_at.isoformat()}
        for s in sessions
    ]


@router.post("/ideas")
async def generate_ideas(
    body: VideoIdeasRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ideas = await chatbot_service.generate_video_ideas(
        user_id=str(current_user.id),
        db=db,
        count=body.count,
        theme=body.theme,
    )
    return {"ideas": ideas}
