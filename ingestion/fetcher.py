"""Event ingestion for real-world current events.

This module provides:
1) A concrete free event source (GDELT Doc API, no key required).
2) Relevance filtering against seeded nation interests.
3) Conversion to the event shape expected by the world engine.
"""

from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
from typing import Any, Optional

import requests

import config
from db.seed import STARTER_NATIONS

log = logging.getLogger(__name__)


class EventSource(ABC):
    """A fetchable source of real-world events."""

    #: Stable identifier stored on each document, e.g. "gdelt" or "reuters-rss".
    source_id: str = "unset"

    @abstractmethod
    def fetch(self, since: datetime | None = None) -> list[dict[str, Any]]:
        """Return raw payloads from the source, newest first."""
        raise NotImplementedError


def normalize(
    source_id: str,
    external_id: str,
    title: str,
    published_at: datetime,
    *,
    body: str = "",
    url: str = "",
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an `events` document.

    (source, external_id) is the dedupe key — see db/schema.py, which enforces
    it with a unique index so re-fetching the same feed is safe.
    """
    return {
        "source": source_id,
        "external_id": external_id,
        "title": title,
        "body": body,
        "url": url,
        "published_at": published_at,
        "raw": raw or {},
    }


def _parse_gdelt_datetime(value: str) -> Optional[datetime]:
    # Typical value: 20260813T101500Z
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _to_gdelt_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        parsed = _parse_gdelt_datetime(value)
        if parsed:
            return parsed
        try:
            # Best-effort ISO parsing fallback.
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Entity matching
# ---------------------------------------------------------------------------
# Because the roster is real states and blocs, an article can be tied to an
# actor by *naming* it — "Beijing", "the White House", "Brussels" — rather than
# by scoring it against a nation's declared interests. That distinction matters:
# interest scoring guesses which nation a story is "about", whereas an entity
# match is a fact about the text. Only genuinely named actors get to react.

#: Short forms are matched case-sensitively against the original text, because
#: lowercasing turns "US" into the pronoun "us" and "EU" into a fragment of
#: dozens of ordinary words. Anything with three or fewer alphanumerics is
#: treated this way and matched in uppercase.
_SHORT_ALIAS_MAX_LEN = 3

#: An article must contain at least one of these to count as geopolitical. The
#: upstream feed queries already bias toward these topics; this is the backstop
#: that keeps "US actress wins award" from moving a relation score.
_GEOPOLITICAL_TERMS: frozenset[str] = frozenset({
    "trade", "tariff", "tariffs", "sanction", "sanctions", "embargo",
    "export", "import", "supply chain", "semiconductor", "chip", "chips",
    "military", "defense", "defence", "troops", "missile", "warship", "navy",
    "nuclear", "weapons", "arms", "strike", "war", "ceasefire", "conflict",
    "diplomatic", "diplomacy", "summit", "treaty", "accord", "alliance",
    "ambassador", "foreign minister", "foreign ministry", "talks",
    "energy", "oil", "gas", "pipeline", "opec", "crude",
    "cyber", "cyberattack", "espionage", "intelligence",
    "climate", "emissions", "carbon",
    "currency", "central bank", "interest rate", "inflation", "imf",
    "border", "territorial", "sovereignty", "airspace", "strait",
    "nato", "united nations", "security council", "brics", "g7", "g20",
    "visa", "immigration", "election", "protest", "coup",
})


def _alias_pattern(alias: str) -> tuple[re.Pattern[str], bool]:
    """Compile one alias into (pattern, case_sensitive).

    Uses explicit `(?<!\\w)` / `(?!\\w)` lookarounds rather than `\\b` so aliases
    that end in punctuation ("u.s.") still anchor correctly.
    """
    alphanumeric = re.sub(r"[^a-z0-9]", "", alias.lower())
    case_sensitive = len(alphanumeric) <= _SHORT_ALIAS_MAX_LEN

    needle = alias.upper() if case_sensitive else alias.lower()
    pattern = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")
    return pattern, case_sensitive


def _build_entity_index() -> dict[str, list[tuple[re.Pattern[str], bool]]]:
    """agent_id -> compiled alias matchers, derived from the seeded personas."""
    index: dict[str, list[tuple[re.Pattern[str], bool]]] = {}
    for nation in STARTER_NATIONS:
        agent_id = nation["agent_id"]
        persona = nation.get("persona", {})

        # The display name is always an alias, even if not listed explicitly.
        aliases = {nation.get("name", "")} | set(persona.get("aliases", []))
        matchers = [_alias_pattern(a) for a in aliases if a and a.strip()]
        index[agent_id] = matchers
    return index


NATION_ENTITIES = _build_entity_index()


def _is_geopolitical(text_lower: str) -> bool:
    return any(term in text_lower for term in _GEOPOLITICAL_TERMS)


def _event_text(event_doc: dict[str, Any]) -> str:
    return " ".join(
        [
            str(event_doc.get("title", "")),
            str(event_doc.get("body", "")),
            str(event_doc.get("url", "")),
            str(event_doc.get("raw", {}).get("domain", "")),
        ]
    )


def _matched_actors(text: str) -> list[tuple[str, int]]:
    """Actors explicitly named in `text`, as (agent_id, distinct alias hits)."""
    text_lower = text.lower()
    matches: list[tuple[str, int]] = []

    for agent_id, matchers in NATION_ENTITIES.items():
        hits = 0
        for pattern, case_sensitive in matchers:
            haystack = text if case_sensitive else text_lower
            if pattern.search(haystack):
                hits += 1
        if hits:
            matches.append((agent_id, hits))

    matches.sort(key=lambda item: item[1], reverse=True)
    return matches


def _relevant_agents(event_doc: dict[str, Any], min_actors: int = 1) -> list[str]:
    """Which seeded actors this article actually concerns, best match first."""
    text = _event_text(event_doc)
    if not _is_geopolitical(text.lower()):
        return []

    matches = _matched_actors(text)
    if len(matches) < min_actors:
        return []
    return [agent_id for agent_id, _ in matches]



class GDELTEventSource(EventSource):
    """Fetch current events from the free GDELT Document API."""

    source_id = "gdelt"
    endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, query: Optional[str] = None, timeout_seconds: int = 15) -> None:
        self.query = query or config.GDELT_QUERY
        self.timeout_seconds = timeout_seconds

    def fetch(self, since: datetime | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "query": self.query,
            "mode": "ArtList",
            "format": "json",
            "sort": "DateDesc",
            "maxrecords": config.EVENT_FETCH_MAX_RECORDS,
        }
        if since is not None:
            params["startdatetime"] = _to_gdelt_datetime(since)

        resp = requests.get(self.endpoint, params=params, timeout=self.timeout_seconds)
        resp.raise_for_status()

        payload = resp.json()
        articles = payload.get("articles", []) if isinstance(payload, dict) else []
        out: list[dict[str, Any]] = []

        for article in articles:
            if not isinstance(article, dict):
                continue

            title = str(article.get("title", "")).strip()
            if not title:
                continue

            url = str(article.get("url", "")).strip()
            body = str(article.get("snippet") or article.get("socialimage") or "").strip()
            seen_dt = _parse_gdelt_datetime(str(article.get("seendate", "")))
            if seen_dt is None:
                seen_dt = datetime.now(timezone.utc)

            external_basis = url or f"{title}|{article.get('domain', '')}|{article.get('seendate', '')}"
            external_id = hashlib.sha1(external_basis.encode("utf-8")).hexdigest()[:24]

            out.append(
                normalize(
                    source_id=self.source_id,
                    external_id=external_id,
                    title=title,
                    body=body,
                    url=url,
                    published_at=seen_dt,
                    raw=article,
                )
            )
        return out


class GoogleNewsRSSSource(EventSource):
    """Fallback free source via Google News RSS query feed (no API key)."""

    source_id = "google_news_rss"
    endpoint = "https://news.google.com/rss/search"

    def __init__(self, query: Optional[str] = None, timeout_seconds: int = 15) -> None:
        self.query = query or config.GOOGLE_NEWS_QUERY
        self.timeout_seconds = timeout_seconds

    def fetch(self, since: datetime | None = None) -> list[dict[str, Any]]:
        params = f"q={quote_plus(self.query)}&hl=en-US&gl=US&ceid=US:en"
        url = f"{self.endpoint}?{params}"
        resp = requests.get(url, timeout=self.timeout_seconds)
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return []

        out: list[dict[str, Any]] = []
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            guid = (item.findtext("guid") or "").strip()

            if not title:
                continue

            published_at = datetime.now(timezone.utc)
            if pub_date:
                try:
                    parsed = parsedate_to_datetime(pub_date)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    published_at = parsed.astimezone(timezone.utc)
                except (TypeError, ValueError):
                    pass

            external_basis = guid or link or title
            external_id = hashlib.sha1(external_basis.encode("utf-8")).hexdigest()[:24]

            if since is not None and published_at < since.astimezone(timezone.utc):
                continue

            out.append(
                normalize(
                    source_id=self.source_id,
                    external_id=external_id,
                    title=title,
                    body=desc,
                    url=link,
                    published_at=published_at,
                    raw={
                        "guid": guid,
                        "pubDate": pub_date,
                    },
                )
            )
        return out


def _to_engine_event(event_doc: dict[str, Any], involved_agents: list[str]) -> dict[str, Any]:
    published_at = _coerce_datetime(event_doc.get("published_at")) or datetime.now(timezone.utc)
    source = str(event_doc.get("source", "external_news"))
    external_id = str(event_doc.get("external_id", ""))
    title = str(event_doc.get("title", "Current event"))
    body = str(event_doc.get("body", ""))

    return {
        "headline": title,
        "description": body,
        "event_type": "current_event",
        "source": source,
        # Promoted to the top level (as well as kept in payload) because the
        # engine dedupes on (source, external_id) before spending a Gemini call
        # per agent — without it, every scheduler cycle re-reacts to the same
        # article. See helpers.log_event_once.
        "external_id": external_id,
        "involved_agents": involved_agents,
        "payload": {
            "external_id": external_id,
            "url": event_doc.get("url", ""),
            "published_at": published_at.isoformat(),
            "raw": event_doc.get("raw", {}),
        },
    }


def fetch_relevant_current_events(
    *,
    since: Optional[datetime] = None,
    max_events: Optional[int] = None,
    source: Optional[EventSource] = None,
) -> list[dict[str, Any]]:
    """Fetch and return engine-ready events relevant to seeded nations.

    Returns event dicts shaped for `WorldEngine.step(custom_events=[...])`.
    """
    sources: list[EventSource]
    if source is not None:
        sources = [source]
    else:
        sources = [GDELTEventSource(), GoogleNewsRSSSource()]
    limit = max_events or config.EVENT_FETCH_MAX_ITEMS

    raw_events: list[dict[str, Any]] = []
    for src in sources:
        try:
            fetched = src.fetch(since=since)
            raw_events.extend(fetched)
        except Exception as exc:
            log.warning("Current event fetch failed from %s: %s", getattr(src, "source_id", "source"), exc)

    if not raw_events:
        return []

    # Rank before truncating. A story naming two or more actors ("EU opens
    # tariff probe into Chinese EVs") produces a directed relation shift, while a
    # single-actor story mostly produces domestic commentary — so multi-actor
    # items earn their slot in the limited per-tick budget first.
    scored: list[tuple[int, dict[str, Any], list[str]]] = []
    seen_external_ids: set[str] = set()

    for doc in raw_events:
        external_id = str(doc.get("external_id", ""))
        if not external_id or external_id in seen_external_ids:
            continue

        agents = _relevant_agents(doc)
        if not agents:
            continue

        seen_external_ids.add(external_id)
        scored.append((len(agents), doc, agents))

    scored.sort(key=lambda item: item[0], reverse=True)

    engine_events = [
        _to_engine_event(doc, agents) for _count, doc, agents in scored[:limit]
    ]

    log.info(
        "Fetched %d relevant current events (from %d raw, %d matched actors)",
        len(engine_events),
        len(raw_events),
        len(scored),
    )
    return engine_events
