import { useEffect, useState } from "react";
import {
  api,
  journalApi,
  type DoctorReport,
  type JournalSummary,
  type ProjectSettingsUpdate,
} from "../api/client";
import type { Op } from "../api/ops";
import type { Level, Project } from "../api/types.gen";
import { loadTheme, saveTheme, type ThemeChoice } from "../theme";
import { DoctorBlock } from "./DoctorBlock";

/** Nord du plan : angle depuis +X, 90 = en haut du plan. */
const NORTH_OPTIONS: { value: number; label: string }[] = [
  { value: 90, label: "en haut du plan" },
  { value: 0, label: "à droite du plan" },
  { value: 270, label: "en bas du plan" },
  { value: 180, label: "à gauche du plan" },
];

/** Réglages du projet (Paramètres avancés) : unités d'affichage (le mètre par défaut, décision
 *  d'augus du 29 septembre 2026) et nord du plan. */
export function SettingsPanel({
  project,
  level,
  onOp,
  onSettings,
}: {
  project: Project;
  level: Level;
  onOp: (op: Op) => void;
  onSettings: (s: ProjectSettingsUpdate) => void;
}) {
  const north = Number.isFinite(level.north_deg) ? level.north_deg : 90;
  return (
    <aside className="inspector">
      <section>
        <h2>Réglages</h2>
        <label className="field">
          <span>Unités</span>
          <select
            value={project.units_display}
            onChange={(e) =>
              onSettings({
                units_display: e.target.value as Project["units_display"],
              })
            }
            title="Unité d'affichage des longueurs ; le plan est toujours en mètres"
          >
            <option value="m">mètres</option>
            <option value="cm">centimètres</option>
            <option value="mm">millimètres</option>
          </select>
        </label>
        <label className="field">
          <span>Nord</span>
          <select
            value={NORTH_OPTIONS.some((o) => o.value === north) ? north : ""}
            onChange={(e) =>
              onOp({
                op: "update_level",
                fields: { north_deg: Number(e.target.value) },
              })
            }
            title="Oriente la boussole du plan et le soleil des rendus"
          >
            {!NORTH_OPTIONS.some((o) => o.value === north) && (
              <option value="">{north}°</option>
            )}
            {NORTH_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <ThemeSwitch />
      </section>
      <section>
        <h2>Chemin automatique</h2>
        <label className="check">
          <input
            type="checkbox"
            aria-label="Chemin automatique après un dépôt"
            checked={project.render.auto_path}
            onChange={(e) =>
              onSettings({
                render: { ...project.render, auto_path: e.target.checked },
              })
            }
          />
          Après un dépôt : ambiance, aménagement, trois vues et leurs rendus
        </label>
        <label className="check">
          <input
            type="checkbox"
            aria-label="Générer au dépôt d'une photo"
            checked={project.render.auto_on_drop}
            onChange={(e) =>
              onSettings({
                render: { ...project.render, auto_on_drop: e.target.checked },
              })
            }
          />
          Une photo déposée sur le tableau d'un projet sans plan lance la construction du plan
        </label>
        <label className="check">
          <input
            type="checkbox"
            aria-label="Finition IA enchaînée"
            checked={project.render.auto_finish}
            onChange={(e) =>
              onSettings({
                render: { ...project.render, auto_finish: e.target.checked },
              })
            }
          />
          Finition IA enchaînée (l'image Blender reste toujours à côté)
        </label>
        <label className="check">
          <input
            type="checkbox"
            aria-label="Image d'inspiration"
            checked={project.render.auto_inspiration}
            onChange={(e) =>
              onSettings({
                render: {
                  ...project.render,
                  auto_inspiration: e.target.checked,
                },
              })
            }
          />
          Image d'inspiration par vue (allure catalogue, « cotes non garanties
          »)
        </label>
        <p className="muted">
          Tout ce que le chemin fait s'annule, et rien de ce que vous avez
          choisi n'est réécrit.
        </p>
      </section>
      <JournalSection project={project} onSettings={onSettings} />
    </aside>
  );
}

const THEMES: { id: ThemeChoice; label: string }[] = [
  { id: "light", label: "Clair" },
  { id: "dark", label: "Sombre" },
  { id: "system", label: "Système" },
];

/** Thème de l'interface (b2) : Clair, Sombre ou Système (défaut), retenu par ce navigateur. */
export function ThemeSwitch() {
  const [theme, setTheme] = useState<ThemeChoice>(loadTheme);
  return (
    <div className="field theme-field">
      <span id="theme-label">Thème</span>
      <div
        className="segmented"
        role="group"
        aria-labelledby="theme-label"
        title="Retenu par ce navigateur ; Système suit le réglage de l'ordinateur"
      >
        {THEMES.map((t) => (
          <button
            key={t.id}
            type="button"
            className={theme === t.id ? "active" : ""}
            aria-pressed={theme === t.id}
            onClick={() => {
              setTheme(t.id);
              saveTheme(t.id);
            }}
          >
            {t.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Journal local des demandes (V2) : actif par défaut, coupé en un clic, vidé sur demande. */
function JournalSection({
  project,
  onSettings,
}: {
  project: Project;
  onSettings: (s: ProjectSettingsUpdate) => void;
}) {
  const [summary, setSummary] = useState<JournalSummary | null>(null);
  const enabled = project.privacy.design_journal;
  useEffect(() => {
    let cancelled = false;
    journalApi.summary(project.id).then(
      (s) => {
        if (!cancelled) setSummary(s);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [project.id, project.history_cursor, enabled]);
  return (
    <section>
      <h2>Journal local</h2>
      <label className="check">
        <input
          type="checkbox"
          aria-label="Journal local des demandes"
          checked={enabled}
          onChange={(e) =>
            onSettings({
              privacy: { ...project.privacy, design_journal: e.target.checked },
            })
          }
        />
        Garder vos consignes et ce que vous en faites (gardé, corrigé, annulé)
      </label>
      <p className="muted">
        Dans le dossier du projet, jamais envoyé ; aucune photo, aucune image,
        aucune coordonnée. Il servira peut-être un jour à ajuster un petit
        modèle, si vous le décidez.
        {summary ? ` ${summary.events} ligne(s).` : ""}
      </p>
      <button
        type="button"
        disabled={!summary || summary.events === 0}
        onClick={() =>
          void journalApi.clear(project.id).then((s) => setSummary(s))
        }
      >
        Vider le journal
      </button>
    </section>
  );
}

/** État de l'installation, lu une fois à l'ouverture des Paramètres avancés. */
export function InstallState() {
  const [report, setReport] = useState<DoctorReport | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api.doctor().then(
      (r) => {
        if (!cancelled) setReport(r);
      },
      () => {
        if (!cancelled) setFailed(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);
  if (report) return <DoctorBlock report={report} />;
  return (
    <p className="muted">
      {failed
        ? "État de l'installation indisponible."
        : "État de l'installation…"}
    </p>
  );
}
