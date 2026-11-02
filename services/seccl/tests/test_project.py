"""The interlace project compiles: decorators registered, DAG resolves, streams present."""

from __future__ import annotations

from pathlib import Path

from interlace.project import Project

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_MODELS = {
    # cursor extractors
    "raw.notifications",
    "raw.messages",
    "raw.transactions",
    "raw.asset_prices",
    # list diffs
    "raw.clients",
    "raw.accounts",
    "raw.assets",
    "raw.portfolios",
    "raw.nodes",
    "raw.users",
    "raw.models",
    "raw.client_products",
    "raw.third_party_products",
    "raw.master_parties",
    "raw.nationalities",
    "raw.transaction_codes",
    "raw.illustrations",
    "raw.dashboard_information",
    "raw.dashboard_exceptions",
    # fan-outs
    "raw.account_details",
    "raw.account_summaries",
    "raw.account_reports",
    "raw.portfolio_summaries",
    # streams -> staging -> seccl
    "staging.transactions",
    "staging.positions",
    "staging.clients",
    "staging.exceptions",
    "staging.messages",
    "staging.transfers",
    # The conformed consumption layer (docs/platform-model.md §3) -- one column set every
    # provider publishes, which is what the materialiser reads.
    "platform.nodes",
    "platform.instruments",
    "platform.clients",
    "platform.accounts",
    "platform.valuations",
    "platform.positions",
    "platform.transactions",
    "derived.account_terms",
    "derived.book_costs",
}
EXPECTED_STREAMS = {"transactions", "positions", "clients", "exceptions", "messages", "transfers"}


def test_project_compiles_with_expected_models_and_streams() -> None:
    project = Project.load(ROOT)
    compiled = project.compile()
    assert EXPECTED_MODELS <= set(compiled.models)
    assert {stream.name for stream in project.streams} == EXPECTED_STREAMS

    # dependency edges resolved from function params / SQL references. A qualified
    # upstream is declared as `raw.assets` and injected as the `raw_assets` param.
    assert compiled.models["raw.asset_prices"].dependencies == ("raw.assets",)
    assert "raw.accounts" in compiled.models["raw.account_details"].dependencies
    # platform.transactions reads `raw` DIRECTLY, not through staging.transactions. The
    # conformed rebuild (docs/platform-model.md §3) collapsed that hop: staging existed to widen
    # and rename columns, which is what the conformed SELECT does anyway, and the extra relation
    # was a second place for the grain to drift. The staging models remain for the marts that still
    # read them.
    assert "raw.transactions" in compiled.models["platform.transactions"].dependencies
    assert "staging.transactions" not in compiled.models["platform.transactions"].dependencies
    # platform.nodes supersedes platform.nodes: same recursion, conformed vocabulary.
    assert "platform.nodes" in compiled.models["platform.transactions"].dependencies
    assert "platform.nodes" in compiled.models["platform.accounts"].dependencies
    assert "derived.account_terms" in compiled.models["platform.accounts"].dependencies
    assert "raw.account_details" in compiled.models["derived.account_terms"].dependencies
    assert "derived.book_costs" in compiled.models["platform.transactions"].dependencies
    assert "raw.transactions" in compiled.models["derived.book_costs"].dependencies

    # cursor columns wired through to the compiled models
    assert compiled.models["raw.transactions"].cursor == "transaction_date"
    assert compiled.models["raw.notifications"].cursor == "created"

    # every scheduled model carries a valid cron (build_triggers would reject junk)
    from interlace.scheduler.engine import build_triggers

    triggers = build_triggers(compiled)
    assert len(triggers) >= 20
