import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  calageApi,
  type CalageAction,
  type CalageAnswer,
  type CalageButton,
  type CalageField,
  type CalageQuestion,
  type CalageReason,
} from "../api/client";
import { decimal } from "../format";

/** KAN-124 (phase 2) : le panneau « Assistant » du parcours guidé (maquette KAN-123, écran 2),
 *  à côté de l'espace, seulement si OPENPLAN_ASSISTANT est allumé (/api/health).
 *
 *  L'app calcule ce qui manque (/calage/missing) ; la réponse libre part au modèle
 *  (/calage/answer), qui ne fait que l'extraire ; l'app montre sa proposition avec SES boutons
 *  (Oui / Corriger / Passer). « Oui » lance le cas d'usage existant du registre : le recalage
 *  par la même action que la carte « Recalibrer » (un pas d'annulation), les autres par leur
 *  route. Sans modèle, en erreur, trop lent ou incompris : le formulaire classique, raison dite.
 *  Le style et le budget attendent un champ où les garder (phase 3). */

const REASONS: Record<Exclude<CalageReason, "">, string> = {
  absent: "L'assistant n'est pas branché : répondez avec le formulaire.",
  indisponible: "L'assistant ne répond pas : répondez avec le formulaire.",
  trop_lent: "L'assistant met trop de temps : répondez avec le formulaire.",
  sortie_invalide: "L'assistant n'a pas compris : répondez avec le formulaire.",
};

const RECALIBRATE = "plan.recalibrate.dimensions:post";

export interface DimensionsBodyLike {
  length_m?: number;
  width_m?: number;
  height_m?: number;
}

type Step =
  | { kind: "ask" }
  | { kind: "answer"; answer: CalageAnswer }
  | { kind: "done"; text: string };

function readMetres(text: string): number | null {
  const found = text.replace(",", ".").match(/\d+(?:\.\d+)?/);
  return found ? Number(found[0]) : null;
}

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : "Le serveur ne répond pas.";
}

