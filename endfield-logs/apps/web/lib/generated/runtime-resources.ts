/**
 * Typed public-source defaults for optional, externally supplied game resources.
 *
 * The repository intentionally does not redistribute extracted game tables,
 * artwork or third-party bundles. An authorized deployment may replace this
 * module in its private build with its locally generated resource manifests.
 * Empty collections mean resources are unavailable; they are not game data.
 */
export type RuntimeCharacter = {
  id: string;
  name: string;
  engName: string;
  professionName: string;
  charTypeName: string;
  rarity: number;
};

export type RuntimeIcon = {
  url: string;
  width: number;
  height: number;
  viewBox: string;
  color?: string;
};

export const officialCharacters: { entries: RuntimeCharacter[] } = { entries: [] };
export const characterAvatars: { avatars: Record<string, { url: string }> } = { avatars: {} };
export const officialIcons: { icons: Record<string, RuntimeIcon> } = { icons: {} };
export const characterArt: { entries: { id: string; fullImage: string }[] } = { entries: [] };
export const siteBackgrounds: { images: string[] } = { images: [] };
