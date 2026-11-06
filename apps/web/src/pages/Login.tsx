import { useState } from "react";
import { ApiError, authApi, type Me } from "../api/client";
import { APP_NAME } from "../brand";
import {
  ACCOUNTS_LINE,
  ACCOUNTS_LINE_PASSWORD,
  PRIVACY_URL,
  TERMS_URL,
  useHosting,
} from "../state/hosting";
import { IconPlan } from "../ui/icons";
import { PASSWORD_HINT, PasswordInput } from "../ui/PasswordInput";

/** B1d : sans session, la page de connexion et rien d'autre (note 17, § 5). Deux temps :
 *  l'adresse, puis le code à 6 chiffres reçu par e-mail (B1b2). La réponse à la demande est
 *  la même que l'adresse ait un compte ou non. Après le code, l'application reprend la page
 *  demandée (l'ancre de l'adresse ne change pas). B1e : ouverte depuis un lien d'invitation,
 *  elle le dit (sans rien du projet) et passe le jeton, qui ouvre l'inscription.
 *  KAN-104 : si le serveur l'ouvre (`password_login` de /api/health), une seconde voie, le mot
 *  de passe, avec « Mot de passe oublié » (un code par e-mail, jamais un lien : D5). Le code
 *  reste la voie principale.
 *  KAN-133 : la case des conditions d'utilisation avant « Recevoir un code » (sans elle, une
 *  adresse sans compte ne reçoit rien ; la page ne sait pas si l'adresse a un compte, elle la
 *  demande donc à tous). Depuis une invitation, et si le mot de passe est ouvert, la personne
 *  invitée peut aussi créer son compte avec un mot de passe (écran 3 de la maquette KAN-104). */

const NEUTRAL = "Si cette adresse peut se connecter, un code vient de partir.";
/** Bêta sur invitation (PO, 19 oct.) : un visiteur non invité ne croit plus le site cassé,
 *  sans qu'on lui dise si son adresse a un compte. Seulement en mode « invite ». */
const BETA_LINE =
  "PlanDesign est en bêta, sur invitation. Déjà inscrit ou invité ? Connectez-vous.";
const NEUTRAL_INVITE =
  "Si cette adresse a un compte ou une invitation, un code vient de partir.";
const NEUTRAL_RESET =
  "Si cette adresse a un compte, un code vient de partir. Il expire dans 30 minutes.";
/** KAN-133 : la réponse de signup/start, la même dans tous les cas. */
const NEUTRAL_SIGNUP =
  "Si cette adresse peut créer un compte, un code vient de partir. Il expire dans 15 minutes.";
const OFFLINE = "Le serveur ne répond pas : réessayez dans un instant.";

function message(e: unknown): string {
  if (e instanceof ApiError) {
    // KAN-105 L2 : le texte du serveur tel quel, 429 compris (débit des tentatives : une
    // minute ; mot de passe : une heure ; quota du jour : le sien).
    return e.message;
  }
  return OFFLINE;
}

/** KAN-104 : pour le mot de passe, le texte du serveur tel quel, 429 compris (l'attente y est
 *  d'une heure, pas d'une minute) ; sa première lettre en capitale. */
function serverText(e: unknown): string {
  if (!(e instanceof ApiError)) return OFFLINE;
  return e.message.charAt(0).toUpperCase() + e.message.slice(1);
}

function emailError(e: unknown, otherwise: (e: unknown) => string): string {
  return e instanceof ApiError && e.status === 422
    ? "Cette adresse e-mail n'est pas valable."
    : otherwise(e);
}

type Step =
  | "email"
  | "code"
  | "name"
  | "password"
  | "forgot"
  | "reset"
  | "signup";
const PASSWORD_STEPS: ReadonlySet<Step> = new Set(["password", "forgot", "reset"]);
/** Les ancres qui ouvrent la page sur une étape du mot de passe : le lien de la fenêtre du
 *  site (LoginDialog.astro), et « oublié » ; effacées une fois connecté. */
