from functools import cache

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClickHouseConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLICKHOUSE_", env_file=".env", extra="ignore")

    host: str
    user: str
    password: str
    port: int = 8443
    secure: bool = True
    server_host_name: str | None = None


class GraphTablesConfig(BaseSettings):
    """Where the canonical node and edge tables live.

    `edges_table` is the dbt-built *source* table on purpose, not the union view: inference
    reads source edges only, so that one run's guess never becomes the next run's evidence.
    """

    model_config = SettingsConfigDict(env_prefix="GRAPH_", env_file=".env", extra="ignore")

    nodes_table: str = "dbt_dev.dim_graph_nodes"
    edges_table: str = "dbt_dev.bridge_graph_edges_source"
    node_id_column: str = "pk_dim_graph_nodes"

    inferred_table: str = "machine_learning.graph_edges_inferred"

    # slicing by market joins event nodes back to the events dim on their natural key
    events_table: str = "dbt.dim_events"
    events_key_column: str = "event_id"
    events_country_column: str = "event_country_iso"


def _clickhouse_config() -> ClickHouseConfig:
    # every field is read from the environment, which the constructor's signature cannot say
    return ClickHouseConfig()  # type: ignore[call-arg]


class Config(BaseSettings):
    clickhouse: ClickHouseConfig = Field(default_factory=_clickhouse_config)
    graph: GraphTablesConfig = Field(default_factory=GraphTablesConfig)


@cache
def get_config() -> Config:
    """Read configuration once, on first use.

    Deliberately not a module-level instance: importing this package must work with no
    credentials so that `--help` and test collection do not need a tunnel.
    """
    try:
        return Config()
    except ValidationError as error:
        missing = ", ".join(
            ".".join(str(part) for part in issue["loc"]) for issue in error.errors()
        )
        raise SystemExit(
            f"configuration is incomplete ({missing}).\n"
            "Copy .env.example to .env and fill in the ClickHouse credentials."
        ) from error
