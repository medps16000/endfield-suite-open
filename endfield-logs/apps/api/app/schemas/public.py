from typing import Any, Literal

from pydantic import BaseModel, Field


class ContractTagResponse(BaseModel):
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


class HotBossRun(BaseModel):
    battleId: str
    durationMs: int
    uploaderNickname: str
    characterKey: str | None = None
    characterName: str
    characterProfession: str | None = None
    characterAvatarUrl: str | None = None
    characterPotential: int | None = None
    weaponRefine: int | None = None
    scorePercent: int | None = None
    contractTagScore: int | None = None
    contractTags: list[ContractTagResponse] = Field(default_factory=list)


class HotBossCard(BaseModel):
    bossSlug: str
    bossKey: str
    bossName: str
    dungeonName: str
    topSpeedRuns: list[HotBossRun]


class BossRankingRow(BaseModel):
    rank: int
    scorePercent: int
    battleId: str
    battleEndAt: str
    characterKey: str | None = None
    characterName: str
    characterProfession: str
    characterAvatarUrl: str | None = None
    characterPotential: int | None = None
    weaponRefine: int | None = None
    accountId: str
    accountDisplayName: str
    dps: float
    rdps: float
    durationMs: int
    rosterSummary: list[str]
    rosterEntries: list["BossRankingRosterEntry"]
    contractTagScore: int | None = None
    contractTags: list[ContractTagResponse] = Field(default_factory=list)


class BossRankingRosterEntry(BaseModel):
    characterKey: str | None = None
    characterName: str
    profession: str
    avatarUrl: str | None = None
    characterPotential: int | None = None
    weaponRefine: int | None = None


class BossProfessionUsageEntry(BaseModel):
    characterKey: str | None = None
    characterName: str
    avatarUrl: str | None = None
    usagePercent: float


class BossProfessionGroup(BaseModel):
    profession: str
    entries: list[BossProfessionUsageEntry]


class BossRankingResponse(BaseModel):
    bossSlug: str
    bossName: str
    dungeonName: str
    metric: Literal["dps", "rdps"]
    professionGroups: list[BossProfessionGroup]
    rows: list[BossRankingRow]


class BossCharacterStatisticsOutlier(BaseModel):
    value: float
    count: int


class BossCharacterStatisticsRow(BaseModel):
    rank: int | None = None
    characterKey: str
    characterName: str
    characterProfession: str
    characterAvatarUrl: str | None = None
    sampleCount: int
    normalSampleCount: int
    outlierCount: int
    insufficientSamples: bool
    lowerWhisker: float | None = None
    p10: float | None = None
    p25: float | None = None
    median: float | None = None
    p75: float | None = None
    p90: float | None = None
    upperWhisker: float | None = None
    maximum: float | None = None
    outliers: list[BossCharacterStatisticsOutlier] = Field(default_factory=list)


class BossCharacterStatisticsResponse(BaseModel):
    scope: Literal["boss", "all"]
    bossSlug: str
    bossName: str
    dungeonName: str
    metric: Literal["dps", "rdps"]
    range: Literal["7d", "14d", "30d", "all"]
    potential: Literal["0", "1-5", "all"]
    includedBossCount: int
    minimumSampleCount: int
    eligibleBattleCount: int
    totalSampleCount: int
    totalOutlierCount: int
    rows: list[BossCharacterStatisticsRow]


class CharacterBossStatisticsRow(BaseModel):
    bossSlug: str
    bossName: str
    dungeonName: str
    rank: int | None = None
    rankedCharacterCount: int
    sampleCount: int
    normalSampleCount: int
    outlierCount: int
    insufficientSamples: bool
    lowerWhisker: float | None = None
    p10: float | None = None
    p25: float | None = None
    median: float | None = None
    p75: float | None = None
    p90: float | None = None
    upperWhisker: float | None = None
    maximum: float | None = None
    outliers: list[BossCharacterStatisticsOutlier] = Field(default_factory=list)


class CharacterBossStatisticsResponse(BaseModel):
    characterKey: str
    characterName: str
    characterProfession: str
    characterAvatarUrl: str | None = None
    metric: Literal["dps", "rdps"]
    range: Literal["7d", "14d", "30d", "all"]
    potential: Literal["0", "1-5", "all"]
    minimumSampleCount: int
    includedBossCount: int
    totalSampleCount: int
    totalOutlierCount: int
    rows: list[CharacterBossStatisticsRow]


class BattleRosterEntryResponse(BaseModel):
    slot: int
    characterKey: str | None = None
    characterName: str
    characterProfession: str | None = None
    characterAvatarUrl: str | None = None
    characterElement: str | None = None
    accountDisplayName: str
    characterLevel: int | None = None
    characterPotential: int | None = None
    weapon: "BattleRosterWeaponResponse | None" = None
    equips: list["BattleRosterEquipResponse"] = Field(default_factory=list)
    skills: list["BattleRosterSkillResponse"] = Field(default_factory=list)


class BattleRosterSkillResponse(BaseModel):
    skillKey: str
    level: int


class BattleRosterWeaponResponse(BaseModel):
    weaponTemplate: str | None = None
    weaponName: str
    weaponLevel: int | None = None
    weaponRefine: int | None = None
    iconUrl: str | None = None
    skills: list["BattleRosterWeaponSkillResponse"] = Field(default_factory=list)


class BattleRosterWeaponSkillResponse(BaseModel):
    skillKey: str
    level: int | None = None
    potentialLevel: int | None = None


class BattleRosterEquipResponse(BaseModel):
    slot: int
    itemId: str | None = None
    pieceName: str
    suitName: str | None = None
    partName: str | None = None
    iconUrl: str | None = None
    enhanceLevels: list[dict[str, Any]] = Field(default_factory=list)
    stats: list[dict[str, Any]] = Field(default_factory=list)


