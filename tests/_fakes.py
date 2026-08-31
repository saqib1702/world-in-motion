"""Dependency-free test doubles.

The offline regression suite has to run with nothing installed but the standard
library, so this module provides:

1. `FakeMongoClient` — an in-memory stand-in implementing the subset of the
   pymongo API this codebase actually uses, including unique-index enforcement
   (which matters: `relations` has a unique index on
   (source_agent_id, target_agent_id), so an id migration can legitimately
   collide and we want tests to see that).
2. `install_stub_modules()` — registers fake `pymongo` / `google.genai` modules
   in sys.modules so `db.mongo` and `llm.gemini` import without the real
   dependencies present.

Nothing here is imported by production code.
"""

from __future__ import annotations

import copy
import sys
import types
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Query / update evaluation
# ---------------------------------------------------------------------------

def _get_path(doc: dict, path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _set_path(doc: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _matches(doc: dict, flt: dict) -> bool:
    for key, expected in flt.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches(doc, sub) for sub in expected):
                return False
            continue

        actual = _get_path(doc, key)

        if isinstance(expected, dict) and any(k.startswith("$") for k in expected):
            for op, operand in expected.items():
                if op == "$in":
                    if actual not in operand:
                        return False
                elif op == "$nin":
                    if actual in operand:
                        return False
                elif op == "$ne":
                    if actual == operand:
                        return False
                elif op == "$gte":
                    if actual is None or not actual >= operand:
                        return False
                elif op == "$lte":
                    if actual is None or not actual <= operand:
                        return False
                elif op == "$gt":
                    if actual is None or not actual > operand:
                        return False
                elif op == "$lt":
                    if actual is None or not actual < operand:
                        return False
                elif op == "$exists":
                    if bool(operand) != (actual is not None):
                        return False
                else:
                    raise NotImplementedError(f"query operator {op} not supported")
            continue

        # Mongo scalar-vs-array semantics: a scalar matches if the field equals
        # it OR the field is an array containing it.
        if isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


def _project(doc: dict, projection: Optional[dict]) -> dict:
    out = copy.deepcopy(doc)
    if not projection:
        return out
    includes = {k for k, v in projection.items() if v and k != "_id"}
    if includes:
        kept = {k: v for k, v in out.items() if k in includes}
        if projection.get("_id", 1):
            kept["_id"] = out.get("_id")
        return kept
    for key, val in projection.items():
        if not val:
            out.pop(key, None)
    return out


def _apply_update(doc: dict, update: dict) -> None:
    for op, payload in update.items():
        if op == "$set":
            for k, v in payload.items():
                _set_path(doc, k, copy.deepcopy(v))
        elif op == "$unset":
            for k in payload:
                doc.pop(k, None)
        elif op == "$inc":
            for k, v in payload.items():
                _set_path(doc, k, (_get_path(doc, k) or 0) + v)
        elif op == "$push":
            for field, spec in payload.items():
                arr = list(_get_path(doc, field) or [])
                if isinstance(spec, dict) and "$each" in spec:
                    arr.extend(copy.deepcopy(spec["$each"]))
                    slice_n = spec.get("$slice")
                    if slice_n is not None:
                        arr = arr[slice_n:] if slice_n < 0 else arr[:slice_n]
                else:
                    arr.append(copy.deepcopy(spec))
                _set_path(doc, field, arr)
        elif op == "$addToSet":
            for field, spec in payload.items():
                arr = list(_get_path(doc, field) or [])
                values = spec["$each"] if isinstance(spec, dict) and "$each" in spec else [spec]
                for v in values:
                    if v not in arr:
                        arr.append(copy.deepcopy(v))
                _set_path(doc, field, arr)
        else:
            raise NotImplementedError(f"update operator {op} not supported")


# ---------------------------------------------------------------------------
# Result objects
# ---------------------------------------------------------------------------

class _UpdateResult:
    def __init__(self, matched: int, modified: int, upserted_id: Any = None):
        self.matched_count = matched
        self.modified_count = modified
        self.upserted_id = upserted_id


class _InsertOneResult:
    def __init__(self, inserted_id: Any):
        self.inserted_id = inserted_id


class _DeleteResult:
    def __init__(self, deleted: int):
        self.deleted_count = deleted


class _BulkWriteResult:
    def __init__(self, matched: int, modified: int):
        self.matched_count = matched
        self.modified_count = modified


class FakeUpdateOne:
    """Stand-in for pymongo.UpdateOne, consumed by FakeCollection.bulk_write.

    Present so `helpers.log_agent_reactions_bulk` exercises its real bulk path
    offline instead of silently falling through to per-item writes — the
    fallback would hide a broken bulk query from the test suite.
    """

    def __init__(self, filter, update, upsert=False):
        self.filter = filter
        self.update = update
        self.upsert = upsert


class _Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda d: (_get_path(d, key) is None, _get_path(d, key)),
                        reverse=direction < 0)
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[:n]
        return self

    def __iter__(self) -> Iterable[dict]:
        return iter(self._docs)

    def __len__(self):
        return len(self._docs)


