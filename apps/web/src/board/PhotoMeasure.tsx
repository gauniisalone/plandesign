import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { createPortal } from "react-dom";
import {
  api,
  ApiError,
  hasToken,
  inspirationApi,
  readTokenForFetch,
  referenceApi,
  type JobStatus,
} from "../api/client";
import type { Project } from "../api/types.gen";
import { formatMeters, parseLength } from "../state/lengthInput";
import {
  beforeAfter,
  fusionOf,
  fusionTooltip,
  measurability,
  referenceOf,
  type Point,
  type PhotoScaleBasis,
} from "../state/photoReference";
import { RenderFromPhotoButton } from "./RealisticRenders";

/** KAN-66 (maquette validée par augus le 20 oct.) : la photo en grand dans un calque ; deux
 *  clics sur une longueur connue (loupe ×4 qui suit le curseur), sa longueur et un libellé
 *  facultatif ; le serveur recale l'estimation et renvoie le projet. Une mesure par photo : la
 *  modifier la remplace, la supprimer rend l'échelle de la hauteur de prise de vue. Un lecteur
 *  voit la mesure, sans rien pouvoir changer. Les refus du serveur sont affichés tels quels. */

const LOUPE = 150;
const ZOOM = 4;
const SEG_COLOR = "#ffd23f";

type Mode = "view" | "pick1" | "pick2" | "length";

const SOURCE_BADGE: Record<PhotoScaleBasis["source"], [string, string]> = {
  reference: ["mesurée", "measured"],
  camera_height: ["hauteur saisie", "estimated"],
  default: ["supposée", "estimated"],
};

function typingTarget(t: EventTarget | null): boolean {
  return (
    t instanceof HTMLElement &&
    (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)
  );
}

function placedOn(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const day = d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
  const time = d.toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
  });
  return `posée le ${day} à ${time}`;
}

/** L'image de la photo : un `<img>` ordinaire, ou fetch + blob quand l'onglet a un jeton
 *  (comme AuthedImg) ; la loupe la redessine depuis cet élément. */
