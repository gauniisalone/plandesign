import { useEffect, useRef, useState } from "react";
import type { ProjectSummary } from "../api/client";
import {
  CONFIRM_WORD,
  confirmMatches,
  confirmWord,
  deleteAllButton,
  type DeleteAllReport,
  deleteAllTitle,
  type DeleteAllResult,
  foldedLabel,
  namedResult,
  progressLine,
  quotedNames,
  splitNames,
} from "../state/deleteAll";

/** KAN-119 : la confirmation de « Supprimer tous mes projets ». Le bouton rouge reste grisé
 *  tant que le champ ne contient pas SUPPRIMER ; Entrée ne valide pas avant, Échap vaut
 *  Annuler. Pendant la suppression (un seul appel au serveur, qui revérifie le mot), la
 *  fenêtre ne se ferme pas. */
export function DeleteAllDialog({
  owned,
  shared,
  accounts,
  remove,
  onDone,
  onClose,
}: {
  owned: ProjectSummary[];
  /** Projets partagés avec soi : ils restent. */
  shared: number;
  accounts: boolean;
  remove: (ids: string[], word: string) => Promise<DeleteAllReport>;
  onDone: (result: DeleteAllResult) => void;
  onClose: () => void;
}) {
  const [typed, setTyped] = useState("");
  const [running, setRunning] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const ok = confirmMatches(typed);
  const n = owned.length;

  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      if (!running) onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, running]);

  const run = async () => {
    if (!ok || running) return;
    setRunning(true);
    const ids = owned.map((p) => p.id);
    const report = await remove(ids, confirmWord(typed)).catch(() => null);
    onDone(namedResult(owned, report));
  };

  const { shown, folded } = splitNames(owned.map((p) => p.name));
  const stays = [
    shared > 0 ? `les projets partagés avec vous (${shared})` : null,
    accounts ? "votre compte" : null,
    "vos réglages",
  ].filter(Boolean);
  return (
    <div
      className="new-plan-backdrop"
      onPointerDown={() => {
        if (!running) onClose();
      }}
    >
      <form
        className="new-plan delete-all"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-all-title"
        onPointerDown={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <h2 id="delete-all-title">{deleteAllTitle(n)}</h2>
        {running ? (
          <p className="delete-all-progress" role="status">
            <span className="spinner" aria-hidden="true" />
            {progressLine(n)}
          </p>
        ) : (
          <>
            <ul className="delete-all-list">
              <li>
                <strong>Supprimés définitivement</strong>, avec leur espace de
                travail, leurs plans, leurs photos, leurs fichiers et leurs
                rendus :{" "}
                {folded.length > 0
                  ? shown.map((name) => `« ${name} »`).join(", ")
                  : `${quotedNames(shown)}.`}
                {folded.length > 0 && (
                  <details>
                    <summary>{foldedLabel(folded.length)}</summary>
                    {quotedNames(folded)}.
                  </details>
                )}
              </li>
              {accounts && (
                <li>
                  <strong>Leurs membres</strong> n'y auront plus accès.
                </li>
              )}
              <li>
                <strong>Restent :</strong> {stays.join(", ")}.
              </li>
            </ul>
            <p className="muted">
              Cela ne s'annule pas. Pensez à exporter d'abord les projets à
              garder.
            </p>
            <label>
              Pour confirmer, saisissez {CONFIRM_WORD}
              <input
                ref={inputRef}
                value={typed}
                placeholder={CONFIRM_WORD}
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => setTyped(e.target.value)}
              />
            </label>
          </>
        )}
        <div className="new-plan-actions">
          <button type="button" onClick={onClose} disabled={running}>
            Annuler
          </button>
          <button
            type="submit"
            className="danger delete-all-go"
            disabled={!ok || running}
          >
            {deleteAllButton(n)}
          </button>
        </div>
      </form>
    </div>
  );
}
