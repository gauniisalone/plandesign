import { useCallback, useEffect, useId, useState } from "react";
import {
  ApiError,
  invitationLink,
  shareApi,
  type InvitationOut,
  type MemberOut,
  type Role,
} from "../api/client";
import { formatDate } from "../model";
import { ROLE_LABEL } from "../state/account";

/** Menu « Partager » (B1e, propriétaire seulement). Un lien d'invitation par personne : rôle et
 *  durée choisis, lien montré UNE fois (le serveur n'en garde que l'empreinte) avec « Copier » ;
 *  il ne part par aucun e-mail, on le transmet soi-même. Liens en attente (révocables) et
 *  membres (rôle, retrait). KAN-55 : inviter une adresse avec un rôle (toujours « Invitation
 *  envoyée », compte ou non) ; invitations en attente, annulables, 14 jours.
 *  KAN-128 : deux sections distinctes, chacune avec son titre, la phrase qui dit QUI pourra
 *  entrer, son rôle et un seul bouton principal : « Inviter une personne » (e-mail, cette
 *  adresse seulement) puis « Lien d'invitation » (à transmettre soi-même ; « Copier » et
 *  « Désactiver »). Le lien public d'un rendu (KAN-101) reste hors de ce panneau. */

const INVITE_ROLES: Role[] = ["viewer", "commenter", "editor"];
const DAYS = [1, 7, 30] as const;

