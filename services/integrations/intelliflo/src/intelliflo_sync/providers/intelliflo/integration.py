"""Intelliflo integration — orchestrates all Intelliflo endpoints."""

from intelliflo_sync.providers.intelliflo import config
from intelliflo_sync.providers.intelliflo import contract as iflo_contract
from intelliflo_sync.providers.intelliflo import db as iflo_db
from intelliflo_sync.shared.base.auth import OAuth2Auth
from intelliflo_sync.shared.base.exceptions import FeatureError
from intelliflo_sync.shared.base.integration import BaseIntegration
from intelliflo_sync.shared.db import fetch_integration_features

# Run order when several features are due: Valuations, then Transactions, then IncomeStatements, then
# IncomeStatementItems (the items attach to a statement, so it must come before them).
FEATURE_ORDER = [
    "Valuations",
    "Transactions",
    "IncomeStatements",
    "IncomeStatementItems",
]


class IntellifloIntegration(BaseIntegration):
    name = "intelliflo"
    cron = config.CRON

    @classmethod
    def cron_for(cls, feature: str) -> str:
        """INTELLIFLO_<FEATURE>_CRON if set, else INTELLIFLO_CRON."""
        return config.feature_cron(feature) or cls.cron

    @classmethod
    def order_features(cls, features: list[str]) -> list[str]:
        """Dependency order (see FEATURE_ORDER); features it does not know run last, alphabetically."""
        rank = {feature: i for i, feature in enumerate(FEATURE_ORDER)}
        return sorted(dict.fromkeys(features), key=lambda f: (rank.get(f, len(rank)), f))

    def __init__(self):
        super().__init__()

    def _make_auth(self, tenant_id: str) -> tuple[OAuth2Auth, OAuth2Auth]:
        """Create the OAuth2Auth instances (general + valuation batch) for a tenant."""
        missing = config.missing_credentials()
        if missing:
            raise ValueError(f"Intelliflo credentials are not configured — set {', '.join(missing)}")
        auth = OAuth2Auth(
            token_url=config.TOKEN_URL,
            client_id=config.CLIENT_ID,
            client_secret=config.CLIENT_SECRET,
            grant_type=config.GRANT_TYPE,
            scope=config.SCOPE,
            extra_data={"tenant_id": tenant_id},
        )
        valuation_auth = OAuth2Auth(
            token_url=config.TOKEN_URL,
            client_id=config.VALUATION_CLIENT_ID,
            client_secret=config.VALUATION_CLIENT_SECRET,
            grant_type=config.GRANT_TYPE,
            scope=config.VALUATION_BATCH_SCOPE,
            extra_data={"tenant_id": tenant_id},
        )
        return auth, valuation_auth

    def run(self, tenant_id: str, features: list[str] | None = None) -> dict:
        """Run `features` for the tenant; with None, the features linked to it in platform_features."""
        firm = iflo_db.get_platform_config(tenant_id)
        if not firm:
            raise ValueError(f"No platform found for tenant_id '{tenant_id}'")
        missing = [k for k in ("firm_id", "provider_id") if not firm.get(k)]
        if missing:
            raise ValueError(f"Platform for tenant_id '{tenant_id}' is missing: {', '.join(missing)}")

        # platform_features is the only place that says what a tenant runs — there is no built-in default list.
        run_features = features if features is not None else fetch_integration_features(firm["platform_id"])
        if not run_features:
            raise ValueError(f"No features are linked to tenant_id '{tenant_id}' in platform_features")
        run_features = self.order_features(run_features)

        self.logger.info(
            "Starting Intelliflo sync for tenant %s (firm %s) — features: %s",
            tenant_id,
            firm.get("firm_id"),
            run_features,
        )

        # A feature nothing implements (its change has not landed, or the name is wrong) FAILS. Counting
        # it a success would record the data date as done, and once the feature does exist that day
        # would be skipped and its data never pushed. `contract.FEATURES` has an entry per implemented one.
        unimplemented = [f for f in run_features if f not in iflo_contract.FEATURES]
        # A feature whose schema requirements are not met is reported as failed; the others still run.
        blocked = iflo_contract.blocked_features(run_features)
        runnable = [f for f in run_features if f not in blocked and f not in unimplemented]
        counts, errors = self._run_firm(firm, runnable)
        errors += [f"{f}: not implemented" for f in unimplemented]
        errors += [f"{f}: schema not ready — missing {', '.join(gaps)}" for f, gaps in blocked.items()]

        result = {f: counts.get(f, {}) for f in run_features}
        self.logger.info("Intelliflo sync complete for tenant %s: %s", tenant_id, result)

        if errors:
            failed_features = {msg.split(":", 1)[0].strip() for msg in errors}
            raise FeatureError(
                f"tenant {tenant_id} had errors: {'; '.join(errors)}",
                data={"results": result, "failed_features": failed_features},
            )
        return result

    def _run_firm(self, firm: dict, features: list[str]) -> tuple[dict, list[str]]:
        """
        Run all requested features sequentially for a single firm.
        Returns (counts_per_feature, error_messages).

        The firm's job owns ONE lake connection, opened by the first feature that reads the lake and closed
        when the job ends, so a firm attaches the lake once a day instead of once per feature. It is passed
        down explicitly: another firm's job has its own, and nothing is shared between them.
        """
        with iflo_db.LakeSession() as lake:
            return self._run_firm_features(firm, features, lake)

    def _run_firm_features(self, firm: dict, features: list[str], lake: iflo_db.LakeSession) -> tuple[dict, list[str]]:
        firm_id = firm["firm_id"]
        counts: dict[str, dict] = {}
        errors: list[str] = []

        self._make_auth(firm["tenant_id"])

        for feature in features:
            # Features arrive one at a time; none is implemented yet.
            self.logger.warning("firm %s: no implementation for feature %s yet", firm_id, feature)
        return counts, errors
