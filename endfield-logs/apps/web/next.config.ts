import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  devIndicators: false,
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  async rewrites() {
    return [
      {
        source: "/operators/:path*",
        destination: "/endaxis/operators/:path*",
      },
      {
        source: "/weapons/:path*",
        destination: "/endaxis/weapons/:path*",
      },
      {
        source: "/equipment/item_equip_t4_suit_usp02_edc_01.webp",
        destination: "/endaxis/equipment/usp02/item_equip_t4_suit_usp02_edc_01.webp",
      },
      {
        source: "/equipment/item_equip_t4_suit_fire_natr01_edc_02.webp",
        destination: "/endaxis/equipment/fire_natr01/item_equip_t4_suit_fire_natr01_edc_02.webp",
      },
      {
        source: "/equipment/item_equip_t4_suit_poise01_edc_02.webp",
        destination: "/endaxis/equipment/poise01/item_equip_t4_suit_poise01_edc_02.webp",
      },
      {
        source: "/equipment/item_equip_t3_suit_atk01_edc_04.webp",
        destination: "/endaxis/equipment/atk01/item_equip_t3_suit_atk01_edc_04.webp",
      },
      {
        source: "/equipment/:path*",
        destination: "/endaxis/equipment/:path*",
      },
      {
        source: "/icons/:path*",
        destination: "/endaxis/icons/:path*",
      },
      {
        source: "/Icon_Enemy/:path*",
        destination: "/endaxis/Icon_Enemy/:path*",
      },
      {
        source: "/contingency_contract/:path*",
        destination: "/endaxis/contingency_contract/:path*",
      },
      {
        source:
          "/endaxis/:path((?!assets/|operators/|weapons/|equipment/|icons/|Icon_Enemy/|contingency_contract/|gamedata\\.json$|logo\\.webp$).*)",
        destination: "/endaxis/index.html",
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/images/:path*",
        headers: [
          {
            key: "Cache-Control",
            value: "public, max-age=31536000, immutable",
          },
        ],
      },
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: "base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'",
          },
          {
            key: "Cross-Origin-Opener-Policy",
            value: "same-origin",
          },
          {
            key: "Cross-Origin-Resource-Policy",
            value: "same-origin",
          },
          {
            key: "Origin-Agent-Cluster",
            value: "?1",
          },
          {
            key: "Permissions-Policy",
            value: "camera=(), geolocation=(), microphone=()",
          },
          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },
          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },
          {
            key: "X-Frame-Options",
            value: "DENY",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
