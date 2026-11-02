// KAN-102 : la logique du module « Ajouter à PlanDesign », sans API du navigateur (testée par
// apps/web/unit/extension.test.ts, `npm run test:unit`).

export const MAX_URL = 2000;
export const TOKEN_PATTERN = /^pde_[0-9a-f]{32}_[A-Za-z0-9_-]{20,90}$/;
export const LAST_CHOICE_KEY = "lastChoice";
export const TOKEN_KEY = "token";

/** Une adresse web gardable (http ou https, sans espace, 2000 caractères au plus) ; null
 *  sinon : une image `data:` ou `blob:` n'a pas d'adresse que le serveur puisse récupérer. */
export function webUrl(raw) {
  if (typeof raw !== "string") return null;
  const url = raw.trim();
  if (!url || url.length > MAX_URL || /\s/.test(url)) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? url
      : null;
  } catch {
    return null;
  }
}

/** Ce que la fenêtre de choix reçoit dans son adresse : l'image et sa page. */
export function readChoice(search) {
  const params = new URLSearchParams(search);
  const raw = params.get("image") ?? "";
  return {
    raw,
    image: webUrl(raw),
    page: webUrl(params.get("page") ?? ""),
  };
}

/** Le jeton collé, nettoyé (espaces, retours à la ligne) ; null s'il n'a pas sa forme. */
export function cleanToken(text) {
  const token = String(text ?? "").replace(/\s+/g, "");
  return TOKEN_PATTERN.test(token) ? token : null;
}

/** La requête vers l'API : le jeton en Bearer, JAMAIS de cookie (`credentials: "omit"`),
 *  aucun référent. */
export function apiRequest(origin, token, path, body) {
  const headers = { Authorization: `Bearer ${token}` };
  const init = {
    method: body === undefined ? "GET" : "POST",
    headers,
    credentials: "omit",
    referrerPolicy: "no-referrer",
    cache: "no-store",
  };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  return { url: `${origin.replace(/\/+$/, "")}/api/extension${path}`, init };
}

/** Le corps de l'ajout d'une image dans un node Moodboard. */
export function imageBody(nodeId, image, page) {
  return { node_id: nodeId, image_url: image, page_url: page ?? null };
}

/** Le choix proposé : le dernier choix s'il existe encore, sinon le premier projet qui a un
 *  Moodboard ; null si aucun. */
export function pickDefault(projects, last) {
  if (last) {
    const project = projects.find((p) => p.id === last.projectId);
    const node = project?.moodboards.find((m) => m.id === last.nodeId);
    if (project && node) return { projectId: project.id, nodeId: node.id };
  }
  const first = projects.find((p) => p.moodboards.length > 0);
  return first ? { projectId: first.id, nodeId: first.moodboards[0].id } : null;
}

/** Le message montré pour une réponse refusée de l'API. */
export function errorMessage(status, detail) {
  switch (status) {
    case 0:
      return "PlanDesign ne répond pas : vérifiez votre connexion, puis réessayez.";
    case 401:
      return "Jeton refusé (révoqué ou mal collé) : créez-en un dans PlanDesign, Réglages › Compte, puis collez-le dans les réglages de l'extension.";
    case 403:
      return "Vous n'êtes pas éditeur de ce projet : l'image ne peut pas y entrer.";
    case 404:
      return "Projet ou Moodboard introuvable : rouvrez la fenêtre pour relire la liste.";
    case 429:
      return "Trop d'images d'un coup : réessayez dans une minute.";
    default:
      return detail ? String(detail) : `Erreur ${status}.`;
  }
}
