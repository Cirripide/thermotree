"""Coalescing, rate-limited orchestrator for /api/zones composite builds.

The /api/zones handler must NOT call build_summer_composite() directly: that
function does synchronous STAC HTTP (via the `requests` library), blocking
GDAL/curl COG reads, and CPU-heavy numpy work, all of which freeze the
asyncio event loop for the full 2-5 minute compute. With one uvicorn worker
that serialises every other request (autocomplete, boundary, /health) behind
whichever user is currently computing.

This runner solves three problems at once:

  1. Off-loop execution. Each composite is dispatched via asyncio.to_thread,
     so the event loop stays free. Threads release the GIL during HTTP and
     C-extension work (numpy / GDAL), so the two composites get real
     parallelism on a multi-core host.

  2. Bounded parallelism. A semaphore caps how many composite cycles can be
     in-flight at once. Each cycle holds a few hundred MB of xarray data;
     without the cap, N concurrent users would OOM the worker.

  3. Coalescing. Two callers asking for the same (osm_id, year) at the same
     time share one compute. The work runs as a detached asyncio task owned
     by the runner — never as part of any caller's request task — so the
     leader's HTTP disconnect cannot cancel the work for everyone else.

GDAL config is process-global; nothing in this codebase mutates it at
runtime via rasterio.env.Env, so threaded reads are safe. If that
assumption changes, this comment should be revisited.
"""
from __future__ import annotations

import asyncio
import logging

from app.services.boundary_service import ValidatedBoundary
from app.services.imagery_providers import (
    LandsatLstProvider,
    Sentinel2NdviProvider,
)
from app.services.indicator_service import IndicatorService
from app.services.zones_service import build_zones_geojson

log = logging.getLogger(__name__)

ZonesKey = tuple[str, int]


class ZonesRunner:
    def __init__(self, max_concurrent: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._inflight: dict[ZonesKey, asyncio.Future[dict]] = {}
        self._dict_lock = asyncio.Lock()
        # Strong references to detached tasks. asyncio only keeps weak refs,
        # so a fire-and-forget task can be garbage-collected mid-run without
        # this set. add_done_callback prunes finished tasks automatically.
        self._tasks: set[asyncio.Task] = set()

    async def run(
        self,
        osm_id: str,
        year: int,
        boundary: ValidatedBoundary,
        time_range: str,
    ) -> dict:
        key: ZonesKey = (osm_id, year)
        async with self._dict_lock:
            existing = self._inflight.get(key)
            if existing is not None:
                future = existing
                is_leader = False
            else:
                future = asyncio.get_running_loop().create_future()
                self._inflight[key] = future
                is_leader = True

        if is_leader:
            task = asyncio.create_task(
                self._run_and_publish(key, future, boundary, time_range),
                name=f"zones-{osm_id}-{year}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        # shield() so a cancelled caller (e.g. HTTP disconnect) does NOT
        # cancel the shared future — that would propagate CancelledError
        # to every other follower awaiting the same (osm_id, year) compute.
        # asyncio.Task.cancel() walks _fut_waiter and cancels it; shield
        # interposes its own future so cancellation stops at the caller.
        return await asyncio.shield(future)

    async def _run_and_publish(
        self,
        key: ZonesKey,
        future: asyncio.Future[dict],
        boundary: ValidatedBoundary,
        time_range: str,
    ) -> None:
        try:
            async with self._semaphore:
                result = await self._compute(boundary, time_range)
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
        else:
            if not future.done():
                future.set_result(result)
        finally:
            async with self._dict_lock:
                self._inflight.pop(key, None)

    async def _compute(
        self,
        boundary: ValidatedBoundary,
        time_range: str,
    ) -> dict:
        heat = IndicatorService(provider=LandsatLstProvider())
        vegetation = IndicatorService(provider=Sentinel2NdviProvider())

        # gather's first-exception semantics: if LST fails, NDVI's awaiter
        # gets cancelled but its underlying thread keeps running to completion
        # (Python threads are uninterruptible) and its result is discarded.
        # Acceptable trade-off; bounded by self._semaphore.
        lst, ndvi = await asyncio.gather(
            asyncio.to_thread(
                heat.build_summer_composite, boundary.bbox, time_range
            ),
            asyncio.to_thread(
                vegetation.build_summer_composite, boundary.bbox, time_range
            ),
        )
        return await asyncio.to_thread(
            build_zones_geojson, boundary.feature, lst, ndvi
        )
