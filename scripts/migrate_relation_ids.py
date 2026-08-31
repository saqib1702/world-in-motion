"""One-off migration: canonicalise `relations` endpoints to agent_ids.

Supersedes the old `fix_solaria.py`, which had two problems:
  * it only fixed the single hardcoded value "Solaria Federation";
  * a blind `update_many` can raise DuplicateKeyError, because `relations` has a
    unique index on (source_agent_id, target_agent_id) — if a correct
    id-keyed doc already exists for the same pair, renaming the display-name doc
    onto it violates that index.

This script resolves every endpoint via db.helpers.resolve_agent_id and, when the
canonical slot is already occupied, MERGES the two documents (newest
`updated_at` wins for score; history is concatenated, de-duplicated and capped)
before deleting the stale one.

Usage:
    python -m scripts.migrate_relation_ids --dry-run    # report only
    python -m scripts.migrate_relation_ids              # apply
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from db import helpers, schema
from db.mongo import get_db

log = logging.getLogger("migrate_relation_ids")

HISTORY_CAP = 50


def _merge_history(a: list, b: list) -> list:
    combined = (a or []) + (b or [])
    seen = set()
    unique = []
    for entry in combined:
        key = (
            entry.get("timestamp"),
            entry.get("score"),
            entry.get("delta"),
            entry.get("reasoning"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    unique.sort(key=lambda e: e.get("timestamp") or "")
    return unique[-HISTORY_CAP:]


def migrate(dry_run: bool = False) -> dict:
    db = get_db()
    relations = db[schema.RELATIONS]

    # Build the alias index from the agents collection up front.
    index = helpers.build_agent_index(force=True)
    known_ids = set(index.values())
    if not known_ids:
        raise SystemExit(
            "No agents found — seed the database first (python -m db.seed), "
            "otherwise there is nothing to resolve names against."
        )
    print(f"Resolver knows {len(known_ids)} agents: {', '.join(sorted(known_ids))}\n")

    stats = {"scanned": 0, "already_ok": 0, "renamed": 0, "merged": 0,
             "self_removed": 0, "unresolvable": 0}
    unresolved_values = set()

    for doc in list(relations.find({})):
        stats["scanned"] += 1
        raw_source = doc.get("source_agent_id")
        raw_target = doc.get("target_agent_id")

        new_source = helpers.resolve_agent_id(raw_source) or raw_source
        new_target = helpers.resolve_agent_id(raw_target) or raw_target

        if new_source not in known_ids or new_target not in known_ids:
            stats["unresolvable"] += 1
            for raw, new in ((raw_source, new_source), (raw_target, new_target)):
                if new not in known_ids:
                    unresolved_values.add(raw)
            print(f"  SKIP  unresolvable: {raw_source!r} -> {raw_target!r}")
            continue

        # A nation having a relation with itself is meaningless — drop it.
        if new_source == new_target:
            stats["self_removed"] += 1
            print(f"  DROP  self-relation: {raw_source!r} -> {raw_target!r}")
            if not dry_run:
                relations.delete_one({"_id": doc["_id"]})
            continue

        if (new_source, new_target) == (raw_source, raw_target):
            stats["already_ok"] += 1
            continue

        existing = relations.find_one({
            "source_agent_id": new_source,
            "target_agent_id": new_target,
        })

        if existing and existing.get("_id") != doc.get("_id"):
            # Canonical slot taken: merge, newest updated_at wins on score.
            stats["merged"] += 1
            keep_new = (doc.get("updated_at") or "") > (existing.get("updated_at") or "")
            winner = doc if keep_new else existing
            print(
                f"  MERGE {raw_source!r} -> {raw_target!r}  into  "
                f"{new_source} -> {new_target}  (score {winner.get('score')})"
            )
            if not dry_run:
                relations.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {
                        "source_agent_id": new_source,
                        "target_agent_id": new_target,
                        "score": winner.get("score", 0.0),
                        "last_delta": winner.get("last_delta", 0.0),
                        "updated_at": winner.get("updated_at"),
                        "history": _merge_history(
                            existing.get("history", []), doc.get("history", [])
                        ),
                    }},
                )
                relations.delete_one({"_id": doc["_id"]})
        else:
            stats["renamed"] += 1
            print(f"  RENAME {raw_source!r} -> {raw_target!r}  ==>  {new_source} -> {new_target}")
            if not dry_run:
                relations.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {
                        "source_agent_id": new_source,
                        "target_agent_id": new_target,
                    }},
                )

    print("\n" + "-" * 60)
    print(f"scanned       : {stats['scanned']}")
    print(f"already ok    : {stats['already_ok']}")
    print(f"renamed       : {stats['renamed']}")
    print(f"merged        : {stats['merged']}")
    print(f"self removed  : {stats['self_removed']}")
    print(f"unresolvable  : {stats['unresolvable']}")
    if unresolved_values:
        print(f"  unresolved values: {sorted(v for v in unresolved_values if v)}")
    print("-" * 60)
    if dry_run:
        print("DRY RUN — nothing was written. Re-run without --dry-run to apply.")
    else:
        print("Migration applied.")

    # Post-condition check.
    if not dry_run:
        remaining = [
            d for d in relations.find({}, {"_id": 0, "source_agent_id": 1, "target_agent_id": 1})
            if d["source_agent_id"] not in known_ids or d["target_agent_id"] not in known_ids
        ]
        if remaining:
            print(f"\nWARNING: {len(remaining)} doc(s) still not canonical: {remaining[:5]}")
        else:
            print("\nVerified: every relation endpoint is now a canonical agent_id.")

    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    args = parser.parse_args()

    config.configure_logging()
    migrate(dry_run=args.dry_run)