export function CalagePanel({
  projectId,
  levelId,
  revision,
  busy,
  onRecalibrate,
  onRan,
}: {
  projectId: string;
  levelId: string;
  /** Change à chaque modification du projet : les questions sont relues. */
  revision: number;
  busy: boolean;
  onRecalibrate: (body: DimensionsBodyLike) => void;
  /** Une autre action du registre a été lancée (régénérer, rendre) : relire le projet. */
  onRan: () => void;
}) {
  const [questions, setQuestions] = useState<CalageQuestion[] | null>(null);
  const [steps, setSteps] = useState<Partial<Record<CalageField, Step>>>({});
  const [drafts, setDrafts] = useState<Partial<Record<CalageField, string>>>({});
  const [previous, setPrevious] = useState<Partial<Record<CalageField, unknown>>>({});
  const [sending, setSending] = useState<CalageField | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    calageApi.missing(projectId, levelId).then(
      (r) => alive && setQuestions(r.questions),
      () => alive && setQuestions([]),
    );
    return () => {
      alive = false;
    };
  }, [projectId, levelId, revision]);

  const setStep = (field: CalageField, step: Step) =>
    setSteps((s) => ({ ...s, [field]: step }));

  const send = useCallback(
    async (field: CalageField, body: { message?: string; skip?: boolean }) => {
      setSending(field);
      setError(null);
      try {
        const answer = await calageApi.answer(projectId, levelId, {
          field,
          ...body,
          previous: previous[field] ?? null,
        });
        setStep(field, { kind: "answer", answer });
      } catch (e) {
        setError(message(e));
      } finally {
        setSending(null);
      }
    },
    [projectId, levelId, previous],
  );

  const run = async (field: CalageField, action: CalageAction, text: string) => {
    if (action.use_case === RECALIBRATE) {
      onRecalibrate(action.body as DimensionsBodyLike);
    } else {
      try {
        await calageApi.run(action);
        onRan();
      } catch (e) {
        setError(message(e));
        return;
      }
    }
    setStep(field, { kind: "done", text });
  };

  const press = (q: CalageQuestion, answer: CalageAnswer, b: CalageButton) => {
    const proposal = answer.proposal;
    if (b.kind === "skip") return void send(q.field, { skip: true });
    if (b.kind === "correct") {
      setPrevious((p) => ({ ...p, [q.field]: proposal?.value ?? null }));
      setDrafts((d) => ({ ...d, [q.field]: "" }));
      return setStep(q.field, { kind: "ask" });
    }
    if (b.action) return void run(q.field, b.action, proposal?.text ?? "");
    setStep(q.field, { kind: "done", text: proposal?.text ?? q.assumption });
  };

  /** Le formulaire classique (repli) : la valeur saisie, revalidée par la route existante. */
  const classic = (q: CalageQuestion, raw: string) => {
    const value = readMetres(raw);
    if (value === null || q.minimum === null || q.maximum === null) return;
    if (value < q.minimum || value > q.maximum) {
      setError(
        `Entre ${decimal(q.minimum)} et ${decimal(q.maximum)} m, s'il vous plaît.`,
      );
      return;
    }
    setError(null);
    onRecalibrate(
      q.field === "hauteur_plafond" ? { height_m: value } : { width_m: value },
    );
    setStep(q.field, { kind: "done", text: `${decimal(value)} m, enregistré.` });
  };

  if (questions === null) return null;
  const open = questions.filter((q) => steps[q.field]?.kind !== "done");
  return (
    <aside className="calage" aria-label="Assistant">
      <div className="calage-head">
        <span className="calage-dot" aria-hidden="true" />
        Assistant
      </div>
      <div className="calage-body">
        <p className="calage-msg">
          {open.length === 0
            ? "Rien ne manque pour le plan."
            : `Il me manque ${open.length} chose${open.length > 1 ? "s" : ""} pour un plan juste. Tout est facultatif.`}
        </p>
        {error && (
          <p className="warn" role="alert">
            {error}
          </p>
        )}
        {questions.map((q) => {
          const step = steps[q.field] ?? { kind: "ask" };
          if (step.kind === "done")
            return (
              <p key={q.field} className="calage-done" role="status">
                {step.text}
              </p>
            );
          return (
            <section key={q.field} className="calage-q" aria-label={q.text}>
              <div className="calage-ql">
                {q.text} <span>{q.reason}</span>
              </div>
              {step.kind === "answer" && step.answer.proposal ? (
                <>
                  <p className="calage-proposal" role="status">
                    {step.answer.proposal.text}
                  </p>
                  <div className="calage-row">
                    {step.answer.proposal.buttons.map((b) => (
                      <button
                        key={b.label}
                        type="button"
                        className={b.kind === "confirm" ? "primary small" : "ghost small"}
                        disabled={busy || sending !== null}
                        onClick={() => press(q, step.answer, b)}
                      >
                        {b.label}
                      </button>
                    ))}
                  </div>
                </>
              ) : step.kind === "answer" ? (
                <ClassicForm
                  question={q}
                  reason={step.answer.reason}
                  busy={busy}
                  onSubmit={(raw) => classic(q, raw)}
                  onChoose={(label) => setStep(q.field, { kind: "done", text: `Pièce : ${label}.` })}
                  onSkip={() => void send(q.field, { skip: true })}
                />
              ) : (
                <form
                  className="calage-row"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const text = (drafts[q.field] ?? "").trim();
                    if (text) void send(q.field, { message: text });
                  }}
                >
                  <input
                    className="calage-input"
                    aria-label={`Votre réponse : ${q.text}`}
                    placeholder={q.unit === "m" ? "ex. 2,50 m" : "Votre réponse"}
                    maxLength={500}
                    value={drafts[q.field] ?? ""}
                    onChange={(e) =>
                      setDrafts((d) => ({ ...d, [q.field]: e.target.value }))
                    }
                  />
                  <button
                    type="submit"
                    className="small"
                    disabled={busy || sending !== null}
                  >
                    {sending === q.field ? "…" : "Envoyer"}
                  </button>
                  {q.skippable && (
                    <button
                      type="button"
                      className="ghost small"
                      disabled={sending !== null}
                      onClick={() => void send(q.field, { skip: true })}
                    >
                      Passer
                    </button>
                  )}
                </form>
              )}
            </section>
          );
        })}
      </div>
    </aside>
  );
}

function ClassicForm({
  question,
  reason,
  busy,
  onSubmit,
  onChoose,
  onSkip,
}: {
  question: CalageQuestion;
  reason: CalageReason;
  busy: boolean;
  onSubmit: (raw: string) => void;
  onChoose: (label: string) => void;
  onSkip: () => void;
}) {
  const [raw, setRaw] = useState("");
  return (
    <>
      {reason && <p className="muted calage-reason">{REASONS[reason]}</p>}
      {question.choices.length > 0 ? (
        <div className="calage-row">
          {question.choices.map((c) => (
            <button
              key={c.value}
              type="button"
              className="small"
              onClick={() => onChoose(c.label)}
            >
              {c.label}
            </button>
          ))}
        </div>
      ) : (
        <form
          className="calage-row"
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit(raw);
          }}
        >
          <label className="calage-field">
            {question.field === "hauteur_plafond"
              ? "Hauteur sous plafond (m)"
              : "Largeur de la pièce (m)"}
            <input
              className="calage-input"
              inputMode="decimal"
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
            />
          </label>
          <button type="submit" className="small" disabled={busy}>
            Valider
          </button>
        </form>
      )}
      <button type="button" className="ghost small" onClick={onSkip}>
        Passer
      </button>
    </>
  );
}
