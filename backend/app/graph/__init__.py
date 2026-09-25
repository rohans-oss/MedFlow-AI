"""V6 — operational supply knowledge graph.

    PostgreSQL (source of truth) ──projection──► graph store (Neo4j in production; any openCypher engine)
                                                   │
                         explanations · impact analysis · predefined graph queries (no LLM, no Graph ML)

The graph is a derived, disposable copy: it can be dropped and rebuilt from PostgreSQL at any time. If the graph store
is down, only the graph features are unavailable — inventory, forecasting, risk, suppliers and procurement never read
from it.
"""
