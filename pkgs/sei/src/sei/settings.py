"""Runtime settings. Instantiate lazily (inside functions) so importing the package
succeeds with no environment configured.

Credentials are **per SEI environment** (i10 / i31 / p04): the mTLS certificate is usually
shared across environments (it is the client identity), while the OAuth user and the
app-gateway AppKey/AppSecret differ. So every field is looked up as::

    SEI_{ENV}_{FIELD}      e.g. SEI_I31_APPKEY
    SEI_{FIELD}            shared fallback, e.g. SEI_PFX_PASSWORD

which is the same convention the monitoring repo's probes use, so one ``.env`` serves both.
"""

from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: SWP base URL per environment. i10/i31 share the test host and differ only by credentials.
ENVIRONMENTS: dict[str, str] = {
    "i10": "https://test.mtls.api.seic.com/api/v1/swp",
    "i31": "https://test.mtls.api.seic.com/api/v1/swp",
    "p04": "https://mtls.api.seic.com/api/v1/swp",
}

OAUTH_PATH = "/oauthsecurity/access_token"


def cred(env: str, field: str, default: str | None = None) -> str | None:
    """Per-environment credential lookup with shared fallback (see module docstring)."""
    e, f = env.upper().replace("-", "_"), field.upper()
    for key in (f"SEI_{e}_{f}", f"SEI_{f}"):
        value = os.environ.get(key)
        if value:
            return value
    return default


class SeiSettings(BaseSettings):
    """SEI SWP connection + auth for one environment.

    Two credentials stack on every call: the **mTLS client certificate** (a PKCS#12 bundle
    SEI issues, which is the machine identity and is what their IP/cert allowlist keys on)
    and an **OAuth password grant** whose token is carried as a bearer. The ``AppKey`` is a
    third, separate credential — an app-gateway header sent on every request including the
    token request, where the app pair also goes in as HTTP Basic.
    """

    model_config = SettingsConfigDict(env_prefix="SEI_", env_file=".env", extra="ignore")

    environment: str = "i31"
    base_url: str = ""
    #: The PKCS#12 bundle SEI issues, base64-encoded (``base64 -w0 sei.pfx``).
    #:
    #: **The only supported form.** A file path was accepted until Aug 2026 and is gone: a path
    #: needs a file on the host and a bind mount, which Coolify's one-env-list model has nowhere
    #: to put, so every real deployment used base64 anyway — and carrying two forms meant two
    #: precedence rules, two "is it configured" checks and an error message that named the wrong
    #: one. That divergence was a live bug (see the git history of ``has_certificate``).
    #: `repr=False` ON EVERY SECRET, and this is not decoration.
    #:
    #: pydantic-settings renders the whole model in a repr, and pytest prints the value of every
    #: fixture argument in a traceback — so one failing integration test posted the live `appkey`
    #: and `appsecret` in plaintext into a terminal, and into anything that terminal was pasted
    #: into. The estate's rule is that secrets never hit logs (`app/email.py` makes the same
    #: argument for a recipient address), and a settings object that is an argument to a fixture
    #: is a log waiting to happen. `pfx_base64` is the private KEY, so it is the worst of them.
    pfx_base64: str = Field(default="", repr=False)
    pfx_password: str = Field(default="", repr=False)
    oauth_username: str = ""
    oauth_password: str = Field(default="", repr=False)
    appkey: str = Field(default="", repr=False)
    appsecret: str = Field(default="", repr=False)
    concurrency: int = 4
    timeout: float = 60.0
    # Split from `timeout` so a TCP connect that hangs (the usual symptom of calling from a
    # non-allowlisted IP) fails fast instead of eating the whole budget.
    connect_timeout: float = 10.0

    @classmethod
    def for_environment(cls, environment: str) -> SeiSettings:
        """Build settings for one SEI environment, resolving each field through
        ``SEI_{ENV}_{FIELD}`` then ``SEI_{FIELD}``."""
        return cls(
            environment=environment,
            base_url=cred(environment, "base_url", ENVIRONMENTS.get(environment, "")) or "",
            pfx_base64=cred(environment, "pfx_base64", "") or "",
            pfx_password=cred(environment, "pfx_password", "") or "",
            oauth_username=cred(environment, "oauth_username", "") or "",
            oauth_password=cred(environment, "oauth_password", "") or "",
            appkey=cred(environment, "appkey", "") or "",
            appsecret=cred(environment, "appsecret", "") or "",
        )

    @property
    def configured(self) -> bool:
        """Whether enough is set to attempt a call.

        One certificate form means one check. While there were two, this asked for the path and
        `ssl_context`/`pfx_material` preferred the base64 — so a container setting only
        `SEI_PFX_BASE64` was refused at `__aenter__` with `SEI is not configured: SEI_PFX_PATH
        unset`, naming the one form it could not supply.
        """
        return bool(
            self.base_url
            and self.pfx_base64
            and self.pfx_password
            and self.oauth_username
            and self.oauth_password
            and self.appkey
        )

    def missing(self) -> list[str]:
        """Which credentials are unset, named as the env vars an operator has to fill in."""
        env = self.environment.upper()
        # In declaration order, so the list reads top to bottom for whoever fills it in.
        pairs = (
            (bool(self.base_url), "SEI_BASE_URL"),
            (bool(self.pfx_base64), "SEI_PFX_BASE64"),
            (bool(self.pfx_password), "SEI_PFX_PASSWORD"),
            (bool(self.oauth_username), f"SEI_{env}_OAUTH_USERNAME"),
            (bool(self.oauth_password), f"SEI_{env}_OAUTH_PASSWORD"),
            (bool(self.appkey), f"SEI_{env}_APPKEY"),
        )
        return [name for present, name in pairs if not present]
