import { useState } from "react";
import { adminApi, type AccountOffer, type Offer } from "../api/client";

/** KAN-126b : l'offre d'un compte, posée par l'administrateur (aucun paiement). La recherche se
 *  fait par l'adresse, envoyée dans le corps (jamais dans l'URL) ; la liste des comptes n'est
 *  pas affichée. Pro par défaut pour tous : rien ne change tant qu'on n'a rien posé ici. */

const LABEL: Record<Offer, string> = { essentiel: "Essentiel", pro: "Pro" };

export function AdminOffer() {
  const [email, setEmail] = useState("");
  const [found, setFound] = useState<AccountOffer | null>(null);
  const [said, setSaid] = useState<{ text: string; failed?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  const find = async () => {
    setBusy(true);
    setSaid(null);
    setFound(null);
    try {
      setFound(await adminApi.findUser(email.trim()));
    } catch (e) {
      setSaid({ text: e instanceof Error ? e.message : String(e), failed: true });
    } finally {
      setBusy(false);
    }
  };
  const choose = async (offer: Offer) => {
    if (!found || offer === found.offer) return;
    setBusy(true);
    setSaid(null);
    try {
      const done = await adminApi.setOffer(found.id, offer);
      setFound(done);
      setSaid({ text: `Offre ${LABEL[done.offer]} posée.` });
    } catch (e) {
      setSaid({ text: e instanceof Error ? e.message : String(e), failed: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="admin-card" aria-label="Offre d'un compte">
      <div className="admin-head">
        <h2>Offre d'un compte</h2>
      </div>
      <p className="admin-note">
        Essentiel ou Pro, sans paiement. Tous les comptes sont en Pro tant que rien
        n'est posé ici ; le changement vaut dès la requête suivante du compte.
      </p>
      <form
        className="admin-offer-find"
        onSubmit={(e) => {
          e.preventDefault();
          if (email.trim()) void find();
        }}
      >
        <label>
          Adresse du compte
          <input
            type="email"
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <button type="submit" disabled={busy || !email.trim()}>
          Chercher
        </button>
      </form>
      {found && (
        <div className="admin-offer-row" role="group" aria-label="Offre">
          <span>{found.email}</span>
          {(["essentiel", "pro"] as const).map((o) => (
            <button
              key={o}
              type="button"
              aria-pressed={found.offer === o}
              disabled={busy}
              onClick={() => void choose(o)}
            >
              {LABEL[o]}
            </button>
          ))}
        </div>
      )}
      {said && (
        <p
          className={`admin-note${said.failed ? " admin-alert" : ""}`}
          role={said.failed ? "alert" : "status"}
        >
          {said.text}
        </p>
      )}
    </section>
  );
}
