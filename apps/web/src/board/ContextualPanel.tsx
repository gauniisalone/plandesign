import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  contextualApi,
  inspirationApi,
  type ContextualPromptResult,
  type ContextualReference,
  type ContextualSource,
  type ContextualVariant,
  type JobStatus,
  type RenderEntry,
} from "../api/client";
import type { Level, Project } from "../api/types.gen";
import { allCameras } from "../model";
import { latestRenders } from "../staleViews";
import { approxClipTokens, CLIP_LIMIT, TOKEN_BUDGET } from "./clipTokens";

/** Rendu contextualisé (A1) : depuis une source du tableau (photo ou vue 3D ; le plan 2D
 *  n'est pas un guide d'image, il enrichit le texte), une intention et un budget, le prompt
 *  enrichi par le plan est montré et modifiable avant de générer les variantes. Les images
 *  du tableau cochées « Référence » (A2) guident l'allure (IP-Adapter), jamais la géométrie. */

const DEFAULT_REFERENCE_SCALE = 0.6;

export type ContextualPick =
  | { kind: "photo"; id: string; label: string }
  | { kind: "render"; id: string; label: string }
  | { kind: "plan"; id: string; label: string };

const VARIANTS: { id: ContextualVariant; label: string; hint: string }[] = [
  {
    id: "conservatrice",
    label: "Conservatrice",
    hint: "garde l'image, change l'allure (0,45)",
  },
  {
    id: "creative",
    label: "Créative",
    hint: "réinterprète matières et lumière (0,65)",
  },
  {
    id: "radicale",
    label: "Radicale",
    hint: "repart presque de zéro, géométrie tenue (0,85)",
  },
];
/** Source photo (A4) : profondeur seule, forces plus hautes, contraintes plus basses. */
const PHOTO_VARIANTS: { id: ContextualVariant; label: string; hint: string }[] = [
  {
    id: "conservatrice",
    label: "Conservatrice",
    hint: "restyle la pièce, garde l'essentiel (0,5 · profondeur 0,9)",
  },
  {
    id: "creative",
    label: "Créative",
    hint: "laisse venir les meubles de l'intention (0,75 · 0,6)",
  },
  {
    id: "radicale",
    label: "Radicale",
    hint: "réinvente la pièce, volume tenu par la profondeur (0,92 · 0,4)",
  },
];
const BUDGETS: {
  id: "economique" | "standard" | "haut_de_gamme";
  label: string;
}[] = [
  { id: "economique", label: "Économique" },
  { id: "standard", label: "Standard" },
  { id: "haut_de_gamme", label: "Haut de gamme" },
];

