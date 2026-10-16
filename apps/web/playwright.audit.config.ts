import { defineConfig } from "@playwright/test";

/**
 * Audit responsive (directive d'augus du 29 sept. 2026) : captures de chaque écran aux largeurs
 * d'ordinateur, avec mesures automatiques (débordement, chevauchement, texte tronqué, petites
 * cibles). Hors de `make e2e` : lancer
 *   OPENPLAN_PORT=8010 OPENPLAN_WEB_PORT=5183 AUDIT_LABEL=avant \
 *     npx playwright test -c playwright.audit.config.ts
 * Les captures vont dans e2e/screenshots/responsive/<AUDIT_LABEL>/.
 */
const apiPort = process.env.OPENPLAN_PORT ?? "8000";
const webPort = process.env.OPENPLAN_WEB_PORT ?? "5173";

export default defineConfig({
  testDir: "e2e-audit",
  timeout: 600_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results-audit",
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    viewport: { width: 1400, height: 900 },
    screenshot: "off",
  },
  webServer: [
    {
      command: `cd ../.. && OPENPLAN_DOTENV= uv run openplan serve --port ${apiPort} --data-dir apps/web/.e2e-data`,
      url: `http://127.0.0.1:${apiPort}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "npm run dev",
      url: `http://127.0.0.1:${webPort}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { OPENPLAN_PORT: apiPort, OPENPLAN_WEB_PORT: webPort },
    },
  ],
});
