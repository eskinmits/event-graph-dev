"""The table inference writes back to, owned by this repo rather than by dbt.

dbt rebuilds its tables from scratch on every run, so inferred edges written into a
dbt-built table would vanish on the next nightly. This table has one writer -- us -- and dbt
only reads it through `stg_machine_learning__graph_edges_inferred`. The DDL is the write
contract on the Ontology & Edge Model page in Notion; change it there first.
"""

import logging

from clickhouse_connect.driver.client import Client

from event_graph.graph.source import _identifier

logger = logging.getLogger(__name__)


def inferred_table_ddl(table: str) -> str:
    """Return the `create table` statement for the inferred-edges table named `table`.

    Same eleven columns as `bridge_graph_edges_source`, so the dbt union view stacks the two
    by name with no casts. One row per edge per `run_id`: rerunning a run replaces its own
    rows, a new run adds a second row for the same edge.
    """
    return f"""
create table if not exists {_identifier(table, "inferred_table")}
(
    edge_id         String,
    src_node_id     String,
    dst_node_id     String,
    predicate       LowCardinality(String),
    source          LowCardinality(String) default 'graph_inferred',
    source_class    LowCardinality(String) default 'graph_inferred',
    run_id          String,
    evidence        String default '{{}}',
    confidence      Float32,
    observed_at     Nullable(DateTime),
    data_updated_at DateTime default now()
)
engine = ReplacingMergeTree(data_updated_at)
order by (edge_id, run_id)
"""


def create_inferred_table(client: Client, table: str) -> bool:
    """Create the inferred-edges table if it is absent. Returns whether it was created."""
    database, _, name = _identifier(table, "inferred_table").rpartition(".")
    existed = bool(
        client.query(
            "select count() from system.tables"
            " where database = {database:String} and name = {name:String}",
            parameters={"database": database or client.database or "default", "name": name},
        ).result_rows[0][0]
    )
    client.command(inferred_table_ddl(table))
    if not existed:
        logger.info("created %s", table)
    return not existed
