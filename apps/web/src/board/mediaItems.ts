import { useEffect, useState } from "react";
import type { MediaRef } from "../api/client";

/** Médias du tableau (C1) : vidéo, PDF et liens, sans aucune requête vers l'extérieur. */

export type MediaItemKind = "video" | "pdf" | "file";

/** Taille de départ sur le tableau (pixels du tableau). */
export const MEDIA_SIZE: Record<
  MediaItemKind | "link" | "contextual",
  [number, number]
> = {
  contextual: [320, 330],
  video: [360, 230],
  pdf: [220, 300],
  file: [220, 72],
  link: [260, 96],
};

/** L'élément d'un fichier joint : vidéo lisible, PDF, sinon carte fichier (comme le
 *  serveur, `pipeline.board.board_kind`). */
export function mediaItemKind(ref: MediaRef): MediaItemKind {
  return ref.kind === "video" ? "video" : ref.kind === "pdf" ? "pdf" : "file";
}

const MAX_URL = 2000;

/** Une adresse de lien acceptée : http ou https, sans espace ; null sinon. */
export function linkFrom(text: string): string | null {
  const url = text.trim();
  return url.length <= MAX_URL && /^https?:\/\/[^\s<>"]+$/i.test(url)
    ? url
    : null;
}

/** Le domaine d'une adresse, montré sur la carte (jamais la page elle-même). */
export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** Le `src` de la première image d'un morceau de HTML, lu par un document inerte (rien n'est
 *  chargé ni exécuté). */
function imageSrc(html: string): string | null {
  if (!html || typeof DOMParser === "undefined") return null;
  const doc = new DOMParser().parseFromString(html, "text/html");
  return doc.querySelector("img[src]")?.getAttribute("src") ?? null;
}

/** KAN-98 L3 : l'adresse d'image d'un glisser-déposer depuis une autre page : l'image du
 *  HTML glissé d'abord (le navigateur y met l'image elle-même), puis la liste d'adresses,
 *  puis le texte ; null si rien d'utilisable. */
export function droppedLink(
  data: { getData: (type: string) => string },
  srcOf: (html: string) => string | null = imageSrc,
): string | null {
  const fromHtml = srcOf(data.getData("text/html"));
  const image = fromHtml ? linkFrom(fromHtml) : null;
  if (image) return image;
  const list = data
    .getData("text/uri-list")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .find((l) => l && !l.startsWith("#"));
  const fromList = list ? linkFrom(list) : null;
  return fromList ?? linkFrom(data.getData("text/plain"));
}

/** Le thème réellement affiché : choisi dans Réglages (`data-theme`), sinon celui du
 *  système ; suivi en direct. */
export function useDarkTheme(): boolean {
  const read = () => {
    const forced = document.documentElement.dataset.theme;
    if (forced === "dark") return true;
    if (forced === "light") return false;
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  };
  const [dark, setDark] = useState(read);
  useEffect(() => {
    const update = () => setDark(read());
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    query?.addEventListener("change", update);
    const watch = new MutationObserver(update);
    watch.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => {
      query?.removeEventListener("change", update);
      watch.disconnect();
    };
  }, []);
  return dark;
}
