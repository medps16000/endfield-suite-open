from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RotationActionKind = Literal["battle_skill", "combo_skill", "ultimate_skill"]


class RotationStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=80)
    atMs: int = Field(ge=0, le=3_600_000)
    characterId: str = Field(min_length=1, max_length=160)
    skillId: str | None = Field(default=None, min_length=1, max_length=160)
    actionKind: RotationActionKind | None = None
    note: str = Field(default="", max_length=500)
    phase: str = Field(default="", max_length=80)

    @model_validator(mode="before")
    @classmethod
    def exact_skill_or_action(cls, value):
        if isinstance(value, dict):
            present = {field for field in ("skillId", "actionKind") if field in value}
            if len(present) != 1 or value[next(iter(present))] is None:
                raise ValueError("步骤必须且只能选择一个技能或动作。")
        return value

    @field_validator("id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("步骤 ID 不能为空白。")
        return value


class RotationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=80)
    dungeonId: str = Field(min_length=1, max_length=160)
    characterIds: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(min_length=4, max_length=4)
    enabled: bool
    steps: list[RotationStep] = Field(min_length=1, max_length=500)
    displayNotes: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("排轴名称不能为空。")
        return value

    @model_validator(mode="after")
    def ordered_steps(self):
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("排轴步骤 ID 不能重复。")
        if any(left.atMs > right.atMs for left, right in zip(self.steps, self.steps[1:])):
            raise ValueError("步骤必须按游戏计时从早到晚排列。")
        return self


class RotationUpdateRequest(RotationCreateRequest):
    revision: int = Field(ge=1)


class RotationPlan(RotationCreateRequest):
    id: str
    position: int
    revision: int
    updatedAt: str


class RotationEnvelope(BaseModel):
    schemaVersion: Literal[2] = 2
    accountId: str
    revision: str
    rotations: list[RotationPlan]


class RotationOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ids: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(max_length=100)


class RotationDeleteResponse(BaseModel):
    ok: Literal[True] = True


class RotationMap(BaseModel):
    id: str
    name: str
    seriesName: str


class RotationSkill(BaseModel):
    id: str
    name: str
    groupId: str


class RotationAction(BaseModel):
    kind: RotationActionKind
    name: str
    skillName: str
    iconUrl: str = ""
    rootSkillIds: list[str]


class RotationCharacter(BaseModel):
    id: str
    name: str
    avatarUrl: str
    skills: list[RotationSkill]
    actions: list[RotationAction] = Field(default_factory=list)


class RotationCatalog(BaseModel):
    schemaVersion: Literal[2] = 2
    maps: list[RotationMap]
    characters: list[RotationCharacter]