class BattleParticipantResponse(BaseModel):
    characterKey: str | None = None
    characterName: str
    characterProfession: str | None = None
    characterAvatarUrl: str | None = None
    characterElement: str | None = None
    accountDisplayName: str
    totalDamage: int
    dps: float
    rdps: float
    maxHit: int | None
    critRate: float | None


class TimelineEventResponse(BaseModel):
    class RdpsContributionResponse(BaseModel):
        characterKey: str
        characterName: str
        value: float

    class BuffEffectResponse(BaseModel):
        zone: str | None = None
        element: str | None = None
        rate: float | None = None
        baseRate: float | None = None
        tickRate: float | None = None
        maxRate: float | None = None

    class PoiseDamageResponse(BaseModel):
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
    sourceCharacterName: str | None
    targetCharacterKey: str | None = None
    targetCharacterName: str | None
    targetPlayerKey: str | None = None
    targetEnemyKey: str | None = None
    eventKey: str | None = None
    eventGroupKey: str | None = None
    eventName: str
    value: int | None = None
    damageElement: str | None = None
    damageSchool: str | None = None
    poiseDamage: PoiseDamageResponse | None = None
    rdpsContributions: list[RdpsContributionResponse] = Field(default_factory=list)
    hitContext: dict[str, Any] | None = None
    durationMs: int | None = None
    actualStartMsFromStart: int | None = None
    actualEndMsFromStart: int | None = None
    actualDurationMs: int | None = None
    effects: list[BuffEffectResponse] = Field(default_factory=list)
    dynamicEffects: list[BuffEffectResponse] = Field(default_factory=list)
    important: bool


class RoleSkillStatResponse(BaseModel):
    characterName: str
    skillKey: str | None = None
    skillName: str
    displayGroupKey: str | None = None
    displayGroupName: str | None = None
    hitCount: int | None = None
    maxHit: int | None = None
    castCount: int
    totalDamage: int
    avgDamage: float
    maxDamage: int


class BattleResponse(BaseModel):
    id: str
    uploaderUserId: str
    visibility: Literal["public"]
    status: Literal["valid", "deleted"]
    dungeonName: str
    bossKey: str | None = None
    bossName: str
    battleStartAt: str
    battleEndAt: str
    durationMs: int
    clearFlag: bool
    totalDamage: int
    totalDps: float
    roster: list[BattleRosterEntryResponse]
    parserVersion: str
    rulesVersion: str
    battleFingerprint: str
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
    timerStartInferred: bool | None = None
    timerWindowValid: bool | None = None
    rdpsPreflightOk: bool | None = None
    rdpsStrictOk: bool | None = None
    # Algorithm eligibility only; clear/timer/visibility and other ranking gates remain separate.
    rdpsRankingEligible: bool = False
    rdpsPreflightBlockerCount: int | None = None
    bossIdentitySource: str | None = None
    dungeonContextId: str | None = None
    dungeonIdentitySource: str | None = None
    loadoutFallbackUsed: bool | None = None
    contractTagScore: int | None = None
    contractTags: list[ContractTagResponse] = Field(default_factory=list)


class ViewerCapabilitiesResponse(BaseModel):
    isUploader: bool
    isAdmin: bool
    canDelete: bool
    canViewVersions: bool


class BattleIntegrityResponse(BaseModel):
    version: str
    canonicalSha256: str
    sealAlgorithm: str | None = None
    serverSeal: str | None = None
    verified: bool


class BattleDetailResponse(BaseModel):
    battle: BattleResponse
    participants: list[BattleParticipantResponse]
    characterStates: list[dict[str, Any]] = Field(default_factory=list)
    timelineEvents: list[TimelineEventResponse]
    roleSkillStats: list[RoleSkillStatResponse]
    integrity: BattleIntegrityResponse
    viewerCapabilities: ViewerCapabilitiesResponse


class ShareSummaryResponse(BaseModel):
    battleId: str
    bossName: str
    dungeonName: str
    durationMs: int
    uploaderNickname: str
    rosterSummary: list[str]
    contractTagScore: int | None = None
    contractTags: list[ContractTagResponse] = Field(default_factory=list)


class UserBattleSummaryResponse(BaseModel):
    id: str
    bossName: str
    dungeonName: str
    battleEndAt: str
    createdAt: str
    durationMs: int
    totalDamage: int
    totalDps: float
    status: Literal["valid", "deleted"]
    parserVersion: str
    rulesVersion: str
    rosterSummary: list[str]
    contractTagScore: int | None = None
    contractTags: list[ContractTagResponse] = Field(default_factory=list)


class UserBossRankingResponse(BaseModel):
    bossSlug: str
    bossName: str
    dungeonName: str
    battleId: str
    rank: int
    scorePercent: int
    durationMs: int
    totalDps: float
    battleEndAt: str
    rosterSummary: list[str]
    rosterEntries: list[BossRankingRosterEntry] = Field(default_factory=list)
    contractTagScore: int | None = None
    contractTags: list[ContractTagResponse] = Field(default_factory=list)


class UserBattlesResponse(BaseModel):
    battles: list[UserBattleSummaryResponse]
    rankings: list[UserBossRankingResponse] = Field(default_factory=list)


class PublicUserRankingsResponse(BaseModel):
    accountId: str
    accountDisplayName: str
    rankings: list[UserBossRankingResponse] = Field(default_factory=list)


class PublicAccountSearchEntryResponse(BaseModel):
    accountId: str
    accountDisplayName: str


class PublicAccountSearchResponse(BaseModel):
    query: str
    hasMore: bool
    accounts: list[PublicAccountSearchEntryResponse] = Field(default_factory=list)


class DeleteBattleResponse(BaseModel):
    ok: bool
