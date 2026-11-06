import { defineConfig } from "@playwright/test";

/**
 * KAN-124 (phase 2) : l'assistant de calage, avec le FAUX modèle du serveur
 * (OPENPLAN_ASSISTANT_URL=fake, refusé hors de la boucle locale). Une configuration à part :
 * le drapeau n'est allumé que pour ces specs, les autres e2e restent sans panneau « Assistant ».
 * Lancer : `npm run e2e:assistant`. Ports : ceux des autres configurations par défaut (8000,
 * 5173) ; jamais en même temps qu'un autre e2e (verrou « e2e »).
 */
const apiPort = process.env.OPENPLAN_PORT ?? "8000";
const webPort = process.env.OPENPLAN_WEB_PORT ?? "5173";

export default defineConfig({
  testDir: "e2e-assistant",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results-assistant",
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    viewport: { width: 1400, height: 900 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `cd ../.. && rm -rf apps/web/.e2e-assistant && OPENPLAN_DOTENV= OPENPLAN_AUTO_PATH=0 OPENPLAN_ASSISTANT=1 OPENPLAN_ASSISTANT_URL=fake uv run openplan serve --port ${apiPort} --data-dir apps/web/.e2e-assistant`,
      url: `http://127.0.0.1:${apiPort}/api/health`,
      reuseExistingServer: false,
      stdout: "pipe",
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
