import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

/** KAN-79 : onnxruntime-web (profondeur dans le navigateur) servi par le front sous /ort/,
 *  jamais par un CDN. Copié de node_modules au démarrage du dev et du build (dossier
 *  public/ort ignoré par git), exclu du précache de la PWA (~28 Mo, chargé à la demande). */
const ORT_FILES = [
  // Le module WebGPU (avec repli WASM) de la 1.30 charge la variante « asyncify ».
  "ort.webgpu.min.mjs",
  "ort-wasm-simd-threaded.asyncify.mjs",
  "ort-wasm-simd-threaded.asyncify.wasm",
];

function ortStatic(): Plugin {
  const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));
  return {
    name: "plandesign-ort",
    buildStart() {
      const from = here("./node_modules/onnxruntime-web/dist/");
      const to = here("./public/ort/");
      mkdirSync(to, { recursive: true });
      for (const name of ORT_FILES) {
        if (!existsSync(from + name))
          throw new Error(`onnxruntime-web : ${name} absent (npm ci)`);
        copyFileSync(from + name, to + name);
      }
    },
  };
}

// L'application ne parle qu'à l'API locale. Le proxy évite le CORS en développement.
// Ports surchargeables (machine où un autre serveur OpenPlan tourne déjà, tests e2e) :
// OPENPLAN_PORT (API, défaut 8000) et OPENPLAN_WEB_PORT (Vite, défaut 5173).
const apiPort = process.env.OPENPLAN_PORT ?? "8000";
const webPort = Number(process.env.OPENPLAN_WEB_PORT ?? "5173");

export default defineConfig({
  plugins: [
    react(),
    ortStatic(),
    // PWA (cadrage 0.4, 4.8) : « Ajouter à l'écran d'accueil », plein écran, chargement
    // instantané du front. Le service worker ne met en cache QUE le front construit : jamais
    // les réponses de l'API (projets, photos), jamais /docs. Inactif en développement et en e2e.
    // Mise à jour (correctif du 4 oct. 2026) : le nouveau service worker attend, l'application
    // l'active elle-même (src/pwa.ts) : tout de suite juste après un chargement, sinon par le
    // bandeau « Nouvelle version disponible ». Avant, l'onglet ouvert gardait l'ancienne version.
    VitePWA({
      registerType: "prompt",
      injectRegister: null,
      includeAssets: ["icons/*.png"],
      manifest: {
        name: "PlanDesign",
        short_name: "PlanDesign",
        description:
          "Photos d'intérieur vers plan 2D, scène 3D et aménagement assisté. Local, gratuit.",
        lang: "fr",
        start_url: "/",
        scope: "/",
        display: "standalone",
        orientation: "any",
        background_color: "#f6f0e5",
        theme_color: "#a63a2e",
        icons: [
          { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
          {
            src: "/icons/icon-maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,png,svg,woff2}"],
        globIgnores: ["ort/**"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/docs/,
          /^\/redoc/,
          /^\/openapi\.json/,
        ],
        runtimeCaching: [],
        cleanupOutdatedCaches: true,
        clientsClaim: true,
      },
      devOptions: { enabled: false },
    }),
  ],
  server: {
    host: "127.0.0.1",
    port: webPort,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: false,
      },
      // B2a : présence (WebSocket par projet, avec les comptes).
      "/ws": {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: false,
        ws: true,
      },
    },
  },
});
