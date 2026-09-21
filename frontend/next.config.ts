import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Required by frontend/Dockerfile, which copies .next/standalone.
  output: "standalone",
  typedRoutes: true,
  // Next 16 removed `next lint`; ESLint runs as its own step (`npm run lint`)
  // in CI, so a lint failure blocks the pipeline rather than the build.
  typescript: { ignoreBuildErrors: false },
  experimental: {
    serverActions: {
      // Uploads go through a server action, and Next caps an action's request
      // body at 1MB. A real filing is bigger than that — the Samsung half-year
      // XBRL instance is 2.6MB, and a statement workbook routinely is too — so
      // the default rejected them before any of this product's code ran, with
      // a 500 and a blank page rather than a message.
      //
      // Set just above the API's own 25MB cap (`IFRS18_UPLOAD_MAX_BYTES`), plus
      // room for multipart overhead, so that the *API* is what refuses an
      // oversize file. It refuses with a problem document naming the limit;
      // Next refuses with a stack trace in a server log nobody is reading.
      bodySizeLimit: "26mb",
    },
  },
};

export default nextConfig;
