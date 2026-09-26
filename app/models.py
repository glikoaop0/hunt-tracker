from datetime import UTC, date, datetime
from enum import Enum

from sqlmodel import Field, Relationship, SQLModel, UniqueConstraint


class HuntStatus(str, Enum):
    idea = "idea"
    scoped = "scoped"
    active = "active"
    retired = "retired"


class RunOutcome(str, Enum):
    no_findings = "no_findings"
    findings = "findings"
    inconclusive = "inconclusive"
    detection_opportunity = "detection_opportunity"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


class RetirementReason(str, Enum):
    converted_to_detection = "converted_to_detection"
    completed_one_off = "completed_one_off"
    hypothesis_rejected = "hypothesis_rejected"
    missing_telemetry = "missing_telemetry"
    superseded = "superseded"
    no_longer_relevant = "no_longer_relevant"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


class Hunt(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    hypothesis: str
    query: str = ""
    status: HuntStatus = HuntStatus.idea
    priority: int = 3
    data_sources: str = ""
    attack_techniques: str = ""
    notes: str = ""
    cadence_days: int | None = None
    next_run: date | None = None
    retirement_reason: RetirementReason | None = None
    retirement_note: str | None = None
    retired_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    runs: list["Run"] = Relationship(back_populates="hunt")
    exclusions: list["Exclusion"] = Relationship(back_populates="hunt")
    campaign_links: list["CampaignHunt"] = Relationship(back_populates="hunt")

    @property
    def technique_count(self) -> int:
        return len([t for t in self.attack_techniques.split(",") if t.strip()])

    @property
    def run_count(self) -> int:
        return len(self.runs)

    @property
    def runs_by_recency(self) -> list["Run"]:
        return sorted(self.runs, key=lambda run: run.ran_at, reverse=True)

    @property
    def active_exclusions(self) -> list["Exclusion"]:
        return [exclusion for exclusion in self.exclusions if exclusion.active]

    @property
    def is_overdue(self) -> bool:
        # next_run is kept on retirement so reactivation restores the schedule,
        # but a retired hunt is not due, so it can never be overdue.
        if self.status == HuntStatus.retired:
            return False
        return self.next_run is not None and self.next_run < date.today()

    @property
    def was_ever_retired(self) -> bool:
        return self.status == HuntStatus.retired or self.retired_at is not None

    @property
    def retirement_reason_label(self) -> str:
        if self.retirement_reason is None:
            return "No reason recorded"
        return self.retirement_reason.label

    @property
    def campaigns(self) -> list["Campaign"]:
        return sorted(
            (link.campaign for link in self.campaign_links),
            key=lambda campaign: campaign.title.lower(),
        )


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    hunt_id: int = Field(foreign_key="hunt.id")
    ran_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    outcome: RunOutcome
    notes: str = ""
    duration_minutes: int | None = None
    query_snapshot: str | None = None
    search_from: datetime | None = None
    search_to: datetime | None = None
    result_count: int | None = None

    hunt: Hunt = Relationship(back_populates="runs")

    @property
    def outcome_label(self) -> str:
        return self.outcome.value.replace("_", " ").capitalize()

    @property
    def query_snapshot_label(self) -> str:
        return self.query_snapshot if self.query_snapshot else "No query captured"

    @property
    def has_search_window(self) -> bool:
        return self.search_from is not None and self.search_to is not None


class Exclusion(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    hunt_id: int = Field(foreign_key="hunt.id")
    run_id: int | None = Field(default=None, foreign_key="run.id")
    value: str
    reason: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    active: bool = True

    hunt: Hunt = Relationship(back_populates="exclusions")


class CampaignStatus(str, Enum):
    planned = "planned"
    active = "active"
    closed = "closed"


class Campaign(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    objective: str
    scope: str = ""
    notes: str = ""
    conclusion: str = ""
    status: CampaignStatus = CampaignStatus.planned
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    memberships: list["CampaignHunt"] = Relationship(back_populates="campaign")

    @property
    def hunts(self) -> list[Hunt]:
        return [
            link.hunt
            for link in sorted(self.memberships, key=lambda link: link.created_at)
        ]


class CampaignHunt(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("campaign_id", "hunt_id", name="uq_campaign_hunt"),)

    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id")
    hunt_id: int = Field(foreign_key="hunt.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    campaign: Campaign = Relationship(back_populates="memberships")
    hunt: Hunt = Relationship(back_populates="campaign_links")