function usePhotoSrc(url: string): string | null {
  const authed = hasToken();
  const [blob, setBlob] = useState<{ url: string; src: string } | null>(null);
  useEffect(() => {
    if (!authed) return;
    let off = false;
    let made: string | null = null;
    fetch(url, { headers: { Authorization: `Bearer ${readTokenForFetch()}` } })
      .then((r) =>
        r.ok ? r.blob() : Promise.reject(new Error(String(r.status))),
      )
      .then((b) => {
        if (off) return;
        made = URL.createObjectURL(b);
        setBlob({ url, src: made });
      })
      .catch(() => undefined);
    return () => {
      off = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [url, authed]);
  if (!authed) return url;
  return blob && blob.url === url ? blob.src : null;
}

export function PhotoMeasure({
  project,
  imageId,
  label,
  readOnly,
  onClose,
  onProjectChanged,
}: {
  project: Project;
  imageId: string;
  label: string;
  readOnly: boolean;
  onClose: () => void;
  onProjectChanged: (project: Project) => void;
}) {
  const image = project.images[imageId];
  const W = image?.width_px ?? 1;
  const H = image?.height_px ?? 1;
  const segment = referenceOf(project, imageId);
  const state = measurability(project, imageId);
  const basis = state.kind === "ready" ? state.basis : null;
  // KAN-95 : plan recoupé sur plusieurs photos ; le survol dit ce que chacune apporte.
  const fusedLevel =
    state.kind === "ready" ? project.levels[state.levelId] : undefined;
  const fused = fusedLevel ? fusionOf(fusedLevel) : null;
  const canMeasure = !readOnly && state.kind === "ready";

  const [mode, setMode] = useState<Mode>(() =>
    canMeasure && !segment ? "pick1" : "view",
  );
  const [p1, setP1] = useState<Point | null>(null);
  const [p2, setP2] = useState<Point | null>(null);
  const [hover, setHover] = useState<{
    at: Point;
    x: number;
    y: number;
  } | null>(null);
  const [lengthText, setLengthText] = useState("");
  const [labelText, setLabelText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [shown, setShown] = useState(0);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const loupeRef = useRef<HTMLCanvasElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    // Remis à vrai à chaque montage (le mode strict démonte et remonte une fois).
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const src = usePhotoSrc(api.imageFileUrl(project.id, imageId));

  // Largeur affichée de la photo : traits et pastilles gardent leur taille à l'écran.
  useLayoutEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const ro = new ResizeObserver(() => setShown(img.clientWidth));
    ro.observe(img);
    setShown(img.clientWidth);
    return () => ro.disconnect();
  }, [src]);
  const u = shown > 0 ? W / shown : 1; // pixels d'origine par pixel d'écran

  const picking = mode === "pick1" || mode === "pick2";

  const backToView = useCallback(() => {
    setMode("view");
    setP1(null);
    setP2(null);
    setHover(null);
    setError(null);
  }, []);

  // Le calque garde le clavier : Échap revient d'un pas (ou ferme), Retour arrière reprend le
  // premier point ; rien n'atteint les raccourcis de l'espace de travail, ni un collage.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (typingTarget(e.target)) {
        if (e.key === "Escape") {
          e.stopPropagation();
          backToView();
        }
        return;
      }
      e.stopPropagation();
      if (e.key === "Escape") {
        e.preventDefault();
        if (mode === "view") onClose();
        else backToView();
      } else if (e.key === "Backspace" && mode === "pick2") {
        e.preventDefault();
        setP1(null);
        setMode("pick1");
      }
    };
    const paste = (e: ClipboardEvent) => {
      if (!typingTarget(e.target)) e.stopPropagation();
    };
    window.addEventListener("keydown", down, true);
    window.addEventListener("paste", paste, true);
    return () => {
      window.removeEventListener("keydown", down, true);
      window.removeEventListener("paste", paste, true);
    };
  }, [mode, onClose, backToView]);

  useEffect(() => {
    rootRef.current?.focus();
  }, []);

  const toImage = (e: ReactPointerEvent): Point | null => {
    const img = imgRef.current;
    if (!img) return null;
    const r = img.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return null;
    const x = ((e.clientX - r.left) / r.width) * W;
    const y = ((e.clientY - r.top) / r.height) * H;
    if (x < 0 || y < 0 || x > W || y > H) return null;
    return [Math.round(x * 10) / 10, Math.round(y * 10) / 10];
  };

  // La loupe ×4 : la photo autour du curseur, quatre fois plus grande qu'à l'écran, réticule
  // au centre ; le premier point et le segment en cours y sont redessinés.
  useEffect(() => {
    const canvas = loupeRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !hover || !picking) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = LOUPE * ratio;
    canvas.height = LOUPE * ratio;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const span = (LOUPE * u) / ZOOM; // pixels d'origine montrés
    const [cx, cy] = hover.at;
    const nx = img.naturalWidth / W;
    const ny = img.naturalHeight / H;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, LOUPE, LOUPE);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(
      img,
      (cx - span / 2) * nx,
      (cy - span / 2) * ny,
      span * nx,
      span * ny,
      0,
      0,
      LOUPE,
      LOUPE,
    );
    const k = LOUPE / span;
    const toLoupe = ([x, y]: Point): Point => [
      (x - cx) * k + LOUPE / 2,
      (y - cy) * k + LOUPE / 2,
    ];
    if (mode === "pick2" && p1) {
      const [ax, ay] = toLoupe(p1);
      ctx.strokeStyle = SEG_COLOR;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(LOUPE / 2, LOUPE / 2);
      ctx.stroke();
      ctx.fillStyle = SEG_COLOR;
      ctx.beginPath();
      ctx.arc(ax, ay, 4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.strokeStyle = "#ff2d55";
    ctx.lineWidth = 1;
    const c = LOUPE / 2;
    ctx.beginPath();
    ctx.moveTo(0, c);
    ctx.lineTo(c - 4, c);
    ctx.moveTo(c + 4, c);
    ctx.lineTo(LOUPE, c);
    ctx.moveTo(c, 0);
    ctx.lineTo(c, c - 4);
    ctx.moveTo(c, c + 4);
    ctx.lineTo(c, LOUPE);
    ctx.stroke();
  }, [hover, picking, mode, p1, u, W, H]);

  const onMove = (e: ReactPointerEvent) => {
    if (!picking) return;
    const at = toImage(e);
    setHover(at ? { at, x: e.clientX, y: e.clientY } : null);
  };

  const onClick = (e: ReactPointerEvent) => {
    if (!picking) return;
    const at = toImage(e);
    if (!at) return;
    setError(null);
    if (mode === "pick1") {
      setP1(at);
      setMode("pick2");
    } else if (p1) {
      // Deux clics au même endroit (moins de 3 px à l'écran) : le second ne compte pas.
      if (Math.hypot(at[0] - p1[0], at[1] - p1[1]) < 3 * u) return;
      setP2(at);
      setHover(null);
      setMode("length");
    }
  };

  const startNew = () => {
    setToast(null);
    setError(null);
    setP1(null);
    setP2(null);
    setLengthText(segment ? formatMeters(segment.length_m) : "");
    setLabelText(segment?.label ?? "");
    setMode("pick1");
  };

  const startEdit = () => {
    if (!segment) return;
    setToast(null);
    setError(null);
    setP1(segment.p1);
    setP2(segment.p2);
    setLengthText(formatMeters(segment.length_m));
    setLabelText(segment.label ?? "");
    setMode("length");
  };

  const parsed = parseLength(lengthText);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!p1 || !p2 || busy) return;
    if (!parsed.ok) {
      setError(parsed.error);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await referenceApi.put(project.id, imageId, {
        p1,
        p2,
        length_m: parsed.meters,
        label: labelText.trim() || null,
      });
      if (!alive.current) return;
      onProjectChanged(next);
      setToast(
        "Estimation recalée : le plan, la 3D et les cotes suivent la mesure.",
      );
      backToView();
    } catch (err) {
      if (alive.current)
        setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const remove = async () => {
    if (!segment || busy) return;
    setBusy(true);
    setError(null);
    setToast(null);
    try {
      const next = await referenceApi.remove(project.id, imageId, segment.id);
      if (!alive.current) return;
      onProjectChanged(next);
      setToast(
        "Mesure retirée : l'échelle revient à la hauteur de prise de vue.",
      );
    } catch (err) {
      if (alive.current)
        setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const reestimate = async () => {
    if (state.kind !== "reestimate" || busy) return;
    setBusy(true);
    setError(null);
    try {
      let current = await referenceApi.reestimate(
        project.id,
        state.levelId,
        imageId,
      );
      setJob(current);
      while (
        (current.status === "running" || current.status === "queued") &&
        alive.current
      ) {
        await new Promise((r) => window.setTimeout(r, 1500));
        current = await inspirationApi.job(project.id, current.id);
        if (alive.current) setJob(current);
      }
      if (!alive.current) return;
      if (current.status === "done") {
        const next = await api.getProject(project.id);
        if (!alive.current) return;
        onProjectChanged(next);
        setJob(null);
        if (measurability(next, imageId).kind === "ready") {
          setToast("Photo prête pour la mesure.");
          setMode("pick1");
        }
      } else {
        setError(current.error ?? "La réestimation n'a pas abouti.");
      }
    } catch (err) {
      if (alive.current)
        setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  // Ce qui est tracé sur la photo : le segment en cours, sinon la mesure posée.
  const drawn: { a: Point; b: Point; dashed: boolean } | null =
    mode === "pick2" && p1 && hover
      ? { a: p1, b: hover.at, dashed: true }
      : (mode === "length" || mode === "pick2") && p1 && p2
        ? { a: p1, b: p2, dashed: false }
        : mode === "view" && segment
          ? { a: segment.p1, b: segment.p2, dashed: false }
          : null;
  const cote =
    mode === "view" && segment
      ? `${formatMeters(segment.length_m)}${segment.label ? ` · ${segment.label}` : ""}`
      : null;
  const mid: Point | null = drawn
    ? [(drawn.a[0] + drawn.b[0]) / 2, (drawn.a[1] + drawn.b[1]) / 2]
    : null;
  // Fraction de la largeur affichée : la bulle de saisie se place à droite du segment, ou à
  // gauche s'il est dans la moitié droite de la photo.
  const midFx = mid ? mid[0] / W : 0;
  const midFy = mid ? mid[1] / H : 0;

  const step =
    mode === "pick1"
      ? [
          "Point 1 sur 2",
          "Cliquez le premier bout d'une longueur connue · Échap pour annuler",
        ]
      : mode === "pick2"
        ? [
            "Point 2 sur 2",
            "Cliquez l'autre bout · Retour arrière pour reprendre le point 1",
          ]
        : mode === "length"
          ? ["Longueur", "Saisissez la longueur réelle · Entrée pour valider"]
          : readOnly
            ? ["Lecture seule", ""]
            : segment
              ? [
                  "Mesure posée",
                  "« Modifier » pour changer les points ou la longueur",
                ]
              : ["Aucune mesure", ""];

  const badge = basis ? SOURCE_BADGE[basis.source] : null;
  const changes = basis ? beforeAfter(basis) : null;

  const loupeStyle = hover
    ? {
        left:
          hover.x + 28 + LOUPE > window.innerWidth
            ? hover.x - 28 - LOUPE
            : hover.x + 28,
        top: hover.y - 28 - LOUPE < 8 ? hover.y + 28 : hover.y - 28 - LOUPE,
      }
    : undefined;

  return createPortal(
    <div
      ref={rootRef}
      tabIndex={-1}
      className="photo-measure"
      role="dialog"
      aria-modal="true"
      aria-label={readOnly ? "Mesures de la photo" : "Mesurer sur la photo"}
      onPointerDown={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
    >
      <div className="pm-stage">
        <div className="pm-top">
          <span className="pm-step">{step[0]}</span>
          <span className="pm-hint">{step[1]}</span>
          {/* KAN-91 (D5) : rendre le point de vue exact de cette photo. */}
          <RenderFromPhotoButton
            project={project}
            imageId={imageId}
            readOnly={readOnly}
          />
        </div>
        <div className="pm-photo-wrap">
          {src ? (
            <div className="pm-photo-box">
              <img
                ref={imgRef}
                src={src}
                alt={label || "Photo"}
                draggable={false}
                className={picking ? "pm-picking" : undefined}
                onLoad={(e) => setShown(e.currentTarget.clientWidth)}
                onPointerMove={onMove}
                onPointerLeave={() => setHover(null)}
                onPointerUp={onClick}
              />
              <svg
                className="pm-overlay"
                viewBox={`0 0 ${W} ${H}`}
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                {drawn && (
                  <g data-testid="pm-segment">
                    <line
                      x1={drawn.a[0]}
                      y1={drawn.a[1]}
                      x2={drawn.b[0]}
                      y2={drawn.b[1]}
                      stroke="#000"
                      strokeOpacity={0.45}
                      strokeWidth={7 * u}
                    />
                    <line
                      x1={drawn.a[0]}
                      y1={drawn.a[1]}
                      x2={drawn.b[0]}
                      y2={drawn.b[1]}
                      stroke={SEG_COLOR}
                      strokeWidth={4 * u}
                      strokeDasharray={
                        drawn.dashed ? `${12 * u} ${8 * u}` : undefined
                      }
                    />
                  </g>
                )}
                {[mode === "view" ? segment?.p1 : p1, drawn ? drawn.b : null]
                  .filter((p): p is Point => !!p)
                  .map(([x, y], i) => (
                    <circle
                      key={i}
                      cx={x}
                      cy={y}
                      r={7 * u}
                      fill={SEG_COLOR}
                      stroke="#fff"
                      strokeWidth={2.5 * u}
                    />
                  ))}
              </svg>
              {cote && mid && (
                <span
                  className="pm-cote"
                  style={{ left: `${midFx * 100}%`, top: `${midFy * 100}%` }}
                >
                  {cote}
                </span>
              )}
              {mode === "length" && mid && (
                <form
                  className={`pm-length${midFx > 0.5 ? " left" : ""}`}
                  style={{
                    left: `${midFx * 100}%`,
                    top: `${Math.min(Math.max(midFy * 100, 15), 70)}%`,
                  }}
                  role="dialog"
                  aria-label="Longueur du segment"
                  onSubmit={(e) => void submit(e)}
                >
                  <h2>Longueur réelle de ce segment</h2>
                  <label>
                    Longueur
                    <input
                      autoFocus
                      value={lengthText}
                      inputMode="decimal"
                      placeholder="ex. 2,04 m ou 204 cm"
                      onChange={(e) => {
                        setLengthText(e.target.value);
                        setError(null);
                      }}
                    />
                  </label>
                  <p
                    className={`pm-parsed${parsed.ok || !lengthText.trim() ? "" : " bad"}`}
                  >
                    {parsed.ok
                      ? `= ${formatMeters(parsed.meters)}`
                      : lengthText.trim()
                        ? parsed.error
                        : "Sans unité : 2 = 2 m, 204 = 204 cm, 2,04 = 2,04 m"}
                  </p>
                  <label>
                    Libellé (facultatif)
                    <input
                      value={labelText}
                      maxLength={60}
                      placeholder="ex. porte, plan de travail"
                      onChange={(e) => setLabelText(e.target.value)}
                    />
                  </label>
                  {error && (
                    <div className="pm-alert" role="alert">
                      <b>Mesure refusée</b>
                      {error}
                    </div>
                  )}
                  <div className="pm-length-actions">
                    <button type="button" className="link" onClick={startNew}>
                      Reprendre les points
                    </button>
                  </div>
                  <div className="pm-length-actions">
                    <span className="pm-sp" />
                    <button type="button" onClick={backToView}>
                      Annuler
                    </button>
                    <button
                      type="submit"
                      className="primary"
                      disabled={busy || !parsed.ok}
                    >
                      {busy ? "Recalage…" : "Recaler l'estimation"}
                    </button>
                  </div>
                </form>
              )}
            </div>
          ) : (
            <span className="pm-missing muted">Chargement de la photo…</span>
          )}
        </div>
      </div>

      <aside className="pm-side" aria-label="Mesures de la photo">
        <header>
          <h2>{readOnly ? "Mesures de la photo" : "Mesurer sur la photo"}</h2>
          <button type="button" className="link" onClick={onClose}>
            Fermer
          </button>
        </header>
        <div className="pm-scale" aria-label="Échelle de l'estimation">
          {basis ? (
            <>
              <span title={fused ? fusionTooltip(fused) : undefined}>
                {basis.text}
              </span>
              {badge && (
                <span className={`pm-badge ${badge[1]}`}>{badge[0]}</span>
              )}
              {changes && (
                <span className="pm-changes muted">
                  Dernier recalage : {changes.join(" · ")}
                </span>
              )}
            </>
          ) : state.kind === "reestimate" ? (
            <>
              <span>
                La profondeur de cette photo n'a pas été gardée : elle doit être
                réestimée avant de pouvoir y mesurer une longueur.
              </span>
              {!readOnly && (
                <button
                  type="button"
                  className="primary"
                  disabled={busy}
                  onClick={() => void reestimate()}
                >
                  Réestimer la photo pour pouvoir mesurer
                </button>
              )}
              {job && (
                <span className="muted" role="status">
                  {job.stage || job.message}
                  {job.remaining_s != null &&
                    ` · environ ${Math.round(job.remaining_s)} s`}
                </span>
              )}
            </>
          ) : (
            <span>
              Cette photo n'a pas servi à estimer un plan : estimez d'abord le
              plan depuis la photo pour pouvoir y mesurer une longueur.
            </span>
          )}
        </div>
        {toast && (
          <div className="pm-toast" role="status">
            {toast}
          </div>
        )}
        {error && mode !== "length" && (
          <div className="pm-alert" role="alert">
            <b>Refusé</b>
            {error}
          </div>
        )}
        <h3>Mesure de référence</h3>
        {segment ? (
          <ul className="pm-list" aria-label="Mesures de référence">
            <li>
              <div>
                <span className="pm-len">{formatMeters(segment.length_m)}</span>
                {segment.label && ` · ${segment.label}`}
              </div>
              <div className="pm-who">{placedOn(segment.created_at)}</div>
              {canMeasure && (
                <div className="pm-acts">
                  <button type="button" onClick={startEdit} disabled={busy}>
                    Modifier
                  </button>
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void remove()}
                    disabled={busy}
                  >
                    Supprimer
                  </button>
                </div>
              )}
            </li>
          </ul>
        ) : (
          <p className="pm-empty muted">Aucune mesure sur cette photo.</p>
        )}
        {canMeasure && !segment && mode === "view" && (
          <button type="button" className="primary" onClick={startNew}>
            Mesurer une longueur
          </button>
        )}
        {readOnly ? (
          <p className="pm-readonly">
            Lecture seule : vous voyez la mesure de cette photo ; seuls les
            éditeurs peuvent en poser ou la changer.
          </p>
        ) : (
          <p className="pm-help muted">
            Choisissez deux points sur un objet dont vous connaissez la longueur
            (une porte fait souvent 2,04 m de haut), puis saisissez-la. La loupe
            aide à viser le coin exact. Une mesure par photo : en poser une
            autre remplace la précédente.
          </p>
        )}
      </aside>

      {picking && hover && (
        <div
          className="pm-loupe"
          style={loupeStyle}
          role="img"
          aria-label="Loupe ×4"
        >
          <canvas ref={loupeRef} width={LOUPE} height={LOUPE} />
          <span className="pm-loupe-tag">×4</span>
        </div>
      )}
    </div>,
    document.body,
  );
}
