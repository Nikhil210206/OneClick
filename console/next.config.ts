import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The console is screen-recorded for the submission video, so nothing may overlay the UI.
  devIndicators: false,

  turbopack: {
    // Turbopack refuses to resolve anything above its project root, and the demo imports the
    // shipped fixtures from `data/fixtures/` rather than keeping its own copy — a copy would
    // drift from the contract api/tests/test_fixtures.py guards the moment a scenario changes.
    // Widening the root to the repo lets that import resolve.
    root: path.join(__dirname, ".."),
  },
};

export default nextConfig;