export function ContextualPanel({
  project,
  level,
  renders,
  pick,
  references,
  onGenerated,
  onClose,
}: {
  project: Project;
  level: Level;
  renders: RenderEntry[];
  pick: ContextualPick;
  /** Les images du tableau cochées « Référence », dans l'ordre du tableau. */
  references: ContextualReference[];
  /** Des variantes sont arrivées : le tableau se resynchronise. */
  onGenerated: () => void;
  onClose: () => void;
}) {
  const [referenceScale, setReferenceScale] = useState(DEFAULT_REFERENCE_SCALE);
  const usedReferences = references.slice(0, 4);
  // Vues 3D rendues du niveau : source par défaut d'une photo (l'aménagement proposé y est
  // déjà, A4) et proposées quand le plan est choisi comme source.
  const views = useMemo(() => {
    const latest = latestRenders(renders);
    return allCameras(level)
      .map((cam) => ({ cam, entry: latest.get(cam.id) }))
      .filter(
        (v): v is { cam: (typeof v)["cam"]; entry: RenderEntry } => !!v.entry,
      );
  }, [level, renders]);
  const firstView = views[0];
  const [source, setSource] = useState<ContextualSource | null>(() => {
    if (pick.kind === "plan") return null;
    if (pick.kind === "photo" && firstView)
      return {
        kind: "render",
        id: firstView.entry.run_id,
        label: firstView.entry.camera_name,
      };
    return { kind: pick.kind, id: pick.id, label: pick.label };
  });
  const [intention, setIntention] = useState("");
  const [budget, setBudget] = useState<
    "economique" | "standard" | "haut_de_gamme"
  >("standard");
  const [built, setBuilt] = useState<ContextualPromptResult | null>(null);
  const [prompt, setPrompt] = useState("");
  const [variants, setVariants] = useState<Set<ContextualVariant>>(
    () => new Set(VARIANTS.map((v) => v.id)),
  );
  const [seed, setSeed] = useState(0);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const alive = useRef(true);
  useEffect(
    () => () => {
      alive.current = false;
    },
    [],
  );

  const tokens = approxClipTokens(prompt);
  const overflow = prompt.trim() !== "" && tokens > TOKEN_BUDGET;

  const build = async () => {
    if (!source) return;
    setBuilding(true);
    setError(null);
    try {
      const out = await contextualApi.prompt(project.id, level.id, {
        intention,
        budget,
        source,
      });
      setBuilt(out);
      setPrompt(out.prompt);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBuilding(false);
    }
  };

  const generate = async () => {
    if (!source || !prompt.trim() || variants.size === 0) return;
    setError(null);
    try {
      let current = await contextualApi.start(project.id, level.id, {
        intention,
        budget,
        source,
        prompt: prompt.trim(),
        variants: VARIANTS.map((v) => v.id).filter((v) => variants.has(v)),
        seed,
        references: usedReferences,
        reference_scale: referenceScale,
      });
      setJob(current);
      while (current.status === "running" && alive.current) {
        await new Promise((r) => window.setTimeout(r, 2000));
        current = await inspirationApi.job(project.id, current.id);
        if (alive.current) setJob(current);
      }
      if (current.status === "failed") setError(current.error ?? "échec");
      else if (current.status === "done") onGenerated();
    } catch (e) {
      setJob(null);
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  const running = job?.status === "running";
  return (
    <section className="contextual" aria-label="Rendu contextualisé">
      <p className="muted">
        Une image guidée par votre source et enrichie par le plan ; le plan et
        la 3D ne bougent pas. Proportions estimées.
      </p>

      <h3>1 · Source</h3>
      {pick.kind === "plan" ? (
        <>
          <p className="muted">
            Le plan 2D n'est pas un guide d'image : il enrichit le texte
            (dimensions, ouvertures). Choisissez une vue 3D rendue de ce plan
            comme source :
          </p>
          {views.length === 0 ? (
            <p className="warn">
              Aucune vue rendue : Résultats › « Rendre toutes les vues », puis
              revenez.
            </p>
          ) : (
            <ul className="contextual-views">
              {views.map(({ cam, entry }) => (
                <li key={cam.id}>
                  <label>
                    <input
                      type="radio"
                      name="contextual-source"
                      checked={source?.id === entry.run_id}
                      onChange={() =>
                        setSource({
                          kind: "render",
                          id: entry.run_id,
                          label: entry.camera_name,
                        })
                      }
                    />
                    {entry.camera_name}
                  </label>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : pick.kind === "photo" ? (
        <>
          <p className="muted">
            Depuis une photo, le mieux est de partir de la vue 3D de
            l'aménagement proposé (l'image montre alors des meubles) ; la photo
            telle quelle ne fait que restyler la pièce, objets compris.
          </p>
          <ul className="contextual-views" aria-label="Source de l'image">
            {views.map(({ cam, entry }) => (
              <li key={cam.id}>
                <label>
                  <input
                    type="radio"
                    name="contextual-source"
                    checked={source?.kind === "render" && source.id === entry.run_id}
                    onChange={() =>
                      setSource({
                        kind: "render",
                        id: entry.run_id,
                        label: entry.camera_name,
                      })
                    }
                  />
                  Vue 3D de l'aménagement · {entry.camera_name}
                  {entry === firstView?.entry && (
                    <span className="muted"> (recommandé)</span>
                  )}
                </label>
              </li>
            ))}
            <li>
              <label>
                <input
                  type="radio"
                  name="contextual-source"
                  checked={source?.kind === "photo"}
                  onChange={() =>
                    setSource({ kind: "photo", id: pick.id, label: pick.label })
                  }
                />
                Cette photo telle quelle
                {pick.label && pick.label !== "Photo" && ` · ${pick.label}`}
                <span className="muted">
                  {" "}
                  — restyler la pièce ; profondeur seule, réglages plus libres
                </span>
              </label>
            </li>
          </ul>
          {views.length === 0 && (
            <p className="muted">
              Aucune vue 3D rendue : Résultats › « Rendre toutes les vues » pour
              un aménagement ; en attendant, la photo est restylée telle quelle.
            </p>
          )}
        </>
      ) : (
        <p>
          <b>Vue 3D</b>
          {pick.label && pick.label !== "Vue" && ` · ${pick.label}`}
          <span className="muted"> — guide profondeur et arêtes</span>
        </p>
      )}

      <h3>Références d'allure</h3>
      {usedReferences.length === 0 ? (
        <p className="muted">
          Aucune : sélectionnez des images du tableau (photos, rendus, images
          jointes) et cochez <b>Référence</b> pour que leur allure (matières,
          ambiance) guide les variantes. Sans référence, le texte seul guide.
        </p>
      ) : (
        <>
          <ul className="contextual-refs" aria-label="Références cochées">
            {usedReferences.map((r) => (
              <li key={`${r.kind}:${r.ref}`}>{r.label}</li>
            ))}
          </ul>
          {references.length > 4 && (
            <p className="muted">
              Quatre références au plus : les suivantes sont ignorées.
            </p>
          )}
          <label className="contextual-scale">
            Poids des références
            <input
              type="range"
              min={0.2}
              max={1}
              step={0.1}
              value={referenceScale}
              onChange={(e) => setReferenceScale(Number(e.target.value))}
              disabled={running}
            />
            <span className="muted">
              {referenceScale.toFixed(1)} — l'allure, jamais la géométrie
            </span>
          </label>
        </>
      )}

      <h3>2 · Intention</h3>
      <textarea
        aria-label="Intention"
        rows={3}
        placeholder="style, ambiance, matériaux, contraintes… (ex. : scandinave, bois clair, lin, garder le lit)"
        value={intention}
        onChange={(e) => setIntention(e.target.value)}
        disabled={running}
      />
      <label className="contextual-budget">
        Budget
        <select
          value={budget}
          onChange={(e) => setBudget(e.target.value as typeof budget)}
          disabled={running}
        >
          {BUDGETS.map((b) => (
            <option key={b.id} value={b.id}>
              {b.label}
            </option>
          ))}
        </select>
        <span className="muted">
          lu comme une contrainte de matières, rien de plus
        </span>
      </label>
      <button
        type="button"
        className="primary"
        disabled={!source || building || running}
        onClick={() => void build()}
      >
        {building
          ? "Construction…"
          : built
            ? "Reconstruire le prompt"
            : "Construire le prompt"}
      </button>

      {built && (
        <>
          <h3>3 · Prompt enrichi (modifiable)</h3>
          <ul
            className="contextual-context"
            aria-label="Ce que le plan apporte"
          >
            {built.context.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
          {built.estimated && (
            <span className="plan-status estimated">proportions estimées</span>
          )}
          <p className="muted">
            Intention lue par{" "}
            {built.reader === "model"
              ? "Gemini (texte seul)"
              : built.reader === "phrases"
                ? "les phrases simples de PlanDesign, sans service extérieur"
                : "personne : pas d'intention, le plan seul"}
            {(built.summary.length > 0 || built.understood.length > 0) &&
              ` — compris : ${(built.summary.length > 0 ? built.summary : built.understood).join(" · ")}`}
            {built.notice && ` — ${built.notice}`}
          </p>
          {built.prompt_fr && (
            <p
              className="contextual-fr"
              aria-label="Traduction française du prompt"
            >
              <span className="muted">En français (lecture seule) : </span>
              {built.prompt_fr}
            </p>
          )}
          <textarea
            aria-label="Prompt final"
            rows={6}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={running}
          />
          <p className={overflow ? "warn" : "muted"} role={overflow ? "alert" : undefined}>
            {overflow
              ? `Trop long : environ ${tokens} jetons, le modèle n'en lit que ${CLIP_LIMIT} et coupe la fin sans prévenir. Raccourcissez.`
              : `Environ ${tokens} jetons sur ${CLIP_LIMIT} lus par le modèle. `}
            {!overflow &&
              "Le modèle lit l'anglais : ce prompt est modifiable ; la traduction ci-dessus décrit le prompt construit."}
          </p>
          <h3>4 · Variantes</h3>
          <ul className="contextual-variants">
            {(source?.kind === "photo" ? PHOTO_VARIANTS : VARIANTS).map((v) => (
              <li key={v.id}>
                <label title={v.hint}>
                  <input
                    type="checkbox"
                    checked={variants.has(v.id)}
                    disabled={running}
                    onChange={(e) => {
                      const next = new Set(variants);
                      if (e.target.checked) next.add(v.id);
                      else next.delete(v.id);
                      setVariants(next);
                    }}
                  />
                  {v.label} <span className="muted">{v.hint}</span>
                </label>
              </li>
            ))}
          </ul>
          {usedReferences.length > 0 && (
            <p className="muted">
              La radicale écoute davantage les références (poids +0,2).
            </p>
          )}
          <label className="contextual-seed">
            Graine
            <input
              type="number"
              min={0}
              value={seed}
              onChange={(e) =>
                setSeed(Math.max(0, Number(e.target.value) || 0))
              }
              disabled={running}
            />
            <span className="muted">la même pour toutes les variantes</span>
          </label>
          <button
            type="button"
            className="primary"
            disabled={running || !prompt.trim() || variants.size === 0}
            onClick={() => void generate()}
          >
            {running
              ? "Génération…"
              : `Générer ${variants.size} variante${variants.size > 1 ? "s" : ""}${usedReferences.length > 0 ? ` avec ${usedReferences.length} référence${usedReferences.length > 1 ? "s" : ""}` : ""}`}
          </button>
        </>
      )}

      {job && (
        <p className="contextual-job" role="status">
          {job.status === "running"
            ? `${job.stage || job.message}${job.remaining_s != null ? ` · environ ${Math.round(job.remaining_s)} s` : ""}`
            : job.status === "done"
              ? "Variantes ajoutées au tableau (cadre Moodboard)."
              : ""}
        </p>
      )}
      {error && (
        <p className="warn" role="alert">
          {error}
        </p>
      )}
      <button type="button" className="link" onClick={onClose}>
        Fermer
      </button>
    </section>
  );
}
