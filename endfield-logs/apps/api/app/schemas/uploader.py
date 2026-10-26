from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CheckDuplicateBattleRequest(BaseModel):
    battleFingerprint: str
    bossKey: str
    parserVersion: str
    rulesVersion: str


class CheckDuplicateBattleResponse(BaseModel):
    duplicate: bool
    battleId: str | None = None
    battleUrl: str | None = None


class UploadBattleRosterEntryRequest(BaseModel):
    slot: int
    characterKey: str
    characterName: str
    accountDisplayName: str | None = None
    characterLevel: int | None = None
    characterPotential: int | None = None
    weapon: "UploadBattleRosterWeaponRequest | None" = None
    equips: list["UploadBattleRosterEquipRequest"] = Field(default_factory=list)
    skills: list["UploadBattleRosterSkillRequest"] = Field(default_factory=list)


class UploadBattleRosterSkillRequest(BaseModel):
    skillKey: str
    level: int


class UploadBattleRosterWeaponRequest(BaseModel):
    weaponTemplate: str | None = None
    weaponName: str
    weaponLevel: int | None = None
    weaponRefine: int | None = None
    iconUrl: str | None = None
    skills: list["UploadBattleWeaponSkillRequest"] = Field(default_factory=list)


class UploadBattleWeaponSkillRequest(BaseModel):
    """武器词条技能等级（同一把武器三词条可不同级，v34 客户端起上传）。"""

    skillKey: str
    level: int | None = None
    potentialLevel: int | None = None


class UploadBattleRosterEquipRequest(BaseModel):
    slot: int
    itemId: str | None = None
    pieceName: str
    suitName: str | None = None
    partName: str | None = None
    iconUrl: str | None = None
    enhanceLevels: list[dict[str, Any]] = Field(default_factory=list)
    stats: list[dict[str, Any]] = Field(default_factory=list)


class UploadBattleParticipantRequest(BaseModel):
    characterKey: str
    characterName: str
    accountDisplayName: str | None = None
    totalDamage: int
    dps: float
    rdps: float
    maxHit: int | None = None
    critRate: float | None = None


class UploadTimelineEventRequest(BaseModel):
    class RdpsContribution(BaseModel):
        characterKey: str
        characterName: str
        value: float

    class BuffEffect(BaseModel):
        zone: str | None = None
        element: str | None = None
        rate: float | None = None
        baseRate: float | None = None
        tickRate: float | None = None
        maxRate: float | None = None

    class PoiseDamage(BaseModel):
        type: str | None = None
        value: float | None = None
        current_value: float | None = None
        source: str | None = None
        source_int: int | None = None
        orig_source: str | None = None
        orig_source_int: int | None = None

    tsMsFromStart: int
    laneType: Literal["skill", "buff"]
    sourceCharacterKey: str | None = None
    sourceCharacterName: str | None = None
    targetCharacterKey: str | None = None
    targetCharacterName: str | None = None
    targetPlayerKey: str | None = None
    targetEnemyKey: str | None = None
    eventType: str
    eventKey: str
    eventGroupKey: str | None = None
    eventName: str
    value: int | None = None
    damageElement: str | None = None
    damageSchool: str | None = None
    poiseDamage: PoiseDamage | None = None
    rdpsContributions: list[RdpsContribution] = Field(default_factory=list)
    hitContext: dict[str, Any] | None = None
    durationMs: int | None = None
    actualStartMsFromStart: int | None = None
    actualEndMsFromStart: int | None = None
    actualDurationMs: int | None = None
    effects: list[BuffEffect] = Field(default_factory=list)
    dynamicEffects: list[BuffEffect] = Field(default_factory=list)
    important: bool = False


class UploadRoleSkillStatRequest(BaseModel):
    characterKey: str
    characterName: str
    accountDisplayName: str | None = None
    skillKey: str
    skillName: str
    castCount: int
    totalDamage: int
    avgDamage: float
    maxDamage: int


class UploadContractTagRequest(BaseModel):
    tagId: int
    score: int
    name: str | None = None
    description: str | None = None
    iconId: str | None = None
    iconUrl: str | None = None
    buffId: str | None = None
    groupId: int | None = None
    conflictId: str | None = None
    terms: list[dict[str, Any]] = Field(default_factory=list)
    values: dict[str, Any] = Field(default_factory=dict)


class UploadBattlePayloadRequest(BaseModel):
    dungeonKey: str
    dungeonName: str
    bossKey: str
    bossName: str
    battleStartAt: datetime
    battleEndAt: datetime
    durationMs: int
    clearFlag: bool
    totalDamage: int
    totalDps: float
    roster: list[UploadBattleRosterEntryRequest]
    battleFingerprint: str
    parserVersion: str
    rulesVersion: str
    timeSource: str | None = None
    timelineZeroSource: str | None = None
    timerStartSeen: bool | None = None
    timerEndSeen: bool | None = None
    officialTimerStartSeen: bool | None = None
    officialTimerEndSeen: bool | None = None
    officialTimerObservedElapsedMs: int | None = None
    packetTimerElapsedMs: int | None = None
    timerIntegrityOk: bool | None = None
    timerIntegrityReasons: list[str] = Field(default_factory=list)
    timerEvidence: dict[str, Any] | None = None
    timerStartInferred: bool | None = None
    timerWindowValid: bool | None = None
    rdpsPreflightOk: bool | None = None
    rdpsStrictOk: bool | None = None
    rdpsPreflightBlockerCount: int | None = None
    bossIdentitySource: str | None = None
    dungeonContextId: str | None = None
    dungeonIdentitySource: str | None = None
    loadoutFallbackUsed: bool | None = None
    contractTagScore: int | None = None
    contractTags: list[UploadContractTagRequest] = Field(default_factory=list)


class UploadBattleCastRequest(BaseModel):
    """完整施法序列（parser v33+，排轴导出 API 消费；老版本客户端不带此段）。"""

    tsMsFromStart: int
    endMsFromStart: int | None = None
    characterKey: str
    skillKey: str
    skillName: str | None = None
    skillSource: str | None = None
    # v34+：该施放后短窗内回技力（主控重击）。老客户端缺省 False。
    recoversEnergy: bool = False


class UploadBattleRequest(BaseModel):
    battle: UploadBattlePayloadRequest
    participants: list[UploadBattleParticipantRequest]
    characterStates: list[dict[str, Any]] = Field(default_factory=list)
    timelineEvents: list[UploadTimelineEventRequest] = Field(default_factory=list)
    roleSkillStats: list[UploadRoleSkillStatRequest] = Field(default_factory=list)
    casts: list[UploadBattleCastRequest] = Field(default_factory=list)


class UploadBattleResponse(BaseModel):
    battleId: str
    battleUrl: str
