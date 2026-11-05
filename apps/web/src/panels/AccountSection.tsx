import { useEffect, useState } from "react";
import { authApi } from "../api/client";
import { useAccount } from "../state/account";
import { useHosting } from "../state/hosting";
import { PasswordBlock, SessionsBlock } from "./AccountPassword";
import { ExtensionTokens } from "./ExtensionTokens";

const MiB = 1024 * 1024;
const mo = (n: number) => `${Math.round(n / MiB)} Mo`;

/** B1d : dans les Réglages, l'adresse connectée, « Me déconnecter » et « Me déconnecter
 *  partout » (toutes les sessions de l'utilisateur). KAN-12 : ma place disque. Rien en mode
 *  local. KAN-104 : « Mot de passe » et « Sessions ouvertes », repliés comme l'extension
 *  (les Réglages de l'accueil restent entiers, KAN-110), lus à l'ouverture, seulement si le
 *  serveur ouvre le mot de passe ; l'administrateur (code seulement) n'a pas « Mot de passe ». */
export function AccountSection() {
  const account = useAccount();
  const passwordOpen = useHosting()?.passwordLogin ?? false;
  const [busy, setBusy] = useState(false);
  /** null : pas encore lu (bloc fermé) ; lu sur GET /auth/me à l'ouverture. */
  const [hasPassword, setHasPassword] = useState<boolean | null>(null);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [sessionsVersion, setSessionsVersion] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [usage, setUsage] = useState<{
    used_bytes: number;
    quota_bytes: number | null;
  } | null>(null);
  useEffect(() => {
    if (!account) return;
    authApi.usage().then(setUsage, () => setUsage(null));
  }, [account]);
  if (!account) return null;
  const run = (action: () => Promise<void>) => {
    setBusy(true);
    void action().finally(() => setBusy(false));
  };
  return (
    <section className="account-section" aria-label="Compte">
      <p className="muted">
        Connecté : <b>{account.me.email}</b>
      </p>
      {usage && (
        <div className="account-space">
          <span>
            Votre espace : {mo(usage.used_bytes)}
            {usage.quota_bytes !== null
              ? ` sur ${mo(usage.quota_bytes)}`
              : " (sans limite)"}
          </span>
          {usage.quota_bytes !== null && (
            <progress
              aria-label="Place utilisée"
              max={usage.quota_bytes}
              value={Math.min(usage.used_bytes, usage.quota_bytes)}
            />
          )}
        </div>
      )}
      {account.me.is_admin && (
        // KAN-109 : le back-office, pour l'administrateur seulement.
        <a className="account-admin" href="#/admin">
          Back-office
        </a>
      )}
      <div className="account-actions">
        <button
          type="button"
          disabled={busy}
          onClick={() => run(account.signOut)}
        >
          Me déconnecter
        </button>
        <button
          type="button"
          className="link"
          disabled={busy}
          title="Ferme toutes vos sessions, sur tous vos appareils"
          onClick={() => {
            if (
              window.confirm(
                passwordOpen
                  ? "Vous déconnecter sur tous vos appareils ? Il faudra un code ou votre mot de passe pour revenir."
                  : "Vous déconnecter sur tous vos appareils ? Il faudra un nouveau code pour revenir.",
              )
            )
              run(account.signOutAll);
          }}
        >
          Me déconnecter partout
        </button>
      </div>
      {notice && (
        <p className="account-notice" role="status">
          {notice}
        </p>
      )}
      {passwordOpen && !account.me.is_admin && (
        <details
          className="account-more"
          onToggle={(e) => {
            if ((e.currentTarget as HTMLDetailsElement).open && hasPassword === null)
              void authApi.me().then(
                (me) => setHasPassword(me?.has_password ?? false),
                () => setHasPassword(null),
              );
          }}
        >
          <summary>Mot de passe</summary>
          {hasPassword !== null && (
            <PasswordBlock
              hasPassword={hasPassword}
              onSet={(text) => {
                setHasPassword(true);
                setNotice(text);
                setSessionsVersion((v) => v + 1);
              }}
            />
          )}
        </details>
      )}
      {passwordOpen && (
        <details
          className="account-more"
          onToggle={(e) => {
            // Relue à chaque ouverture : une session ouverte ailleurs entre-temps y paraît.
            const open = (e.currentTarget as HTMLDetailsElement).open;
            setSessionsOpen(open);
            if (open) setSessionsVersion((v) => v + 1);
          }}
        >
          <summary>Sessions ouvertes</summary>
          {sessionsOpen && (
            <SessionsBlock version={sessionsVersion} onNotice={setNotice} />
          )}
        </details>
      )}
      <ExtensionTokens />
    </section>
  );
}
