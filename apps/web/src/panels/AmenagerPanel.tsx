import { ZONE_LABELS } from "../intent";
import { useEffect, useState } from "react";
import {
  aiApi,
  catalogApi,
  renderApi,
  type LayoutBrief,
  type JobStatus,
  type RenderEntry,
  type AiStatus,
  type BriefItem,
  type CritiqueBody,
  type CritiqueRun,
  type IntentionRun,
  type LayoutBody,
  type LayoutProposal,
  type LayoutRun,
  type ProjectSettingsUpdate,
  type ReviseBody,
} from "../api/client";
import type { Level, Project } from "../api/types.gen";
import { rooms } from "../model";

interface Props {
  project: Project;
  level: Level;
  busy: boolean;
  lastBrief: IntentionRun | null;
  lastLayouts: LayoutRun | null;
  ghost: LayoutProposal | null;
  onSettings: (s: ProjectSettingsUpdate) => void;
  /** « Écrire à l'assistant » (P5c) : la demande en phrase passe par lui. */
  onAskAssistant: () => void;
  onProposeLayouts: (body: LayoutBody) => void;
  onPreviewProposal: (proposal: LayoutProposal | null) => void;
  onApplyProposal: (proposal: LayoutProposal) => void;
  lastCritique: CritiqueRun | null;
  onCritique: (body: CritiqueBody) => void;
  onRevise: (body: ReviseBody) => void;
  compare: LayoutProposal | null;
  onCompare: (proposal: LayoutProposal | null) => void;
  renders: RenderEntry[];
  renderJob: JobStatus | null;
  onRenderProposal: (proposal: LayoutProposal) => void;
}

/** Fiche « Aménager » en trois pas (directive d'augus du 27 sept.) : 1 le souhait (rôle
 *  Intention, ou brief à la main sans modèle), 2 le brief à relire, 3 les dispositions à
 *  appliquer. Le consentement pour un modèle distant est demandé ici, au moment de l'envoi ;
 *  le journal réseau et l'état du modèle sont dans Paramètres avancés › Assistant IA. */
export function AmenagerPanel({
  project,
  level,
  busy,
  lastBrief,
  lastLayouts,
  ghost,
  onAskAssistant,
  onProposeLayouts,
  onPreviewProposal,
  onApplyProposal,
  lastCritique,
  onCritique,
  onRevise,
  compare,
  onCompare,
  renders,
  renderJob,
  onRenderProposal,
}: Props) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const privacy = project.privacy;
  const consented = privacy.allow_remote_models && privacy.remote_consent;
  useEffect(() => {
    aiApi.status().then(setStatus, () =>
      setStatus({
        configured: false,
        model_id: null,
        destination: null,
        remote: null,
        supports_images: null,
      }),
    );
  }, []);
  // Blender présent ? (vignettes 3D des variantes) ; galerie déjà rendue pour ce projet.
  const [blender, setBlender] = useState<BlenderInfo | null>(null);
  const [gallery, setGallery] = useState<RenderEntry[]>([]);
  useEffect(() => {
    renderApi.estimate(project.id, level.id, 32).then(
      (e) =>
        setBlender({
          available: e.blender_available,
          message: e.message,
          device: e.device_label,
        }),
      () => setBlender({ available: false, message: "", device: "" }),
    );
    renderApi.list(project.id).then(setGallery, () => undefined);
  }, [project.id, level.id]);
  const allRenders = renders.length > 0 ? renders : gallery;
  return (
    <aside className="inspector amenager">
      <IntentionSection
        projectId={project.id}
        level={level}
        busy={busy}
        last={lastBrief}
        onAskAssistant={onAskAssistant}
        onPropose={onProposeLayouts}
      />
      {lastLayouts && (
        <LayoutsSection
          run={lastLayouts}
          busy={busy}
          ghost={ghost}
          onPreview={onPreviewProposal}
          onApply={onApplyProposal}
          critique={lastCritique}
          canUseModel={!!status?.configured && (!status.remote || consented)}
          destination={status?.destination ?? null}
          onCritique={onCritique}
          onRevise={onRevise}
          compare={compare}
          onCompare={onCompare}
          renders={allRenders}
          renderJob={renderJob}
          blender={blender}
          projectId={project.id}
          onRender={onRenderProposal}
        />
      )}
    </aside>
  );
}

