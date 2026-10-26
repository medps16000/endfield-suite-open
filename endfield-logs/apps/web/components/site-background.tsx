"use client";

import { useEffect, useRef, useState } from "react";
import { siteBackgrounds as backgrounds } from "../lib/generated/runtime-resources";

export function SiteBackground() {
  const [source, setSource] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    if (!backgrounds.images.length) return;
    let previous: string | null = null;
    try { previous = sessionStorage.getItem("endfield-site-background"); } catch { /* Storage is optional. */ }
    const alternatives = backgrounds.images.filter((image) => image !== previous);
    const choices = alternatives.length ? alternatives : backgrounds.images;
    const image = choices[Math.floor(Math.random() * choices.length)];
    setSource(image);
    try { sessionStorage.setItem("endfield-site-background", image); } catch { /* Keep the selected scene without storage. */ }
  }, []);

  return <div className="site-background" aria-hidden="true" data-loaded={loaded}>
    {source && (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={source} alt="" onLoad={() => setLoaded(true)} />
    )}
  </div>;
}
