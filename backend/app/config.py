"""Runtime configuration from environment variables (no secrets in code)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    project: str = field(default_factory=lambda: _env("TENSOR_GCP_PROJECT", "ai-builder-nexusforge"))
    gemini_location: str = field(default_factory=lambda: _env("TENSOR_GEMINI_LOCATION", "global"))
    # Routing/lookup hops use Flash-Lite (~1 s/call); analysis and planning use Flash with a thinking budget.
    fast_model: str = field(default_factory=lambda: _env("TENSOR_FAST_MODEL", "gemini-3.5-flash-lite"))
    planner_model: str = field(default_factory=lambda: _env("TENSOR_PLANNER_MODEL", "gemini-3.8-flash"))
    # Speech-to-Text v2 (per-participant streams). latest_long + phrase boost gave the fastest wake detection.
    stt_location: str = field(default_factory=lambda: _env("TENSOR_STT_LOCATION", "global"))
    stt_model: str = field(default_factory=lambda: _env("TENSOR_STT_MODEL", "latest_long"))
    stt_language: str = field(default_factory=lambda: _env("TENSOR_STT_LANGUAGE", "en-US"))
    # Transcript and commands: Chirp 3 (far more accurate on accented, noisy speech; no interim results).
    stt_accurate_model: str = field(default_factory=lambda: _env("TENSOR_STT_ACCURATE_MODEL", "chirp_3"))
    stt_accurate_location: str = field(default_factory=lambda: _env("TENSOR_STT_ACCURATE_LOCATION", "us"))
    stt_accurate_language: str = field(default_factory=lambda: _env("TENSOR_STT_ACCURATE_LANGUAGE", "en-IN"))
    tts_voice: str = field(default_factory=lambda: _env("TENSOR_TTS_VOICE", "en-IN-Chirp3-HD-Achird"))
    search_serving_config: str = field(default_factory=lambda: _env(
        "TENSOR_SEARCH_SERVING_CONFIG",
        "projects/ai-builder-nexusforge/locations/global/collections/default_collection/engines/tensor-companies/servingConfigs/default_search"))
    search_branch: str = field(default_factory=lambda: _env(
        "TENSOR_SEARCH_BRANCH",
        "projects/ai-builder-nexusforge/locations/global/collections/default_collection/dataStores/tensor-companies-kb/branches/default_branch"))
    docai_processor: str = field(default_factory=lambda: _env(
        "TENSOR_DOCAI_PROCESSOR", "projects/201727568367/locations/us/processors/cce833a35a55d5a9"))
    bucket: str = field(default_factory=lambda: _env("TENSOR_BUCKET", "ai-builder-nexusforge-tensor"))
    bq_dataset: str = field(default_factory=lambda: _env("TENSOR_BQ_DATASET", "tensor"))
    bq_location: str = field(default_factory=lambda: _env("TENSOR_BQ_LOCATION", "asia-south1"))
    # Integrations: real when configured, otherwise clearly labelled sandbox executors.
    jira_base_url: str | None = field(default_factory=lambda: _env("TENSOR_JIRA_BASE_URL"))
    jira_email: str | None = field(default_factory=lambda: _env("TENSOR_JIRA_EMAIL"))
    jira_api_token: str | None = field(default_factory=lambda: _env("TENSOR_JIRA_API_TOKEN"))
    gmail_client_id: str | None = field(default_factory=lambda: _env("TENSOR_GMAIL_CLIENT_ID"))
    gmail_client_secret: str | None = field(default_factory=lambda: _env("TENSOR_GMAIL_CLIENT_SECRET"))
    gmail_refresh_token: str | None = field(default_factory=lambda: _env("TENSOR_GMAIL_REFRESH_TOKEN"))
    gmail_sender: str | None = field(default_factory=lambda: _env("TENSOR_GMAIL_SENDER"))
    # Demo directory: everyone resolves to plus-addresses of the operator, so real sends never reach strangers.
    directory_base_email: str = field(default_factory=lambda: _env("TENSOR_DIRECTORY_BASE_EMAIL", "demo@example.com"))
    ai_enabled: bool = field(default_factory=lambda: _env("TENSOR_AI", "on").lower() not in ("off", "0", "false"))
    store: str = field(default_factory=lambda: _env("TENSOR_STORE", "firestore"))  # firestore | memory
    # Accounts: session cookies are Secure on HTTPS hosting; sign-ups stop at a company cap (credit protection).
    cookie_secure: bool = field(default_factory=lambda: _env("TENSOR_COOKIE_SECURE", "0") in ("1", "true"))
    max_companies: int = field(default_factory=lambda: int(_env("TENSOR_MAX_COMPANIES", "25")))

    @property
    def jira_live(self) -> bool:
        return bool(self.jira_base_url and self.jira_email and self.jira_api_token)

    @property
    def gmail_live(self) -> bool:
        return bool(self.gmail_client_id and self.gmail_client_secret and self.gmail_refresh_token and self.gmail_sender)


PEOPLE = {
    "david": dict(name="David Mathew", role="Security Lead", jira="david", project="SEC"),
    "sarah": dict(name="Sarah Iyer", role="Operations Lead", jira="sarah", project=None),
    "rahul": dict(name="Rahul Verma", role="Staff Engineer, Platform", jira="rahul", project="PLAT"),
    "priya": dict(name="Priya Nair", role="Engineering Manager, Platform", jira="priya", project="PLAT"),
    "vyshnav": dict(name="Vyshnav", role="Product Lead", jira="vyshnav", project=None),
}


def directory(settings: Settings) -> list[dict]:
    """The demo company's team (Acme Fintech); other companies keep their team on the company record."""
    user, _, domain = settings.directory_base_email.partition("@")
    return [dict(key=k, email=f"{user}+{k}@{domain}", **v) for k, v in PEOPLE.items()]
