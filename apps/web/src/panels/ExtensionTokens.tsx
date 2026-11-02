import { useState, type FormEvent } from "react";
import { ApiError, extensionApi, type ExtensionToken } from "../api/client";

const day = (iso: string) =>
  new Date(iso).toLocaleDateString("fr-FR", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

/** KAN-102 : dans la section Compte, les jetons du module navigateur « Ajouter à
 *  PlanDesign » : créer (le jeton n'est montré qu'une fois), lister, révoquer. Fermé par
 *  défaut (une ligne) : les Réglages de l'accueil restent entiers (KAN-110) ; la liste n'est
 *  lue qu'à l'ouverture. */
export function ExtensionTokens() {
  const [tokens, setTokens] = useState<ExtensionToken[] | null>(null);
  const [name, setName] = useState("");
  const [fresh, setFresh] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    extensionApi.list().then(setTokens, (e: unknown) => {
      setError(e instanceof ApiError ? e.message : String(e));
    });

  const run = (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    void action()
      .catch((e: unknown) =>
        setError(e instanceof ApiError ? e.message : String(e)),
      )
      .finally(() => setBusy(false));
  };

  const create = (event: FormEvent) => {
    event.preventDefault();
    run(async () => {
      const made = await extensionApi.create(name);
      setFresh(made.secret);
      setCopied(false);
      setName("");
      await load();
    });
  };

  const revoke = (token: ExtensionToken) => {
    if (
      !window.confirm(
        `Révoquer « ${token.name} » ? L'extension qui l'utilise ne pourra plus rien ajouter.`,
      )
    )
      return;
    run(async () => {
      await extensionApi.revoke(token.id);
      await load();
    });
  };

  const copy = () => {
    if (!fresh) return;
    void navigator.clipboard?.writeText(fresh).then(
      () => setCopied(true),
      () => setCopied(false),
    );
  };

  return (
    <details
      className="account-extension"
      onToggle={(e) => {
        if ((e.currentTarget as HTMLDetailsElement).open && tokens === null)
          void load();
      }}
    >
      <summary>Extension navigateur</summary>
      <p className="muted">
        Le module « Ajouter à PlanDesign » envoie une image du web dans un
        Moodboard, d'un clic droit. Son jeton ne sert qu'à cela.
      </p>
      {fresh && (
        <div className="account-extension-fresh" role="status">
          <p>Copiez ce jeton dans l'extension : il ne sera plus affiché.</p>
          <div className="account-extension-row">
            <input
              readOnly
              value={fresh}
              aria-label="Nouveau jeton de l'extension"
              onFocus={(e) => e.currentTarget.select()}
            />
            <button type="button" onClick={copy}>
              {copied ? "Copié" : "Copier"}
            </button>
          </div>
        </div>
      )}
      {tokens && tokens.length > 0 && (
        <ul className="account-extension-list" aria-label="Jetons de l'extension">
          {tokens.map((t) => (
            <li key={t.id}>
              <span className="account-extension-name">{t.name}</span>
              <span className="muted">
                créé le {day(t.created_at)} ·{" "}
                {t.last_used_at
                  ? `utilisé le ${day(t.last_used_at)}`
                  : "jamais utilisé"}
              </span>
              <button
                type="button"
                className="link"
                disabled={busy}
                aria-label={`Révoquer ${t.name}`}
                onClick={() => revoke(t)}
              >
                Révoquer
              </button>
            </li>
          ))}
        </ul>
      )}
      {tokens && tokens.length === 0 && (
        <p className="muted">Aucun jeton pour l'instant.</p>
      )}
      <form className="account-extension-row" onSubmit={create}>
        <input
          value={name}
          maxLength={60}
          placeholder="Nom (ex. Chrome du bureau)"
          aria-label="Nom du jeton"
          onChange={(e) => setName(e.target.value)}
        />
        <button type="submit" disabled={busy}>
          Créer un jeton pour l'extension
        </button>
      </form>
      {error && (
        <p className="danger" role="alert">
          {error}
        </p>
      )}
    </details>
  );
}