# ---------------------------------------------------------------------------
# Collection / database / client
# ---------------------------------------------------------------------------

class FakeCollection:
    def __init__(self, name: str, dup_error):
        self.name = name
        self._docs: list[dict] = []
        self._next_id = 1
        self._unique_indexes: list[list[str]] = []
        self._dup_error = dup_error

    # --- index management ---
    def create_index(self, keys, **kwargs):
        if isinstance(keys, str):
            fields = [keys]
        else:
            fields = [k for k, _ in keys]
        if kwargs.get("unique"):
            sparse = kwargs.get("sparse", False)
            spec = fields
            if spec not in self._unique_indexes:
                self._unique_indexes.append(spec)
                self._sparse = getattr(self, "_sparse", {})
                self._sparse[tuple(spec)] = sparse
        return kwargs.get("name", "_".join(fields))

    def _violates_unique(self, candidate: dict, ignore_id: Any = None) -> bool:
        sparse_map = getattr(self, "_sparse", {})
        for fields in self._unique_indexes:
            values = [_get_path(candidate, f) for f in fields]
            if sparse_map.get(tuple(fields)) and any(v is None for v in values):
                continue
            for other in self._docs:
                if ignore_id is not None and other.get("_id") == ignore_id:
                    continue
                if [_get_path(other, f) for f in fields] == values:
                    return True
        return False

    # --- reads ---
    def find_one(self, flt=None, projection=None):
        for doc in self._docs:
            if _matches(doc, flt or {}):
                return _project(doc, projection)
        return None

    def find(self, flt=None, projection=None):
        return _Cursor([_project(d, projection) for d in self._docs if _matches(d, flt or {})])

    def count_documents(self, flt=None):
        return sum(1 for d in self._docs if _matches(d, flt or {}))

    # --- writes ---
    def insert_one(self, doc):
        new = copy.deepcopy(doc)
        new.setdefault("_id", self._next_id)
        if self._violates_unique(new):
            raise self._dup_error(f"duplicate key in {self.name}")
        self._next_id += 1
        self._docs.append(new)
        return _InsertOneResult(new["_id"])

    def insert_many(self, docs):
        return [self.insert_one(d).inserted_id for d in docs]

    def update_one(self, flt, update, upsert=False):
        for doc in self._docs:
            if _matches(doc, flt):
                before = copy.deepcopy(doc)
                candidate = copy.deepcopy(doc)
                _apply_update(candidate, update)
                if self._violates_unique(candidate, ignore_id=doc.get("_id")):
                    raise self._dup_error(f"duplicate key in {self.name}")
                doc.clear()
                doc.update(candidate)
                return _UpdateResult(1, 0 if before == doc else 1)

        if not upsert:
            return _UpdateResult(0, 0)

        new: dict = {}
        for k, v in flt.items():
            if not k.startswith("$") and not isinstance(v, dict):
                _set_path(new, k, v)
        _apply_update(new, update)
        new["_id"] = self._next_id
        if self._violates_unique(new):
            raise self._dup_error(f"duplicate key in {self.name}")
        self._next_id += 1
        self._docs.append(new)
        return _UpdateResult(0, 0, upserted_id=new["_id"])

    def update_many(self, flt, update):
        modified = 0
        for doc in list(self._docs):
            if _matches(doc, flt):
                candidate = copy.deepcopy(doc)
                _apply_update(candidate, update)
                if self._violates_unique(candidate, ignore_id=doc.get("_id")):
                    raise self._dup_error(f"duplicate key in {self.name}")
                if candidate != doc:
                    doc.clear()
                    doc.update(candidate)
                    modified += 1
        return _UpdateResult(modified, modified)

    def delete_one(self, flt):
        for doc in list(self._docs):
            if _matches(doc, flt):
                self._docs.remove(doc)
                return _DeleteResult(1)
        return _DeleteResult(0)

    def delete_many(self, flt):
        victims = [d for d in self._docs if _matches(d, flt or {})]
        for v in victims:
            self._docs.remove(v)
        return _DeleteResult(len(victims))

    def bulk_write(self, operations, ordered=True):
        matched = modified = 0
        for op in operations:
            if not isinstance(op, FakeUpdateOne):
                raise NotImplementedError(f"bulk op {type(op).__name__} not supported")
            try:
                res = self.update_one(op.filter, op.update, upsert=op.upsert)
            except Exception:
                if ordered:
                    raise
                continue
            matched += res.matched_count
            modified += res.modified_count
        return _BulkWriteResult(matched, modified)

    def watch(self, pipeline=None, **kwargs):
        raise NotImplementedError("change streams require a replica set")


