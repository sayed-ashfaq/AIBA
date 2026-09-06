"""{qualified_name: models.Table}  ->  a networkx graph, plus the traversals over it.

Nodes are qualified table names. Edges are foreign keys, stored **undirected** —
a join reads either way — with the originating models.FKEdge kept on the edge so
rendering can still emit `child.col = parent.col` in the right order.

Every edge carries `weight=1.0`: "shortest path" then means "fewest joins", and
networkx's steiner_tree needs a weight attribute to choose between parallel
edges on a MultiGraph (without it, nx 3.6 raises KeyError: 'weight').

Pure graph: no DB, no embeddings, no LLM. Depends only on models + networkx.
"""

from itertools import combinations
from typing import Iterable

import networkx as nx
from networkx.algorithms.approximation import steiner_tree as _nx_steiner_tree

from app.agents.subagents.sql_agent.schema_linking import models


def build_graph(tables: dict[str, models.Table]) -> nx.MultiGraph:
    """Tables -> nodes, foreign keys -> edges. MultiGraph because two tables can
    be linked by more than one FK (e.g. flight.origin_id and flight.dest_id both
    -> airport.id) and we want to keep both for rendering."""
    g = nx.MultiGraph()
    g.add_nodes_from(tables)  # include tables with no FK at all — isolated nodes
    for table in tables.values():
        for fk in table.foreign_keys:
            if fk.from_table not in tables or fk.to_table not in tables:
                continue
            g.add_edge(fk.from_table, fk.to_table, weight=1.0, fk=fk)
    return g


def shortest_path(g: nx.MultiGraph, a: str, b: str) -> list[str]:
    """Table names on a shortest FK path from a to b, inclusive. [] if either is
    absent or they sit in disconnected parts of the schema."""
    if a not in g or b not in g:
        return []
    if a == b:
        return [a]
    try:
        return nx.shortest_path(g, a, b)
    except nx.NetworkXNoPath:
        return []


def connecting_tables(g: nx.MultiGraph, anchors: Iterable[str]) -> list[str]:
    """Smallest set of tables that connects every anchor (an approximate Steiner
    tree), including the bridge tables in between.

    Restricted to the connected component of the first reachable anchor:
    steiner_tree's approximation measures distances across the whole graph it is
    handed and trips over the schema's isolated tables (migrations, audit logs
    with no FKs). Anchors outside that component are dropped — logged by the
    caller, not here.
    """
    present = [a for a in dict.fromkeys(anchors) if a in g]
    if len(present) <= 1:
        return present
    if len(present) == 2:
        return shortest_path(g, present[0], present[1])

    component = nx.node_connected_component(g, present[0])
    reachable = [a for a in present if a in component]
    if len(reachable) <= 1:
        return reachable

    try:
        tree = _nx_steiner_tree(g.subgraph(component), reachable, weight="weight")
        return list(tree.nodes)
    except (nx.NetworkXNoPath, nx.NodeNotFound, KeyError):
        # fall back to the union of pairwise shortest paths
        nodes: set[str] = set(reachable)
        for x, y in combinations(reachable, 2):
            nodes.update(shortest_path(g, x, y))
        return list(nodes)


def fk_neighbours(g: nx.MultiGraph, nodes: Iterable[str], hops: int = 1) -> set[str]:
    """Every table within `hops` FK steps of any node in `nodes`, excluding
    `nodes` themselves. The 1-hop "sphere" that pulls in directly related
    tables an embedding match would miss (a lookup table, a status dimension)."""
    start = {n for n in nodes if n in g}
    seen = set(start)
    frontier = set(start)
    for _ in range(max(hops, 0)):
        nxt = {nb for n in frontier for nb in g.neighbors(n)} - seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    return seen - start


def edges_within(g: nx.MultiGraph, nodes: Iterable[str]) -> list[models.FKEdge]:
    """The FK edges with both endpoints in `nodes`, de-duplicated. These are the
    join conditions for the focused schema."""
    seen: set[tuple[str, str, str, str]] = set()
    out: list[models.FKEdge] = []
    for _u, _v, data in g.subgraph(set(nodes)).edges(data=True):
        fk: models.FKEdge = data["fk"]
        key = (fk.from_table, fk.from_column, fk.to_table, fk.to_column)
        if key not in seen:
            seen.add(key)
            out.append(fk)
    return out
