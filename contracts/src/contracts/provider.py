"""Providers: agent cards, the registry entry and the HTTP task contract (plan §9.2, D18, D19).

Every provider is an opaque endpoint + agent card. Nothing here knows any provider's traits.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.base import Contract, DocId, ExternalContract, ProviderId, UsdAmount

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]


class AgentCard(ExternalContract):
    """Served at GET {base_url}/p/{slug}/.well-known/agent-card.json."""

    slug: Slug
    name: str
    description: str
    version: Annotated[str, StringConstraints(pattern=r"^[^@\s]+$")]
    job_types: list[str]
    price_per_doc_usd: UsdAmount = Field(
        description="List price; used only by the Candidate Finder's budget filter (D19)"
    )
    region: str = Field(description='"global" or a region code')


class RegisteredProvider(Contract):
    card: AgentCard
    base_url: str

    @property
    def provider_id(self) -> str:
        """The provider ID used everywhere: "{slug}@{version}"."""
        return f"{self.card.slug}@{self.card.version}"

    @property
    def tasks_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/p/{self.card.slug}/tasks"


class ProviderTask(Contract):
    """Body of POST /p/{slug}/tasks. Identical for trials and real jobs: no trial flag, ever."""

    task_id: str
    requester_id: str
    document_url: str = Field(description="{DOCS_BASE_URL}/d/{doc_id}.pdf (D17)")
    output_schema: dict[str, Any]
    deadline_ms: int = Field(gt=0, description="Time allowed to answer, in ms from receipt")
    offered_price: UsdAmount = Field(description="USD offered for this document")


class ProviderResult(ExternalContract):
    """200 response to a ProviderTask.

    `output` is a plain dict on purpose: a result whose output isn't a valid Invoice still
    parses here, and the Scorer marks that document `invalid` (score 0).
    """

    task_id: str
    output: dict[str, Any]
    cost_usd: UsdAmount = Field(
        description="The provider's actual model spend for this task (sum of x-parley-v1-cost)"
    )
    provider_version: str


class ProviderError(ExternalContract):
    """Error body. 402 + {"error": "budget_exhausted"} pauses the run (D18)."""

    error: str


SubmissionStatus = Literal["ok", "timeout", "error", "not_run"]


class Submission(Contract):
    """The Trial Runner's record of one task sent to one provider.

    ok = 200 + a parseable ProviderResult; timeout = no answer before the deadline;
    error = any other non-200 or an unparseable body; not_run = never sent (run paused, D18).
    """

    task_id: str
    doc_id: DocId
    provider: ProviderId
    status: SubmissionStatus
    output: dict[str, Any] | None = Field(description="Raw provider output, not validated")
    latency_ms: int | None = Field(ge=0, description="None when not_run")
    cost_usd: UsdAmount | None = Field(description="From ProviderResult; None when unknown")

    @model_validator(mode="after")
    def _output_matches_status(self) -> "Submission":
        if (self.status == "ok") != (self.output is not None):
            raise ValueError("output must be set exactly when status is 'ok'")
        return self
