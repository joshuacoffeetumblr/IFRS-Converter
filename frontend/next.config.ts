import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Required by frontend/Dockerfile, which copies .next/standalone.
  output: "standalone",
  typedRoutes: true,
  // Next 16 removed `next lint`; ESLint runs as its own step (`npm run lint`)
  // in CI, so a lint failure blocks the pipeline rather than the build.
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;
