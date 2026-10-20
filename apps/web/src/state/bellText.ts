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
    case "member.joined":
      return `${who} a rejoint le projet`;
    case "render.done":
      return line.mine ? "Votre rendu est prêt" : `Le rendu de ${who} est prêt`;
    case "board.changed":
      return `${who} a modifié l'espace de travail${more}`;
    case "plan.changed":
      return `${who} a modifié le plan${more}`;
  }
}

/** La ligne entière, telle que lue par un lecteur d'écran. */
export function lineText(line: BellLine, now: number = Date.now()): string {
  return `${actionText(line)} · ${line.project_name} · ${relativeTime(line.at, now)}`;
}
