"""Hermetic runtime verification of /api/zones concurrency.

Runs the real FastAPI app in-process via httpx ASGITransport and drives the
real ZonesRunner. The two external dependencies are stubbed so the script
finishes in ~10–15 s with deterministic timings:

  * app.state.geocoder      -> FakeGeocoder (instant boundary)
  * zones_runner.IndicatorService -> FakeIndicatorService (time.sleep stub)
  * zones_runner.build_zones_geojson -> no-op

Five behavioral guarantees are checked. Exit 0 if all pass.

Run from the repo root or from backend/:
    cd backend && ./venv/bin/python scripts/verify_concurrency.py
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# Make `app.*` imports work regardless of cwd.
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

import httpx
from httpx import ASGITransport

from app.main import app
from app.services import zones_runner as zr
from app.services.geocoder.base import (
    BoundaryResult,
    GeocoderProvider,
    PlaceCandidate,
)
from app.services.zones_runner import ZonesRunner

SYNTH_SECONDS = 2.0
FAKE_BBOX = (9.0, 45.0, 9.5, 45.5)  # ~few thousand km², under max_aoi_area_km2


# ---- Stubs ---------------------------------------------------------------

class FakeGeocoder(GeocoderProvider):
    async def search(self, q: str, limit: int = 10) -> list[PlaceCandidate]:
        return []

    async def fetch_boundary(self, osm_id: str) -> BoundaryResult:
        return BoundaryResult(
            osm_id=osm_id,
            feature={
                "type": "Feature",
                "bbox": list(FAKE_BBOX),
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [FAKE_BBOX[0], FAKE_BBOX[1]],
                        [FAKE_BBOX[2], FAKE_BBOX[1]],
                        [FAKE_BBOX[2], FAKE_BBOX[3]],
                        [FAKE_BBOX[0], FAKE_BBOX[3]],
                        [FAKE_BBOX[0], FAKE_BBOX[1]],
                    ]],
                },
                "properties": {"osm_id": osm_id, "display_name": f"Fake {osm_id}"},
            },
            bbox=FAKE_BBOX,
        )


class _Counter:
    def __init__(self) -> None:
        self.value = 0
        self._lock = threading.Lock()

    def increment(self) -> None:
        with self._lock:
            self.value += 1

    def reset(self) -> None:
        with self._lock:
            self.value = 0


COUNTER = _Counter()


class FakeIndicatorService:
    """Drop-in for IndicatorService. `build_summer_composite` is sync and
    sleeps SYNTH_SECONDS — exactly mirroring the real composite's "blocking
    code inside an asyncio.to_thread call" shape. If the runner ever stops
    wrapping this in to_thread, scenarios A, C, D will immediately show it."""

    def __init__(self, provider) -> None:
        pass  # provider is irrelevant to the stub

    def build_summer_composite(self, aoi_bbox, time_range):
        COUNTER.increment()
        time.sleep(SYNTH_SECONDS)
        return {"_fake": True}


def _fake_build_zones_geojson(boundary_feature, lst, ndvi):
    return {"type": "FeatureCollection", "features": []}


zr.IndicatorService = FakeIndicatorService
zr.build_zones_geojson = _fake_build_zones_geojson


# ---- Harness -------------------------------------------------------------

@dataclass
class Result:
    name: str
    passed: bool
    wall_s: float
    expected: str
    detail: str = ""


def _attach(max_concurrent: int) -> None:
    """Fresh state on app.state before each scenario."""
    COUNTER.reset()
    app.state.geocoder = FakeGeocoder()
    app.state.zones_runner = ZonesRunner(max_concurrent=max_concurrent)


def _client(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        **kwargs,
    )


async def scenario_a_parallelism() -> Result:
    _attach(max_concurrent=2)
    async with _client(timeout=10) as c:
        t0 = time.perf_counter()
        r1, r2 = await asyncio.gather(
            c.get("/api/zones/R1/2024"),
            c.get("/api/zones/R2/2024"),
        )
        wall = time.perf_counter() - t0
    ok = r1.status_code == 200 and r2.status_code == 200 and wall < 3.0
    return Result(
        name="A. parallelism (2 different cities)",
        passed=ok,
        wall_s=wall,
        expected="<3.0s",
        detail=f"r1={r1.status_code} r2={r2.status_code}",
    )


async def scenario_b_coalescing() -> Result:
    _attach(max_concurrent=2)
    async with _client(timeout=10) as c:
        t0 = time.perf_counter()
        r1, r2 = await asyncio.gather(
            c.get("/api/zones/R1/2024"),
            c.get("/api/zones/R1/2024"),
        )
        wall = time.perf_counter() - t0
    # call_count==2 → one shared compute (LST + NDVI).
    # call_count==4 → coalescing failed (two independent computes).
    ok = (
        r1.status_code == 200
        and r2.status_code == 200
        and wall < 3.0
        and COUNTER.value == 2
    )
    return Result(
        name="B. coalescing (same key x2)",
        passed=ok,
        wall_s=wall,
        expected="<3.0s & calls==2",
        detail=f"calls={COUNTER.value}",
    )


async def scenario_c_event_loop_responsive() -> Result:
    _attach(max_concurrent=2)
    async with _client(timeout=10) as c:
        zones_task = asyncio.create_task(c.get("/api/zones/R1/2024"))
        await asyncio.sleep(0.3)  # let the compute thread start

        t_side = time.perf_counter()
        side1, side2 = await asyncio.gather(
            c.get("/"),  # health-style root endpoint
            c.get("/api/geocode/search?q=ber"),
        )
        side_wall = time.perf_counter() - t_side
        zones_resp = await zones_task

    ok = (
        zones_resp.status_code == 200
        and side1.status_code == 200
        and side2.status_code == 200
        and side_wall < 0.5
    )
    return Result(
        name="C. event-loop responsive during compute",
        passed=ok,
        wall_s=side_wall,
        expected="side-requests <0.5s while zones in flight",
        detail=f"/={side1.status_code} /search={side2.status_code}",
    )


async def scenario_d_semaphore() -> Result:
    _attach(max_concurrent=1)  # force serialisation
    async with _client(timeout=15) as c:
        t0 = time.perf_counter()
        r1, r2 = await asyncio.gather(
            c.get("/api/zones/R1/2024"),
            c.get("/api/zones/R2/2024"),
        )
        wall = time.perf_counter() - t0
    ok = (
        r1.status_code == 200
        and r2.status_code == 200
        and 3.5 <= wall < 5.0
    )
    return Result(
        name="D. semaphore bounding (max=1)",
        passed=ok,
        wall_s=wall,
        expected="3.5s ≤ wall < 5.0s",
        detail=f"r1={r1.status_code} r2={r2.status_code}",
    )


async def scenario_e_leader_cancel() -> Result:
    """Test the runner directly. The HTTP-cancel chain through ASGITransport
    is fiddly and orthogonal to the guarantee under test — what we actually
    care about is: cancelling the leader's awaiter must not cancel the
    detached compute, and followers awaiting the same key must still get
    their result."""
    _attach(max_concurrent=2)
    runner = app.state.zones_runner

    # Build a ValidatedBoundary the runner expects (same shape BoundaryService
    # would produce on a real request).
    from app.services.boundary_service import ValidatedBoundary
    from app.services.imagery_providers.base import aoi_bbox_area_km2
    br = await app.state.geocoder.fetch_boundary("R1")
    vb = ValidatedBoundary(
        osm_id=br.osm_id,
        feature=br.feature,
        bbox=br.bbox,
        area_km2=aoi_bbox_area_km2(br.bbox),
    )
    time_range = "2024-06-01/2024-08-31"

    leader_cancelled = False
    follower_result: dict | None = None

    async def leader() -> None:
        nonlocal leader_cancelled
        try:
            await runner.run("R1", 2024, vb, time_range)
        except asyncio.CancelledError:
            leader_cancelled = True
            raise

    async def follower() -> None:
        nonlocal follower_result
        await asyncio.sleep(0.2)  # ensure leader has registered the inflight key
        follower_result = await runner.run("R1", 2024, vb, time_range)

    t0 = time.perf_counter()
    leader_task = asyncio.create_task(leader())
    follower_task = asyncio.create_task(follower())
    await asyncio.sleep(0.4)  # let leader register + follower attach
    leader_task.cancel()
    await asyncio.gather(leader_task, follower_task, return_exceptions=True)
    wall = time.perf_counter() - t0

    ok = (
        leader_cancelled
        and follower_result is not None
        and COUNTER.value == 2  # one shared compute survived the leader's cancel
    )
    return Result(
        name="E. leader-cancel isolation",
        passed=ok,
        wall_s=wall,
        expected="follower got result & calls==2 & leader cancelled",
        detail=(
            f"leader_cancelled={leader_cancelled} "
            f"follower_got_result={follower_result is not None} "
            f"calls={COUNTER.value}"
        ),
    )


def _print(r: Result) -> None:
    tag = "[PASS]" if r.passed else "[FAIL]"
    print(
        f"{tag} {r.name:<42} wall={r.wall_s:5.2f}s  "
        f"(expected {r.expected})  {r.detail}"
    )


async def main() -> int:
    print(f"SYNTH_SECONDS={SYNTH_SECONDS}s — each composite-stub sleeps this long\n")
    scenarios = [
        scenario_a_parallelism,
        scenario_b_coalescing,
        scenario_c_event_loop_responsive,
        scenario_d_semaphore,
        scenario_e_leader_cancel,
    ]
    results: list[Result] = []
    for scenario in scenarios:
        results.append(await scenario())
        _print(results[-1])

    print()
    failed = [r for r in results if not r.passed]
    if failed:
        print(f"FAILED: {len(failed)}/{len(results)} scenarios")
        return 1
    print(f"PASSED: {len(results)}/{len(results)} scenarios")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
