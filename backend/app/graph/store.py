"""Graph store executors: one interface, the same openCypher text for every backend.

    Neo4jExecutor     official `neo4j` driver over Bolt (production; docker-compose service `neo4j`)
    FalkorExecutor    FalkorDB client over RESP (an openCypher engine used where Neo4j is not available)

Queries in this package stick to the openCypher subset both engines support (MATCH / OPTIONAL MATCH / MERGE / UNWIND /
WITH / collect / count / list comprehensions / CASE / pattern predicates). Values returned are plain scalars, lists and
maps — never driver node objects — so results are identical across backends.
"""

import threading
import time
from datetime import date, datetime
from typing import Any, Protocol

from app.core.config import settings


class GraphUnavailable(RuntimeError):
    """The graph store cannot be reached. Graph features degrade; nothing else is affected."""


class Executor(Protocol):
    backend: str

    def run(self, cypher: str, params: dict | None = None) -> list[dict[str, Any]]: ...

    def ping(self) -> None: ...

    def ensure_indexes(self, labels: list[str]) -> None: ...

    def close(self) -> None: ...


def _plain(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_plain(x) for x in v]
    if isinstance(v, datetime | date):
        return v.isoformat()
    if hasattr(v, "iso_format"):  # neo4j.time types
        return v.iso_format()
    return v


class Neo4jExecutor:
    backend = "neo4j"

    def __init__(self, url: str, user: str, password: str, database: str | None, timeout: float):
        from neo4j import GraphDatabase  # imported lazily: the app starts without a graph

        self._driver = GraphDatabase.driver(url, auth=(user, password), connection_timeout=timeout,
                                            max_transaction_retry_time=timeout)
        self._db = database

    def run(self, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        try:
            with self._driver.session(database=self._db) as s:
                return [_plain(r.data()) for r in s.run(cypher, params or {})]
        except Exception as e:  # neo4j.exceptions.ServiceUnavailable, AuthError, …
            if type(e).__name__ in ("ServiceUnavailable", "AuthError", "SessionExpired") or isinstance(e, OSError):
                raise GraphUnavailable(f"Neo4j: {e}") from e
            raise

    def ping(self) -> None:
        try:
            self._driver.verify_connectivity()
        except Exception as e:
            raise GraphUnavailable(f"Neo4j at {settings.GRAPH_URL}: {e}") from e

    def ensure_indexes(self, labels: list[str]) -> None:
        for lb in labels:
            self.run(f"CREATE INDEX {lb.lower()}_key IF NOT EXISTS FOR (n:{lb}) ON (n.key)")
            self.run(f"CREATE INDEX {lb.lower()}_hospital IF NOT EXISTS FOR (n:{lb}) ON (n.hospital_id)")

    def close(self) -> None:
        self._driver.close()


class FalkorExecutor:
    backend = "falkordb"

    def __init__(self, url: str, graph: str, timeout: float):
        from falkordb import FalkorDB

        self._db = FalkorDB.from_url(url, socket_timeout=timeout, socket_connect_timeout=timeout)
        self._g = self._db.select_graph(graph)
        self._indexed: set[str] = set()

    def run(self, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        try:
            r = self._g.query(cypher, params or {})
        except (ConnectionError, TimeoutError, OSError) as e:
            raise GraphUnavailable(f"FalkorDB: {e}") from e
        except Exception as e:
            if type(e).__module__.startswith("redis") and "Connection" in type(e).__name__:
                raise GraphUnavailable(f"FalkorDB: {e}") from e
            raise
        cols = [h[1] if isinstance(h, list | tuple) else h for h in (r.header or [])]
        return [_plain(dict(zip(cols, row, strict=True))) for row in (r.result_set or [])]

    def ping(self) -> None:
        try:
            self._db.connection.ping()
        except Exception as e:
            raise GraphUnavailable(f"FalkorDB at {settings.GRAPH_URL}: {e}") from e

    def ensure_indexes(self, labels: list[str]) -> None:
        existing = {(r.get("label"), p) for r in self._safe("CALL db.indexes() YIELD label, properties")
                    for p in (r.get("properties") or [])}
        for lb in labels:
            for prop in ("key", "hospital_id"):
                if (lb, prop) not in existing:
                    self._safe(f"CREATE INDEX FOR (n:{lb}) ON (n.{prop})")

    def _safe(self, cypher: str) -> list[dict]:
        try:
            return self.run(cypher)
        except GraphUnavailable:
            raise
        except Exception:
            return []

    def close(self) -> None:
        try:
            self._db.connection.close()
        except Exception:
            pass


_lock = threading.Lock()
_executor: Executor | None = None
_failed_at: float = 0.0
_last_error: str | None = None
RETRY_AFTER_SECONDS = 15  # don't hammer a graph store that is down (every graph request would wait for the timeout)


def configured() -> bool:
    return settings.GRAPH_BACKEND.lower() in ("neo4j", "falkordb")


def _build() -> Executor:
    b = settings.GRAPH_BACKEND.lower()
    if b == "neo4j":
        if not settings.GRAPH_PASSWORD:
            raise GraphUnavailable("Neo4j password not configured (set GRAPH_PASSWORD in the environment).")
        return Neo4jExecutor(settings.GRAPH_URL, settings.GRAPH_USER, settings.GRAPH_PASSWORD,
                             None if settings.GRAPH_NAME in ("", "medflow") else settings.GRAPH_NAME,
                             settings.GRAPH_TIMEOUT_SECONDS)
    if b == "falkordb":
        return FalkorExecutor(settings.GRAPH_URL, settings.GRAPH_NAME, settings.GRAPH_TIMEOUT_SECONDS)
    raise GraphUnavailable("The knowledge graph is disabled (GRAPH_BACKEND=disabled).")


def get_executor() -> Executor:
    """The shared executor; raises GraphUnavailable fast (without waiting for a timeout) right after a failure."""
    global _executor, _failed_at, _last_error
    with _lock:
        if _executor is not None:
            return _executor
        if _failed_at and time.monotonic() - _failed_at < RETRY_AFTER_SECONDS:
            raise GraphUnavailable(_last_error or "graph store unavailable")
        try:
            ex = _build()
            ex.ping()
        except GraphUnavailable as e:
            _failed_at, _last_error = time.monotonic(), str(e)
            raise
        except Exception as e:  # driver import/URL errors
            _failed_at, _last_error = time.monotonic(), f"{type(e).__name__}: {e}"
            raise GraphUnavailable(_last_error) from e
        _executor, _failed_at, _last_error = ex, 0.0, None
        return ex


def mark_failed(err: Exception) -> None:
    """Drop the executor after a connection failure so the next call reconnects (after RETRY_AFTER_SECONDS)."""
    global _executor, _failed_at, _last_error
    with _lock:
        if _executor is not None:
            _executor.close()
        _executor, _failed_at, _last_error = None, time.monotonic(), str(err)


def reset(executor: Executor | None = None) -> None:
    """Tests: install a specific executor (or clear state)."""
    global _executor, _failed_at, _last_error
    with _lock:
        _executor, _failed_at, _last_error = executor, 0.0, None


def last_error() -> str | None:
    return _last_error
