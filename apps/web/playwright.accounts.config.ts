import { createHash } from "node:crypto";
import { defineConfig } from "@playwright/test";
import { SHARE_ACCOUNT } from "./e2e-accounts/share-account";
import { E2E_WORKER_TOKEN } from "./e2e-online/render-account";

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
// KAN-112 : la CI pose E2E_DATABASE_URL (Postgres neuf par job) ; vide ou absente, la base
// SQLite jetable d'avant. La migration se fait au lancement dans les deux cas.
const databaseUrl =
  process.env.E2E_DATABASE_URL?.trim() || `sqlite:///${dir}/comptes.db`;
// KAN-101 : un compte autorisé aux rendus réalistes (ses rendus faits sont posés en base par
// e2e-accounts/seed_done_render.py : la place d'un Mo refuserait une demande), et les liens
// publics allumés (éteints par défaut en production). Le test du réglage éteint lance son
// propre second serveur. KAN-99 : la galerie n'ouvre que si le rendu est allumé (liste
// blanche ET empreinte du jeton du poste) ; aucun poste ne tourne ici, le jeton n'est pas lu.
const workerHash = createHash("sha256").update(E2E_WORKER_TOKEN).digest("hex");
const env = [
  "OPENPLAN_DOTENV=",
  "OPENPLAN_AUTO_PATH=0",
  `OPENPLAN_DATABASE_URL='${databaseUrl.replaceAll("'", "")}'`,
  "OPENPLAN_MAILER=fake",
  "OPENPLAN_SIGNUP=open",
  "OPENPLAN_ADMIN_EMAIL=admin@example.org",
  `OPENPLAN_PUBLIC_URL=http://localhost:${webPort}`,
  // KAN-12 : une place d'un Mo par compte, pour éprouver le refus (aucun autre test ici
  // n'envoie de fichier).
  "OPENPLAN_QUOTA_MB=1",
  // KAN-109 : le journal d'accès d'uvicorn, coupé en production (RGPD), rallumé pour ce
  // serveur jetable : le garde des passages y cherche les réponses 500.
  "OPENPLAN_UVICORN_ACCESS_LOG=1",
  // KAN-109 : le journal du site (écrit par le test du back-office), lu chaque seconde.
  `OPENPLAN_ACCESS_LOG_DIR=${dir}/access-log`,
  "OPENPLAN_ACCESS_LOG_INTERVAL_S=1",
  `OPENPLAN_RENDER_ACCOUNTS=${SHARE_ACCOUNT.id}`,
  `OPENPLAN_WORKER_TOKEN_SHA256=${workerHash}`,
  "OPENPLAN_SHARE_LINKS=1",
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
        `uv run --extra comptes python apps/web/e2e-online/seed_render_account.py ` +
        `'${databaseUrl.replaceAll("'", "")}' ${SHARE_ACCOUNT.id} ${SHARE_ACCOUNT.email} && ` +
        `${env} uv run --extra comptes openplan serve --port ${apiPort} --data-dir ${dir}/data`,
      url: `http://127.0.0.1:${apiPort}/api/health`,
      reuseExistingServer: false,
      // Journal d'accès d'uvicorn (sortie standard) gardé dans le journal des e2e : une
      // preuve quand un test tombe ; le garde de commit29 y voit aussi les réponses 500.
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
  ],
});
