"""Built during the Meta Global AI Developer Hackathon (new work).

Who may see the per-analysis cost breakdown: the founder (FOUNDER_EMAIL) or anyone while STUDIO_EXPOSE_COST=1 (local
testing). Everyone else gets neither the numbers nor a hint that they exist: the analysis payloads served to users and to
public share links never carry `_cost`.
"""


def cost_visible(email: str | None) -> bool:
    from app.core.config import settings
    if settings.STUDIO_EXPOSE_COST:
        return True
    founder = (settings.FOUNDER_EMAIL or "").strip().lower()
    return bool(founder and email and email.strip().lower() == founder)


def strip_cost(raw: dict, email: str | None = None, *, public: bool = False) -> dict:
    """Remove the cost data from a result payload unless the viewer may see it (a public share never may)."""
    if isinstance(raw, dict) and (public or not cost_visible(email)):
        raw.pop("_cost", None)
    return raw
