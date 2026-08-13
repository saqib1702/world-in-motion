"""Real-world news and event ingestion."""

from ingestion.fetcher import EventSource, GDELTEventSource, fetch_relevant_current_events, normalize

try:
	from ingestion.scheduler import ScheduledIngestionRunner, get_or_start_runner, stop_runner
except ImportError:  # pragma: no cover - optional at import time until deps are installed
	ScheduledIngestionRunner = None  # type: ignore[assignment]
	get_or_start_runner = None  # type: ignore[assignment]
	stop_runner = None  # type: ignore[assignment]

__all__ = [
	"EventSource",
	"GDELTEventSource",
	"fetch_relevant_current_events",
	"normalize",
	"ScheduledIngestionRunner",
	"get_or_start_runner",
	"stop_runner",
]
