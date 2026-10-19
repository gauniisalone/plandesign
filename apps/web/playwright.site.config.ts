import { defineConfig } from "@playwright/test";

/**
 * Fenêtre « Se connecter » du site public (apps/site/login/login.js) : le site construit avec
 * PUBLIC_LOGIN_WINDOW=1 et servi en local, l'app avec les comptes (mêmes réglages que
 * playwright.accounts.config.ts : SQLite jetable, mailer `fake`, inscription ouverte) et
 * OPENPLAN_SITE_ORIGIN = l'origine locale du site. Sans GPU.
 * Lancer : `npm run e2e:site`. Ports par défaut hors de ceux des autres e2e (API 8015,
 * front 5188, site 4388), surchargeables par OPENPLAN_PORT, OPENPLAN_WEB_PORT et
 * OPENPLAN_SITE_PORT. `localhost` partout : site et app sont alors du même site (cookie
 * `SameSite=Lax` envoyé par le fetch de la fenêtre), comme plandesign.fr et app.plandesign.fr.
 */
const apiPort = process.env.OPENPLAN_PORT ?? "8015";
const webPort = process.env.OPENPLAN_WEB_PORT ?? "5188";
const sitePort = process.env.OPENPLAN_SITE_PORT ?? "4388";
const dir = "apps/web/.e2e-site";
const env = [
  "OPENPLAN_DOTENV=",
  "OPENPLAN_AUTO_PATH=0",
  `OPENPLAN_DATABASE_URL=sqlite:///${dir}/comptes.db`,
  "OPENPLAN_MAILER=fake",
  "OPENPLAN_SIGNUP=open",
  "OPENPLAN_ADMIN_EMAIL=admin@example.org",
  `OPENPLAN_PUBLIC_URL=http://localhost:${webPort}`,
  `OPENPLAN_SITE_ORIGIN=http://localhost:${sitePort}`,
].join(" ");

export default defineConfig({
  testDir: "e2e-site",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results-site",
  use: {
    baseURL: `http://localhost:${sitePort}`,
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
      stdout: "pipe",
      timeout: 90_000,
    },
    {
      command: "npm run dev",
      url: `http://127.0.0.1:${webPort}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { OPENPLAN_PORT: apiPort, OPENPLAN_WEB_PORT: webPort },
    },
    {
      // Le site avec l'option, l'app sur le front local (Vite relaie /api vers l'API).
      command:
        `cd ../site && PUBLIC_LOGIN_WINDOW=1 PUBLIC_APP_ORIGIN=http://localhost:${webPort} ` +
        `npm run build && npm run preview -- --host 127.0.0.1 --port ${sitePort}`,
      url: `http://127.0.0.1:${sitePort}/`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
