/** KAN-138 : le battement de cœur de la connexion de présence (live.ts), sans rien du réseau
 *  ni de React (testé par `node --test`).
 *
 *  Avant, la connexion était tenue pour morte après 60 s SANS MESSAGE, vérifiées au tic de la
 *  minuterie du ping (25 s). Dans un onglet en arrière-plan, le navigateur bride cette
 *  minuterie (environ une fois par minute après 5 min) : au tic suivant, le dernier pong avait
 *  plus de 60 s, et la connexion, pourtant vivante, était refaite toutes les 7 à 8 min
 *  (constat de l'Infra après la MEP 41). Le silence se mesure maintenant depuis le PREMIER
 *  PING RESTÉ SANS RÉPONSE : tant que chaque ping a sa réponse, l'écart entre les tics ne
 *  compte pas ; un réseau mort est toujours vu, après `silenceMs` sans réponse à un ping. */

export interface Beat {
  /** Quand est parti le premier ping encore sans réponse ; null : tout a été répondu. */
  pendingSince: number | null;
}

export const FRESH: Beat = { pendingSince: null };

/** Un message reçu du serveur (pong ou autre) : la connexion vit, rien n'est en attente. */
export function heard(): Beat {
  return FRESH;
}

/** Un tic de la minuterie : `dead` (connexion à refaire), sinon on pinge, et l'attente part
 *  de ce ping s'il n'y en avait pas déjà une. */
export function tick(
  beat: Beat,
  now: number,
  silenceMs: number,
): { dead: boolean; beat: Beat } {
  if (beat.pendingSince !== null && now - beat.pendingSince > silenceMs)
    return { dead: true, beat };
  return { dead: false, beat: { pendingSince: beat.pendingSince ?? now } };
}
