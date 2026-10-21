import { useState } from "react";
import { ApiError, myInvitationsApi, type MyInvitation } from "../api/client";
import { invitationText } from "../state/myInvitations";

/** KAN-55 : les invitations nominatives qui m'attendent, en tête de la cloche (comptes
 *  seulement). « Fanny vous invite sur « Grenier » (éditeur) », avec « Accepter » (je deviens
 *  membre avec ce rôle et le projet s'ouvre) et « Refuser ». */

export function BellInvitations({
  list,
  onDone,
}: {
  list: MyInvitation[];
  /** Après Accepter ou Refuser : relire ; `projectId` quand le projet doit s'ouvrir. */
  onDone: (projectId: string | null) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (list.length === 0) return null;

  const act = async (i: MyInvitation, accept: boolean) => {
    setBusy(i.id);
    setError(null);
    try {
      if (accept) {
        const joined = await myInvitationsApi.accept(i.id);
        onDone(joined.project_id);
      } else {
        await myInvitationsApi.decline(i.id);
        onDone(null);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      onDone(null);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="bell-invites" aria-label="Invitations">
      <ul className="bell-lines">
        {list.map((i) => (
          <li key={i.id} className="bell-invite">
            <span className="bell-avatar" aria-hidden="true">
              {i.inviter_name.slice(0, 1).toUpperCase()}
            </span>
            <span className="bell-text">
              <span className="bell-action">{invitationText(i)}</span>
              <span className="bell-invite-actions">
                <button
                  type="button"
                  className="primary"
                  disabled={busy !== null}
                  onClick={() => void act(i, true)}
                >
                  Accepter
                </button>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void act(i, false)}
                >
                  Refuser
                </button>
              </span>
            </span>
          </li>
        ))}
      </ul>
      {error && (
        <p className="warn" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
