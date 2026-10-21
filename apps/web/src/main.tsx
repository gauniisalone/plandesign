import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.tsx";
import { startPwa } from "./pwa";
import { applyTheme, forgetAccent, loadTheme } from "./theme";
import { UpdateBanner } from "./UpdateBanner";
import "./index.css";

// Version du front construit (lue par le test de mise à jour PWA) ; « dev » en développement.
document.documentElement.dataset.build =
  import.meta.env.VITE_OPENPLAN_BUILD ?? "dev";
// Thème choisi dans Réglages (Système par défaut), posé avant le premier affichage.
applyTheme(loadTheme());
// KAN-77 : l'accent est l'ocre du site ; un ancien choix (Indigo, Violet, Provence) est oublié.
forgetAccent();
startPwa();

const root = document.getElementById("root");
if (!root) {
  throw new Error("Élément #root introuvable");
}

createRoot(root).render(
  <StrictMode>
    <App />
    <UpdateBanner />
  </StrictMode>,
);
