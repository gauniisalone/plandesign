import { createHash } from "node:crypto";
import { defineConfig } from "@playwright/test";
import { E2E_WORKER_TOKEN, RENDER_ACCOUNT } from "./e2e-online/render-account";

/**
 * MVP en ligne sans GPU (19 oct.) : comme les e2e des comptes, mais SANS Blender (binaire
 * introuvable) et SANS poids de modèles (dossier vide) : c'est l'image de production. torch reste
 * installé dans l'environnement, mais sans poids les adaptateurs neuronaux sont indisponibles.
 * Vérifie qu'aucun bouton ne mène à une erreur de calcul.
 *
 * Reprise de la configuration B1d : e2e avec les comptes actifs.
 * Base SQLite jetable migrée au lancement, mailer `fake` (son point de test donne le code ;
 * refusé par l'API hors boucle locale), inscription ouverte, origine publique = Vite.
 * Lancer : `make e2e` (après les e2e sans comptes), ou
 * `npx playwright test -c playwright.accounts.config.ts` (mêmes variables de ports).
 * `localhost` (et non 127.0.0.1) : Chromium y accepte le cookie `Secure` sur http.
 */
const apiPort = process.env.OPENPLAN_PORT ?? "8000";
const webPort = process.env.OPENPLAN_WEB_PORT ?? "5173";
const dir = "apps/web/.e2e-online";
// KAN-89 : compte autorisé aux rendus réalistes et empreinte du jeton du poste simulé.
const workerHash = createHash("sha256").update(E2E_WORKER_TOKEN).digest("hex");
const env = [
  "OPENPLAN_DOTENV=",
  "OPENPLAN_AUTO_PATH=0",
  `OPENPLAN_DATABASE_URL=sqlite:///${dir}/comptes.db`,
  "OPENPLAN_MAILER=fake",
  "OPENPLAN_SIGNUP=open",
  "OPENPLAN_ADMIN_EMAIL=admin@example.org",
  `OPENPLAN_PUBLIC_URL=http://localhost:${webPort}`,
  "OPENPLAN_BLENDER_BIN=/nonexistent/blender",
  `OPENPLAN_MODELS_DIR=${dir}/weights`,
  `OPENPLAN_MATERIALS_DIR=${dir}/materials`,
  `OPENPLAN_RENDER_ACCOUNTS=${RENDER_ACCOUNT.id}`,
  `OPENPLAN_WORKER_TOKEN_SHA256=${workerHash}`,
  // KAN-109 : journal d'accès d'uvicorn rallumé pour ce serveur jetable (garde des 500).
  "OPENPLAN_UVICORN_ACCESS_LOG=1",
].join(" ");

export default defineConfig({
  testDir: "e2e-online",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results-online",
  use: {
    baseURL: `http://localhost:${webPort}`,
    viewport: { width: 1400, height: 900 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        `cd ../.. && rm -rf ${dir} && mkdir -p ${dir}/weights ${dir}/materials && ` +
        `${env} uv run --extra comptes openplan accounts migrate && ` +
        `uv run --extra comptes python apps/web/e2e-online/seed_render_account.py ` +
        `sqlite:///${dir}/comptes.db ${RENDER_ACCOUNT.id} ${RENDER_ACCOUNT.email} && ` +
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
