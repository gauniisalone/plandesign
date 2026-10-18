import { useState } from "react";
import { useAccount } from "../state/account";

/** B1d : dans les Réglages, l'adresse connectée, « Me déconnecter » et « Me déconnecter
 *  partout » (toutes les sessions de l'utilisateur). Rien en mode local. */
export function AccountSection() {
  const account = useAccount();
  const [busy, setBusy] = useState(false);
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
