import { useState } from "react";
import { ApiError, authApi, type Me } from "../api/client";
import { APP_NAME } from "../brand";
import { TERMS_URL } from "../state/hosting";
import { IconPlan } from "../ui/icons";

/** KAN-133 (MH6) : à la connexion, si la version acceptée des conditions d'utilisation n'est
 *  pas celle en vigueur (`terms_pending` de /auth/me), cet écran passe avant tout le reste :
 *  « J'accepte » enregistre la version montrée ; sinon, on peut se déconnecter. */

const OFFLINE = "Le serveur ne répond pas : réessayez dans un instant.";

export function TermsUpdate({
  me,
  onAccepted,
  onSignOut,
}: {
  me: Me;
  onAccepted: () => void;
  onSignOut: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const url = me.terms_url ?? TERMS_URL;

  const accept = async () => {
    setBusy(true);
    setError(null);
    try {
      await authApi.acceptTerms(me.terms_version ?? "");
      onAccepted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : OFFLINE);
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <main className="login-card" aria-labelledby="terms-title">
        <span className="brand-mark">
          <IconPlan />
        </span>
        <h1 id="terms-title">Conditions d'utilisation</h1>
        <p>
          Les conditions d'utilisation de {APP_NAME} ont changé. Pour
          continuer, lisez-les et acceptez la nouvelle version.
        </p>
        <p>
          <a href={url} target="_blank" rel="noopener">
            Lire les conditions d'utilisation
          </a>
        </p>
        {error && (
          <p className="warn" role="alert">
            {error}
          </p>
        )}
        <div className="terms-actions">
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() => void accept()}
          >
            {busy ? "Enregistrement…" : "J'accepte"}
          </button>
          <button type="button" className="link" onClick={onSignOut}>
            Me déconnecter
          </button>
        </div>
      </main>
    </div>
  );
}
