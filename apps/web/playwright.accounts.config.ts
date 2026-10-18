import { defineConfig } from "@playwright/test";

/**
 * B1d : e2e avec les comptes actifs (page de connexion, session, rôle, /nouveau).
 * Base SQLite jetable migrée au lancement, mailer `fake` (son point de test donne le code ;
 * refusé par l'API hors boucle locale), inscription ouverte, origine publique = Vite.
 * Lancer : `make e2e` (après les e2e sans comptes), ou
 * `npx playwright test -c playwright.accounts.config.ts` (mêmes variables de ports).
 * `localhost` (et non 127.0.0.1) : Chromium y accepte le cookie `Secure` sur http.
 */
const apiPort = process.env.OPENPLAN_PORT ?? "8000";
const webPort = process.env.OPENPLAN_WEB_PORT ?? "5173";
const dir = "apps/web/.e2e-accounts";
const env = [
  "OPENPLAN_DOTENV=",
  "OPENPLAN_AUTO_PATH=0",
  `OPENPLAN_DATABASE_URL=sqlite:///${dir}/comptes.db`,
  "OPENPLAN_MAILER=fake",
  "OPENPLAN_SIGNUP=open",
  "OPENPLAN_ADMIN_EMAIL=admin@example.org",
  `OPENPLAN_PUBLIC_URL=http://localhost:${webPort}`,
].join(" ");

export default defineConfig({
  testDir: "e2e-accounts",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results-accounts",
  use: {
    baseURL: `http://localhost:${webPort}`,
    viewport: { width: 1400, height: 900 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        `cd ../.. && rm -rf ${dir} && mkdir -p ${dir} && ` +
        `${env} uv run --extra comptes openplan accounts migrate && ` +
        `${env} uv run --extra comptes openplan serve --port ${apiPort} --data-dir ${dir}/data`,
      url: `http://127.0.0.1:${apiPort}/api/health`,
      reuseExistingServer: false,
      timeout: 90_000,
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
