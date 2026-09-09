"""Data shapes for schema linking.

Every module in this package speaks in terms of these types — they are the
contract at each hand-off (introspection -> graph -> linking -> render). Pure
data, no behaviour: nothing here reaches a database, an LLM, or the network.

Plain dataclasses rather than Pydantic on purpose: this data originates from our
own introspection code, not from an untrusted HTTP request, so there is nothing
to validate or coerce at runtime. Same convention as db.py's TableSchema /
DbContext.
"""

from dataclasses import dataclass, field

import networkx as nx
import numpy as np


@dataclass(frozen=True)
class Column:
    """One column of one table.

    frozen: a column's identity never changes after introspection reads it, so
    the same instance is safe to reference from the graph, the embedding text,
    and the rendered schema without any risk of one caller mutating it for all.
    """

    name: str
    type: str  # str(sqlalchemy_type), e.g. "VARCHAR(255)" — only ever shown to the LLM
    pk: bool
    nullable: bool
    # A few real values, for low-cardinality columns only, so the SQL generator
    # filters on the actual literal ("status = 'SHIPPED'", not "'shipped'").
    # Empty tuple = not sampled (high-cardinality, or sampling skipped). A tuple
    # rather than None so callers can iterate with no guard; tuple rather than
    # list so a frozen Column stays fully immutable and hashable.
    sample_values: tuple[str, ...] = ()
    # True  -> sample_values is the column's COMPLETE distinct set: the generator
    #          can filter with `=` on the exact literal shown.
    # False -> sample_values is examples only (or empty): the generator must not
    #          assume the user's wording matches one of them — use ILIKE.
    values_complete: bool = False

@dataclass(frozen=True)
class FKEdge:
    """One foreign-key link.

    Fully specifies a join condition: from_table.from_column = to_table.to_column.
    """

    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class Table:
    """One table: its structure now, its description later.

    Not frozen — `description` is filled in by descriptions.py (Step 7), long
    after introspection builds the rest. Every other field is set once at
    creation.
    """

    name: str  # qualified, e.g. "sales.customer" or bare "customer" — matches db.qualify()
    columns: list[Column]
    foreign_keys: list[FKEdge] = field(default_factory=list)
    description: str | None = None  # LLM one-liner; None until Step 7 populates it


@dataclass
class SchemaGraph:
    """Everything built once per connection and cached for its lifetime.

    Not frozen: builder.py attaches `embeddings` after constructing the rest,
    and the tables' `description`s are filled in the same pass.
    """

    # nodes = table names, edges = FKs. MultiGraph because two tables can be
    # linked by more than one foreign key (a composite/second FK between them).
    graph: nx.MultiGraph
    tables: dict[str, Table]  # keyed by Table.name
    # Stable ordering: row i of `embeddings` corresponds to table_names[i]. Without
    # a fixed list there is no way to map a similarity score back to a table.
    table_names: list[str] = field(default_factory=list)
    embeddings: np.ndarray | None = None  # shape (len(table_names), dim); filled by builder.py


@dataclass(frozen=True)
class FocusedSchema:
    """What linking.py produces for one question — the slice that goes to SQL generation.

    Structured, not a rendered string: the eval harness compares `path_tables`
    against gold tables, logs want the anchors, the UI may highlight them.
    render.py turns this into text, separately. (frozen freezes the attribute
    bindings, not the lists themselves — treat the lists as read-only by
    convention.)
    """

    question: str
    anchor_tables: list[str]  # matched by embedding similarity
    path_tables: list[str]    # anchors + bridge tables from graph traversal — the full set
    edges: list[FKEdge]       # join conditions connecting path_tables
