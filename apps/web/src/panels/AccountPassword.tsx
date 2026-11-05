import { useCallback, useEffect, useState } from "react";
import { ApiError, authApi, type SessionOut } from "../api/client";
import {
  browserLabel,
  dayLabel,
  methodLabel,
  seenLabel,
} from "../state/sessionLabel";
import { PASSWORD_HINT, PasswordInput } from "../ui/PasswordInput";

/** KAN-104, Réglages › Compte : « Mot de passe » (le définir, après une preuve récente par
 *  code, ou le changer avec l'actuel) et « Sessions ouvertes » (fermer chacune). Le texte des
 *  refus est celui du serveur ; une erreur de saisie n'y est jamais un 401 (qui déconnecterait :
 *  test_kan104_password.py). Montrés seulement si le serveur ouvre le mot de passe. */

const OFFLINE = "Le serveur ne répond pas : réessayez dans un instant.";
const CLOSED_OTHERS = "Vos autres sessions ont été fermées.";

function serverText(e: unknown): string {
  if (!(e instanceof ApiError)) return OFFLINE;
  return e.message.charAt(0).toUpperCase() + e.message.slice(1);
}

type Stage = "idle" | "code" | "new";

/** `hasPassword` : lu sur GET /auth/me ; `onSet` : le compte en a un désormais. */
export function PasswordBlock({
  hasPassword,
  onSet,
}: {
  hasPassword: boolean;
  onSet: (notice: string) => void;
}) {
  const [stage, setStage] = useState<Stage>("idle");
  const [code, setCode] = useState("");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      // Preuve passée (plus de 10 min) : on revient au code, avec le texte du serveur.
      if (err instanceof ApiError && err.status === 403 && !hasPassword)
        setStage("idle");
      setError(serverText(err));
    } finally {
      setBusy(false);
    }
  };

  const done = (text: string) => {
    setStage("idle");
    setCode("");
    setCurrent("");
    setNext("");
    onSet(`${text} ${CLOSED_OTHERS}`);
  };

  const errorLine = error && (
    <p className="warn" role="alert">
      {error}
    </p>
  );
  const newField = (
    <>
      <label htmlFor="account-new-password">Nouveau mot de passe</label>
      <PasswordInput
        id="account-new-password"
        value={next}
        onChange={setNext}
        autoComplete="new-password"
        describedBy="account-password-hint"
      />
      <p id="account-password-hint" className="muted">
        {PASSWORD_HINT} Vos autres sessions seront fermées.
      </p>
    </>
  );

  if (hasPassword) {
    return (
      <form
        className="account-password"
        aria-label="Changer le mot de passe"
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            await authApi.setPassword(next, current);
            done("Mot de passe changé.");
          });
        }}
      >
        <label htmlFor="account-current-password">Mot de passe actuel</label>
        <PasswordInput
          id="account-current-password"
          value={current}
          onChange={setCurrent}
          autoComplete="current-password"
        />
        {newField}
        {errorLine}
        <div className="account-actions">
          <button type="submit" disabled={busy}>
            Changer le mot de passe
          </button>
        </div>
      </form>
    );
  }

  if (stage === "idle") {
    return (
      <div className="account-password">
        <p className="muted">
          Vous vous connectez par un code reçu par e-mail. Un mot de passe est
          facultatif. Pour confirmer que c'est bien vous, nous envoyons d'abord
          un code à votre adresse.
        </p>
        {errorLine}
        <div className="account-actions">
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await authApi.reauthStart();
                setCode("");
                setStage("code");
              })
            }
          >
            Recevoir un code de confirmation
          </button>
        </div>
      </div>
    );
  }

  if (stage === "code") {
    return (
      <form
        className="account-password"
        aria-label="Confirmer avec le code"
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            await authApi.reauthVerify(code);
            setStage("new");
          });
        }}
      >
        <p className="muted" role="status">
          Un code vient de partir vers votre adresse. Il expire dans 15 minutes.
        </p>
        <label htmlFor="account-reauth-code">Code à 6 chiffres</label>
        <input
          id="account-reauth-code"
          className="login-code"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9 ]{6,8}"
          maxLength={8}
          required
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        {errorLine}
        <div className="account-actions">
          <button type="button" className="link" onClick={() => setStage("idle")}>
            Annuler
          </button>
          <button type="submit" disabled={busy}>
            Confirmer
          </button>
        </div>
      </form>
    );
  }

  return (
    <form
      className="account-password"
      aria-label="Définir un mot de passe"
      onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          await authApi.setPassword(next);
          done("Mot de passe défini.");
        });
      }}
    >
      <p className="muted" role="status">
        C'est bien vous. Vous avez 10 minutes pour choisir le mot de passe.
      </p>
      {newField}
      {errorLine}
      <div className="account-actions">
        <button type="button" className="link" onClick={() => setStage("idle")}>
          Annuler
        </button>
        <button type="submit" disabled={busy}>
          Définir le mot de passe
        </button>
      </div>
    </form>
  );
}

/** Mes sessions ouvertes ; `version` relit la liste (après un mot de passe défini ou changé,
 *  il ne reste que celle-ci). */
export function SessionsBlock({
  version,
  onNotice,
}: {
  version: number;
  onNotice: (notice: string) => void;
}) {
  const [sessions, setSessions] = useState<SessionOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    authApi.sessions().then(
      (list) => {
        setSessions(list);
        setError(null);
      },
      (err: unknown) => setError(serverText(err)),
    );
  }, []);
  useEffect(load, [load, version]);

  const close = async (session: SessionOut) => {
    setBusy(session.id);
    setError(null);
    try {
      await authApi.closeSession(session.id);
      onNotice(`Session fermée : ${browserLabel(session.user_agent)}.`);
      load();
    } catch (err) {
      setError(serverText(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="account-sessions">
      {error && (
        <p className="warn" role="alert">
          {error}
        </p>
      )}
      {sessions && (
        <ul aria-label="Sessions ouvertes">
          {sessions.map((s) => (
            <li key={s.id}>
              <span>
                <b>{browserLabel(s.user_agent)}</b>
                <span className="muted">
                  {methodLabel(s.auth_method)} · ouverte le {dayLabel(s.created_at)}{" "}
                  · vue {seenLabel(s.last_used_at)}
                </span>
              </span>
              {s.current ? (
                <span className="account-sessions-here">Cette session</span>
              ) : (
                <button
                  type="button"
                  disabled={busy === s.id}
                  aria-label={`Fermer la session ${browserLabel(s.user_agent)}`}
                  onClick={() => void close(s)}
                >
                  Fermer
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
