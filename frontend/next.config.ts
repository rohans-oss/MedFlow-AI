import type { NextConfig } from "next";

// The browser only ever talks to the Next.js origin; /api/* is proxied to FastAPI.
// This keeps auth cookies first-party (HttpOnly, SameSite=Lax) and avoids CORS in production.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // Model training (POST /api/forecasts/train) can exceed the rewrite proxy's 30 s default once V2B's
  // rolling validation folds run on larger datasets; allow up to 3 minutes before the proxy gives up.
  experimental: { proxyTimeout: 180_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
