import { useState } from "react";
import { ApiError, authApi, type Me } from "../api/client";
import { APP_NAME } from "../brand";
import { ACCOUNTS_PRIVACY } from "../state/hosting";
import { IconPlan } from "../ui/icons";

/** B1d : sans session, la page de connexion et rien d'autre (note 17, § 5). Deux temps :
 *  l'adresse, puis le code à 6 chiffres reçu par e-mail (B1b2). La réponse à la demande est
 *  la même que l'adresse ait un compte ou non. Après le code, l'application reprend la page
 *  demandée (l'ancre de l'adresse ne change pas). */

const NEUTRAL = "Si cette adresse peut se connecter, un code vient de partir.";

function message(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 429)
      return "Trop de tentatives : réessayez dans une minute.";
    return e.message;
  }
  return "Le serveur ne répond pas : réessayez dans un instant.";
}

export function Login({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authApi.start(email.trim());
      setCode("");
      setStep("code");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 422
          ? "Cette adresse e-mail n'est pas valable."
          : message(err),
      );
    } finally {
      setBusy(false);
    }
  };

  const verify = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await authApi.verify(email.trim(), code));
    } catch (err) {
      setError(`Connexion impossible : ${message(err)}`);
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <main className="login-card" aria-label={`Se connecter à ${APP_NAME}`}>
        <span className="brand-mark">
          <IconPlan />
        </span>
        <h1>Se connecter à {APP_NAME}</h1>
        {step === "email" ? (
          <form onSubmit={(e) => void ask(e)}>
            <p className="muted">
              Vos designs et plans en un clic. Un code de connexion vous est
              envoyé par e-mail, sans mot de passe.
            </p>
            <label htmlFor="login-email">Adresse e-mail</label>
            <input
              id="login-email"
              type="email"
              autoComplete="email"
              required
              maxLength={320}
              value={email}
              autoFocus
              onChange={(e) => setEmail(e.target.value)}
            />
            {error && (
              <p className="warn" role="alert">
                {error}
              </p>
            )}
            <button type="submit" className="primary" disabled={busy}>
              {busy ? "Envoi…" : "Recevoir un code"}
            </button>
          </form>
        ) : (
          <form onSubmit={(e) => void verify(e)}>
            <p className="muted" role="status">
              {NEUTRAL} Il expire dans 15 minutes.
            </p>
            <label htmlFor="login-code">Code à 6 chiffres</label>
            <input
              id="login-code"
              className="login-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9 ]{6,8}"
              maxLength={8}
              required
              value={code}
              autoFocus
              onChange={(e) => setCode(e.target.value)}
            />
            {error && (
              <p className="warn" role="alert">
                {error}
              </p>
            )}
            <button type="submit" className="primary" disabled={busy}>
              {busy ? "Vérification…" : "Se connecter"}
            </button>
            <button
              type="button"
              className="link"
              onClick={() => {
                setStep("email");
                setError(null);
              }}
            >
              Changer d'adresse ou recevoir un nouveau code
            </button>
          </form>
        )}
      </main>
      <footer className="login-foot muted">{ACCOUNTS_PRIVACY}</footer>
    </div>
  );
}
