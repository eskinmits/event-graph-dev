from functools import cache

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from event_graph.config import get_config


@cache
def get_client() -> Client:
    """Return the shared ClickHouse client, connecting on first use."""
    settings = get_config().clickhouse

    return clickhouse_connect.get_client(
        host=settings.host,
        port=settings.port,
        username=settings.user,
        password=settings.password,
        secure=settings.secure,
        server_host_name=settings.server_host_name,
    )
