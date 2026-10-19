import { useEffect, useState } from "react";
import { authApi } from "../api/client";
import { useAccount } from "../state/account";

const MiB = 1024 * 1024;
const mo = (n: number) => `${Math.round(n / MiB)} Mo`;

/** B1d : dans les Réglages, l'adresse connectée, « Me déconnecter » et « Me déconnecter
 *  partout » (toutes les sessions de l'utilisateur). KAN-12 : ma place disque. Rien en mode
 *  local. */
export function AccountSection() {
  const account = useAccount();
  const [busy, setBusy] = useState(false);
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
                "Vous déconnecter sur tous vos appareils ? Il faudra un nouveau code pour revenir.",
              )
            )
              run(account.signOutAll);
          }}
        >
          Me déconnecter partout
        </button>
      </div>
    </section>
  );
}