const HASH_STEPS: Readonly<Record<string, Step>> = {
  "#/mot-de-passe": "password",
  "#/mot-de-passe-oublie": "forgot",
};

function clearStepHash(): void {
  if (window.location.hash in HASH_STEPS)
    window.history.replaceState(null, "", window.location.pathname);
}

export function Login({
  onSignedIn,
  invitation,
}: {
  onSignedIn: (me: Me) => void;
  /** Jeton d'un lien d'invitation (lu dans l'ancre). */
  invitation?: string;
}) {
  // KAN-104 : le lien « Se connecter avec un mot de passe » de la fenêtre du site arrive ici
  // sur cette étape (le site n'appelle aucune route du mot de passe) ; « oublié » de même.
  const [step, setStep] = useState<Step>(
    () => HASH_STEPS[window.location.hash] ?? "email",
  );
  /** B2a : compte tout juste créé ; « Votre nom » est proposé avant d'entrer. */
  const [fresh, setFresh] = useState<Me | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  /** KAN-133 : la case des conditions, envoyée à la demande du code et à sa vérification. */
  const [terms, setTerms] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Lu au démarrage (App attend /api/health avant cette page) : pas d'attente ici.
  const hosting = useHosting();
  const inviteOnly = hosting?.inviteOnly ?? false;
  const passwordOpen = hosting?.passwordLogin ?? false;
  // Une personne invitée n'a pas encore de compte : pas de connexion par mot de passe ; mais
  // (KAN-133) elle peut en créer un avec un mot de passe, ou continuer par code.
  const withPassword = passwordOpen && !invitation;
  const withSignup = passwordOpen && Boolean(invitation);

  const go = (next: Step) => {
    setError(null);
    setStep(next);
  };

  /** Une étape du mot de passe : `busy` le temps de la requête, l'erreur sous le bouton. */
  const run = async (
    action: () => Promise<void>,
    onError: (e: unknown) => string,
  ) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(onError(err));
    } finally {
      setBusy(false);
    }
  };

  const ask = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authApi.start(email.trim(), invitation, terms);
      setCode("");
      setStep("code");
    } catch (err) {
      setError(emailError(err, message));
    } finally {
      setBusy(false);
    }
  };

  const verify = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await authApi.verify(email.trim(), code, invitation, terms);
      if (me.first_login) {
        setFresh(me);
        setStep("name");
        setBusy(false);
        return;
      }
      onSignedIn(me);
    } catch (err) {
      setError(`Connexion impossible : ${message(err)}`);
      setBusy(false);
    }
  };

  const finish = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!fresh) return;
    const typed = name.trim();
    if (!typed) return onSignedIn(fresh);
    setBusy(true);
    try {
      onSignedIn(await authApi.setName(typed));
    } catch {
      onSignedIn(fresh); // le nom se redonne plus tard ; on entre quand même
    }
  };

  const signInWithPassword = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const me = await authApi.passwordLogin(email.trim(), password);
      clearStepHash();
      onSignedIn(me);
    }, serverText);
  };

  /** KAN-133 : « Créer un compte avec un mot de passe » (même formulaire que le code : la
   *  case et l'adresse y sont vérifiées par le navigateur avant l'envoi). */
  const askSignup = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (!e.currentTarget.form?.reportValidity()) return;
    void run(
      async () => {
        await authApi.signupStart(email.trim(), invitation);
        setCode("");
        setPassword("");
        setStep("signup");
      },
      (err) => emailError(err, serverText),
    );
  };

  const signup = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const me = await authApi.signupVerify(
        email.trim(),
        code,
        password,
        name.trim(),
        invitation,
      );
      onSignedIn(me);
    }, serverText);
  };

  const askReset = (e: React.FormEvent) => {
    e.preventDefault();
    void run(
      async () => {
        await authApi.forgot(email.trim());
        setCode("");
        setPassword("");
        setStep("reset");
      },
      (err) => emailError(err, serverText),
    );
  };

  const reset = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const me = await authApi.reset(email.trim(), code, password);
      clearStepHash(); // arrivé par le lien du site : l'accueil, sans l'ancre de l'étape
      onSignedIn(me);
    }, serverText);
  };

  const errorLine = error && (
    <p className="warn" role="alert">
      {error}
    </p>
  );
  const emailField = (
    <>
      <label htmlFor="login-email">Adresse e-mail</label>
      <input
        id="login-email"
        type="email"
        autoComplete={step === "password" ? "username" : "email"}
        required
        maxLength={320}
        value={email}
        autoFocus // NOSONAR S9379 : premier champ de l'étape
        onChange={(e) => setEmail(e.target.value)}
      />
    </>
  );
  const codeField = (
    <>
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
        autoFocus // NOSONAR S9379 : le code attendu juste après l'envoi
        onChange={(e) => setCode(e.target.value)}
      />
    </>
  );
  const termsBox = (
    <label className="login-terms">
      <input
        type="checkbox"
        required
        checked={terms}
        onChange={(e) => setTerms(e.target.checked)}
      />
      <span>
        J'accepte les{" "}
        <a href={TERMS_URL} target="_blank" rel="noopener">
          conditions d'utilisation
        </a>
      </span>
    </label>
  );

  // Mot de passe fermé sur ce serveur (ou depuis une invitation) : jamais ses étapes, même
  // arrivé par le lien du site.
  const shown: Step =
    (PASSWORD_STEPS.has(step) && !withPassword) || (step === "signup" && !withSignup)
      ? "email"
      : step;
  let body: React.ReactNode;
  if (shown === "name") {
    body = (
      <form onSubmit={(e) => void finish(e)}>
        <p className="muted">
          C'est ce que verront les autres membres de vos projets. Sans nom, le
          début de votre adresse.
        </p>
        <label htmlFor="login-name">Votre nom (facultatif)</label>
        <input
          id="login-name"
          autoComplete="name"
          maxLength={80}
          value={name}
          autoFocus // NOSONAR S9379 : seul champ de l'étape « Votre nom »
          onChange={(e) => setName(e.target.value)}
        />
        <button type="submit" className="primary" disabled={busy}>
          Continuer
        </button>
        <button
          type="button"
          className="link"
          onClick={() => fresh && onSignedIn(fresh)}
        >
          Passer
        </button>
      </form>
    );
  } else if (shown === "email") {
    body = (
      <form onSubmit={(e) => void ask(e)}>
        {invitation ? (
          <p className="login-invite" role="status">
            Vous avez été invité à un projet {APP_NAME} : connectez-vous avec
            votre adresse e-mail pour l'ouvrir.
          </p>
        ) : inviteOnly ? (
          <p className="login-beta">{BETA_LINE}</p>
        ) : (
          <p className="muted">Vos designs et plans en un clic.</p>
        )}
        {emailField}
        {termsBox}
        {errorLine}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Envoi…" : "Recevoir un code"}
        </button>
        {(withPassword || withSignup) && (
          <span className="login-or" aria-hidden="true">
            ou
          </span>
        )}
        {withPassword && (
          <button
            type="button"
            className="link"
            onClick={() => go("password")}
          >
            Se connecter avec un mot de passe
          </button>
        )}
        {withSignup && (
          <button
            type="button"
            className="link"
            disabled={busy}
            onClick={askSignup}
          >
            Créer un compte avec un mot de passe
          </button>
        )}
      </form>
    );
  } else if (shown === "code") {
    body = (
      <form onSubmit={(e) => void verify(e)}>
        <p className="muted" role="status">
          {inviteOnly ? NEUTRAL_INVITE : NEUTRAL} Il expire dans 15 minutes.
        </p>
        <p className="muted login-spam">
          Pas reçu ? Regardez dans vos courriers indésirables.
        </p>
        {codeField}
        {errorLine}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Vérification…" : "Se connecter"}
        </button>
        <button type="button" className="link" onClick={() => go("email")}>
          Changer d'adresse ou recevoir un nouveau code
        </button>
      </form>
    );
  } else if (shown === "signup") {
    body = (
      <form onSubmit={signup}>
        <h2 className="login-step">Créer votre compte</h2>
        <p className="muted" role="status">
          {NEUTRAL_SIGNUP}
        </p>
        <p className="muted login-spam">
          Pas reçu ? Regardez dans vos courriers indésirables.
        </p>
        {codeField}
        <label htmlFor="login-new-password">Mot de passe</label>
        <PasswordInput
          id="login-new-password"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          describedBy="login-password-hint"
        />
        <p id="login-password-hint" className="muted">
          {PASSWORD_HINT}
        </p>
        <label htmlFor="login-name">Votre nom (facultatif)</label>
        <input
          id="login-name"
          autoComplete="name"
          maxLength={80}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        {errorLine}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Vérification…" : "Créer mon compte"}
        </button>
        <button type="button" className="link" onClick={() => go("email")}>
          Recevoir un code plutôt
        </button>
      </form>
    );
  } else if (shown === "password") {
    body = (
      <form onSubmit={signInWithPassword}>
        {emailField}
        <label htmlFor="login-password">Mot de passe</label>
        <PasswordInput
          id="login-password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
        />
        {errorLine}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Vérification…" : "Se connecter"}
        </button>
        <button type="button" className="link" onClick={() => go("forgot")}>
          Mot de passe oublié ?
        </button>
        <button type="button" className="link" onClick={() => go("email")}>
          Recevoir un code plutôt
        </button>
      </form>
    );
  } else if (shown === "forgot") {
    body = (
      <form onSubmit={askReset}>
        <h2 className="login-step">Mot de passe oublié</h2>
        <p className="muted">
          Nous envoyons un code à votre adresse pour en choisir un nouveau.
        </p>
        {emailField}
        {errorLine}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Envoi…" : "Recevoir un code"}
        </button>
        <button type="button" className="link" onClick={() => go("password")}>
          Retour à la connexion
        </button>
      </form>
    );
  } else {
    body = (
      <form onSubmit={reset}>
        <h2 className="login-step">Nouveau mot de passe</h2>
        <p className="muted" role="status">
          {NEUTRAL_RESET}
        </p>
        <p className="muted login-spam">
          Pas reçu ? Regardez dans vos courriers indésirables.
        </p>
        {codeField}
        <label htmlFor="login-new-password">Nouveau mot de passe</label>
        <PasswordInput
          id="login-new-password"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          describedBy="login-password-hint"
        />
        <p id="login-password-hint" className="muted">
          {PASSWORD_HINT} Vos autres sessions seront fermées.
        </p>
        {errorLine}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Vérification…" : "Changer le mot de passe et me connecter"}
        </button>
        <button type="button" className="link" onClick={() => go("forgot")}>
          Recevoir un nouveau code
        </button>
      </form>
    );
  }

  return (
    <div className="login">
      <main className="login-card" aria-label={`Se connecter à ${APP_NAME}`}>
        <span className="brand-mark">
          <IconPlan />
        </span>
        <h1>Se connecter à {APP_NAME}</h1>
        {body}
      </main>
      <footer className="login-foot muted">
        {withPassword || withSignup ? ACCOUNTS_LINE_PASSWORD : ACCOUNTS_LINE}{" "}
        <a href={PRIVACY_URL} target="_blank" rel="noopener">
          Confidentialité
        </a>{" "}
        ·{" "}
        <a href={TERMS_URL} target="_blank" rel="noopener">
          Conditions d'utilisation
        </a>
      </footer>
    </div>
  );
}
