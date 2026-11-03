"""Built during the Meta Global AI Developer Hackathon (new work).

One import for every Claude call site of the original pipeline:

    from app.studio.llm_switch import anthropic

Normally that is the real `anthropic` package. With AI_PROVIDER=meta it is app.studio.meta_llm_shim (Muse Spark behind
the same AsyncAnthropic interface), and the anthropic package is never imported. With AI_PROVIDER unset the behaviour
is exactly the original.
"""
from app.core.config import settings

if settings.META_MODE:
    from app.studio import meta_llm_shim as anthropic  # noqa: F401
else:
    import anthropic  # noqa: F401
