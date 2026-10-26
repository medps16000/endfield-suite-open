const ATTACK_INDEX_RE = /_attack(\d+)(?:$|_)/i;
const COMBO_SKILL_KEY_RE = /(?:^|_)combo(?:_\d+)?_skill(?:$|_)/i;
const SKILL_KEY_OVERRIDES: Record<string, string> = {
  buff_common_cryst_triggered_physical_break: "寒冷击破触发",
};
const SKILL_NAME_OVERRIDES: Record<string, string> = {
  "cryst triggered physical break": "寒冷击破触发",
};

export type BattleSkillCategory = "normal" | "skill" | "combo" | "heavy" | "ultimate" | "other";

export function getPreferredBattleSkillDisplayName(skillName: string, skillKey?: string | null): string {
  if (skillKey) {
    const skillKeyOverride = SKILL_KEY_OVERRIDES[skillKey.toLowerCase()];
    if (skillKeyOverride) {
      return skillKeyOverride;
    }
  }
  const skillNameOverride = SKILL_NAME_OVERRIDES[skillName.trim().toLowerCase()];
  if (skillNameOverride) {
    return skillNameOverride;
  }
  return skillName;
}

export function getBattleSkillCategory(skillName: string, skillKey?: string | null): BattleSkillCategory {
  const displayName = getPreferredBattleSkillDisplayName(skillName, skillKey).trim();
  if (/^A[1-4]$/i.test(displayName)) {
    return "normal";
  }
  if (displayName === "战技") {
    return "skill";
  }
  if (displayName === "连携技") {
    return "combo";
  }
  if (displayName === "重击" || displayName === "处决") {
    return "heavy";
  }
  if (displayName === "终结技") {
    return "ultimate";
  }
  if (!skillKey) {
    return "other";
  }

  const lowered = skillKey.toLowerCase();
  if (lowered.includes("_ult_attack") || lowered.includes("_ultimate_skill")) {
    return "ultimate";
  }
  if (COMBO_SKILL_KEY_RE.test(lowered)) {
    return "combo";
  }
  if (lowered.includes("_normal_skill")) {
    return "skill";
  }
  if (lowered.includes("execute") || lowered.includes("execution")) {
    return "heavy";
  }
  if (lowered.includes("power_attack") || lowered.includes("plunging_attack") || lowered.includes("heavy_attack")) {
    return "heavy";
  }

  const attackMatch = ATTACK_INDEX_RE.exec(lowered);
  if (attackMatch) {
    const attackIndex = Number.parseInt(attackMatch[1] ?? "", 10);
    if (attackIndex >= 1 && attackIndex <= 4) {
      return "normal";
    }
    return "normal";
  }

  return "other";
}

export function getBattleSkillCategoryLabel(skillName: string, skillKey?: string | null): string {
  const category = getBattleSkillCategory(skillName, skillKey);
  switch (category) {
    case "normal":
      return "普攻";
    case "skill":
      return "战技";
    case "combo":
      return "连携";
    case "heavy":
      return "重击";
    case "ultimate":
      return "终结技";
    default:
      return "其他";
  }
}
