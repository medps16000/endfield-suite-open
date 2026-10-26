from typing import Literal

from pydantic import BaseModel, Field


class ProfileCount(BaseModel):
    key: str
    name: str
    count: int
    percent: float
    iconUrl: str | None = None


class ProfileRanking(BaseModel):
    rank: int
    battleId: str
    accountId: str
    accountDisplayName: str
    durationMs: int
    battleEndAt: str
    potential: int | None
    refinement: int | None


class ProfileCharacterRanking(BaseModel):
    rank: int
    characterKey: str
    characterName: str
    sampleCount: int
    bestDurationMs: int
    battleId: str
    accountDisplayName: str


class ProfileBoss(BaseModel):
    bossSlug: str
    bossName: str
    dungeonName: str
    sampleCount: int
    characterRank: int | None = None
    bestDurationMs: int | None = None
    rankedCharacterCount: int = 0
    characterRows: list[ProfileCharacterRanking] = Field(default_factory=list)
    rows: list[ProfileRanking] = Field(default_factory=list)


class CharacterProfileResponse(BaseModel):
    characterKey: str
    range: Literal["7d", "14d", "30d", "all"]
    bossSlug: str | None
    generatedAt: str
    latestBattleAt: str | None
    sampleCount: int
    accountCount: int
    bossCount: int
    potentials: list[ProfileCount]
    refinements: list[ProfileCount]
    combinations: list[ProfileCount]
    teammates: list[ProfileCount]
    weapons: list[ProfileCount]
    equipment: list[ProfileCount]
    equipmentUnknownSamples: int
    bosses: list[ProfileBoss]
    bossOptions: list[ProfileBoss]
