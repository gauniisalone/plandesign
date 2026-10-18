import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Project } from "../api/types.gen";
import { parseNouveau } from "../nouveau";

/** `#/nouveau?…` (B1d) : arrivée depuis le formulaire du site. Un projet au nom automatique,
 *  ses exigences préremplies (consigne et réponses guidées, lues par le compositeur), puis
 *  l'espace de travail s'ouvre, compositeur ouvert. Rien ne se génère tout seul. */

/** Une seule création par adresse, même si React monte deux fois (mode strict) ou si la page
 *  est rechargée avant la redirection. */
const started = new Map<string, Promise<Project>>();

async function create(query: string): Promise<Project> {
  const prefill = parseNouveau(query);
  const project = await api.createAutoNamedProject(false);
  if (
    !prefill ||
    (!prefill.instruction && !Object.keys(prefill.answers).length)
  )
    return project;
  return api.updateSettings(project.id, {
    ...(prefill.instruction
      ? { pending_instruction: prefill.instruction }
      : {}),
    ...(Object.keys(prefill.answers).length
      ? { pending_answers: prefill.answers }
      : {}),
  });
}

export function NewFromSite({
  query,
  onOpen,
}: {
  query: string;
  /** `board` : l'espace de travail, où est le compositeur (et non l'onglet d'arrivée par
   *  défaut d'un projet à consigne, le plan). */
  onOpen: (id: string, step: "board") => void;
}) {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let promise = started.get(query);
    if (!promise) {
      promise = create(query);
      started.set(query, promise);
    }
    promise.then(
      (project) => onOpen(project.id, "board"),
      (e: unknown) => setError(String(e)),
    );
  }, [query, onOpen]);
  return (
    <div className="login">
      <main className="login-card" aria-busy={!error}>
        {error ? (
          <p className="warn" role="alert">
            L'espace de travail n'a pas pu être créé : {error}
          </p>
        ) : (
          <p className="muted" role="status">
            <span className="spinner" aria-hidden="true" /> Ouverture de votre
            espace de travail…
          </p>
        )}
      </main>
    </div>
  );
}
