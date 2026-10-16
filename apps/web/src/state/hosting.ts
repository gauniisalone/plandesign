import { useEffect, useState } from "react";
import { api } from "../api/client";

/** Où tourne PlanDesign (T6), pour des mentions de confidentialité justes : « local » (sur la
 *  machine de la personne : rien ne part) ou « online » (serveur PlanDesign : les projets y
 *  sont gardés). Lu une fois depuis /api/health, public et sans donnée sensible. */
export interface Hosting {
  online: boolean;
  /** Des rapports d'erreur filtrés (ni texte, ni photo, ni nom) partent vers le suivi. */
  errorReports: boolean;
}

const LOCAL: Hosting = { online: false, errorReports: false };
let pending: Promise<Hosting> | null = null;

export function loadHosting(): Promise<Hosting> {
  pending ??= api.health().then(
    (h) => ({
      online: Boolean(h.exposed),
      errorReports: Boolean(h.error_reports),
    }),
    () => LOCAL,
  );
  return pending;
}

/** Tant que la réponse n'est pas là, la mention la plus prudente n'est pas « tout reste sur
 *  cette machine » : `null` laisse l'appelant ne rien affirmer. */
export function useHosting(): Hosting | null {
  const [hosting, setHosting] = useState<Hosting | null>(null);
  useEffect(() => {
    let alive = true;
    void loadHosting().then((h) => {
      if (alive) setHosting(h);
    });
    return () => {
      alive = false;
    };
  }, []);
  return hosting;
}

/** Où sont gardés photos et plans. */
export function storedWhere(hosting: Hosting | null): string {
  if (hosting === null) return "";
  return hosting.online
    ? "vos photos et plans sont gardés sur le serveur PlanDesign"
    : "vos photos et plans restent sur cette machine";
}

/** La mention complète, selon l'hébergement et la case Gemini (A1 : elle suit l'état réel). */
export function privacyLine(hosting: Hosting | null, gemini: boolean): string {
  if (hosting === null) return "";
  const parts = [storedWhere(hosting)];
  parts.push(
    gemini
      ? "votre texte part chez Google (Gemini), jamais les photos"
      : hosting.online
        ? "rien n'est envoyé ailleurs"
        : "rien ne part",
  );
  if (hosting.errorReports)
    parts.push(
      "un plantage envoie un rapport technique sans vos textes ni vos photos",
    );
  return parts.join(" ; ");
}
