export type ContractTag = {
  tagId: number;
  score: number;
  name?: string | null;
  description?: string | null;
  iconId?: string | null;
  iconUrl?: string | null;
  buffId?: string | null;
  groupId?: number | null;
  conflictId?: string | null;
  terms?: Record<string, unknown>[];
  values?: Record<string, unknown>;
};

export type HotBossRun = {
  battleId: string;
  durationMs: number;
  uploaderNickname: string;
  characterKey: string | null;
  characterName: string;
  characterProfession: string | null;
  characterAvatarUrl: string | null;
  characterPotential?: number | null;
  weaponRefine?: number | null;
  scorePercent?: number | null;
  contractTagScore?: number | null;
  contractTags?: ContractTag[];
};

export type HotBossCard = {
  bossSlug: string;
  bossKey: string;
  bossName: string;
  dungeonName: string;
  topSpeedRuns: HotBossRun[];
};

export type BossRankingRow = {
  rank: number;
  scorePercent: number;
  battleId: string;
  battleEndAt: string;
  characterKey: string | null;
  characterName: string;
  characterProfession: string;
  characterAvatarUrl: string | null;
  characterPotential?: number | null;
  weaponRefine?: number | null;
  accountId: string;
  accountDisplayName: string;
  dps: number;
  rdps: number;
  durationMs: number;
  rosterSummary: string[];
  rosterEntries: {
    characterKey: string | null;
    characterName: string;
    profession: string;
    avatarUrl: string | null;
    characterPotential?: number | null;
    weaponRefine?: number | null;
  }[];
  contractTagScore?: number | null;
  contractTags?: ContractTag[];
};

export type BossRankingResponse = {
  bossSlug: string;
  bossName: string;
  dungeonName: string;
  metric: "dps" | "rdps";
  professionGroups: {
    profession: string;
    entries: {
      characterKey: string | null;
      characterName: string;
      avatarUrl: string | null;
      usagePercent: number;
    }[];
  }[];
  rows: BossRankingRow[];
};

export type BossCharacterStatisticsResponse = {
  scope: "boss" | "all";
  bossSlug: string;
  bossName: string;
  dungeonName: string;
  metric: "dps" | "rdps";
  range: "7d" | "14d" | "30d" | "all";
  potential: "0" | "1-5" | "all";
  includedBossCount: number;
  minimumSampleCount: number;
  eligibleBattleCount: number;
  totalSampleCount: number;
  totalOutlierCount: number;
  rows: {
    rank: number | null;
    characterKey: string;
    characterName: string;
    characterProfession: string;
    characterAvatarUrl: string | null;
    sampleCount: number;
    normalSampleCount: number;
    outlierCount: number;
    insufficientSamples: boolean;
    lowerWhisker: number | null;
    p10: number | null;
    p25: number | null;
    median: number | null;
    p75: number | null;
    p90: number | null;
    upperWhisker: number | null;
    maximum: number | null;
    outliers: {
      value: number;
      count: number;
    }[];
  }[];
};

export type BattleRosterEntry = {
  slot: number;
  characterKey: string | null;
  characterName: string;
  characterProfession: string | null;
  characterAvatarUrl: string | null;
  characterElement?: string | null;
  accountDisplayName: string;
  characterLevel: number | null;
  characterPotential: number | null;
  weapon: {
    weaponTemplate: string | null;
    weaponName: string;
    weaponLevel: number | null;
    weaponRefine: number | null;
    iconUrl: string | null;
  } | null;
  equips: {
    slot: number;
    itemId: string | null;
    pieceName: string;
    suitName: string | null;
    partName: string | null;
    iconUrl: string | null;
  }[];
};

export type BattleParticipant = {
  characterKey: string | null;
  characterName: string;
  characterProfession: string | null;
  characterAvatarUrl: string | null;
  characterElement?: string | null;
  accountDisplayName: string;
  totalDamage: number;
  dps: number;
  rdps: number;
  maxHit: number | null;
  critRate: number | null;
};

export type TimelineEvent = {
  tsMsFromStart: number;
  laneType: "skill" | "buff";
  sourceCharacterKey?: string | null;
  sourceCharacterName: string | null;
  targetCharacterKey?: string | null;
  targetCharacterName: string | null;
  targetPlayerKey?: string | null;
  targetEnemyKey?: string | null;
  eventKey?: string | null;
  eventGroupKey?: string | null;
  eventName: string;
  value: number | null;
  damageElement?: string | null;
  damageSchool?: string | null;
  poiseDamage?: {
    type?: string | null;
    value?: number | null;
    current_value?: number | null;
    source?: string | null;
    source_int?: number | null;
    orig_source?: string | null;
    orig_source_int?: number | null;
  } | null;
  rdpsContributions?: {
    characterKey: string;
    characterName: string;
    value: number;
  }[];
  hitContext?: Record<string, unknown> | null;
  durationMs: number | null;
  actualStartMsFromStart?: number | null;
  actualEndMsFromStart?: number | null;
  actualDurationMs?: number | null;
  effects?: {
    zone?: string | null;
    element?: string | null;
    rate?: number | null;
    baseRate?: number | null;
    tickRate?: number | null;
    maxRate?: number | null;
  }[];
  dynamicEffects?: {
    zone?: string | null;
    element?: string | null;
    rate?: number | null;
    baseRate?: number | null;
    tickRate?: number | null;
    maxRate?: number | null;
  }[];
  important: boolean;
};

