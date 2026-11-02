"""``@action`` and the call machinery.

There is one decorator. An action that touches ``ctx.session`` is a *leaf* and does its own
work; an action that calls other actions is a *composite*. Which one a body is is decided at
run time, not declared — the leaf options below are read only when a run turns out to be a leaf,
and the registry warns if a body that only composes declares them.

Calling a decorated action from inside another action's body does not execute it inline: it
asks the active :class:`~baton.replay.ReplayPass` to find or create a child run and return its
memoised result. Outside a pass a call raises, except ``.direct(ctx, ...)`` which runs the raw
body against a caller-supplied context (unit tests, and the app's handler tests).
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any, cast

from baton.policy import ON_FAILURE_VALUES, Delay, OnFailure
from baton.registry import Registry, default_registry

if TYPE_CHECKING:
    from baton.context import RunContext
    from baton.replay import ReplayPass

# The pass a child call routes to. Set by the engine around a body; unset otherwise.
_active_pass: contextvars.ContextVar[ReplayPass | None] = contextvars.ContextVar(
    "baton_active_pass", default=None
)


def active_pass() -> ReplayPass | None:
    return _active_pass.get()


@dataclass(frozen=True, slots=True)
class ActionOptions:
    key: str
    name: str
    description: str = ""
    subject_kinds: tuple[str, ...] = ()
    one_open_per_subject: bool = False
    # leaf options — ignored on a composite
    retries: int = 0
    retry_delay: Delay = 0.0
    retry_jitter: float = 0.0
    retry_on: Callable[[BaseException], bool] | None = None
    attempt_timeout_seconds: float | None = None
    wait_timeout_seconds: float | None = None
    idempotent: bool = False
    guard: Callable[..., Awaitable[str | None]] | None = None
    # any run
    timeout_seconds: float | None = None
    on_failure: OnFailure = "fail"
    redact: Callable[[Any], Any] | None = None
    store_params: bool = True
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionDefinition[R]:
    # Actions take keyword arguments only, so the body signature is not captured as a ParamSpec;
    # callers pass a dict and the engine validates it. R is the return type a direct call sees.
    fn: Callable[..., Awaitable[R]]
    options: ActionOptions

    @property
    def key(self) -> str:
        return self.options.key

    @property
    def name(self) -> str:
        return self.options.name

    async def __call__(self, ctx: RunContext, **kwargs: Any) -> R:
        return cast(R, await _dispatch(self, ctx, kwargs, key=None, wait_for=()))

    async def submit(
        self,
        ctx: RunContext,
        *,
        key: str | None = None,
        wait_for: Sequence[str] = (),
        **kwargs: Any,
    ) -> R:
        return cast(R, await _dispatch(self, ctx, kwargs, key=key, wait_for=tuple(wait_for)))

    def with_options(self, **overrides: Any) -> ActionDefinition[R]:
        """A call-site variant — e.g. ``on_failure="continue"`` for best-effort. The variant is
        not registered; it shares ``key`` with the original and is only used for this call."""
        allowed = {f.name for f in fields(ActionOptions)}
        bad = set(overrides) - allowed
        if bad:
            raise TypeError(f"unknown action option(s): {sorted(bad)}")
        return ActionDefinition(self.fn, replace(self.options, **overrides))

    async def direct(self, ctx: RunContext, **kwargs: Any) -> R:
        """Run the raw body against ``ctx``, bypassing the engine. For unit tests only."""
        return await self.fn(ctx, **kwargs)


async def _dispatch(
    defn: ActionDefinition[Any],
    ctx: RunContext,
    kwargs: dict[str, Any],
    *,
    key: str | None,
    wait_for: tuple[str, ...],
) -> Any:
    from baton.errors import ProgrammingError

    pass_ = _active_pass.get()
    if pass_ is None:
        raise ProgrammingError(
            f"{defn.key} was called outside an action run; use .direct(ctx, ...) to run it bare"
        )
    return await pass_.call(defn, kwargs, key=key, wait_for=wait_for)


def action[R](
    *,
    key: str,
    name: str | None = None,
    description: str = "",
    subject_kinds: tuple[str, ...] = (),
    one_open_per_subject: bool = False,
    retries: int = 0,
    retry_delay: Delay = 0.0,
    retry_jitter: float = 0.0,
    retry_on: Callable[[BaseException], bool] | None = None,
    attempt_timeout_seconds: float | None = None,
    wait_timeout_seconds: float | None = None,
    idempotent: bool = False,
    guard: Callable[..., Awaitable[str | None]] | None = None,
    timeout_seconds: float | None = None,
    on_failure: OnFailure = "fail",
    redact: Callable[[Any], Any] | None = None,
    store_params: bool = True,
    meta: Mapping[str, Any] | None = None,
    registry: Registry | None = None,
) -> Callable[[Callable[..., Awaitable[R]]], ActionDefinition[R]]:
    if on_failure not in ON_FAILURE_VALUES:
        raise ValueError(f"on_failure must be one of {sorted(ON_FAILURE_VALUES)}")

    def wrap(body: Callable[..., Awaitable[R]]) -> ActionDefinition[R]:
        options = ActionOptions(
            key=key,
            name=name or body.__name__.replace("_", " ").strip().capitalize(),
            description=description,
            subject_kinds=subject_kinds,
            one_open_per_subject=one_open_per_subject,
            retries=retries,
            retry_delay=retry_delay,
            retry_jitter=retry_jitter,
            retry_on=retry_on,
            attempt_timeout_seconds=attempt_timeout_seconds,
            wait_timeout_seconds=wait_timeout_seconds,
            idempotent=idempotent,
            guard=guard,
            timeout_seconds=timeout_seconds,
            on_failure=on_failure,
            redact=redact,
            store_params=store_params,
            meta=dict(meta or {}),
        )
        defn = ActionDefinition(body, options)
        (registry or default_registry).register(defn)
        return defn

    return wrap
