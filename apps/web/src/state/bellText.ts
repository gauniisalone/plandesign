import type { BellLine } from "../api/client";

/** KAN-52 : le texte d'une ligne de la cloche et du badge, sans dépendance (testé par
 *  `npm run test:unit`). « Fanny a commenté « Photo 2 » · Grenier · il y a 5 min ». */

/** Le badge : rien quand tout est lu, « 9+ » au-delà de 9. */
export function badgeText(unread: number): string | null {
  if (unread <= 0) return null;
  return unread > 9 ? "9+" : String(unread);
}

/** Heure relative (« à l'instant », « il y a 5 min », « hier »). */
export function relativeTime(at: string, now: number = Date.now()): string {
  const seconds = Math.round((new Date(at).getTime() - now) / 1000);
  if (Math.abs(seconds) < 60) return "à l'instant";
  // Court, comme dans le ticket : « il y a 5 min », « il y a 3 h », « hier ».
  const rtf = new Intl.RelativeTimeFormat("fr", {
    numeric: "auto",
    style: "short",
  });
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) return rtf.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return rtf.format(hours, "hour");
  const days = Math.round(hours / 24);
  if (Math.abs(days) < 30) return rtf.format(days, "day");
  return rtf.format(Math.round(days / 30), "month");
}

function quoted(label: string | null): string | null {
  return label ? `« ${label} »` : null;
}

/** KAN-58 : l'élément touché, quand il n'a pas de nom. */
const KIND_NOUN: Record<string, string> = {
  photo: "une photo",
  render: "un rendu",
  note: "un post-it",
  frame: "un cadre",
  plan: "le plan",
  file: "un fichier",
  video: "une vidéo",
  pdf: "un PDF",
  link: "un lien",
  contextual: "un rendu contextualisé",
};

function plural(n: number, word: string): string {
  return `${n} ${word}${n > 1 ? "s" : ""}`;
}

/** KAN-58 : le détail d'une modification de l'espace de travail (« Gilles a supprimé
 *  « Photo 2 » », « … · 2 ajoutés, 1 supprimé ») ; sans détail, « · 12 changements ». */
function boardText(line: BellLine): string {
  const who = line.actor_name;
  const { added = 0, changed = 0, removed = 0, recolored = 0 } = line;
  const object =
    quoted(line.label) ?? (line.item_kind ? KIND_NOUN[line.item_kind] : null);
  const more = line.count > 1 ? ` · ${line.count} changements` : "";
  // KAN-65 : seule la couleur a changé (« a changé la couleur de « Canapé » »).
  // Seuls les post-its ont une couleur.
  if (recolored > 0 && recolored === changed && !added && !removed) {
    const of = line.label
      ? `de « ${line.label} »`
      : line.item_kind
        ? "d'un post-it"
        : `de ${plural(recolored, "post-it")}`;
    return `${who} a changé la couleur ${of}${more}`;
  }
  if (object) {
    const verb =
      removed && !added && !changed
        ? "a supprimé"
        : added && !changed && !removed
          ? "a ajouté"
          : "a modifié";
    return `${who} ${verb} ${object}${more}`;
  }
  const parts = [
    added ? plural(added, "ajouté") : null,
    changed ? plural(changed, "modifié") : null,
    removed ? plural(removed, "supprimé") : null,
  ].filter(Boolean);
  return parts.length
    ? `${who} a modifié l'espace de travail · ${parts.join(", ")}`
    : `${who} a modifié l'espace de travail${more}`;
}

/** Ce qui s'est passé, sans le projet ni l'heure. */
export function actionText(line: BellLine): string {
  const who = line.actor_name;
  const object = quoted(line.label);
  const more = line.count > 1 ? ` · ${line.count} changements` : "";
  switch (line.type) {
    case "comment.created":
      return `${who} a commenté ${object ?? "l'espace de travail"}`;
    case "comment.replied":
      return object
        ? `${who} a répondu sur ${object}`
        : `${who} a répondu dans un fil`;
    case "comment.resolved":
      return object
        ? `${who} a résolu le fil sur ${object}`
        : `${who} a résolu un fil`;
    case "comment.reopened":
      return object
        ? `${who} a rouvert le fil sur ${object}`
        : `${who} a rouvert un fil`;
    case "member.joined":
      return `${who} a rejoint le projet`;
    case "render.done":
      return line.mine ? "Votre rendu est prêt" : `Le rendu de ${who} est prêt`;
    case "board.changed":
      return boardText(line);
    case "plan.changed":
      // Un seul geste : son libellé (« Ajouter un mur », « Annulé : … »).
      return line.label && line.count === 1
        ? `${who} a modifié le plan · ${line.label}`
        : `${who} a modifié le plan${more}`;
  }
}

/** La ligne entière, telle que lue par un lecteur d'écran. */
export function lineText(line: BellLine, now: number = Date.now()): string {
  return `${actionText(line)} · ${line.project_name} · ${relativeTime(line.at, now)}`;
}
