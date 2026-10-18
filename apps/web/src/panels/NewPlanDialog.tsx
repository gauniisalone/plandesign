import { useEffect, useRef, useState } from "react";
import type { NewPlanBody, NewPlanKind } from "../api/client";

/** Les trois sortes, avec les mots d'augus (T7e). */
const KINDS: { kind: NewPlanKind; label: string }[] = [
  { kind: "variant", label: "Variante de ce plan" },
  { kind: "storey", label: "Nouvel étage" },
  { kind: "empty", label: "Plan vide" },
];

/** « + Nouveau plan » (T7e) : une petite fenêtre à deux champs, un nom libre et une sorte ;
 *  pas de paragraphe (règle du moins de texte possible). Échap ou « Annuler » ferment. */
export function NewPlanDialog({
  busy,
  onCreate,
  onClose,
}: {
  busy: boolean;
  onCreate: (body: NewPlanBody) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<NewPlanKind>("variant");
  const nameRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    nameRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  const label = KINDS.find((k) => k.kind === kind)?.label ?? "";
  return (
    <div className="new-plan-backdrop" onPointerDown={onClose}>
      <form
        className="new-plan"
        role="dialog"
        aria-modal="true"
        aria-label="Nouveau plan"
        onPointerDown={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          onCreate({ name: name.trim() || label, kind });
        }}
      >
        <h2>Nouveau plan</h2>
        <label>
          Nom
          <input
            ref={nameRef}
            value={name}
            maxLength={60}
            placeholder={label}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          Sorte
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as NewPlanKind)}
          >
            {KINDS.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <div className="new-plan-actions">
          <button type="button" onClick={onClose}>
            Annuler
          </button>
          <button type="submit" className="primary" disabled={busy}>
            Créer
          </button>
        </div>
      </form>
    </div>
  );
}
