# MedFlow AI — web app

Next.js 16 (App Router) · TypeScript · Tailwind v4 · TanStack Query · React Hook Form + Zod · Recharts.

```bash
npm install
npm run dev          # http://localhost:3000, proxies /api → BACKEND_URL (default http://localhost:8000)
npm run lint && npm run typecheck && npm run build
npm run test:e2e     # Playwright; needs API running with demo data (set PW_CHROMIUM_PATH to reuse a local Chromium)
```

`proxy.ts` is the Next 16 replacement for `middleware.ts`. See the root README for the full stack.
