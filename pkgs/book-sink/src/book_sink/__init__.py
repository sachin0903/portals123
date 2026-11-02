"""The provider pipelines' write side: a tenant's conformed slice -> stage -> the book.

One package, three providers. What used to be two hops the API drove -- `app/fanout.py` projecting
a tenant's slice, `app/fanout_listener.py` reacting to a daemon run, `app/materialise.py` upserting
the OLTP model, all three now DELETED -- is one chain of interlace models inside the provider's own
DAG. That reverses
docs/platform-model.md §5 on purpose: the write goes to the daemon, with `book_writer` as the
credential and platform.merge_rejects as the record.

Read `register.register` first: it is the whole shape in one function.
"""

from book_sink.api_log import SyncApiLog, install_api_log
from book_sink.config import SinkConfig, SinkUnavailable, from_env
from book_sink.entities import (
    CONFORMED,
    Entity,
    Parent,
    Requirement,
    for_provider,
    required_columns,
)
from book_sink.merge import (
    EntityLedger,
    landing_relation,
    landing_schema,
    landing_table,
    merge_tenant,
)
from book_sink.register import BOOK_ALIAS, provider_schema, register, tenant_model_key
from book_sink.tenants import TenantSlice, enabled

__all__ = [
    "BOOK_ALIAS",
    "CONFORMED",
    "Entity",
    "EntityLedger",
    "Parent",
    "Requirement",
    "SinkConfig",
    "SinkUnavailable",
    "SyncApiLog",
    "TenantSlice",
    "enabled",
    "install_api_log",
    "for_provider",
    "from_env",
    "landing_relation",
    "landing_schema",
    "landing_table",
    "merge_tenant",
    "provider_schema",
    "required_columns",
    "register",
    "tenant_model_key",
]
