import { useEffect, useRef, useState } from "react";
import {
  api,
  type DoctorReport,
  homeApi,
  type HomeSettings,
  type ProjectSummary,
} from "../api/client";
import { APP_NAME } from "../brand";
import { formatDate } from "../model";
import { AccountSection } from "../panels/AccountSection";
import { DoctorBlock } from "../panels/DoctorBlock";
import { ThemeSwitch } from "../panels/SettingsPanel";
import type { Step } from "../panels/Toolbar";
import { useDarkTheme } from "../board/mediaItems";
import { ROLE_LABEL, useAccount } from "../state/account";
import {
  ACCOUNTS_FOOT,
  PRIVACY_URL,
  privacyLine,
  useHosting,
} from "../state/hosting";
import { IconGear, IconPlan, IconPlus, IconTrash } from "../ui/icons";

interface Props {
  onOpen: (id: string, step?: Step) => void;
}

const GEMINI_TIP =
  "En palier gratuit, Google peut utiliser le texte envoyé pour améliorer ses produits. " +
  "Seul le texte part chez Google ; les photos, plans et noms de projet n'y vont jamais.";

/** Galerie de tableaux (T5, principe d'augus : un projet = un tableau). La première carte
 *  « + Nouveau projet » ouvre directement un tableau vide ; chaque projet montre sa vignette
 *  (le rendu retenu ou le plus récent, sinon le plan, la première photo ou la note
 *  Exigences), son nom (renommable) et son état. Plus d'écran à prompt : exigences, photos
 *  et plan se donnent sur le tableau. */
export function Home({ onOpen }: Props) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [apiOk, setApiOk] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [doctor, setDoctor] = useState<DoctorReport | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [homeSettings, setHomeSettings] = useState<HomeSettings | null>(null);
  const [creating, setCreating] = useState(false);
  const gemini = Boolean(
    homeSettings?.remote_model && homeSettings.gemini_text,
  );
  const hosting = useHosting();
  const account = useAccount();
  // Avec les comptes, le bilan de l'installation est réservé aux administrateurs.
  const showDoctor = !account || account.me.is_admin;

  useEffect(() => {
    if (showDoctor) api.doctor().then(setDoctor, () => undefined);
    homeApi.settings().then(setHomeSettings, () => undefined);
  }, [showDoctor]);

  const refresh = () =>
    api.listProjects().then(
      (list) => {
        setProjects(list);
        setApiOk(true);
      },
      (e: unknown) => {
        setApiOk(false);
        setError(String(e));
      },
    );
  useEffect(() => {
    void refresh();
  }, []);

  const toggleGemini = (on: boolean) => {
    setHomeSettings((s) => (s ? { ...s, gemini_text: on } : s));
    homeApi.setGemini(on).then(setHomeSettings, (e: unknown) => {
      setHomeSettings((s) => (s ? { ...s, gemini_text: !on } : s));
      setError(String(e));
    });
  };

  // « + Nouveau projet » : nom automatique (modifiable), le tableau s'ouvre tout de suite.
  const createProject = async () => {
    setCreating(true);
    try {
      const project = await api.createAutoNamedProject(gemini);
      onOpen(project.id);
    } catch (e) {
      setError(String(e));
      setCreating(false);
    }
  };

  return (
    <div className="boards">
      <header className="boards-top">
        <span className="brand-mark">
          <IconPlan />
        </span>
        <span className="boards-brand">{APP_NAME}</span>
        <span className="chip muted">vos espaces de travail</span>
        <span className="boards-sp" />
        <button
          type="button"
          className={`icon${settingsOpen ? " active" : ""}`}
          onClick={() => setSettingsOpen((o) => !o)}
          aria-label="Réglages"
          aria-expanded={settingsOpen}
          title={
            account
              ? "Réglages : thème, Gemini, compte"
              : "Réglages : thème, Gemini, état de l'installation"
          }
        >
          <IconGear />
        </button>
        {settingsOpen && (
          <div className="home-settings" role="dialog" aria-label="Réglages">
            <ThemeSwitch />
            {homeSettings?.remote_model && (
              <label className="home-gemini" title={GEMINI_TIP}>
                <input
                  type="checkbox"
                  checked={gemini}
                  onChange={(e) => toggleGemini(e.target.checked)}
                />
                <span>
                  Mieux comprendre mon texte avec Gemini — le texte seul part
                  chez Google, jamais les photos
                </span>
              </label>
            )}
            {doctor && <DoctorBlock report={doctor} />}
            <AccountSection />
          </div>
        )}
      </header>

      <main className="boards-main">
        {apiOk === false && (
          <div className="error-banner">
            L'API locale ne répond pas. Lance <code>make start</code> puis
            recharge la page.
          </div>
        )}
        <h1>Vos espaces de travail</h1>
        <p className="muted">
          Un espace de travail par projet : vos exigences, vos photos, vos
          inspirations, le plan et les rendus au même endroit.
        </p>
        <ul className="boards-cards" aria-label="Projets">
          <li>
            <button
              type="button"
              className="boards-new"
              onClick={() => void createProject()}
              disabled={creating}
            >
              <IconPlus />
              <span>{creating ? "Création…" : "Nouveau projet"}</span>
            </button>
          </li>
          {projects?.map((p) => (
            <ProjectCard
              key={p.id}
              project={p}
              onOpen={() => onOpen(p.id)}
              onChanged={() => void refresh()}
              onError={setError}
            />
          ))}
        </ul>
        {projects && projects.length === 0 && (
          <p className="muted">
            Aucun projet pour l'instant : « Nouveau projet » ouvre un espace de
            travail vide.
          </p>
        )}
        {error && <p className="warn">{error}</p>}
      </main>
      {account ? (
        // B1e : avec les comptes, le pied ne dit que la mention (détail sur le site).
        <footer className="boards-foot">
          <b>{APP_NAME}</b> · {ACCOUNTS_FOOT} ·{" "}
          <a href={PRIVACY_URL} target="_blank" rel="noopener">
            Confidentialité
          </a>
        </footer>
      ) : (
        <footer className="boards-foot">
          <b>{APP_NAME}</b>
          {/* A1, T6 : la mention suit l'état réel (machine ou serveur, case Gemini, suivi des
            erreurs) ; tant que l'hébergement n'est pas connu, elle n'affirme rien. */}
          {hosting && <> · {privacyLine(hosting, gemini)}</>} · une
          reconstruction estimée n'est jamais un relevé certifié.
        </footer>
      )}
    </div>
  );
}