export type RoleSkillStat = {
  characterName: string;
  skillKey?: string | null;
  skillName: string;
  displayGroupKey?: string | null;
  displayGroupName?: string | null;
  hitCount?: number | null;
  maxHit?: number | null;
  castCount: number;
  totalDamage: number;
  avgDamage: number;
  maxDamage: number;
};

export type BattleDetailResponse = {
  battle: {
    id: string;
    uploaderUserId: string;
    visibility: "public";
    status: "valid" | "deleted";
    dungeonName: string;
    bossKey?: string | null;
    bossName: string;
    battleStartAt: string;
    battleEndAt: string;
    durationMs: number;
    clearFlag: boolean;
    totalDamage: number;
    totalDps: number;
    roster: BattleRosterEntry[];
    parserVersion: string;
    rulesVersion: string;
    battleFingerprint: string;
    timeSource: string | null;
    timelineZeroSource: string | null;
    timerStartSeen: boolean | null;
    timerEndSeen: boolean | null;
    officialTimerStartSeen: boolean | null;
    officialTimerEndSeen: boolean | null;
    officialTimerObservedElapsedMs: number | null;
    packetTimerElapsedMs: number | null;
    timerIntegrityOk: boolean | null;
    timerIntegrityReasons: string[];
    timerStartInferred: boolean | null;
    timerWindowValid: boolean | null;
    rdpsPreflightOk: boolean | null;
    rdpsStrictOk: boolean | null;
    rdpsRankingEligible: boolean;
    rdpsPreflightBlockerCount: number | null;
    bossIdentitySource: string | null;
    dungeonContextId: string | null;
    dungeonIdentitySource: string | null;
    loadoutFallbackUsed: boolean | null;
    contractTagScore?: number | null;
    contractTags?: ContractTag[];
  };
  participants: BattleParticipant[];
  characterStates: Record<string, unknown>[];
  timelineEvents: TimelineEvent[];
  roleSkillStats: RoleSkillStat[];
  integrity: {
    version: string;
    canonicalSha256: string;
    sealAlgorithm: string | null;
    serverSeal: string | null;
    verified: boolean;
  };
  viewerCapabilities: {
    isUploader: boolean;
    isAdmin: boolean;
    canDelete: boolean;
    canViewVersions: boolean;
  };
};

export type ShareSummaryResponse = {
  battleId: string;
  bossName: string;
  dungeonName: string;
  durationMs: number;
  uploaderNickname: string;
  rosterSummary: string[];
  contractTagScore?: number | null;
  contractTags?: ContractTag[];
};

export type UserBattlesResponse = {
  battles: {
    id: string;
    bossName: string;
    dungeonName: string;
    battleEndAt: string;
    createdAt: string;
    durationMs: number;
    totalDamage: number;
    totalDps: number;
    status: "valid" | "deleted";
    parserVersion: string;
    rulesVersion: string;
    rosterSummary: string[];
    contractTagScore?: number | null;
    contractTags?: ContractTag[];
  }[];
  rankings: {
    bossSlug: string;
    bossName: string;
    dungeonName: string;
    battleId: string;
    rank: number;
    scorePercent: number;
    durationMs: number;
    totalDps: number;
    battleEndAt: string;
    rosterSummary: string[];
    rosterEntries?: BossRankingRow["rosterEntries"];
    contractTagScore?: number | null;
    contractTags?: ContractTag[];
  }[];
};

export type PublicUserRankingsResponse = {
  accountId: string;
  accountDisplayName: string;
  rankings: UserBattlesResponse["rankings"];
};

export type AuthUser = {
  id: string;
  email: string;
  nickname: string;
  isAdmin: boolean;
};

export type AuthMeResponse = {
  authenticated: boolean;
  user: AuthUser | null;
};

export type AdminDashboardResponse = {
  overview: {
    totalUsers: number;
    adminUsers: number;
    totalBattles: number;
    validBattles: number;
    deletedBattles: number;
  };
  users: {
    id: string;
    email: string | null;
    nickname: string;
    isAdmin: boolean;
    isDisabled: boolean;
    createdAt: string | null;
    totalBattles: number;
    validBattles: number;
    deletedBattles: number;
    lastBattleAt: string | null;
  }[];
  battles: {
    id: string;
    uploaderUserId: string;
    uploaderEmail: string | null;
    uploaderNickname: string;
    bossName: string;
    dungeonName: string;
    battleEndAt: string;
    createdAt: string;
    durationMs: number;
    totalDamage: number;
    totalDps: number;
    status: string;
    visibility: string;
    parserVersion: string;
    rulesVersion: string;
    rosterSummary: string[];
  }[];
};