class FakeDatabase:
    def __init__(self, name: str, dup_error):
        self.name = name
        self._collections: dict[str, FakeCollection] = {}
        self._dup_error = dup_error

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection(name, self._dup_error)
        return self._collections[name]

    def __getattr__(self, name: str) -> FakeCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def list_collection_names(self):
        return list(self._collections)

    def command(self, *_a, **_k):
        return {"ok": 1}


class FakeMongoClient:
    def __init__(self, *_a, **_k):
        self._dbs: dict[str, FakeDatabase] = {}
        self.admin = FakeDatabase("admin", _dup_error_cls())

    def __getitem__(self, name: str) -> FakeDatabase:
        if name not in self._dbs:
            self._dbs[name] = FakeDatabase(name, _dup_error_cls())
        return self._dbs[name]

    def close(self):
        return None


_DUP_ERROR_CLS = None


def _dup_error_cls():
    global _DUP_ERROR_CLS
    if _DUP_ERROR_CLS is None:
        install_stub_modules()
        _DUP_ERROR_CLS = sys.modules["pymongo.errors"].DuplicateKeyError
    return _DUP_ERROR_CLS


# ---------------------------------------------------------------------------
# Module stubs
# ---------------------------------------------------------------------------

_INSTALLED = False


def install_stub_modules() -> None:
    """Register fake `pymongo` and `google.genai` modules in sys.modules."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # --- pymongo ---
    errors_mod = types.ModuleType("pymongo.errors")

    class PyMongoError(Exception):
        pass

    class DuplicateKeyError(PyMongoError):
        pass

    class ServerSelectionTimeoutError(PyMongoError):
        pass

    errors_mod.PyMongoError = PyMongoError
    errors_mod.DuplicateKeyError = DuplicateKeyError
    errors_mod.ServerSelectionTimeoutError = ServerSelectionTimeoutError

    database_mod = types.ModuleType("pymongo.database")
    database_mod.Database = FakeDatabase

    collection_mod = types.ModuleType("pymongo.collection")
    collection_mod.Collection = FakeCollection

    pymongo_mod = types.ModuleType("pymongo")
    pymongo_mod.MongoClient = FakeMongoClient
    pymongo_mod.ASCENDING = 1
    pymongo_mod.DESCENDING = -1
    pymongo_mod.UpdateOne = FakeUpdateOne
    pymongo_mod.errors = errors_mod
    pymongo_mod.database = database_mod
    pymongo_mod.collection = collection_mod

    sys.modules.setdefault("pymongo", pymongo_mod)
    sys.modules.setdefault("pymongo.errors", errors_mod)
    sys.modules.setdefault("pymongo.database", database_mod)
    sys.modules.setdefault("pymongo.collection", collection_mod)

    # --- google.genai (only the names llm/gemini.py touches at import time) ---
    genai_mod = types.ModuleType("google.genai")
    types_mod = types.ModuleType("google.genai.types")

    class _GenerateContentConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    types_mod.GenerateContentConfig = _GenerateContentConfig

    class _Client:
        def __init__(self, *_a, **_k):
            raise RuntimeError("stub Gemini client: offline tests must patch llm.gemini.generate")

    genai_mod.Client = _Client
    genai_mod.types = types_mod

    try:
        import google  # noqa: F401
    except ImportError:  # pragma: no cover
        google_pkg = types.ModuleType("google")
        google_pkg.__path__ = []  # mark as package
        sys.modules["google"] = google_pkg

    sys.modules.setdefault("google.genai", genai_mod)
    sys.modules.setdefault("google.genai.types", types_mod)
