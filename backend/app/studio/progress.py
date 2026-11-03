"""Built during the Meta Global AI Developer Hackathon (new work).

Real stage progress for the analysis screen. The original pipeline reports {stage: status} as it works and this class
writes it to Redis under prysm:progress:{job_id}, where /prysm/analyse/status/{job_id} picks it up (additive field).
Progress is a nicety: a failed write never breaks an analysis.
"""
import asyncio
import logging

log = logging.getLogger(__name__)
PROGRESS_TTL = 3600


class Progress:
    """Collects {stage: status} (running / ok / skipped / error) and writes it to Redis as stages report in."""

    def __init__(self, job_id: str | None):
        self.job_id = job_id
        self.state: dict[str, str] = {}
        self._writes: list[asyncio.Future] = []

    def __call__(self, stage: str, status: str) -> None:
        self.state[stage] = status
        if self.job_id:
            try:
                self._writes.append(asyncio.ensure_future(self._write(dict(self.state))))
            except RuntimeError:  # no running loop (called from a worker thread): drop the update
                pass

    async def _write(self, snapshot: dict) -> None:
        try:
            from app.core.cache import cache_set
            await cache_set(f"prysm:progress:{self.job_id}", snapshot, ttl=PROGRESS_TTL)
        except Exception as e:
            log.debug("progress write failed: %s", e)

    async def flush(self) -> None:
        if self._writes:
            await asyncio.gather(*self._writes, return_exceptions=True)


async def tracked(cb, stage: str, aw):
    """Await `aw` and report the stage to `cb` (a Progress, or None): running -> ok, or error if it raised (the error still propagates)."""
    if cb is not None:
        cb(stage, "running")
    try:
        result = await aw
    except BaseException:
        if cb is not None:
            cb(stage, "error")
        raise
    if cb is not None:
        cb(stage, "ok" if result else "skipped")
    return result


class StageGroup:
    """Several parallel tasks that count as one stage: running at the first start, ok when the last one finishes."""

    def __init__(self, cb, stage: str, count: int = 0):
        self.cb, self.stage, self.remaining, self.failed = cb, stage, count, False

    def run(self, aw):
        """Join the group (counted now, when the task is created) and return the awaitable to gather."""
        self.remaining += 1
        return self._run(aw)

    async def _run(self, aw):
        if self.cb is not None:
            self.cb(self.stage, "running")
        try:
            return await aw
        except BaseException:
            self.failed = True
            raise
        finally:
            self.remaining -= 1
            if self.remaining <= 0 and self.cb is not None:
                self.cb(self.stage, "error" if self.failed else "ok")
