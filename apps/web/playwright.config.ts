import { defineConfig } from "@playwright/test";

/**
 * Tests de bout en bout dans un vrai navigateur (Chromium headless).
 * Deux serveurs sont lancés : l'API (dossier de données jetable) et Vite.
 * Lancer : `make e2e` (ou `cd apps/web && npx playwright test`).
 * Si le port 8000 ou 5173 est déjà pris (un `make start` qui tourne), choisir d'autres ports :
 * `OPENPLAN_PORT=8010 OPENPLAN_WEB_PORT=5183 make e2e`.
 */
const apiPort = process.env.OPENPLAN_PORT ?? "8000";
const webPort = process.env.OPENPLAN_WEB_PORT ?? "5173";

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results",
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    viewport: { width: 1400, height: 900 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // OPENPLAN_DOTENV vide : le .env personnel (clé d'API distante) n'est pas lu par ce serveur jetable.
      // OPENPLAN_AUTO_PATH=0 : les projets des tests ne lancent pas le chemin automatique
      // (rendus Blender) ; le test du chemin l'active pour son projet.
      command: `cd ../.. && OPENPLAN_DOTENV= OPENPLAN_AUTO_PATH=0 uv run openplan serve --port ${apiPort} --data-dir apps/web/.e2e-data`,
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
