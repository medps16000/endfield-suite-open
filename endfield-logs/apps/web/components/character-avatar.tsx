"use client";

import { useState } from "react";

type CharacterAvatarProps = {
  name: string;
  avatarUrl?: string | null;
  size?: "sm" | "md" | "lg";
  progression?: { potential?: number | null; refinement?: number | null };
};

export function CharacterAvatar({ name, avatarUrl, size = "md", progression }: CharacterAvatarProps) {
  const [failed, setFailed] = useState(false);
  const initial = name.slice(0, 1);
  const showImage = Boolean(avatarUrl) && !failed;

  const avatar = (
    <span className={`character-avatar character-avatar-${size}`} title={name}>
      {showImage ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          alt={name}
          className="character-avatar-image"
          decoding="async"
          loading="lazy"
          onError={() => setFailed(true)}
          src={avatarUrl ?? undefined}
        />
      ) : (
        <span className="character-avatar-fallback">{initial}</span>
      )}
    </span>
  );
  if (!progression) {
    return avatar;
  }
  const potential = Number.isInteger(progression.potential) && progression.potential! >= 0 && progression.potential! <= 5
    ? progression.potential
    : null;
  const refinement = Number.isInteger(progression.refinement) && progression.refinement! >= 1 && progression.refinement! <= 6
    ? progression.refinement
    : null;
  const description = `潜能 ${potential ?? "未知"}，武器精炼 ${refinement ?? "未知"}`;
  return (
    <span className={`character-avatar-with-progression character-avatar-with-progression-${size}`}>
      {avatar}
      <span className="character-progression" role="img" aria-label={description} title={description}>
        <span className="character-progression-potential" data-unknown={potential === null}>{potential ?? "—"}</span>
        <span className="character-progression-separator">+</span>
        <span className="character-progression-refinement" data-unknown={refinement === null}>{refinement ?? "—"}</span>
      </span>
    </span>
  );
}
