import { useState } from "react";

/** KAN-104 : un champ de mot de passe, avec « Afficher » / « Masquer ». `autoComplete` dit au
 *  navigateur s'il doit proposer le mot de passe gardé (« current-password ») ou en proposer
 *  un nouveau (« new-password »). Le mot de passe ne quitte jamais ce champ que vers l'API. */
export function PasswordInput({
  id,
  value,
  onChange,
  autoComplete,
  describedBy,
  autoFocus = false,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "current-password" | "new-password";
  describedBy?: string;
  autoFocus?: boolean;
}) {
  const [shown, setShown] = useState(false);
  return (
    <span className="password-input">
      <input
        id={id}
        type={shown ? "text" : "password"}
        autoComplete={autoComplete}
        required
        maxLength={128}
        value={value}
        aria-describedby={describedBy}
        autoFocus={autoFocus} // NOSONAR S9379 : seul champ attendu à cette étape
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        className="link"
        aria-pressed={shown}
        aria-controls={id}
        onClick={() => setShown((s) => !s)}
      >
        {shown ? "Masquer" : "Afficher"}
      </button>
    </span>
  );
}

/** La règle du serveur (12 à 128 caractères), dite avant de taper. */
export const PASSWORD_HINT =
  "12 caractères au moins. Une phrase de quelques mots est plus facile à retenir.";