const PLACE_LABEL: Record<string, string> = {
  wall: "un mur",
  window: "une fenêtre",
  door: "une porte",
  door_swing: "le débattement d'une porte",
  radiator: "un radiateur",
  low_wall: "la zone basse sous pente",
  ridge: "le faîtage",
  outlet: "une prise",
  corner: "un coin de la pièce",
};

function describeItem(item: BriefItem): string {
  const parts: string[] = [];
  if (item.zone) parts.push(`zone ${ZONE_LABELS[item.zone] ?? item.zone}`);
  if (item.dimensions_m) {
    const d = item.dimensions_m;
    const dims = [d.width, d.depth, d.height]
      .map((v) => (v == null ? "?" : v.toFixed(2)))
      .join(" × ");
    parts.push(`dimensions demandées ${dims} m`);
  }
  if (item.near.length)
    parts.push(
      `près de ${item.near.map((p) => PLACE_LABEL[p] ?? p).join(", ")}`,
    );
  if (item.away_from.length)
    parts.push(
      `loin de ${item.away_from.map((p) => PLACE_LABEL[p] ?? p).join(", ")}`,
    );
  return parts.join(" · ");
}

/** Rôle Intention : le souhait de l'utilisateur, l'aperçu exact de ce qui partira (et vers
 *  où) avant l'envoi, puis le brief (meubles, priorités, préférences) à relire et corriger.
 *  Aucune position : le placement vient du générateur local (3.4), sans modèle, depuis
 *  le brief relu ; sans modèle, le brief se compose à la main. */
