// Page publique de PlanDesign : sortie 100 % statique dans dist/,
// servie telle quelle par Caddy. Aucun script client, aucun style en ligne
// (compatible avec une CSP stricte : default-src 'self').
import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://plandesign.fr",
  output: "static",
  trailingSlash: "always",
  build: {
    format: "directory",
    inlineStylesheets: "never",
    assets: "_assets",
  },
  compressHTML: true,
  devToolbar: { enabled: false },
});
