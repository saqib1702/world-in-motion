"""Scheduled ingestion job: fetch current events and feed them into world ticks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config
from engine.tick import WorldEngine
from ingestion.fetcher import fetch_relevant_current_events

log = logging.getLogger(__name__)


class ScheduledIngestionRunner:
    """Runs a periodic fetch->tick pipeline in process."""

    def __init__(
        self,
        engine: Optional[WorldEngine] = None,
        interval_minutes: Optional[int] = None,
    ) -> None:
        self.engine = engine or WorldEngine(run_id=config.SCHEDULED_RUN_ID)
        self.interval_minutes = interval_minutes or config.EVENT_FETCH_INTERVAL_MINUTES
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self._last_fetch: Optional[datetime] = None
        self._lock = Lock()
        self._started = False

    def _job(self) -> None:
        if not self._lock.acquire(blocking=False):
            log.info("Scheduled ingestion job already running; skipping this trigger.")
            return

        try:
            since = self._last_fetch
            now = datetime.now(timezone.utc)

            events = fetch_relevant_current_events(
                since=since,
                max_events=config.EVENT_FETCH_MAX_ITEMS,
            )
            self._last_fetch = now

            if not events:
                log.info("Scheduled ingestion found no new relevant events.")
                return

            summary = self.engine.step(custom_events=events)
            log.info(
                "Scheduled tick complete: tick=%s events=%d actions=%d",
                summary.get("tick"),
                len(summary.get("events_processed", [])),
                len(summary.get("actions_taken", [])),
            )
        except Exception as exc:
            log.exception("Scheduled ingestion tick failed: %s", exc)
        finally:
            self._lock.release()

    def start(self) -> None:
        if self._started:
            return

        trigger = IntervalTrigger(minutes=self.interval_minutes)
        self.scheduler.add_job(
            self._job,
            trigger=trigger,
            id="scheduled-ingestion-world-tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        self._started = True
        log.info("Scheduled ingestion runner started (interval=%d min)", self.interval_minutes)

    def shutdown(self) -> None:
        if not self._started:
            return
        self.scheduler.shutdown(wait=False)
        self._started = False
        log.info("Scheduled ingestion runner stopped")


_RUNNER: Optional[ScheduledIngestionRunner] = None


def get_or_start_runner() -> ScheduledIngestionRunner:
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = ScheduledIngestionRunner()
    _RUNNER.start()
    return _RUNNER


def stop_runner() -> None:
    global _RUNNER
    if _RUNNER is not None:
        _RUNNER.shutdown()
        _RUNNER = None