function text(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

export function ShareDialog({
  projectId,
  onClose,
}: {
  projectId: string;
  onClose: () => void;
}) {
  const [role, setRole] = useState<Role>("viewer");
  const [days, setDays] = useState<(typeof DAYS)[number]>(7);
  const [link, setLink] = useState<string | null>(null);
  // KAN-128 : l'identifiant du lien montré, pour « Désactiver » sans quitter la section.
  const [linkId, setLinkId] = useState<string | null>(null);
  const [linkOff, setLinkOff] = useState(false);
  const [copied, setCopied] = useState(false);
  const personTitle = useId();
  const linkTitle = useId();
  const [pending, setPending] = useState<InvitationOut[]>([]);
  const [members, setMembers] = useState<MemberOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // KAN-55 : invitation nominative (adresse et rôle).
  const [email, setEmail] = useState("");
  const [emailRole, setEmailRole] = useState<Role>("editor");
  const [sent, setSent] = useState<string | null>(null);

  const refresh = useCallback(() => {
    shareApi.invitations(projectId).then(setPending, (e) => setError(text(e)));
    shareApi.members(projectId).then(setMembers, (e) => setError(text(e)));
  }, [projectId]);
  useEffect(refresh, [refresh]);

  const create = async () => {
    setBusy(true);
    setError(null);
    setCopied(false);
    setLinkOff(false);
    try {
      const made = await shareApi.invite(projectId, role, days);
      setLink(invitationLink(made.token));
      setLinkId(made.id);
      refresh();
    } catch (e) {
      setError(text(e));
    } finally {
      setBusy(false);
    }
  };
  const inviteEmail = async () => {
    if (!email.trim()) return;
    setBusy(true);
    setError(null);
    setSent(null);
    try {
      const done = await shareApi.inviteEmail(
        projectId,
        email.trim(),
        emailRole,
      );
      setSent(done.detail);
      setEmail("");
      refresh();
    } catch (e) {
      setError(text(e));
    } finally {
      setBusy(false);
    }
  };
  const copy = async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setError(
        "Copie impossible : sélectionnez le lien et copiez-le vous-même.",
      );
    }
  };
  const invited = pending.filter((p) => p.email);
  const links = pending.filter((p) => !p.email);
  const act = (run: () => Promise<unknown>) => {
    setError(null);
    run().then(refresh, (e) => setError(text(e)));
  };
  /** Désactive un lien d'invitation ; si c'est celui qui est montré, il disparaît aussitôt. */
  const disable = (id: string) =>
    act(async () => {
      await shareApi.revoke(projectId, id);
      if (id === linkId) {
        setLink(null);
        setLinkId(null);
        setCopied(false);
        setLinkOff(true);
      }
    });

  return (
    <div className="dialog-backdrop" onPointerDown={onClose}>
      <section
        className="share-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Partager le projet"
        onPointerDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <header className="share-head">
          <h2>Partager</h2>
          <button
            type="button"
            className="icon"
            aria-label="Fermer"
            onClick={onClose}
          >
            ×
          </button>
        </header>

        <section className="share-section" aria-labelledby={personTitle}>
          <h3 id={personTitle}>Inviter une personne</h3>
          <p className="share-who">
            Elle reçoit un e-mail ; seule cette adresse pourra entrer.
          </p>
          <form
            className="share-new"
            aria-label="Inviter par e-mail"
            onSubmit={(e) => {
              e.preventDefault();
              void inviteEmail();
            }}
          >
            <label>
              Adresse e-mail
              <input
                type="email"
                autoComplete="off"
                maxLength={320}
                required
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  setSent(null);
                }}
              />
            </label>
            <label>
              Rôle
              <select
                aria-label="Rôle de l'invité"
                value={emailRole}
                onChange={(e) => setEmailRole(e.target.value as Role)}
              >
                {INVITE_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABEL[r]}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" className="primary" disabled={busy}>
              Inviter
            </button>
          </form>
          {sent && (
            <p className="muted" role="status">
              {sent}
            </p>
          )}
          {invited.length > 0 && (
            <>
              <h4>Invitations en attente</h4>
              <ul className="share-list" aria-label="Invitations en attente">
                {invited.map((p) => (
                  <li key={p.id}>
                    <span className="share-email">{p.email}</span>
                    <span className="muted">
                      {ROLE_LABEL[p.role]} · expire le{" "}
                      {formatDate(p.expires_at)}
                    </span>
                    <button
                      type="button"
                      className="link"
                      aria-label={`Annuler l'invitation de ${p.email}`}
                      onClick={() =>
                        act(() => shareApi.revoke(projectId, p.id))
                      }
                    >
                      Annuler
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>

        <section className="share-section" aria-labelledby={linkTitle}>
          <h3 id={linkTitle}>Lien d'invitation</h3>
          <p className="share-who">
            Toute personne qui a ce lien peut rejoindre le projet tant qu'il est
            actif.
          </p>
          <div className="share-new" role="group" aria-label="Nouveau lien">
            <label>
              Rôle
              <select
                aria-label="Rôle du lien"
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
              >
                {INVITE_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABEL[r]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Valable
              <select
                value={days}
                onChange={(e) =>
                  setDays(Number(e.target.value) as (typeof DAYS)[number])
                }
              >
                {DAYS.map((d) => (
                  <option key={d} value={d}>
                    {d === 1 ? "1 jour" : `${d} jours`}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => void create()}
            >
              Créer un lien d'invitation
            </button>
          </div>
          {link && linkId && (
            <div className="share-link">
              <input
                readOnly
                aria-label="Lien d'invitation à copier"
                value={link}
                onFocus={(e) => e.currentTarget.select()}
              />
              <span className="share-link-actions">
                <button type="button" onClick={() => void copy()}>
                  {copied ? "Copié" : "Copier"}
                </button>
                <button
                  type="button"
                  aria-label="Désactiver ce lien"
                  onClick={() => disable(linkId)}
                >
                  Désactiver
                </button>
              </span>
              <p className="muted">
                Ce lien n'est montré qu'une fois et sert à une seule personne :
                envoyez-le-lui vous-même.
              </p>
            </div>
          )}
          {linkOff && (
            <p className="muted" role="status">
              Lien désactivé : il ne permet plus d'entrer.
            </p>
          )}
          {links.length > 0 && (
            <>
              <h4>Liens en attente</h4>
              <ul className="share-list" aria-label="Liens en attente">
                {links.map((p) => (
                  <li key={p.id}>
                    <span>
                      {ROLE_LABEL[p.role]} · expire le{" "}
                      {formatDate(p.expires_at)}
                    </span>
                    <button
                      type="button"
                      className="link"
                      onClick={() => disable(p.id)}
                    >
                      Désactiver
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>

        <h3>Membres</h3>
        <ul className="share-list" aria-label="Membres">
          {members.map((m) => (
            <li key={m.user_id}>
              <span className="share-email">
                {m.email}
                {m.you ? " (vous)" : ""}
              </span>
              {m.you ? (
                <span className="muted">{ROLE_LABEL[m.role]}</span>
              ) : (
                <>
                  <select
                    aria-label={`Rôle de ${m.email}`}
                    value={m.role}
                    onChange={(e) =>
                      act(() =>
                        shareApi.setRole(
                          projectId,
                          m.user_id,
                          e.target.value as Role,
                        ),
                      )
                    }
                  >
                    {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
                      <option key={r} value={r}>
                        {ROLE_LABEL[r]}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="link"
                    onClick={() => {
                      if (window.confirm(`Retirer ${m.email} de ce projet ?`))
                        act(() => shareApi.remove(projectId, m.user_id));
                    }}
                  >
                    Retirer
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
        {error && (
          <p className="warn" role="alert">
            {error}
          </p>
        )}
      </section>
    </div>
  );
}