function IntentionSection({
  projectId,
  level,
  busy,
  last,
  onAskAssistant,
  onPropose,
}: {
  projectId: string;
  level: Level;
  busy: boolean;
  last: IntentionRun | null;
  onAskAssistant: () => void;
  onPropose: (body: LayoutBody) => void;
}) {
  const [roomId, setRoomId] = useState("");
  /** Catégories du catalogue, pour composer le brief à la main. */
  const [catalogCategories, setCatalogCategories] = useState<string[]>([]);
  useEffect(() => {
    let cancelled = false;
    catalogApi.list().then(
      (c) => {
        if (!cancelled)
          setCatalogCategories([...new Set(c.assets.map((a) => a.category))]);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);
  const [draft, setDraft] = useState<BriefItem[] | null>(null);
  const [draftFor, setDraftFor] = useState<string | null>(null);
  // Le brouillon éditable suit le dernier brief reçu (motif « valeur précédente »).
  if (last && draftFor !== last.run_id) {
    setDraftFor(last.run_id);
    setDraft(last.brief.items);
  }
  /** Brief par zones (V1) : d'après l'ambiance de la pièce, sans modèle. */
  const [zoneBase, setZoneBase] = useState<LayoutBrief | null>(null);
  const [zoneError, setZoneError] = useState<string | null>(null);
  const fromZones = () => {
    setZoneError(null);
    void aiApi.zoneBrief(projectId, level.id, roomId || null).then(
      (b) => {
        setZoneBase(b);
        setDraft(b.items);
      },
      (e: unknown) => setZoneError(e instanceof Error ? e.message : String(e)),
    );
  };
  const categories =
    last?.preview.categories ??
    (catalogCategories.length
      ? catalogCategories
      : [...new Set((draft ?? []).map((i) => i.category))]);
  const addItem = () =>
    setDraft((d) => [
      ...(d ?? []),
      {
        category: categories[0] ?? "bed_double",
        count: 1,
        priority: "required",
        dimensions_m: null,
        near: [],
        away_from: [],
      },
    ]);
  const propose = () =>
    onPropose({
      brief: {
        room_use: last?.brief.room_use ?? zoneBase?.room_use ?? "",
        items: draft ?? [],
        keep: last?.brief.keep ?? [],
        avoid: last?.brief.avoid ?? [],
        notes: last?.brief.notes ?? zoneBase?.notes ?? "",
        dropped: [],
      },
      room_id: roomId || null,
      variants: 3,
    });
  const levelRooms = rooms(level);
  const items = draft ?? [];
  const updateItem = (index: number, patch: Partial<BriefItem>) =>
    setDraft((d) =>
      (d ?? []).map((it, i) => (i === index ? { ...it, ...patch } : it)),
    );
  const removeItem = (index: number) =>
    setDraft((d) => (d ?? []).filter((_, i) => i !== index));
  return (
    <section className="intention">
      <h2>
        <span className="step">1</span> Votre demande
      </h2>
      <p className="muted">
        Une phrase (« propose-moi trois aménagements avec un coin bureau ») :
        écrivez-la à l'assistant, il prépare les dispositions et les montre ici.
        Sans phrase : le brief par zones, d'après l'ambiance de la pièce, ou
        composé à la main.
      </p>
      <div className="row">
        <button className="primary" disabled={busy} onClick={onAskAssistant}>
          Écrire à l'assistant
        </button>
      </div>
      {levelRooms.length > 1 && (
        <label className="field">
          <span>Pièce</span>
          <select value={roomId} onChange={(e) => setRoomId(e.target.value)}>
            <option value="">la plus grande</option>
            {levelRooms.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name ?? "pièce"}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="row">
        <button
          disabled={busy}
          onClick={fromZones}
          title="Sans modèle : zones (couchage, lecture, rangement…) et kits de meubles d'après l'ambiance et la surface de la pièce, densité visée 0,3 à 0,5 meuble par m²"
        >
          Brief par zones
        </button>
      </div>
      {zoneError && <p className="warn">{zoneError}</p>}
      {zoneBase && draft && !last && (
        <p className="muted zone-notes">{zoneBase.notes}</p>
      )}
      {(last || draft) && (
        <>
          <h2>
            <span className="step">2</span>{" "}
            {last ? "Le brief, à relire" : "Le brief, composé à la main"}
          </h2>
          {last && (
            <p className="muted">
              {last.result.model_ids.join(", ")} ·{" "}
              {last.result.duration_s.toFixed(1)} s
              {last.brief.room_use ? ` · usage : ${last.brief.room_use}` : ""}
            </p>
          )}
          {items.length === 0 && (
            <p className="warn">
              {last
                ? "Aucun meuble retenu : reformulez à l'assistant ou ajoutez un meuble."
                : "Ajoutez les meubles voulus, puis demandez des dispositions."}
            </p>
          )}
          <ul className="list brief">
            {items.map((item, i) => (
              <li key={i}>
                <select
                  value={item.category}
                  onChange={(e) => updateItem(i, { category: e.target.value })}
                  title="Catégorie du catalogue"
                >
                  {categories.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>{" "}
                ×{" "}
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={item.count}
                  onChange={(e) =>
                    updateItem(i, {
                      count: Math.max(1, Number(e.target.value)),
                    })
                  }
                  style={{ width: "3.5em" }}
                />{" "}
                <select
                  value={item.priority}
                  onChange={(e) =>
                    updateItem(i, {
                      priority: e.target.value as BriefItem["priority"],
                    })
                  }
                >
                  <option value="required">indispensable</option>
                  <option value="nice_to_have">si possible</option>
                </select>{" "}
                <span className="muted">{describeItem(item)}</span>{" "}
                <button className="link danger" onClick={() => removeItem(i)}>
                  retirer
                </button>
              </li>
            ))}
          </ul>
          <button
            className="link"
            onClick={addItem}
            disabled={categories.length === 0}
            title="Ajoute une ligne au brief (catégorie du catalogue, à choisir)"
          >
            + ajouter un meuble
          </button>
          {last && last.brief.avoid.length > 0 && (
            <p className="muted">À éviter : {last.brief.avoid.join(" ; ")}</p>
          )}
          {last && last.brief.keep.length > 0 && (
            <p className="muted">
              {last.brief.keep.length} élément(s) à conserver en place.
            </p>
          )}
          {last && last.brief.dropped.length > 0 && (
            <>
              <h3>Écarté de la réponse du modèle, et pourquoi</h3>
              <ul className="list">
                {last.brief.dropped.map((d, i) => (
                  <li key={i} className="muted">
                    {d}
                  </li>
                ))}
              </ul>
            </>
          )}
          {last?.result.warnings
            .filter((w) => !last.brief.dropped.includes(w))
            .map((w, i) => (
              <p key={i} className="warn">
                {w}
              </p>
            ))}
          <button
            disabled={busy || items.length === 0}
            onClick={propose}
            title="Moteur géométrique local, sans modèle : trois variantes valides à comparer ; rien n'entre dans le plan sans « Appliquer »"
          >
            Proposer des dispositions
          </button>
        </>
      )}
      {!last && !draft && categories.length > 0 && (
        <p className="muted">
          Ou composez le brief à la main :{" "}
          <button className="link" onClick={addItem}>
            + ajouter un meuble
          </button>
        </p>
      )}
    </section>
  );
}

/** Variantes du générateur : chacune avec son score et sa phrase (ce qui est placé, les
 *  souhaits tenus ou manqués, les remarques souples). Voir sur le plan = fantôme ;
 *  Appliquer = geste utilisateur, annulable. */
function LayoutsSection({
  run,
  busy,
  ghost,
  onPreview,
  onApply,
  critique,
  canUseModel,
  destination,
  onCritique,
  onRevise,
  compare,
  onCompare,
  renders,
  renderJob,
  blender,
  projectId,
  onRender,
}: {
  run: LayoutRun;
  busy: boolean;
  ghost: LayoutProposal | null;
  onPreview: (proposal: LayoutProposal | null) => void;
  onApply: (proposal: LayoutProposal) => void;
  critique: CritiqueRun | null;
  canUseModel: boolean;
  destination: string | null;
  onCritique: (body: CritiqueBody) => void;
  onRevise: (body: ReviseBody) => void;
  compare: LayoutProposal | null;
  onCompare: (proposal: LayoutProposal | null) => void;
  renders: RenderEntry[];
  renderJob: JobStatus | null;
  blender: BlenderInfo | null;
  projectId: string;
  onRender: (proposal: LayoutProposal) => void;
}) {
  const rendering = renderJob?.status === "running";
  return (
    <section className="layouts">
      <h2>
        <span className="step">3</span> Les dispositions
      </h2>
      <p className="muted">
        Calcul local en {run.result.duration_s.toFixed(1)} s ·{" "}
        {run.proposals.length} variante(s) sans collision, hors des débattements
        de porte et sous la hauteur libre. Rien n'est dans le plan tant que vous
        n'appliquez pas.
      </p>
      {blender && !blender.available && (
        <p className="muted">
          Sans Blender, pas de vignette 3D des variantes : l'aperçu 3D du plan
          reste disponible (bouton 3D de la barre du bas).
        </p>
      )}
      {rendering && renderJob && (
        <p className="muted" aria-live="polite">
          Vignette en cours ({renderJob.device}) :{" "}
          {renderJob.elapsed_s.toFixed(0)} s
          {renderJob.expected_s
            ? ` sur environ ${renderJob.expected_s.toFixed(0)} s`
            : ""}
          … la page reste utilisable.
        </p>
      )}
      {run.proposals.length === 0 && (
        <p className="warn">Aucune variante valide pour ce brief.</p>
      )}
      <ol className="list variants">
        {run.proposals.map((p) => {
          const shown = ghost?.index === p.index && ghost?.seed === p.seed;
          return (
            <li key={p.index} className={shown ? "selected" : ""}>
              {/* Une phrase de synthèse ; le détail (règles, souhaits manqués) est replié. */}
              <p className="variant-summary">
                <strong>Variante {p.index}</strong> · {p.summary}
              </p>
              <details className="variant-details">
                <summary>détail</summary>
                <p className="muted small">
                  Score {p.score >= 0 ? "+" : ""}
                  {p.score.toFixed(1)} (plus il est haut, mieux les souhaits et
                  les règles sont tenus)
                </p>
                <ul className="list">
                  {p.violations.map((v, i) => (
                    <li key={i} className={`severity-${v.severity}`}>
                      {v.message}
                    </li>
                  ))}
                  {p.missed.slice(1).map((m, i) => (
                    <li key={`m${i}`} className="muted">
                      {m}
                    </li>
                  ))}
                </ul>
              </details>
              <div className="row variant-actions">
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => onApply(p)}
                  aria-label="Appliquer cette variante"
                  title="Ajoute ces meubles au plan (source « généré », annulable)"
                >
                  Appliquer
                </button>
                <button
                  className={shown ? "active" : ""}
                  aria-pressed={shown}
                  onClick={() => onPreview(shown ? null : p)}
                  title="Dessine les meubles en pointillé sur le plan, sans les ajouter"
                >
                  {shown ? "masquer sur le plan" : "voir sur le plan"}
                </button>
              </div>
              <div className="row variant-more">
                <button
                  className="link"
                  disabled={busy}
                  onClick={() =>
                    onCritique({
                      proposal: p,
                      brief: run.brief,
                      room_id: run.room_id,
                      use_model: canUseModel,
                    })
                  }
                  title={
                    canUseModel
                      ? `Remarques des règles, puis avis du modèle (${destination ?? "configuré"}) : le texte de la variante part, jamais la photo`
                      : "Remarques des règles locales (aucun modèle sollicité)"
                  }
                >
                  Avis sur cette variante
                </button>
                {!shown && (
                  <button
                    className="link"
                    onClick={() =>
                      onCompare(compare?.index === p.index ? null : p)
                    }
                    title="Dessine cette variante en vert à côté de celle montrée en orange, avec une comparaison côte à côte"
                  >
                    {compare?.index === p.index
                      ? "ne plus comparer"
                      : "comparer"}
                  </button>
                )}
                <button
                  className="link"
                  disabled={busy || !blender?.available || rendering}
                  onClick={() => onRender(p)}
                  title={
                    blender?.available
                      ? `Rendu Blender de cette variante sans l'appliquer (${blender.message}) ; le résultat s'affiche ici et dans la galerie de l'écran Rendre`
                      : "Blender introuvable : installez-le (Paramètres avancés › Blender) ; l'aperçu 3D du plan reste disponible"
                  }
                >
                  Vignette 3D
                </button>
              </div>
              {renders.some((r) => r.variant === p.index) && (
                <div className="variant-thumbs">
                  {renders
                    .filter((r) => r.variant === p.index)
                    .slice(0, 3)
                    .map((r) => {
                      const url = renderApi.imageUrl(projectId, r);
                      return (
                        <a
                          key={r.run_id}
                          className="variant-thumb"
                          href={url}
                          target="_blank"
                          rel="noreferrer"
                          title={`${r.samples} éch. · ${r.device}${r.render_s != null ? ` · ${r.render_s.toFixed(0)} s` : ""}`}
                        >
                          <img
                            src={url}
                            alt={`Vignette de la variante ${p.index}`}
                          />
                        </a>
                      );
                    })}
                </div>
              )}
              {critique &&
                critique.proposal_index === p.index &&
                critique.proposal_seed === p.seed && (
                  <CritiqueBlock
                    critique={critique}
                    proposal={p}
                    run={run}
                    busy={busy}
                    onRevise={onRevise}
                  />
                )}
            </li>
          );
        })}
      </ol>
      {ghost && compare && compare.index !== ghost.index && (
        <CompareTable a={ghost} b={compare} onStop={() => onCompare(null)} />
      )}
      {run.result.warnings.map((w, i) => (
        <p key={i} className="warn">
          {w}
        </p>
      ))}
    </section>
  );
}

/** Remarques d'une variante (3.5) : chacune cite le meuble et la règle, triées par
 *  importance ; l'action en un clic recalcule une variante révisée, à relire. Les règles
 *  locales viennent d'abord, l'avis du modèle (s'il est sollicité) ensuite. */
function CritiqueBlock({
  critique,
  proposal,
  run,
  busy,
  onRevise,
}: {
  critique: CritiqueRun;
  proposal: LayoutProposal;
  run: LayoutRun;
  busy: boolean;
  onRevise: (body: ReviseBody) => void;
}) {
  const c = critique.critique;
  return (
    <div className="remarks">
      <h3>
        Remarques (
        {critique.model_count > 0 || critique.result.model_ids.length > 0
          ? "règles locales + modèle"
          : "règles locales"}
        )
      </h3>
      <p className="muted">
        {critique.rules_count} par les règles · {critique.model_count} par le
        modèle
        {critique.result.model_ids.length > 0
          ? ` (${critique.result.model_ids.join(", ")})`
          : ""}{" "}
        · {critique.result.duration_s.toFixed(1)} s. Rien n'est appliqué :
        chaque action recalcule une variante à relire.
      </p>
      {c.verdict && (
        <p>
          <em>{c.verdict}</em>
        </p>
      )}
      {c.remarks.length === 0 && (
        <p>
          Aucune remarque : la variante respecte les règles connues et le brief.
        </p>
      )}
      <ol className="list">
        {c.remarks.map((r, i) => {
          const action = r.action;
          return (
            <li key={i} className={`severity-${r.importance}`}>
              <strong>{r.furniture_label}</strong> · {r.rule_label}
              {r.origin === "model" ? " · avis du modèle" : ""} — {r.message}
              {action && (
                <>
                  {" "}
                  <button
                    className="link"
                    disabled={busy}
                    onClick={() =>
                      onRevise({
                        proposal,
                        action,
                        brief: run.brief,
                        room_id: run.room_id,
                      })
                    }
                    title="Recalcule cette variante avec ce changement ; à relire, rien n'entre dans le plan sans « Appliquer »"
                  >
                    {action.label}
                  </button>
                </>
              )}
            </li>
          );
        })}
      </ol>
      {c.strengths.length > 0 && (
        <p className="muted">Points forts : {c.strengths.join(" · ")}</p>
      )}
      {c.dropped.length > 0 && (
        <details>
          <summary>Écarté de la réponse du modèle ({c.dropped.length})</summary>
          <ul className="list">
            {c.dropped.map((d, i) => (
              <li key={i} className="muted">
                {d}
              </li>
            ))}
          </ul>
        </details>
      )}
      {critique.result.warnings
        .filter((w) => !c.dropped.includes(w))
        .map((w, i) => (
          <p key={i} className="muted">
            {w}
          </p>
        ))}
    </div>
  );
}

/** Deux variantes côte à côte (3.6) : A en orange (fantôme), B en vert sur le plan. */
function CompareTable({
  a,
  b,
  onStop,
}: {
  a: LayoutProposal;
  b: LayoutProposal;
  onStop: () => void;
}) {
  const names = (p: LayoutProposal) =>
    p.furniture.map((f) => f.name ?? f.category).join(", ") || "—";
  const rows: [string, string, string][] = [
    ["Score", a.score.toFixed(1), b.score.toFixed(1)],
    ["Meubles placés", names(a), names(b)],
    [
      "Non placés",
      a.unplaced.map((u) => u.label).join(", ") || "—",
      b.unplaced.map((u) => u.label).join(", ") || "—",
    ],
    [
      "Souhaits tenus",
      a.satisfied.join(" · ") || "—",
      b.satisfied.join(" · ") || "—",
    ],
    [
      "Souhaits manqués",
      a.missed.join(" · ") || "—",
      b.missed.join(" · ") || "—",
    ],
    [
      "Remarques",
      a.violations.map((v) => v.message).join(" · ") || "aucune",
      b.violations.map((v) => v.message).join(" · ") || "aucune",
    ],
  ];
  return (
    <div className="compare">
      <h3>Côte à côte</h3>
      <p className="muted">
        A (orange, sur le plan) = variante {a.index} · B (vert) = variante{" "}
        {b.index}. « Appliquer » reste dans la liste ci-dessus.
      </p>
      <table>
        <thead>
          <tr>
            <th></th>
            <th>A · variante {a.index}</th>
            <th>B · variante {b.index}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, va, vb]) => (
            <tr key={label}>
              <th>{label}</th>
              <td>{va}</td>
              <td>{vb}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="link" onClick={onStop}>
        ne plus comparer
      </button>
    </div>
  );
}

/** Blender présent ou non, et ce que l'écran Rendre annoncerait pour une vignette. */
interface BlenderInfo {
  available: boolean;
  message: string;
  device: string;
}
