export type ProfileCount = { key: string; name: string; count: number; percent: number; iconUrl: string | null };
export type ProfileRanking = {
  rank: number; battleId: string; accountId: string; accountDisplayName: string;
  durationMs: number; battleEndAt: string; potential: number | null; refinement: number | null;
};
export type ProfileBoss = {
  bossSlug: string; bossName: string; dungeonName: string; sampleCount: number; rows: ProfileRanking[];
  characterRank: number | null; rankedCharacterCount: number;
  bestDurationMs?: number | null;
  characterRows: { rank: number; characterKey: string; characterName: string; sampleCount: number;
    bestDurationMs: number; battleId: string; accountDisplayName: string }[];
};
export type CharacterProfile = {
  characterKey: string; range: "7d" | "14d" | "30d" | "all"; bossSlug: string | null;
  generatedAt: string; latestBattleAt: string | null; sampleCount: number; accountCount: number; bossCount: number;
  potentials: ProfileCount[]; refinements: ProfileCount[]; combinations: ProfileCount[];
  teammates: ProfileCount[]; weapons: ProfileCount[]; equipment: ProfileCount[];
  equipmentUnknownSamples: number; bosses: ProfileBoss[]; bossOptions: ProfileBoss[];
};