/** Une carte de la galerie : vignette, nom (double-clic ou crayon pour renommer), état,
 *  corbeille. Un clic sur la vignette ou le nom ouvre le tableau. */
function ProjectCard({
  project: p,
  onOpen,
  onChanged,
  onError,
}: {
  project: ProjectSummary;
  onOpen: () => void;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const dark = useDarkTheme();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(p.name);
  // Un clic ouvre le projet, un double-clic le renomme : le clic attend un instant.
  const clickTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (clickTimer.current !== null) window.clearTimeout(clickTimer.current);
    },
    [],
  );

  const commit = async () => {
    const name = draft.trim();
    setEditing(false);
    if (!name || name === p.name) return;
    try {
      await api.updateSettings(p.id, { name });
      onChanged();
    } catch (e) {
      onError(String(e));
    }
  };

  const remove = async () => {
    if (
      !window.confirm(
        `Supprimer définitivement « ${p.name} » (espace de travail, scène, images, rendus) ?`,
      )
    )
      return;
    try {
      await api.deleteProject(p.id);
      onChanged();
    } catch (e) {
      onError(String(e));
    }
  };

  const photos =
    p.image_count === 0
      ? "Aucune photo"
      : `${p.image_count} photo${p.image_count > 1 ? "s" : ""}`;
  const plan =
    p.wall_count === 0
      ? "pas encore de plan"
      : `${p.wall_count} mur${p.wall_count > 1 ? "s" : ""}`;
  const info = `modifié le ${formatDate(p.updated_at)} · ${photos} · ${plan}`;
  // B1e : « Renommer » à partir d'éditeur, « Supprimer » pour le propriétaire (le serveur
  // refuse de toute façon) ; le rôle est dit quand on n'est pas propriétaire.
  const role = p.role ?? "owner";
  const canRename = role === "owner" || role === "editor";
  const canDelete = role === "owner";
  return (
    <li className="boards-card">
      <button
        type="button"
        className="boards-cover"
        onClick={onOpen}
        aria-label={`Ouvrir « ${p.name} »`}
      >
        <img
          src={api.coverUrl(p.id, p.updated_at, dark ? "dark" : "light")}
          alt=""
          loading="lazy"
        />
      </button>
      <span className="boards-card-foot">
        {editing ? (
          <input
            className="home-rename"
            aria-label={`Nouveau nom de « ${p.name} »`}
            value={draft}
            autoFocus
            maxLength={200}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => void commit()}
            onKeyDown={(e) => {
              if (e.key === "Enter") void commit();
              if (e.key === "Escape") setEditing(false);
            }}
          />
        ) : (
          <button
            type="button"
            className="home-name"
            title={`${p.name} · ${info}`}
            onClick={() => {
              if (clickTimer.current !== null)
                window.clearTimeout(clickTimer.current);
              clickTimer.current = window.setTimeout(() => {
                clickTimer.current = null;
                onOpen();
              }, 250);
            }}
            onDoubleClick={(e) => {
              e.preventDefault();
              if (clickTimer.current !== null)
                window.clearTimeout(clickTimer.current);
              clickTimer.current = null;
              if (!canRename) return onOpen();
              setDraft(p.name);
              setEditing(true);
            }}
          >
            {p.name}
          </button>
        )}
        <span className="muted boards-card-info">
          {role !== "owner" && (
            <span className="chip muted boards-role">
              {ROLE_LABEL[role].toLowerCase()}
            </span>
          )}
          {info}
        </span>
        <span className="boards-card-acts">
          {canRename && (
            <button
              type="button"
              className="icon home-act"
              aria-label={`Renommer « ${p.name} »`}
              title="Renommer (ou double-clic sur le nom)"
              onClick={() => {
                setDraft(p.name);
                setEditing(true);
              }}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.8}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4" />
              </svg>
            </button>
          )}
          {canDelete && (
            <button
              type="button"
              className="icon home-act"
              aria-label={`Supprimer « ${p.name} »`}
              title="Supprimer"
              onClick={() => void remove()}
            >
              <IconTrash />
            </button>
          )}
        </span>
      </span>
    </li>
  );
}
