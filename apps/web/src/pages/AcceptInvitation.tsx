import { useEffect, useState } from "react";
import { ApiError, authApi } from "../api/client";

/** `#/invitation/<jeton>` une fois connecté (B1e) : le lien est accepté (on devient membre avec
 *  le rôle du lien), puis le projet s'ouvre ; l'adresse est remplacée, le jeton ne reste pas
 *  dans l'historique. Lien expiré, révoqué ou déjà servi : un seul message. */

/** Une seule acceptation par jeton, même si React monte deux fois (mode strict). */
const started = new Map<string, Promise<string>>();

export function AcceptInvitation({
  token,
  onOpen,
  onHome,
}: {
  token: string;
  onOpen: (id: string, step: "board") => void;
  onHome: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let promise = started.get(token);
    if (!promise) {
      promise = authApi.acceptInvitation(token).then((r) => r.project_id);
      started.set(token, promise);
    }
    promise.then(
      (id) => onOpen(id, "board"),
      (e: unknown) =>
        setError(
          e instanceof ApiError
            ? e.message
            : "Le serveur ne répond pas : réessayez dans un instant.",
        ),
    );
  }, [token, onOpen]);
  return (
    <div className="login">
      <main className="login-card" aria-busy={!error}>
        {error ? (
          <>
            <p className="warn" role="alert">
              {error}
            </p>
            <button type="button" className="primary" onClick={onHome}>
              Vos espaces de travail
            </button>
          </>
        ) : (
          <p className="muted" role="status">
            <span className="spinner" aria-hidden="true" /> Ouverture du projet
            partagé…
          </p>
        )}
      </main>
    </div>
  );
}
