"""Pydantic models for request bodies and the skill content schema."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SHA_RE = r"^[0-9a-f]{64}$"


class _Loose(BaseModel):
    """Unknown fields are ignored (forward compatible clients / LLM extras)."""

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------- skill content
class Target(_Loose):
    role: str | None = None
    label: str | None = None
    identifier: str | None = None
    path: list[str] = Field(default_factory=list)
    window_title: str | None = None


class ElementPresent(_Loose):
    role: str | None = None
    label: str | None = None


class Expect(_Loose):
    window_title_contains: str | None = None
    element_present: ElementPresent | None = None


ActionType = Literal["open_app", "open_url", "click", "type", "key", "menu", "wait"]


class Action(_Loose):
    type: ActionType
    target: Target | None = None
    text: str | None = None
    key: str | None = None
    url: str | None = None


FilterDecision = Literal["keep", "review", "drop"]


class StepFilter(_Loose):
    """Why the relevance filter thinks a step may not be needed (shown to reviewers)."""

    decision: FilterDecision = "keep"
    reason: str = Field(default="on_task", max_length=200)
    p_drop: float = Field(default=0.0, ge=0, le=1)
    source: str = Field(default="local", max_length=40)


class Step(_Loose):
    index: int = 0
    title: str = Field(min_length=1, max_length=300)
    instruction: str = Field(default="", max_length=4000)
    app: str | None = None
    action: Action
    expect: Expect | None = None
    screenshot_sha256: str | None = Field(default=None, pattern=_SHA_RE)
    irreversible: bool = False
    # Relevance filtering. Excluded steps stay in the draft (greyed out, restorable) and are
    # removed when the skill is published. `source_seqs` ties a step back to recorded events.
    excluded: bool = False
    filter: StepFilter | None = None
    source_seqs: list[int] = Field(default_factory=list, max_length=200)


class SkillInput(_Loose):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    example: str | None = None


class SkillContent(_Loose):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=2000)
    apps: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    inputs: list[SkillInput] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list, max_length=500)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _renumber(self) -> "SkillContent":
        for i, s in enumerate(self.steps, start=1):
            s.index = i
        self.tags = list(dict.fromkeys(t.strip().lower() for t in self.tags if t and t.strip()))[:20]
        self.apps = list(dict.fromkeys(a.strip() for a in self.apps if a and a.strip()))
        return self

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------- recordings
class EventApp(_Loose):
    bundle_id: str | None = None
    name: str | None = None


class EventWindow(_Loose):
    title: str | None = None


class EventElement(_Loose):
    role: str | None = None
    subrole: str | None = None
    label: str | None = None
    identifier: str | None = None
    path: list[str] = Field(default_factory=list)
    value_kind: Literal["text", "secure", "none"] | None = None


EventType = Literal["app_activate", "click", "type", "key", "menu", "window_open", "url_change", "scroll"]


class Event(_Loose):
    seq: int
    ts: datetime
    type: EventType
    app: EventApp | None = None
    window: EventWindow | None = None
    element: EventElement | None = None
    text: str | None = None
    key: str | None = None
    url: str | None = None
    screenshot_sha256: str | None = Field(default=None, pattern=_SHA_RE)


class ClientInfo(_Loose):
    app_version: str | None = None
    os_version: str | None = None
    device_id: str | None = None


class RecordingCreate(_Loose):
    title_hint: str | None = Field(default=None, max_length=300)
    intent: str | None = Field(default=None, max_length=1000)  # "What did you just do?" answer
    started_at: datetime
    ended_at: datetime
    client: ClientInfo = Field(default_factory=ClientInfo)
    events: list[Event]


# ---------------------------------------------------------------- misc bodies
class DevLogin(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    name: str = Field(min_length=1, max_length=200)
    team: str | None = Field(default=None, max_length=100)


class PresignRequest(BaseModel):
    sha256: str = Field(pattern=_SHA_RE)
    content_type: Literal["image/jpeg", "image/png"]
    bytes: int = Field(gt=0)


Visibility = Literal["private", "team", "org"]


class SkillCreate(BaseModel):
    content: SkillContent
    team_id: str | None = None
    visibility: Visibility = "private"


class SkillPatch(BaseModel):
    content: SkillContent | None = None
    team_id: str | None = None
    visibility: Visibility | None = None


class SuggestFix(BaseModel):
    step_index: int = Field(ge=1)
    new_target: Target
    note: str | None = Field(default=None, max_length=2000)


class RunCreate(BaseModel):
    skill_id: str
    version: int = Field(ge=1)
    mode: Literal["guided", "auto"]
    inputs: dict[str, Any] = Field(default_factory=dict)


class RunStepCreate(BaseModel):
    step_index: int = Field(ge=1)
    status: Literal["ok", "repaired", "failed", "skipped", "confirmed"]
    strategy: Literal["deterministic", "llm_repair", "vision", "human"]
    duration_ms: int = Field(default=0, ge=0)
    detail: Any = None


class RunFinish(BaseModel):
    status: Literal["succeeded", "failed", "aborted"]
    error: str | None = Field(default=None, max_length=4000)


class UiNode(_Loose):
    role: str | None = None
    label: str | None = None
    identifier: str | None = None
    path: list[str] = Field(default_factory=list)


class RepairRequest(BaseModel):
    skill_id: str
    version: int = Field(ge=1)
    step_index: int = Field(ge=1)
    ui_tree: list[UiNode] = Field(max_length=300)


class FlagsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recording_enabled: bool | None = None
    replay_enabled: bool | None = None
    llm_enabled: bool | None = None
    max_recording_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    screenshot_policy: Literal["key_moments", "none"] | None = None
    filter_enabled: bool | None = None
    filter_drop_threshold: float | None = Field(default=None, gt=0, le=1)
    filter_review_threshold: float | None = Field(default=None, gt=0, le=1)
    split_tasks_enabled: bool | None = None
