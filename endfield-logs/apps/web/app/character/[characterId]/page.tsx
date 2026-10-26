import { notFound } from "next/navigation";
import { characterArt, characterAvatars, officialCharacters } from "../../../lib/generated/runtime-resources";
import { CharacterProfileView } from "../../../components/character-profile";
import { fetchPublicApiServer } from "../../../lib/api/public-server";
import type { CharacterProfile } from "../../../lib/api/character-profile";

type Props = { params: Promise<{ characterId: string }>; searchParams: Promise<{ range?: string; boss?: string }> };
export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: Props) {
  const { characterId } = await params;
  const sourceId = characterId === "chr_9000_endmin" ? "chr_0002_endminm" : characterId;
  const character = officialCharacters.entries.find((entry) => entry.id === sourceId);
  return { title: `${character?.name ?? "角色"} · 配装与竞速统计` };
}

export default async function CharacterPage({ params, searchParams }: Props) {
  const { characterId } = await params;
  const sourceId = characterId === "chr_9000_endmin" ? "chr_0002_endminm" : characterId;
  const selected = officialCharacters.entries.find((entry) => entry.id === sourceId);
  const avatars: Record<string, { url: string }> = characterAvatars.avatars;
  if (!selected || !avatars[characterId] || !selected.professionName || !selected.charTypeName) notFound();
  const art = characterArt.entries.find((entry) => entry.id === characterId);
  const character = { id: characterId, name: selected.name, englishName: selected.engName,
    profession: selected.professionName, element: selected.charTypeName, rarity: selected.rarity,
    avatarImage: avatars[characterId].url, fullImage: art?.fullImage };
  const query = await searchParams;
  const range = ["7d", "14d", "30d"].includes(query.range ?? "") ? query.range! : "all";
  const filters = new URLSearchParams({ range, details: "false" });
  if (query.boss) filters.set("boss", query.boss);
  let profile: CharacterProfile | null = null;
  let error: string | undefined;
  try {
    profile = await fetchPublicApiServer<CharacterProfile>(`/api/characters/${characterId}/profile?${filters}`, { cache: "no-store" },
      process.env.CHARACTER_PROFILE_API_BASE_URL_INTERNAL?.trim().replace(/\/+$/, "") || undefined);
  } catch {
    error = "统计服务暂时不可用，请稍后重试。";
  }
  return <CharacterProfileView character={character} profile={profile} error={error} />;
}
