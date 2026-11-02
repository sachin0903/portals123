# The event-schema registry — the single, declared source of truth for what an action event
# (`audit.actions`) CARRIES. Named `event_schema` (not `events`) to stay clear of `app/events.py`,
# the WorkOS Events consumer. See `app/event_schema/registry.py`.
from app.event_schema import registry

__all__ = ["registry"]
