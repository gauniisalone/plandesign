import { useState } from "react";
import {
  ApiError,
  authApi,
  signalSignedOut,
  type DeletePreview,
} from "../api/client";

/** KAN-132, Réglages › Compte : « Mes données » (archive préparée en tâche de fond, lien par
 *  e-mail, 24 heures) et « Supprimer mon compte » (aperçu, choix du nouveau propriétaire de
 *  chaque projet partagé, code reçu par e-mail, adresse retapée). Montrés seulement si le
 *  serveur les ouvre (OPENPLAN_ACCOUNT_SELF_SERVICE). Les refus sont le texte du serveur. */

const OFFLINE = "Le serveur ne répond pas : réessayez dans un instant.";
const ROLE: Record<string, string> = {
  viewer: "lecteur",
  commenter: "commentateur",
  editor: "éditeur",
  owner: "propriétaire",
};

function serverText(e: unknown): string {
  if (!(e instanceof ApiError)) return OFFLINE;
  return e.message.charAt(0).toUpperCase() + e.message.slice(1);
}

export function ExportBlock() {
  const [busy, setBusy] = useState(false);
  const [line, setLine] = useState<string | null>(null);
  return (
    <div className="account-password">
      <p className="muted">
        Une archive de votre compte : vos projets (photos, plans, rendus), vos
        messages dans les projets des autres et la mesure de l'usage. Elle se
        prépare en quelques minutes ; le lien arrive par e-mail et vaut 24
        heures.
      </p>
      {line && (
        <p className="account-notice" role="status">
          {line}
        </p>
      )}
      <div className="account-actions">
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            authApi
              .exportData()
              .then((r) => setLine(r.detail), (e) => setLine(serverText(e)))
              .finally(() => setBusy(false));
          }}
        >
          Exporter mes données
        </button>
      </div>
    </div>
  );
}

export function DeleteBlock({
  email,
  preview,
}: {
  email: string;
  preview: DeletePreview;
}) {
  const [heirs, setHeirs] = useState<Record<string, string>>(() =>
    Object.fromEntries(preview.shared.map((p) => [p.id, p.heir])),
  );
  const [stage, setStage] = useState<"idle" | "code">("idle");
  const [code, setCode] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    action()
      .catch((e: unknown) => setError(serverText(e)))
      .finally(() => setBusy(false));
  };

  if (preview.refusal)
    return (
      <div className="account-password">
        <p className="muted">{preview.refusal}</p>
      </div>
    );

  return (
    <form
      className="account-password"
      aria-label="Supprimer mon compte"
      onSubmit={(e) => {
        e.preventDefault();
        run(async () => {
          await authApi.deleteAccount(code, typed, heirs);
          signalSignedOut();
        });
      }}
    >
      <p className="muted">
        La suppression est définitive. Vos messages, réactions et rendus dans
        les projets des autres restent, signés « Ancien membre ».
      </p>
      {preview.deleted.length > 0 && (
        <p>
          Effacés avec leurs fichiers :{" "}
          {preview.deleted.map((p) => `« ${p.name} »`).join(", ")}.
        </p>
      )}
      {preview.shared.map((p) => (
        <div key={p.id}>
          <label htmlFor={`account-heir-${p.id}`}>
            Nouveau propriétaire de « {p.name} »
          </label>
          <select
            id={`account-heir-${p.id}`}
            value={heirs[p.id]}
            onChange={(e) => setHeirs({ ...heirs, [p.id]: e.target.value })}
          >
            {p.members.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.name} ({ROLE[m.role] ?? m.role})
              </option>
            ))}
          </select>
        </div>
      ))}
      {stage === "idle" ? (
        <div className="account-actions">
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              run(async () => {
                await authApi.deleteCode();
                setStage("code");
              })
            }
          >
            Recevoir un code de confirmation
          </button>
        </div>
      ) : (
        <>
          <p className="muted" role="status">
            Un code vient de partir vers votre adresse. Il expire dans 15
            minutes.
          </p>
          <label htmlFor="account-delete-code">Code à 6 chiffres</label>
          <input
            id="account-delete-code"
            className="login-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9 ]{6,8}"
            maxLength={8}
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          <label htmlFor="account-delete-email">
            Retapez votre adresse ({email})
          </label>
          <input
            id="account-delete-email"
            type="email"
            autoComplete="off"
            required
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
          <div className="account-actions">
            <button
              type="button"
              className="link"
              onClick={() => setStage("idle")}
            >
              Annuler
            </button>
            <button type="submit" className="danger" disabled={busy}>
              Supprimer définitivement mon compte
            </button>
          </div>
        </>
      )}
      {error && (
        <p className="warn" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
