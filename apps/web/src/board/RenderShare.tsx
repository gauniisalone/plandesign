import { useCallback, useEffect, useState } from "react";
import { shareLinksApi, shareLinkUrl, type ShareLink } from "../api/client";

/** KAN-101 : « Partager » un rendu réaliste par un lien public, en lecture seule (l'image et
 *  ce qui l'a guidée, jamais le projet). Éditeurs seulement, et seulement si le serveur a
 *  OPENPLAN_SHARE_LINKS (sinon l'appelant ne monte pas ce bloc). Le lien n'est montré qu'à sa
 *  création (le serveur n'en garde que l'empreinte) ; la liste des liens actifs du projet
 *  permet de les révoquer d'un clic. */

function day(iso: string): string {
  return new Date(iso).toLocaleDateString("fr-FR", {
    day: "numeric",
    month: "short",
  });
}

export function RenderShare({
  projectId,
  requestId,
}: {
  projectId: string;
  requestId: string;
}) {
  const [showName, setShowName] = useState(true);
  const [links, setLinks] = useState<ShareLink[]>([]);
  const [made, setMade] = useState<{ id: string; url: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLinks(await shareLinksApi.list(projectId));
    } catch {
      /* serveur injoignable : on garde la dernière lecture */
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Un autre rendu : le lien tout juste créé n'est plus le sien.
  useEffect(() => {
    setMade(null);
    setCopied(false);
  }, [requestId]);

  const act = async (job: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await job();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const create = () =>
    act(async () => {
      const created = await shareLinksApi.create(projectId, requestId, showName);
      setMade({ id: created.id, url: shareLinkUrl(created.token) });
      setCopied(false);
      await refresh();
    });

  const revoke = (id: string) =>
    act(async () => {
      await shareLinksApi.revoke(projectId, id);
      if (made?.id === id) setMade(null);
      await refresh();
    });

  const copy = () => {
    if (!made) return;
    void navigator.clipboard?.writeText(made.url).then(
      () => setCopied(true),
      () => undefined,
    );
  };

  return (
    <>
      <h3 className="rview-h">Lien public</h3>
      <label>
        <input
          type="checkbox"
          checked={showName}
          onChange={(e) => setShowName(e.target.checked)}
        />{" "}
        Afficher le nom du projet
      </label>
      <button type="button" disabled={busy} onClick={() => void create()}>
        Partager
      </button>
      {made && (
        <>
          <input
            type="text"
            readOnly
            value={made.url}
            aria-label="Lien public du rendu"
            onFocus={(e) => e.currentTarget.select()}
          />
          <button type="button" onClick={copy}>
            {copied ? "Lien copié ✓" : "Copier le lien"}
          </button>
          <small>
            Lisible sans compte pendant 30 jours, ou jusqu'à sa révocation.
          </small>
        </>
      )}
      {links.length > 0 && (
        <>
          <h3 className="rview-h">Liens actifs du projet</h3>
          <ul className="rview-origin" aria-label="Liens publics actifs">
            {links.map((l) => (
              <li key={l.id}>
                {l.request_id === requestId ? "Ce rendu" : "Autre rendu"} ·
                expire le {day(l.expires_at)}{" "}
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Révoquer le lien du ${day(l.created_at)} (${l.request_id === requestId ? "ce rendu" : "autre rendu"})`}
                  onClick={() => void revoke(l.id)}
                >
                  Révoquer
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {error && (
        <p className="rview-error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}
