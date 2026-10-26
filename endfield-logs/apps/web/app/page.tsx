import type { Metadata } from "next";
import { officialCharacters, characterAvatars } from "../lib/generated/runtime-resources";
import { CharacterDirectory, type DirectoryCharacter } from "../components/character-directory";

export const metadata: Metadata = {
  title: "角色档案 · 终末地战斗日志",
  description: "查找角色，探索配装与竞速表现。",
};

export default function HomePage() {
  const avatars: Record<string, { url: string }> = characterAvatars.avatars;
  const characters: DirectoryCharacter[] = Object.entries(avatars).map(([id, avatar]) => {
    // The directory presents the two administrator variants as one character.
    const sourceId = id === "chr_9000_endmin" ? "chr_0002_endminm" : id;
    const entry = officialCharacters.entries.find((character) => character.id === sourceId);
    if (!entry || !entry.professionName || !entry.charTypeName) throw new Error(`头像缺少对应的官方角色资料：${id}`);
    return { id, name: entry.name, englishName: entry.engName, profession: entry.professionName,
      element: entry.charTypeName, rarity: entry.rarity, avatarImage: avatar.url };
  });
  return <CharacterDirectory characters={characters} />;
}
