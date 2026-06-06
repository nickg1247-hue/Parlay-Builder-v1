"""Simulate concurrent site traffic against user-facing routes (read-only, cached)."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


@dataclass
class Result:
    name: str
    status: int
    ms: float
    error: str | None = None
    expected: bool = False


@dataclass
class Report:
    total: int = 0
    ok: int = 0
    failed: int = 0
    results: list[Result] = field(default_factory=list)

    def add(self, r: Result) -> None:
        self.total += 1
        if r.expected:
            self.ok += 1
        elif r.error or r.status >= 400:
            self.failed += 1
        else:
            self.ok += 1
        self.results.append(r)


def _get(path: str, label: str) -> Result:
    t0 = time.perf_counter()
    try:
        resp = client.get(path)
        ms = (time.perf_counter() - t0) * 1000
        err = None
        if resp.status_code >= 400:
            err = resp.text[:200]
        return Result(label, resp.status_code, ms, err)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return Result(label, 0, ms, str(exc))


def _load_game_ids(game_date: date, limit: int) -> list[str]:
    cache = ROOT / "data" / "processed" / f"mlb_schedule_{game_date.isoformat()}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return [str(g["game_id"]) for g in data.get("games", [])[:limit]]
    resp = client.get(f"/api/schedule/mlb?date={game_date.isoformat()}")
    if resp.status_code != 200:
        return []
    return [str(g["game_id"]) for g in resp.json().get("games", [])[:limit]]


def user_session(session_id: int, game_date: date, game_ids: list[str]) -> list[Result]:
    """One browser visit: home → slate APIs → each game insights."""
    d = game_date.isoformat()
    out: list[Result] = []
    pages = ["/", "/mlb", "/mlb/board", "/mlb/lab"]
    for p in pages:
        out.append(_get(p, f"s{session_id}:{p}"))

    apis = [
        "/api/status/refresh",
        "/health",
        f"/api/schedule/mlb?date={d}",
        f"/api/scores/today?sport=mlb&date={d}",
        "/api/backtest/saved",
    ]
    for a in apis:
        out.append(_get(a, f"s{session_id}:{a}"))

    for gid in game_ids:
        out.append(_get(f"/mlb/game/{gid}", f"s{session_id}:page/game/{gid}"))
        out.append(_get(f"/api/games/mlb/{gid}?date={d}", f"s{session_id}:api/game/{gid}"))
        out.append(
            _get(
                f"/api/games/mlb/{gid}/insights?date={d}",
                f"s{session_id}:api/insights/{gid}",
            )
        )
    return out


def ticker_burst(game_date: date, n: int) -> list[Result]:
    """Simulate many clients polling scores every 60s at once."""
    d = game_date.isoformat()
    path = f"/api/scores/today?sport=mlb&date={d}"
    return [_get(path, f"ticker:{i}") for i in range(n)]


def edge_cases(game_date: date) -> list[Result]:
    d = game_date.isoformat()
    rows = [
        _get("/api/games/mlb/invalid-game-id?date=" + d, "edge:bad_game"),
        _get("/api/games/mlb/999999999/insights?date=" + d, "edge:missing_insights"),
        _get("/api/schedule/mlb?date=not-a-date", "edge:bad_date"),
        _get("/static/app.js", "static:app.js"),
        _get("/static/app.css", "static:app.css"),
    ]
    for r in rows:
        if r.name in ("edge:bad_game", "edge:missing_insights") and r.status == 404:
            r.expected = True
        if r.name == "edge:bad_date" and r.status in (422, 400, 0):
            r.expected = True
    return rows


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(len(ordered) * pct / 100)
    idx = min(idx, len(ordered) - 1)
    return ordered[idx]


def run_stress(
    *,
    sessions: int,
    workers: int,
    games_per_session: int,
    ticker_burst_n: int,
    game_date: date,
) -> int:
    game_ids = _load_game_ids(game_date, games_per_session)
    if not game_ids:
        print(f"No games found for {game_date}; run morning_refresh.py first.")
        return 1

    report = Report()
    t0 = time.perf_counter()

    print(f"Stress test — date={game_date} games={len(game_ids)} "
          f"sessions={sessions} workers={workers}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(user_session, i, game_date, game_ids)
            for i in range(sessions)
        ]
        futures.append(pool.submit(ticker_burst, game_date, ticker_burst_n))
        futures.append(pool.submit(edge_cases, game_date))

        for fut in as_completed(futures):
            for r in fut.result():
                report.add(r)

    elapsed = time.perf_counter() - t0
    latencies = [r.ms for r in report.results if r.status < 400]
    failures = [r for r in report.results if r.error or r.status >= 400]

    print()
    print("=== Summary ===")
    print(f"Requests:  {report.total}")
    print(f"OK:        {report.ok}")
    print(f"Failed:    {report.failed}")
    print(f"Duration:  {elapsed:.1f}s")
    if latencies:
        print(f"Latency ms — min: {min(latencies):.0f}  "
              f"p50: {percentile(latencies, 50):.0f}  "
              f"p95: {percentile(latencies, 95):.0f}  "
              f"max: {max(latencies):.0f}")

    if failures:
        print()
        print("=== Failures (first 15) ===")
        for r in failures[:15]:
            print(f"  [{r.status}] {r.name}: {r.error or 'HTTP error'}")

    slow = sorted(
        [r for r in report.results if r.ms > 2000 and r.status < 400],
        key=lambda x: -x.ms,
    )
    if slow:
        print()
        print("=== Slow OK requests >2s (first 10) ===")
        for r in slow[:10]:
            print(f"  {r.ms:.0f}ms  {r.name}")

    print()
    if report.failed:
        print("RESULT: FAIL — fix errors above")
        return 1
    if percentile(latencies, 95) > 5000:
        print("RESULT: WARN — p95 latency >5s; consider cache tuning")
        return 0
    print("RESULT: PASS")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Stress test Parlay Builder site")
    parser.add_argument("--sessions", type=int, default=10, help="Concurrent user sessions")
    parser.add_argument("--workers", type=int, default=8, help="Thread pool size")
    parser.add_argument("--games", type=int, default=15, help="Games per session insights")
    parser.add_argument("--ticker-burst", type=int, default=30, help="Simultaneous score polls")
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    game_date = date.fromisoformat(args.date) if args.date else date.today()
    raise SystemExit(
        run_stress(
            sessions=args.sessions,
            workers=args.workers,
            games_per_session=args.games,
            ticker_burst_n=args.ticker_burst,
            game_date=game_date,
        )
    )


if __name__ == "__main__":
    main()
