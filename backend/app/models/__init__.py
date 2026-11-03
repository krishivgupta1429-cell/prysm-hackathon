# app/models/__init__.py
# Import all models so Base.metadata is populated for Alembic migrations.

from app.models.user import User, PlanTier                          # noqa: F401
from app.models.media import Media, MediaSource, MediaStatus        # noqa: F401
from app.models.analysis import Analysis, AnalysisStatus, ViralityConfidence  # noqa: F401
from app.models.chat import ChatSession, ChatMessage, CreatorProfile  # noqa: F401
from app.models.prysm_analysis import PrysmAnalysis, PerformanceFeedback  # noqa: F401
from app.models.intelligence import (                                # noqa: F401
    ViralPattern, HookType, EmotionalTone,
    CreatorIntelligence, ScrapingStatus,
    CompetitorAnalysis,
    ClipJob, ClipStatus,
    TimelineFeedback,
)
from app.models.research_reel import ResearchReel                    # noqa: F401
