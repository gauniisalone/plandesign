import type {
  Calibration,
  DerivedLevel,
  Furniture,
  Measurement,
  Point2,
  Project,
  Source,
  StepResult,
  ValidationReport,
  Violation,
} from "./types.gen";
import type { Op } from "./ops";
import { tuned } from "../netTuning";

const BASE = "/api";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly report?: ValidationReport,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface EditResult {
  project: Project;
  report: ValidationReport;
  derived: DerivedLevel;
}

/** T7e : sorte de « + Nouveau plan » : variante de ce plan, nouvel étage, plan vide. */
export type NewPlanKind = "variant" | "storey" | "empty";

export interface NewPlanBody {
  name: string;
  kind: NewPlanKind;
  /** Le plan ouvert ; absent = le premier du projet. */
  source_level_id?: string | null;
}

export interface CalibrationResult {
  calibration: Calibration;
  project: Project;
  applied: boolean;
}

export interface ProjectSummary {
  id: string;
  name: string;
  updated_at: string;
  /** Dossier sur la machine ; absent quand l'API est exposée ou les comptes actifs. */
  path: string | null;
  created_at: string | null;
  image_count: number;
  wall_count: number;
  has_thumbnail: boolean;
  /** B1e : rôle de l'utilisateur (toujours « owner » en mode local). */
  role?: Role;
}

/** Corps de création d'une mesure : les champs à défaut sont facultatifs côté client. */
export interface MeasurementCreate {
  kind: Measurement["kind"];
  value: number;
  tolerance?: number;
  element_id?: string;
  vertex_ids?: [string, string];
  note?: string;
}

export interface ProjectSettingsUpdate {
  name?: string;
  units_display?: Project["units_display"];
  privacy?: Project["privacy"];
  render?: Project["render"];
  /** Consigne gardée depuis l'accueil (L1b) ; "" l'efface. */
  pending_instruction?: string;
  /** Réponses guidées de la note Exigences (T1) ; un objet vide les efface. */
  pending_answers?: GuidedAnswers;
}

export type ImageRef = Project["images"][string];

export interface ImageImportResult {
  image: ImageRef;
  warnings: string[];
  project: Project;
}

const TOKEN_KEY = "openplan_api_token";

/** Jeton d'accès quand l'API est exposée hors de la boucle locale : gardé pour l'onglet
 *  (sessionStorage), envoyé en en-tête, jamais dans l'URL. Demandé une fois sur 401. */
function readToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function askToken(): string | null {
  const token = window.prompt(
    "L'API demande un jeton d'accès (affiché au lancement de `openplan serve`) :",
  );
  if (!token) return null;
  try {
    sessionStorage.setItem(TOKEN_KEY, token.trim());
  } catch {
    /* stockage indisponible : le jeton ne vaut que pour cette requête */
  }
  // Le jeton connu, le cookie de lecture suit (images, téléchargements, GLB).
  void openReadSession(token.trim());
  return token.trim();
}

/** Un jeton est connu de cet onglet (API exposée) : les images du tableau passent alors par
 *  fetch + blob avec l'en-tête, repli sûr si le cookie de lecture est bloqué. */
export function hasToken(): boolean {
  return readToken() !== null;
}

/** Le jeton pour un fetch d'image hors `request` (jamais mis dans une URL). */
export function readTokenForFetch(): string {
  return readToken() ?? "";
}

const READ_SESSION_RENEW_MS = 6 * 3600 * 1000;
let readSessionTimer: number | null = null;

/** Cookie de lecture (API exposée) : `POST /api/session` avec le jeton pose un cookie
 *  HttpOnly limité à GET/HEAD, qui porte les `<img src>`, `<a download>` et le GLB ; 12 h,
 *  renouvelé toutes les 6 h tant que l'onglet vit. Sans jeton (boucle locale) : rien. */
export async function openReadSession(
  token: string | null = readToken(),
): Promise<void> {
  if (!token) return;
  try {
    await fetch(`${BASE}/session`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    /* hors ligne ou API locale : les images passeront par fetch + blob */
  }
  if (readSessionTimer === null)
    readSessionTimer = window.setInterval(
      () => void openReadSession(),
      READ_SESSION_RENEW_MS,
    );
}

export const sessionApi = {
  open: () => openReadSession(),
  close: () => request<void>("/session", { method: "DELETE" }),
};

/** B1d : comptes actifs (lu dans /api/health au démarrage). Une 401 veut alors dire « plus de
 *  session » : l'application revient à la page de connexion, sans demander de jeton. */
let accountsMode = false;
let signedOutHandler: (() => void) | null = null;
/** B2a : la connexion de présence de cet onglet ; envoyée avec chaque requête, pour que le
 *  serveur ne nous annonce pas nos propres changements. */
let liveConn: string | null = null;

export function setLiveConn(conn: string | null): void {
  liveConn = conn;
}

export function setAccountsMode(on: boolean, onSignedOut: () => void): void {
  accountsMode = on;
  signedOutHandler = onSignedOut;
}

/** KAN-49 : session perdue apprise ailleurs que par une réponse 401 (connexion de présence
 *  refusée puis /api/auth/me en 401) : la page de connexion tout de suite, sans dépendre
 *  d'une autre requête (un rechargement lent ou coupé la retardait, parfois pour toujours).
 *  L'adresse ne change pas : après la reconnexion, on revient où l'on était. */
export function signalSignedOut(): void {
  if (accountsMode) signedOutHandler?.();
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  // Un corps texte est du JSON ; un FormData (import de fichier) laisse le navigateur fixer le type.
  const headers = new Headers(init.headers);
  if (typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const token = readToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (liveConn) headers.set("X-Live-Conn", liveConn);
  let response = await fetch(BASE + path, { ...init, headers });
  if (response.status === 401 && accountsMode) {
    signedOutHandler?.();
  } else if (response.status === 401) {
    const fresh = askToken();
    if (fresh) {
      headers.set("Authorization", `Bearer ${fresh}`);
      response = await fetch(BASE + path, { ...init, headers });
    }
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    let report: ValidationReport | undefined;
    try {
      const body = (await response.json()) as {
        detail?: unknown;
        report?: ValidationReport;
      };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail !== undefined) detail = JSON.stringify(body.detail);
      report = body.report;
    } catch {
      /* corps non JSON : on garde le statut */
    }
    throw new ApiError(detail, response.status, report);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Connexion lente (25 oct.) : un envoi sans le moindre progrès pendant ce délai est abandonné
 *  (avant, `fetch` sans délai pouvait attendre pour toujours). Le délai court depuis le dernier
 *  octet parti, puis pendant la réponse : un envoi lent mais qui avance n'est jamais coupé. */
export const STALL_MS = 45_000;

/** L'envoi n'a pas abouti : connexion trop lente ou coupée (aucun progrès pendant STALL_MS,
 *  ou réseau perdu). Le message est en ton neutre, prêt à être montré. */
export class UploadStalled extends Error {
  constructor() {
    super("la connexion est trop lente ou coupée");
    this.name = "UploadStalled";
  }
}

/** Le corps compressé en gzip par le navigateur (`CompressionStream`) ; null s'il ne sait pas
 *  le faire (le corps part alors tel quel). */
export async function gzipped(data: Blob): Promise<Blob | null> {
  if (typeof CompressionStream === "undefined") return null;
  try {
    const stream = data.stream().pipeThrough(new CompressionStream("gzip"));
    return await new Response(stream).blob();
  } catch {
    return null;
  }
}

/** Un envoi avec sa progression réelle (`XMLHttpRequest` : `fetch` ne la donne pas), un délai
 *  sans progrès (STALL_MS) et les mêmes en-têtes et erreurs que `request`. `onProgress` reçoit
 *  la part déjà envoyée (0 à 1). */
function sendWithProgress<T>(
  path: string,
  init: {
    method: string;
    headers: Record<string, string>;
    body: Blob;
    onProgress?: (fraction: number) => void;
    stallMs?: number;
  },
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(init.method, BASE + path);
    xhr.withCredentials = true;
    for (const [k, v] of Object.entries(init.headers))
      xhr.setRequestHeader(k, v);
    const token = readToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    if (liveConn) xhr.setRequestHeader("X-Live-Conn", liveConn);
    let timer = 0;
    const arm = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(
        () => {
          xhr.abort();
          reject(new UploadStalled());
        },
        init.stallMs ?? tuned("stallMs", STALL_MS),
      );
    };
    xhr.upload.onprogress = (e) => {
      arm();
      if (e.lengthComputable && e.total > 0)
        init.onProgress?.(e.loaded / e.total);
    };
    xhr.upload.onload = () => {
      init.onProgress?.(1);
      arm(); // la réponse a le même délai
    };
    xhr.onerror = () => {
      window.clearTimeout(timer);
      reject(new UploadStalled());
    };
    xhr.onload = () => {
      window.clearTimeout(timer);
      let body: { detail?: unknown; report?: ValidationReport } | T | undefined;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : undefined;
      } catch {
        body = undefined;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as T);
        return;
      }
      if (xhr.status === 401 && accountsMode) signedOutHandler?.();
      const err = (body ?? {}) as {
        detail?: unknown;
        report?: ValidationReport;
      };
      const detail =
        typeof err.detail === "string"
          ? err.detail
          : err.detail !== undefined
            ? JSON.stringify(err.detail)
            : `${xhr.status} ${xhr.statusText}`;
      reject(new ApiError(detail, xhr.status, err.report));
    };
    arm();
    xhr.send(init.body);
  });
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});

/** B1d : l'utilisateur connecté (comptes actifs). */
export interface Me {
  id: string;
  email: string;
  name: string;
  is_admin: boolean;
  /** Compte créé par cette connexion : la fenêtre propose « Votre nom ». */
  first_login?: boolean;
}

export type Role = "viewer" | "commenter" | "editor" | "owner";

/** B1e : un lien d'invitation en attente (le jeton n'est rendu qu'à la création). */
export interface InvitationOut {
  id: string;
  role: Role;
  created_at: string;
  expires_at: string;
  /** KAN-55 : l'adresse d'une invitation nominative ; absente pour un lien. */
  email?: string | null;
}

export interface MemberOut {
  user_id: string;
  email: string;
  name: string;
  role: Role;
  you: boolean;
}

/** Menu « Partager » (B1e) : réservé au propriétaire. */
export const shareApi = {
  invite: (pid: string, role: Role, days: 1 | 7 | 30) =>
    request<InvitationOut & { token: string }>(
      `/projects/${pid}/invitations`,
      json({ role, days }),
    ),
  invitations: (pid: string) =>
    request<InvitationOut[]>(`/projects/${pid}/invitations`),
  revoke: (pid: string, id: string) =>
    request<void>(`/projects/${pid}/invitations/${id}`, { method: "DELETE" }),
  /** KAN-55 : toujours « Invitation envoyée », que l'adresse ait un compte ou non. */
  inviteEmail: (pid: string, email: string, role: Role) =>
    request<{ detail: string }>(
      `/projects/${pid}/invitations/email`,
      json({ email, role }),
    ),
  members: (pid: string) => request<MemberOut[]>(`/projects/${pid}/members`),
  setRole: (pid: string, userId: string, role: Role) =>
    request<MemberOut[]>(`/projects/${pid}/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
  remove: (pid: string, userId: string) =>
    request<MemberOut[]>(`/projects/${pid}/members/${userId}`, {
      method: "DELETE",
    }),
};

/** KAN-55 : une invitation nominative qui m'attend (cloche, comptes seulement). */
export interface MyInvitation {
  id: string;
  project_id: string;
  project_name: string;
  role: Role;
  inviter_name: string;
  created_at: string;
  expires_at: string;
}

export const myInvitationsApi = {
  list: () => request<MyInvitation[]>("/invitations"),
  accept: (id: string) =>
    request<{ project_id: string; role: Role }>(`/invitations/${id}/accept`, {
      method: "POST",
    }),
  decline: (id: string) =>
    request<void>(`/invitations/${id}/decline`, { method: "POST" }),
};

/** Le lien à copier : le jeton dans l'ancre (jamais envoyé au serveur dans une URL). */
export function invitationLink(token: string): string {
  return `${window.location.origin}/#/invitation/${token}`;
}

/** KAN-89 : rendus réalistes calculés par le poste d'augus (comptes autorisés seulement). */
export interface RealisticStatus {
  allowed: boolean;
  /** `waiting` : en ligne, mais le poste finit un autre travail (verrou, GPU, tests). */
  worker: "online" | "offline" | "paused" | "busy" | "waiting";
  quota: number;
  used_today: number;
}

export interface RealisticRequest {
  id: string;
  level_id: string;
  camera_id: string;
  state: "pending" | "taken" | "done" | "failed" | "expired";
  created_at: string;
  finished_at: string | null;
  expires_at: string;
  seconds: number | null;
  width: number | null;
  height: number | null;
  error: string | null;
  image: boolean;
  /** KAN-91 : début du calcul (barre d'avancement). */
  taken_at?: string | null;
  /** KAN-91 (D5) : la photo dont la caméra estimée est la vue du rendu ; null : vue libre. */
  photo_id?: string | null;
  /** KAN-97 : mots de style lus par le poste (français), à défaut les noms des images
   * d'inspiration ; [] : sans inspiration ; null : rendu d'avant KAN-97. */
  guided_by?: string[] | null;
  /** KAN-97 : vrai quand `guided_by` vient des images lues par le poste. */
  guided_read?: boolean;
}

/** KAN-97 : « Guidé par : … » d'un rendu, ou null s'il n'en dit rien (rendu ancien). */
export function guidedLine(r: RealisticRequest): string | null {
  const g = r.guided_by;
  if (g == null) return null;
  if (g.length === 0) return "Sans inspiration";
  return `Guidé par : ${g.join(", ")}${r.guided_read ? "" : " (non lues par le poste)"}`;
}

/** KAN-91 : une pastille de réaction (KAN-78) sur un rendu réaliste. */
export interface RealisticReaction {
  item_id: string;
  key: ReactionKey;
  emoji: string;
  count: number;
  mine: boolean;
  names: string[];
}

export interface RealisticListing {
  status: RealisticStatus;
  requests: RealisticRequest[];
  /** Médiane des derniers rendus faits (s), sinon 120. */
  typical_seconds?: number;
  cover_id?: string | null;
  reactions?: RealisticReaction[];
}

export const realisticApi = {
  status: () => request<RealisticStatus>("/realistic/status"),
  list: (pid: string) =>
    request<RealisticListing>(`/projects/${pid}/realistic`),
  ask: (pid: string, levelId: string, cameraId: string) =>
    request<RealisticRequest>(
      `/projects/${pid}/levels/${levelId}/realistic`,
      json({ camera_id: cameraId }),
    ),
  imageUrl: (pid: string, requestId: string) =>
    `${BASE}/projects/${pid}/realistic/${requestId}/image`,
  /** KAN-91 (D5) : le point de vue exact de la photo (sa caméra estimée). */
  fromPhoto: (pid: string, levelId: string, imageId: string) =>
    request<RealisticRequest>(
      `/projects/${pid}/levels/${levelId}/realistic/from-photo`,
      json({ image_id: imageId }),
    ),
  /** KAN-91 : même vue, autre tirage. */
  variant: (pid: string, requestId: string) =>
    request<RealisticRequest>(
      `/projects/${pid}/realistic/${requestId}/variant`,
      { method: "POST" },
    ),
  setCover: (pid: string, requestId: string) =>
    request<{ cover_id: string | null }>(
      `/projects/${pid}/realistic/${requestId}/cover`,
      { method: "PUT" },
    ),
  clearCover: (pid: string) =>
    request<{ cover_id: string | null }>(`/projects/${pid}/realistic/cover`, {
      method: "DELETE",
    }),
  /** KAN-91 (D2) : la carte Rendus a-t-elle un plan relié ? Lu sur GET …/board/inputs/{id} du
   *  lot L1 (`can_render`). Route absente (base d'avant L1) ou carte inconnue : null, l'appelant
   *  garde son repli. */
  planLinked: (pid: string, frameId: string): Promise<boolean | null> =>
    request<{ can_render?: boolean }>(
      `/projects/${pid}/board/inputs/${frameId}`,
    ).then(
      (r) => (typeof r.can_render === "boolean" ? r.can_render : null),
      () => null,
    ),
  react: (pid: string, requestId: string, key: ReactionKey, on: boolean) =>
    request<RealisticReaction[]>(
      `/projects/${pid}/realistic/${requestId}/reactions/${key}`,
      { method: on ? "PUT" : "DELETE" },
    ),
};

/** Connexion par code (B1b2) et rôle par projet (B1d). */
export const authApi = {
  /** Même réponse que l'adresse ait un compte ou non. */
  /** `invitation` (B1e) : le jeton d'un lien d'invitation, qui ouvre l'inscription. */
  start: (email: string, invitation?: string) =>
    request<{ detail: string }>(
      "/auth/email/start",
      json({ email, invitation: invitation ?? null }),
    ),
  verify: (email: string, code: string, invitation?: string) =>
    request<Me>(
      "/auth/email/verify",
      json({ email, code, invitation: invitation ?? null }),
    ),
  /** B1e : le lien d'invitation ouvert (jeton lu dans l'ancre, jamais dans une URL). */
  acceptInvitation: (token: string) =>
    request<{ project_id: string; role: Role }>(
      "/auth/invitation/accept",
      json({ token }),
    ),
  /** null sans session : lu hors de `request`, une 401 attendue ne déclenche rien. */
  me: async (): Promise<Me | null> => {
    const response = await fetch(`${BASE}/auth/me`);
    if (response.status === 401) return null;
    if (!response.ok)
      throw new ApiError(
        `${response.status} ${response.statusText}`,
        response.status,
      );
    return (await response.json()) as Me;
  },
  /** « Votre nom » (B2a) : ce que voient les autres membres ; vide, le début de l'adresse. */
  setName: (name: string) =>
    request<Me>("/auth/me", {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  logoutAll: () => request<void>("/auth/logout-all", { method: "POST" }),
  role: (pid: string) => request<{ role: Role }>(`/projects/${pid}/role`),
  /** KAN-12 : ma place disque (octets) ; `quota_bytes` null : sans limite. */
  usage: () =>
    request<{ used_bytes: number; quota_bytes: number | null }>("/auth/usage"),
};

/** KAN-12 : place restante sur MES projets (le serveur refuse de toute façon, en 413) ; connue
 *  pour un projet dont je suis propriétaire, jamais pour celui d'un autre (sa place reste à
 *  lui). Sert à prévenir AVANT d'envoyer un fichier trop gros, pas après un long envoi. */
const spaceLeft = new Map<string, number>();

export function setSpaceLeft(pid: string, left: number | null): void {
  if (left === null) spaceLeft.delete(pid);
  else spaceLeft.set(pid, left);
}

const MiB = 1024 * 1024;
function tooBig(pid: string, file: File): ApiError | null {
  const left = spaceLeft.get(pid);
  if (left === undefined || file.size <= left) return null;
  const mo = (n: number) => `${Math.max(0, Math.round(n / MiB))} Mo`;
  return new ApiError(
    `Pas assez de place : ce fichier (${mo(file.size)}) dépasse la place qui reste (${mo(left)}). Supprimez des fichiers ou des projets.`,
    413,
  );
}

/** Bilan de l'installation (`openplan doctor`) : chaque manque avec sa commande. */
export interface DoctorCheck {
  key: string;
  label: string;
  status: "ok" | "missing" | "info";
  detail: string;
  fix: string | null;
  required: boolean;
}

export interface DoctorReport {
  version: string;
  ok: boolean;
  checks: DoctorCheck[];
}

export const api = {
  health: () =>
    request<{
      status: string;
      version: string;
      exposed?: boolean;
      error_reports?: boolean;
      /** B1d : comptes actifs (page de connexion, session). */
      accounts?: boolean;
      /** Avec les comptes : inscription sur invitation (bêta) ou ouverte. */
      signup?: "invite" | "open";
    }>("/health"),
  doctor: () => request<DoctorReport>("/doctor"),
  listProjects: () => request<ProjectSummary[]>("/projects"),
  createProject: (name: string) =>
    request<Project>("/projects", json({ name })),
  /** Accueil (L1a) : projet au nom automatique (« Projet · 6 oct. », puis la pièce). */
  createAutoNamedProject: (remoteTextConsent = false) =>
    request<Project>(
      "/projects",
      json({ name_auto: true, remote_text_consent: remoteTextConsent }),
    ),
  /** Accueil sans fichier (L3) : plan généré depuis le programme d'un logement, plan de
   *  départ simplifié pour une pièce seule avec sa surface, ou rien (consigne gardée). */
  programme: (
    pid: string,
    lid: string,
    text: string,
    answers?: GuidedAnswers,
  ) =>
    request<ProgrammeResult>(
      `/projects/${pid}/levels/${lid}/programme`,
      json({ text, answers: answers ?? null }),
    ),
  /** Accueil (L1b) : pièce rectangulaire 4:3 de cette surface, « plan de départ simplifié ». */
  starter: (pid: string, lid: string, areaM2: number) =>
    request<Project>(
      `/projects/${pid}/levels/${lid}/starter`,
      json({ area_m2: areaM2 }),
    ),
  /** L2 : recale le plan d'une photo sur la surface donnée (au-delà de 15 % d'écart). */
  areaGuard: (pid: string, lid: string, areaM2: number) =>
    request<AreaGuardResult>(
      `/projects/${pid}/levels/${lid}/area-guard`,
      json({ area_m2: areaM2 }),
    ),
  /** Annule le recalage sur la surface (un clic, même après d'autres pas). */
  areaGuardUndo: (pid: string, lid: string) =>
    request<Project>(`/projects/${pid}/levels/${lid}/area-guard/undo`, {
      method: "POST",
    }),
  /** À l'ouverture : nom automatique remis à jour (jamais un nom donné à la main). */
  autoName: (id: string) =>
    request<Project>(`/projects/${id}/name/auto`, { method: "POST" }),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  thumbnailUrl: (id: string, version: string) =>
    `${BASE}/projects/${id}/thumbnail?v=${encodeURIComponent(version)}`,
  /** T5 : vignette de la galerie (rendu, sinon plan, photo ou note Exigences). */
  coverUrl: (id: string, version: string, theme: "light" | "dark" = "light") =>
    `${BASE}/projects/${id}/cover?theme=${theme}&v=${encodeURIComponent(version)}`,
  deleteProject: (id: string) =>
    request<void>(`/projects/${id}`, { method: "DELETE" }),
  updateSettings: (id: string, body: ProjectSettingsUpdate) =>
    request<Project>(`/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  applyOp: (pid: string, lid: string, op: Op) =>
    request<EditResult>(`/projects/${pid}/levels/${lid}/ops`, json(op)),
  /** T7e : « + Nouveau plan » (variante de ce plan, nouvel étage, plan vide). */
  newPlan: (pid: string, body: NewPlanBody) =>
    request<EditResult & { level_id: string }>(
      `/projects/${pid}/levels`,
      json(body),
    ),
  /** KAN-91 (retours MEP 14) : « Supprimer le plan » ; 409 avec la raison (seul plan, premier
   *  plan, étage qui a des variantes). */
  deletePlan: (pid: string, lid: string) =>
    request<Project>(`/projects/${pid}/levels/${lid}`, { method: "DELETE" }),
  undo: (pid: string) =>
    request<Project>(`/projects/${pid}/undo`, { method: "POST" }),
  redo: (pid: string) =>
    request<Project>(`/projects/${pid}/redo`, { method: "POST" }),
  derived: (pid: string, lid: string) =>
    request<DerivedLevel>(`/projects/${pid}/levels/${lid}/derived`),
  validation: (pid: string) =>
    request<ValidationReport>(`/projects/${pid}/validation`),
  addMeasurement: (pid: string, body: MeasurementCreate) =>
    request<Project>(`/projects/${pid}/measurements`, json(body)),
  removeMeasurement: (pid: string, mid: string) =>
    request<Project>(`/projects/${pid}/measurements/${mid}`, {
      method: "DELETE",
    }),
  calibrate: (pid: string, lid: string, dryRun: boolean) =>
    request<CalibrationResult>(
      `/projects/${pid}/levels/${lid}/calibrate?dry_run=${dryRun ? "true" : "false"}`,
      { method: "POST" },
    ),
  exportSvgUrl: (pid: string, lid: string, theme: "light" | "dark" = "light") =>
    `${BASE}/projects/${pid}/exports/svg?level_id=${lid}${theme === "dark" ? "&theme=dark" : ""}`,
  exportPdfUrl: (pid: string, lid: string) =>
    `${BASE}/projects/${pid}/exports/pdf?level_id=${lid}`,
  exportSectionPdfUrl: (
    pid: string,
    lid: string,
    axis: "x" | "y",
    value: number,
  ) =>
    `${BASE}/projects/${pid}/exports/section-pdf?level_id=${lid}&axis=${axis}&value=${value.toFixed(2)}`,
  exportDxfUrl: (pid: string, lid: string) =>
    `${BASE}/projects/${pid}/exports/dxf?level_id=${lid}`,
  exportJsonUrl: (pid: string, share = false) =>
    `${BASE}/projects/${pid}/exports/json${share ? "?share=true" : ""}`,
  /** Sauvegarde complète en zip : projet, tableau, photos, fichiers joints, rendus (B1). */
  exportArchiveUrl: (pid: string) => `${BASE}/projects/${pid}/exports/archive`,
  exportGlbUrl: (
    pid: string,
    lid: string,
    version: number,
    style: "provenance" | "realistic" = "provenance",
  ) =>
    `${BASE}/projects/${pid}/exports/glb?level_id=${lid}&v=${version}&style=${style}`,
  blenderStatus: () =>
    request<{ available: boolean; binary: string | null }>("/blender"),
  runBlender: (pid: string, lid: string) =>
    request<{ run_id: string; result: StepResult }>(
      `/projects/${pid}/levels/${lid}/blender`,
      {
        method: "POST",
      },
    ),
  /** Rendu Cycles avec matières depuis la caméra de la photo (bloquant côté serveur). */
  renderPhoto: (pid: string, lid: string, samples: number, gpu: boolean) =>
    request<{ run_id: string; result: StepResult }>(
      `/projects/${pid}/levels/${lid}/render?samples=${samples}&gpu=${gpu ? "true" : "false"}`,
      { method: "POST" },
    ),
  blenderArtifactUrl: (pid: string, runId: string, name: string) =>
    `${BASE}/projects/${pid}/runs/${runId}/blender/${name}`,
  /** KAN-91 : `levelId`, le plan ouvert : la photo entre dans la carte Photos de ce plan. */
  uploadImage: (pid: string, file: File, levelId?: string) => {
    const refused = tooBig(pid, file);
    if (refused) return Promise.reject(refused);
    const form = new FormData();
    form.append("file", file, file.name);
    const query = levelId ? `?level_id=${encodeURIComponent(levelId)}` : "";
    return request<ImageImportResult>(`/projects/${pid}/images${query}`, {
      method: "POST",
      body: form,
    });
  },
  deleteImage: (pid: string, imageId: string) =>
    request<Project>(`/projects/${pid}/images/${imageId}`, {
      method: "DELETE",
    }),
  imageQuality: (pid: string, imageId: string) =>
    request<{ quality: Record<string, number>; warnings: string[] }>(
      `/projects/${pid}/images/${imageId}/quality`,
    ),
  imageFileUrl: (pid: string, imageId: string) =>
    `${BASE}/projects/${pid}/images/${imageId}/file`,
  detect: (pid: string, lid: string, imageId: string, cameraHeightM: number) =>
    request<DetectResult>(
      `/projects/${pid}/levels/${lid}/detect`,
      json({ image_id: imageId, camera_height_m: cameraHeightM }),
    ),
  detectionArtifactUrl: (pid: string, runId: string, name: string) =>
    `${BASE}/projects/${pid}/runs/${runId}/detection/${name}`,
  /** Plan depuis la profondeur (sol, murs verticaux, renfoncements) sur un niveau vide. */
  detectDepth: (
    pid: string,
    lid: string,
    imageId: string,
    cameraHeightM: number | null,
  ) =>
    request<DepthPlanResult>(
      `/projects/${pid}/levels/${lid}/detect-depth`,
      json({ image_id: imageId, camera_height_m: cameraHeightM }),
    ),
  /** Portes et fenêtres vues sur la photo, projetées sur le mur hôte par la caméra de la
   *  photo (elle doit être dans le plan) : ouvertures « observées », corrigeables. */
  detectOpenings: (pid: string, lid: string, imageId: string) =>
    request<OpeningsResult>(
      `/projects/${pid}/levels/${lid}/images/${imageId}/openings`,
      { method: "POST" },
    ),
  openingsArtifactUrl: (pid: string, runId: string, name: string) =>
    `${BASE}/projects/${pid}/runs/${runId}/openings/${name}`,
  /** « Tourner la vue » : l'orientation d'une photo n'est connue qu'à un quart de tour
   *  près ; la caméra est replacée et sa visée tourne, aucun mur ne bouge. */
  rotateView: (pid: string, lid: string, cameraId: string, quarterTurns = 1) =>
    request<EditResult>(
      `/projects/${pid}/levels/${lid}/views/${cameraId}/rotate`,
      json({ quarter_turns: quarterTurns }),
    ),
  /** « Garder cette photo » : le plan suit la photo, le mur le moins sûr est déplacé. */
  adoptView: (pid: string, lid: string, cameraId: string) =>
    request<EditResult>(
      `/projects/${pid}/levels/${lid}/views/${cameraId}/adopt`,
      {
        method: "POST",
      },
    ),
};

export interface DimensionRange {
  min: number;
  default: number;
  max: number;
}

export interface CatalogAsset {
  id: string;
  category: string;
  label: string;
  width: DimensionRange;
  depth: DimensionRange;
  height: DimensionRange;
  anchor: "free" | "wall" | "eave_wall";
  clearance: { front: number; sides: number; back: number };
  tags: string[];
  license: string;
  author: string;
  notes: string;
  /** Pièce de la bibliothèque (filtre du panneau Meuble). */
  room: CatalogRoom;
  /** Modèle 3D et vignette, relatifs à `assets/furniture/` ; servis par l'API. */
  model: string | null;
  preview: string | null;
}

export type CatalogRoom =
  "sejour" | "chambre" | "cuisine" | "salle_de_bain" | "bureau";

/** Libellés des pièces, dans l'ordre du filtre (même liste que `ROOM_LABELS` côté API). */
export const ROOM_LABELS: Record<CatalogRoom, string> = {
  sejour: "Séjour",
  chambre: "Chambre",
  cuisine: "Cuisine",
  salle_de_bain: "Salle de bain",
  bureau: "Bureau",
};

/** Symbole 2D d'une famille de meubles (F4), en coordonnées unité de la boîte :
 *  u le long de la largeur, v le long de la profondeur, face avant en v = +0,5. */
export interface SymbolPath {
  points: [number, number][];
  closed: boolean;
}

export interface FurnitureSymbol {
  outline: "rect" | "ellipse";
  paths: SymbolPath[];
}

export interface SymbolSet {
  symbols: Record<string, FurnitureSymbol>;
  generic: FurnitureSymbol;
}

export interface Catalog {
  version: number;
  license_note: string;
  assets: CatalogAsset[];
}

export const catalogApi = {
  /** Catalogue de meubles (boîtes cotées, modèles CC0) : lecture seule. */
  list: () => request<Catalog>("/catalog"),
  /** Symboles 2D par famille (les mêmes que l'export SVG et le PDF). */
  symbols: () => request<SymbolSet>("/catalog/symbols"),
  /** Vignette du meuble (image servie par l'API, comme les vignettes de projet). */
  previewUrl: (assetId: string) =>
    `${BASE}/catalog/${encodeURIComponent(assetId)}/preview`,
};

export interface DepthPlanResult {
  run: {
    run_id: string;
    project: Project;
    camera_height_m: number | null;
    walls_seen: number;
    result: StepResult;
  };
  report: ValidationReport;
}

export interface DetectResult {
  run: {
    run_id: string;
    project: Project;
    detection: StepResult;
    floorplan: StepResult;
  };
  report: ValidationReport;
}

/** Une boîte du détecteur : gardée (offset, largeur…) ou écartée (motif). */
export interface ObservedOpening {
  category: "door" | "window";
  kind: "door" | "window" | null;
  bbox_px: [number, number, number, number];
  score: number;
  host_wall: string | null;
  host_wall_name: string | null;
  offset_m: number | null;
  width_m: number | null;
  height_m: number | null;
  sill_m: number | null;
  filtered: string | null;
  reason: string;
  element_id: string | null;
}

export interface OpeningsResult {
  run: {
    run_id: string;
    image_id: string;
    project: Project;
    observations: ObservedOpening[];
    result: StepResult;
  };
  report: ValidationReport;
}

export interface AiStatus {
  configured: boolean;
  model_id: string | null;
  destination: string | null;
  remote: boolean | null;
  supports_images: boolean | null;
}

export interface VlmElement {
  category: string;
  bbox_px: [number, number, number, number];
  score: number;
  label: string | null;
  model_id: string;
}

export interface PerceptionRun {
  run_id: string;
  perception: {
    elements: {
      category: string;
      box: [number, number, number, number];
      label: string | null;
      confidence: number;
    }[];
    room: {
      shape: "rectangular" | "l_shaped" | "other";
      width_m: number | null;
      depth_m: number | null;
      ceiling_height_m: number | null;
      confidence: number;
    };
    notes: string;
  };
  elements: VlmElement[];
  result: StepResult;
}

export interface NetworkLogEntry {
  timestamp: string;
  model_id: string;
  destination: string;
  remote: boolean;
  purpose: string;
  images: number;
  request_bytes: number | null;
  response_bytes: number | null;
  duration_s: number;
  ok: boolean;
  error: string | null;
  /** Sonde texte sans image envoyée avant une requête avec image à un service distant. */
  probe?: boolean;
}

export interface DepthRun {
  run_id: string;
  image_id: string;
  stats: {
    min_m: number;
    p10_m: number;
    median_m: number;
    p90_m: number;
    max_m: number;
    saturated_fraction: number;
  };
  result: StepResult;
}

/** Valeur avec son incertitude 1-sigma et sa source (estimated, inferred, ou user_defined
 *  une fois corrigée par l'utilisateur). */
export interface EstimateValue {
  value: number;
  sigma: number;
  source: Source;
  note: string | null;
}

/** Combles validés par l'utilisateur : chaque valeur garde sa source. */
export interface AtticGeometryBody {
  length: EstimateValue;
  width: EstimateValue;
  ridge_height: EstimateValue;
  eave_height: EstimateValue;
  camera_height?: EstimateValue | null;
  gable_distance?: EstimateValue | null;
  image_id?: string | null;
  run_id?: string | null;
}

export interface AtticApplyResult {
  project: Project;
  report: ValidationReport;
  result: StepResult;
}

export interface AtticPlane {
  kind: "floor" | "ceiling" | "slope" | "gable" | "side_wall" | "other";
  normal: [number, number, number];
  distance_m: number;
  tilt_deg: number;
  inlier_fraction: number;
  rms_m: number;
}

export interface AtticRun {
  run_id: string;
  image_id: string;
  estimate: {
    camera_height: EstimateValue | null;
    ridge_height: EstimateValue | null;
    eave_heights: EstimateValue[];
    width: EstimateValue | null;
    usable_width: EstimateValue | null;
    gable_distance: EstimateValue | null;
    length: EstimateValue | null;
    ceiling_height: EstimateValue | null;
    slope_pitch_deg: number[];
    planes: AtticPlane[];
    scale_factor: number;
    scale_note: string | null;
  };
  result: StepResult;
}

export const atticApi = {
  /** Sans hauteur, l'échelle du modèle de profondeur est gardée ; avec, tout est recalé dessus. */
  run: (pid: string, imageId: string, cameraHeightM?: number) =>
    request<AtticRun>(
      `/projects/${pid}/images/${imageId}/attic`,
      json({ camera_height_m: cameraHeightM ?? null }),
    ),
  artifactUrl: (pid: string, runId: string, name: string) =>
    `${BASE}/projects/${pid}/runs/${runId}/attic/${name}`,
  /** Crée murs, pentes, pièce et caméra sur un niveau vide (409 sinon). */
  apply: (pid: string, lid: string, body: AtticGeometryBody) =>
    request<AtticApplyResult>(
      `/projects/${pid}/levels/${lid}/attic`,
      json(body),
    ),
};

export interface InspirationRun {
  run_id: string;
  image_id: string;
  prompt: string;
  seed: number;
  generation_s: number;
  peak_vram_mb: number | null;
  source: "generated";
  result: StepResult;
}

/** Tâche de fond côté serveur (génération d'image) : la requête ne l'attend pas. Lancée
 *  pendant un autre calcul, elle attend (`queued`) et démarre seule ensuite. */
export interface JobStatus {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  /** Place dans la file quand la tâche attend (1 = la prochaine), sinon absent. */
  queue_position?: number | null;
  device: string;
  expected_s: number | null;
  elapsed_s: number;
  message: string;
  /** Étape en cours d'une tâche à étapes (« Recherche des murs… »), sinon vide. */
  stage?: string;
  /** Temps restant estimé en direct (jamais 0 tant qu'une étape tourne), sinon absent. */
  remaining_s?: number | null;
  result: unknown;
  error: string | null;
}

/** Une tâche à suivre encore : elle calcule, ou elle attend son tour. */
export function jobActive(job: JobStatus | null | undefined): boolean {
  return job?.status === "running" || job?.status === "queued";
}

/* Construction automatique du plan à l'import : photo d'une pièce ou image d'un plan. */
export interface AutoStage {
  key: string;
  label: string;
  status: "done" | "skipped" | "failed";
  detail: string;
}

export interface EstimateConfidence {
  level: "fiable" | "a_verifier" | "approximatif";
  label: string;
  reason: string;
  expected_error: number;
  factors: { key: string; label: string; error: number; advice: string }[];
}

export interface AutoPlanRun {
  run_id: string;
  image_id: string;
  kind: "photo" | "plan";
  method: "lines" | "depth" | "attic" | "plan_image" | "view" | "none";
  backup: string | null;
  scores: Record<string, number>;
  stages: AutoStage[];
  /** À dire une fois : ce qui manque et sa commande, échelle devinée. */
  notices: string[];
  low_confidence: boolean;
  /** KAN-31 : niveau de confiance (« fiable », « à vérifier », « approximatif ») et sa
   *  raison principale, au lieu d'un pourcentage ; null si rien n'a été estimé. */
  estimate_confidence?: EstimateConfidence | null;
  /** KAN-91 L5 : la photo montre un détail (un meuble, un coin), pas la pièce ; l'espace de
   *  travail l'annonce avec le guide de prise de photo. */
  close_up?: boolean;
  project: Project;
  result: StepResult;
}

export interface AutoPlanResult {
  run: AutoPlanRun;
  report: ValidationReport;
}

export const autoplanApi = {
  /** Lance la construction du plan depuis l'image ; l'état se suit par la tâche. */
  start: (pid: string, lid: string, imageId: string, kind?: "photo" | "plan") =>
    request<JobStatus>(
      `/projects/${pid}/levels/${lid}/images/${imageId}/autoplan${kind ? `?kind=${kind}` : ""}`,
      { method: "POST" },
    ),
};

/** KAN-79 : profondeur calculée par le navigateur (float16, grille de 640 px au plus). */
export const browserDepthApi = {
  /** La carte (float16, 640 px au plus : ~600 Ko) compressée en gzip quand le navigateur le
   *  sait, avec sa progression réelle et un délai sans progrès (connexion lente, 25 oct.). */
  put: async (
    pid: string,
    lid: string,
    imageId: string,
    grid: Uint16Array,
    width: number,
    height: number,
    onProgress?: (fraction: number) => void,
  ) => {
    const raw = new Blob([grid as Uint16Array<ArrayBuffer>]);
    const packed = await gzipped(raw);
    const headers: Record<string, string> = {
      "Content-Type": "application/octet-stream",
    };
    if (packed) headers["Content-Encoding"] = "gzip";
    return sendWithProgress<{
      image_id: string;
      depth_size: [number, number];
      model_id: string;
    }>(
      `/projects/${pid}/levels/${lid}/images/${imageId}/depth?image_width=${width}&image_height=${height}`,
      { method: "PUT", headers, body: packed ?? raw, onProgress },
    );
  },
  /** La photo telle que le serveur la garde (orientation appliquée). */
  photo: async (pid: string, imageId: string): Promise<Blob> => {
    // Connexion lente (25 oct.) : jamais d'attente sans fin (avant : `fetch` sans délai).
    const stop = new AbortController();
    const timer = window.setTimeout(() => stop.abort(), 4 * STALL_MS);
    try {
      const res = await fetch(api.imageFileUrl(pid, imageId), {
        credentials: "same-origin",
        signal: stop.signal,
      });
      if (!res.ok)
        throw new ApiError(`photo illisible (${res.status})`, res.status);
      return await res.blob();
    } catch (e) {
      if (e instanceof ApiError) throw e;
      throw new UploadStalled();
    } finally {
      window.clearTimeout(timer);
    }
  },
};

export interface DeviceInfo {
  requested: string;
  device: string;
  label: string;
}

/* Assistant qui agit sur le plan (directive 3). */
export interface AssistantAction {
  kind: string;
  status: "done" | "refused" | "question";
  summary: string;
  element_ids: string[];
  reason: string;
}

export interface AssistantTurn {
  run_id: string;
  text: string;
  message: string;
  actions: AssistantAction[];
  highlighted: string[];
  committed: number;
  proposals: LayoutProposal[];
  layouts: LayoutRun | null;
  dropped: string[];
  model_id: string | null;
}

export interface AssistantPreview {
  configured: boolean;
  model_id: string | null;
  destination: string | null;
  remote: boolean | null;
  consented: boolean;
  text: string;
  never: string[];
}

/** Un message de la conversation, côté navigateur. */
/** Une partie d'un tour de l'assistant unifié (P5) : fait, refusé ou question, et pourquoi. */
export interface SayPart {
  kind: "ambiance" | "action" | "answer";
  status: "done" | "refused" | "question";
  summary: string;
  reason?: string;
  by?: "phrases" | "model";
  element_ids?: string[];
}

/** Un tour de l'assistant unifié (P5a) : ambiance et gestes d'une même phrase. */
export interface SayTurn {
  text: string;
  parts: SayPart[];
  reader: "phrases" | "model";
  model_id: string | null;
  notice: string;
  /** Pas d'historique du tour : « annuler » les défait tous. */
  committed: number;
  room_id: string | null;
  highlighted: string[];
  assistant: AssistantTurn | null;
}

export interface SayResult extends EditResult {
  turn: SayTurn;
  /** La case « Gemini » de l'accueil, rappelée dans le panneau. */
  gemini_case: boolean;
  remote_model: boolean;
}

/** Le fil relu du journal local (20 derniers tours) ; vide si le journal est coupé. */
export interface AssistantThread {
  journal_enabled: boolean;
  turns: { text: string; parts: SayPart[]; by: string | null }[];
}

export interface AssistantMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  turn?: AssistantTurn;
  /** Tour de l'assistant unifié (P5) : parties, lecteur, pas à annuler. */
  say?: SayTurn;
  /** Relu du journal (tour d'une session précédente) : pas d'« annuler ». */
  fromJournal?: boolean;
  undone?: boolean;
}

export const assistantApi = {
  preview: (pid: string, lid: string, text: string) =>
    request<AssistantPreview>(
      `/projects/${pid}/levels/${lid}/assistant/preview`,
      json({ text }),
    ),
  /** Assistant unifié (P5) : une phrase → ambiance de la pièce en contexte et gestes. */
  say: (
    pid: string,
    lid: string,
    text: string,
    contextId: string | null,
    lastElementId: string | null,
  ) =>
    request<SayResult>(
      `/projects/${pid}/levels/${lid}/assistant/say`,
      json({ text, context_id: contextId, last_element_id: lastElementId }),
    ),
  thread: (pid: string) =>
    request<AssistantThread>(`/projects/${pid}/assistant/thread`),
};

/* « Recalibrer » : une longueur connue met tout le niveau à l'échelle. */
export interface RecalibrateBody {
  real_m: number;
  a?: Point2;
  b?: Point2;
  wall_id?: string;
  ceiling?: boolean;
}

export interface RecalibrateSummary {
  factor: number;
  current_m: number;
  real_m: number;
  what: string;
  /** « 9,65 × 5,00 m → 8,00 × 4,20 m » quand longueur et largeur ont été données (R3). */
  text?: string;
}

/** R3 : dimensions connues de la pièce, chacune facultative. */
export interface DimensionsBody {
  length_m?: number | null;
  width_m?: number | null;
  height_m?: number | null;
}

export interface RoomSize {
  length_m: number;
  width_m: number;
  height_m: number;
}

export interface DimensionsSummary {
  before: RoomSize;
  after: RoomSize;
  sx: number;
  sy: number;
  sz: number;
  uniform: boolean;
  text: string;
  notes: string[];
}

export interface RecalibrateResult extends EditResult {
  summary: RecalibrateSummary;
}

export const recalibrateApi = {
  apply: (pid: string, lid: string, body: RecalibrateBody) =>
    request<RecalibrateResult>(
      `/projects/${pid}/levels/${lid}/recalibrate`,
      json(body),
    ),
  /** Dimensions actuelles de la pièce et effet de celles saisies, sans rien modifier. */
  previewDimensions: (pid: string, lid: string, body: DimensionsBody) =>
    request<DimensionsSummary>(
      `/projects/${pid}/levels/${lid}/recalibrate/dimensions/preview`,
      json(body),
    ),
  dimensions: (pid: string, lid: string, body: DimensionsBody) =>
    request<RecalibrateResult & { dimensions: DimensionsSummary }>(
      `/projects/${pid}/levels/${lid}/recalibrate/dimensions`,
      json(body),
    ),
};

/* KAN-66 : mesure de référence sur une photo (une par photo : le PUT remplace) ; le PUT et
   le DELETE renvoient le projet entier recalé. */
export interface PhotoReferenceBody {
  p1: [number, number];
  p2: [number, number];
  length_m: number;
  label: string | null;
}

export const referenceApi = {
  put: (pid: string, imageId: string, body: PhotoReferenceBody) =>
    request<Project>(`/projects/${pid}/images/${imageId}/reference`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  remove: (pid: string, imageId: string, segmentId: string) =>
    request<Project>(
      `/projects/${pid}/images/${imageId}/reference/${segmentId}`,
      { method: "DELETE" },
    ),
  /** « Réestimer la photo pour pouvoir mesurer » : 202 et une tâche ; le projet se relit à
   *  la fin. */
  reestimate: (pid: string, lid: string, imageId: string) =>
    request<JobStatus>(
      `/projects/${pid}/levels/${lid}/images/${imageId}/reestimate`,
      { method: "POST" },
    ),
};

export const inspirationApi = {
  /** Lance l'image d'inspiration locale (source generated) et renvoie l'état de la tâche. */
  start: (
    pid: string,
    imageId: string,
    prompt: string,
    seed: number,
    steps: number,
  ) =>
    request<JobStatus>(
      `/projects/${pid}/images/${imageId}/inspire`,
      json({ prompt, seed, steps }),
    ),
  job: (pid: string, jobId: string) =>
    request<JobStatus>(`/projects/${pid}/jobs/${jobId}`),
  /** Annule une tâche en attente (409 si elle a déjà démarré). */
  cancel: (pid: string, jobId: string) =>
    request<JobStatus>(`/projects/${pid}/jobs/${jobId}`, { method: "DELETE" }),
  device: () => request<DeviceInfo>("/device"),
  artifactUrl: (pid: string, runId: string, name: string) =>
    `${BASE}/projects/${pid}/runs/${runId}/inspiration/${name}`,
};

export const depthApi = {
  run: (pid: string, imageId: string) =>
    request<DepthRun>(`/projects/${pid}/images/${imageId}/depth`, {
      method: "POST",
    }),
  artifactUrl: (pid: string, runId: string, name: string) =>
    `${BASE}/projects/${pid}/runs/${runId}/depth/${name}`,
};

export interface IntentionBody {
  text: string;
  room_id?: string | null;
  keep?: string[] | null;
}

/** Ce qui partirait au modèle, mot pour mot, et vers où : affiché avant tout envoi. */
export interface IntentionPreview {
  text: string;
  room_description: string;
  categories: string[];
  keep: string[];
  model_id: string | null;
  destination: string | null;
  remote: boolean | null;
  characters: number;
}

export type BriefPlace =
  | "wall"
  | "window"
  | "door"
  | "door_swing"
  | "radiator"
  | "low_wall"
  | "ridge"
  | "outlet"
  | "corner"
  | "skylight";

export interface BriefItem {
  category: string;
  count: number;
  priority: "required" | "nice_to_have";
  dimensions_m: {
    width: number | null;
    depth: number | null;
    height: number | null;
  } | null;
  near: BriefPlace[];
  away_from: BriefPlace[];
  /** Zone de la pièce (V1, brief par zones). */
  zone?: "couchage" | "lecture" | "bureau" | "rangement" | "detente" | null;
}

export interface LayoutBrief {
  room_use: string;
  items: BriefItem[];
  keep: string[];
  avoid: string[];
  notes: string;
  dropped: string[];
}

export interface IntentionRun {
  run_id: string;
  level_id: string;
  preview: IntentionPreview;
  brief: LayoutBrief;
  result: StepResult;
}

export interface LayoutBody {
  brief: Omit<
    LayoutBrief,
    "room_use" | "keep" | "avoid" | "notes" | "dropped"
  > &
    Partial<LayoutBrief>;
  room_id?: string | null;
  variants?: number;
  seed?: number;
  budget_s?: number;
}

export interface Unplaced {
  category: string;
  label: string;
  reason: string;
}

/** Une variante : meubles à ajouter (pas encore dans la scène), score, phrase de synthèse,
 *  remarques souples. Les violations dures l'auraient écartée. */
export interface LayoutProposal {
  index: number;
  seed: number;
  score: number;
  valid: boolean;
  summary: string;
  furniture: Furniture[];
  unplaced: Unplaced[];
  violations: Violation[];
  satisfied: string[];
  missed: string[];
}

export interface LayoutRun {
  run_id: string;
  level_id: string;
  room_id: string | null;
  brief: LayoutBrief;
  proposals: LayoutProposal[];
  result: StepResult;
}

/* Rôle Critique (3.5) : remarques sur une variante, chacune citant un meuble et une règle,
   triées par importance, avec une action en un clic qui recalcule une variante à relire. */
export type ActionKind = "move_near" | "move_away" | "remove";

export interface CritiqueAction {
  kind: ActionKind;
  furniture_id: string;
  target: BriefPlace | null;
  label: string;
}

export interface Remark {
  importance: "high" | "medium" | "low";
  rule: string;
  rule_label: string;
  furniture_id: string | null;
  furniture_label: string;
  message: string;
  action: CritiqueAction | null;
  /** `rules` : règles géométriques locales ; `model` : avis du modèle (jamais appliqué seul). */
  origin: "rules" | "model";
}

export interface Critique {
  verdict: string;
  remarks: Remark[];
  strengths: string[];
  dropped: string[];
}

export interface CritiqueBody {
  proposal: LayoutProposal;
  brief?: LayoutBody["brief"] | null;
  room_id?: string | null;
  /** false : règles seules, même si un modèle est configuré (aucun envoi). */
  use_model?: boolean;
}

export interface CritiquePreview {
  text: string;
  model_id: string | null;
  destination: string | null;
  remote: boolean | null;
  characters: number;
}

export interface CritiqueRun {
  run_id: string;
  level_id: string;
  proposal_index: number;
  proposal_seed: number;
  critique: Critique;
  rules_count: number;
  model_count: number;
  preview: CritiquePreview;
  result: StepResult;
}

export interface ReviseBody {
  proposal: LayoutProposal;
  action: CritiqueAction;
  brief?: LayoutBody["brief"] | null;
  room_id?: string | null;
}

/* Glisser-déposer d'un meuble (3.6) : les règles à la position essayée, sans rien modifier. */
export interface FurnitureMoveBody {
  position: Point2;
  yaw?: number;
  /** Redimensionnement (E2) : largeur et profondeur essayées, en mètres. */
  width?: number;
  depth?: number;
}

/* Bibliothèque (F3) : un meuble du catalogue essayé sous le curseur, avant d'être posé. */
export interface FurniturePlaceBody {
  asset_id: string;
  position: Point2;
  yaw?: number;
}

export interface FurnitureMoveCheck {
  valid: boolean;
  violations: Violation[];
  /** Règle dure en deux mots (« fenêtre masquée ») ; vide si tout va bien. */
  message: string;
}

export const layoutApi = {
  /** Générateur local (3.4) : brief → variantes ; la scène ne change pas. */
  propose: (pid: string, lid: string, body: LayoutBody) =>
    request<LayoutRun>(`/projects/${pid}/levels/${lid}/layouts`, json(body)),
  /** « Appliquer cette variante » : geste utilisateur, annulable. */
  apply: (pid: string, lid: string, proposal: LayoutProposal) =>
    request<EditResult>(
      `/projects/${pid}/levels/${lid}/layouts/apply`,
      json({ proposal }),
    ),
  /** Ce qui partirait au modèle pour critiquer une variante (texte seul) : aucun envoi. */
  critiquePreview: (pid: string, lid: string, body: CritiqueBody) =>
    request<CritiquePreview>(
      `/projects/${pid}/levels/${lid}/layouts/critique/preview`,
      json(body),
    ),
  /** Remarques : règles locales, puis modèle si configuré et autorisé ; rien d'appliqué. */
  critique: (pid: string, lid: string, body: CritiqueBody) =>
    request<CritiqueRun>(
      `/projects/${pid}/levels/${lid}/layouts/critique`,
      json(body),
    ),
  /** Action d'une remarque → variante révisée à relire (409 si aucune position). */
  /** Pendant un glisser : les règles à la position essayée (aucune modification). */
  checkMove: (pid: string, lid: string, fid: string, body: FurnitureMoveBody) =>
    request<FurnitureMoveCheck>(
      `/projects/${pid}/levels/${lid}/furniture/${fid}/check`,
      json(body),
    ),
  /** Escalier tourné ou porte retournée (E4) : les règles dans l'état essayé. */
  checkElement: (
    pid: string,
    lid: string,
    eid: string,
    body: {
      position?: { x: number; y: number };
      yaw?: number;
      width?: number;
      swing?: {
        hinge: "left" | "right";
        direction: "inward" | "outward";
        angle?: number;
      };
    },
  ) =>
    request<FurnitureMoveCheck>(
      `/projects/${pid}/levels/${lid}/elements/${eid}/check`,
      json(body),
    ),
  /** Avant de poser un meuble de la bibliothèque : les règles à cette position. */
  checkPlace: (pid: string, lid: string, body: FurniturePlaceBody) =>
    request<FurnitureMoveCheck>(
      `/projects/${pid}/levels/${lid}/furniture/check`,
      json(body),
    ),
  /** Vignette Blender d'une variante sans l'appliquer : 202, suivi par `inspirationApi.job`. */
  render: (
    pid: string,
    lid: string,
    body: {
      proposal: LayoutProposal;
      samples?: number;
      gpu?: boolean;
      daylight?: string | null;
    },
  ) =>
    request<JobStatus>(
      `/projects/${pid}/levels/${lid}/layouts/render`,
      json(body),
    ),
  revise: (pid: string, lid: string, body: ReviseBody) =>
    request<LayoutProposal>(
      `/projects/${pid}/levels/${lid}/layouts/revise`,
      json(body),
    ),
  artifactUrl: (pid: string, runId: string, name: string) =>
    `${BASE}/projects/${pid}/runs/${runId}/layout/${name}`,
};

/* Import d'un plan 2D existant (U1) : DXF → segments en mètres → murs observés. */
export interface PlanSegment {
  start: Point2;
  end: Point2;
  layer: string;
  thickness: number | null;
}

export interface PlanLayer {
  name: string;
  segments: number;
  selected: boolean;
}

export type PlanKind = "dxf" | "pdf" | "svg" | "image";

export interface PlanImportPreview {
  kind: PlanKind;
  units: string;
  segments: PlanSegment[];
  layers: PlanLayer[];
  bounds: { min: Point2; max: Point2 } | null;
  entity_count: number;
  merged_pairs: number;
  /** Plan papier : dénominateur de l'échelle 1:n utilisée (à changer si besoin). */
  paper_scale: number | null;
  warnings: string[];
}

export interface PlanApplyBody {
  segments: PlanSegment[];
  scale?: number;
  kind?: PlanKind;
  source?: "observed" | "estimated";
}

export const planApi = {
  /** Lit le fichier (DXF, PDF vectoriel, SVG) et renvoie ses tracés ; rien n'entre dans la
   *  scène, rien n'est gardé. `paperScale` : échelle papier 1:n choisie. */
  preview: (pid: string, lid: string, file: File, paperScale?: number) => {
    const form = new FormData();
    form.append("file", file, file.name);
    const query = paperScale ? `?paper_scale=${paperScale}` : "";
    return request<PlanImportPreview>(
      `/projects/${pid}/levels/${lid}/import-plan/preview${query}`,
      { method: "POST", body: form },
    );
  },
  /** Traits épais de l'image mise en fond (heuristique locale) → segments estimés. */
  previewImage: (pid: string, lid: string, imageId: string) =>
    request<PlanImportPreview>(
      `/projects/${pid}/levels/${lid}/import-plan/preview-image`,
      json({ image_id: imageId }),
    ),
  /** Les segments retenus deviennent des murs « observés » (un commit, annulable). */
  apply: (pid: string, lid: string, body: PlanApplyBody) =>
    request<EditResult>(
      `/projects/${pid}/levels/${lid}/import-plan`,
      json(body),
    ),
};

/* Écran Rendre (R1) : estimation avant de lancer, rendu par point de vue en tâche de fond,
   galerie des rendus. */
export interface RenderEstimate {
  blender_available: boolean;
  device: string;
  device_label: string;
  samples: number;
  expected_s: number;
  message: string;
}

export interface RenderEntry {
  run_id: string;
  camera_id: string | null;
  /** T7e : le plan rendu ; absent des rendus d'avant (retrouvé par la caméra). */
  level_id?: string | null;
  camera_name: string;
  samples: number;
  device: string;
  render_s: number | null;
  width_px: number;
  height_px: number;
  created_at: string;
  image: string;
  /** Préréglage d'éclairage (matin, midi, soir, nuit) ; null = neutre. */
  daylight: string | null;
  north_deg: number;
  /** Vignette d'une variante d'aménagement : son numéro ; null = rendu de la scène. */
  variant: number | null;
  /** Finition IA faite sur ce rendu (D5) : image `generated` à côté de l'image Blender. */
  finished?: FinishEntry | null;
  /** Rendu en coupe (E3) : « horizontale à 1,20 m », « verticale x = 3,00 m ». */
  section?: string | null;
  /** Sortie B (V1) : image d'inspiration, `generated`, « cotes non garanties ». */
  inspiration?: InspirationEntry | null;
  /** « Version catalogue » demandée (V1.7b) : « matières libres ». */
  catalogue?: InspirationEntry | null;
  /** Galerie (US2) : titre donné par l'utilisateur ; null = nom du point de vue. */
  title?: string | null;
  /** Images gardées en favori (étoile), par variante. */
  favorites?: RenderVariant[];
  /** Vue du rendu : ensemble, zone, détail, à la main, photo, variante d'aménagement. */
  view_group?: ViewGroup;
  /** Ambiance de la pièce au moment du rendu (« Scandinave · chaleureux »). */
  ambiance?: string | null;
}

/** Les images d'un rendu : Blender, référence (Blender + finition), inspiration, catalogue. */
export type RenderVariant =
  "blender" | "finished" | "inspiration" | "catalogue";
export type ViewGroup =
  "overall" | "zone" | "detail" | "hand" | "photo" | "variant";

export interface InspirationEntry {
  run_id: string;
  image: string;
  source: string;
  label: string;
  mode: "txt2img" | "img2img";
  strength: number | null;
  control_scale: number;
  prompt: string;
  seed: number;
  model_ids: string[];
  duration_s: number;
  peak_vram_mb: number | null;
  created_at: string;
  note: string;
}

export type FinishStyle =
  "naturel" | "chalet" | "scandinave" | "industriel" | "contemporain";

export interface FinishEntry {
  run_id: string;
  image: string;
  source: string;
  strength: number;
  style: FinishStyle;
  prompt: string;
  seed: number;
  model_ids: string[];
  duration_s: number;
  peak_vram_mb: number | null;
  guided_by: string[];
  created_at: string;
  note: string;
}

/** Journal local des demandes (V2) : actif ou coupé, nombre de lignes. */
export interface JournalSummary {
  enabled: boolean;
  events: number;
  location: string;
}

export const journalApi = {
  summary: (pid: string) => request<JournalSummary>(`/projects/${pid}/journal`),
  /** Vide le journal du projet (sur demande). */
  clear: (pid: string) =>
    request<JournalSummary>(`/projects/${pid}/journal`, { method: "DELETE" }),
  /** Mesure du parcours (P0) : un geste d'interface au journal local ; un échec est ignoré
   *  (la mesure ne gêne jamais le travail). */
  ui: (pid: string, event: UiEvent, detail?: string) => {
    request<{ recorded: boolean }>(
      `/projects/${pid}/journal/ui`,
      json({ event, detail: detail ?? null }),
    ).catch(() => undefined);
  },
  metrics: (pid: string) => request<ProjectMetrics>(`/projects/${pid}/metrics`),
};

export type UiEvent =
  | "tab_changed"
  | "refine_clicked"
  | "render_requested"
  | "generate_clicked"
  /** T7d2 : visionneuse des rendus ouverte (ce n'est pas un geste). */
  | "viewer_opened";

/** Ce qu'un clic sur un rendu désigne (P4). */
export interface PickResult {
  kind: "element" | "decor" | "none";
  element_id: string | null;
  element_type: string | null;
  category: string | null;
  name: string;
  decor_kind: string | null;
  on_demand: boolean;
}

/** Indicateurs du parcours (P0), calculés sur la machine. */
export interface ProjectMetrics {
  project_id: string;
  first_render_at: string | null;
  ttfr_s: number | null;
  gestures_before_first_render: number;
  gestures_after_first_render: number;
  tab_changes: number;
  refine_clicks: number;
  render_requests: number;
  renders: number;
  rendered_views: number;
  stale_views: number;
  /** Désignation dans un rendu (P4c) : clics, élément trouvé, décor sans porteur, rien. */
  picks: number;
  picks_resolved: number;
  picks_decor: number;
  picks_none: number;
  pick_resolution: number | null;
  journal_enabled: boolean;
}

/** Chemin « zéro geste » (V1, point 6) : ce qui a été fait, étape par étape. */
export interface AutopilotStep {
  name: string;
  label: string;
  status: "done" | "skipped" | "failed";
  note: string;
  seconds: number;
}

/** Une demande de l'accueil confrontée au résultat (L2) : tenue, en partie, non tenue. */
export interface RequestItem {
  label: string;
  wanted: string;
  status: "ok" | "partial" | "missing";
  detail: string;
}

export interface AutopilotRun {
  steps: AutopilotStep[];
  run_ids: string[];
  finished: string[];
  inspired: string[];
  total_s: number;
  /** Respect de la demande écrite ou choisie sur l'accueil (L2). */
  request?: RequestItem[];
}

/** Questions guidées de l'accueil (L2) ; tout est facultatif. */
export interface GuidedAnswers {
  space?: "appartement" | "maison" | "studio" | "piece" | "combles" | null;
  room_type?: string | null;
  area_m2?: number | null;
  bedrooms?: number | null;
  style?: string | null;
  aspect?: "4:3" | "4:5" | null;
  light_time?: "matin" | "midi" | "heure_doree" | "soir" | null;
  mood?: string[];
  furniture?: string[];
  avoid?: string[];
}

/** Garde-fou de surface (L2) : recalage appliqué ou non, et pourquoi. */
export interface AreaGuardResult {
  applied: boolean;
  factor: number;
  before_m2: number | null;
  wanted_m2: number;
  reason: string;
}

export const pilotApi = {
  /** 202 : la tâche à étapes tourne ; suivre par `inspirationApi.job` (champ `stage`). */
  start: (
    pid: string,
    lid: string,
    instruction?: string,
    answers?: GuidedAnswers,
  ) =>
    request<JobStatus>(
      `/projects/${pid}/levels/${lid}/autopilot`,
      instruction || answers
        ? json({ instruction: instruction || null, answers: answers ?? null })
        : { method: "POST" },
    ),
};

/** Consigne libre traduite en ambiance (V1, point 5). */
export interface Translation {
  fields: Record<string, unknown>;
  understood: string[];
  /** Les mêmes choses en mots simples, dans l'ordre de la consigne (ligne « Compris »). */
  summary?: string[];
  notes: string[];
  dropped: string[];
  by: string;
}

export interface InstructionOutcome {
  translation: Translation;
  understanding: "model" | "phrases" | "none";
  notice: string;
}

/** Ligne « Compris » de l'accueil : qui a lu le texte (Gemini, case cochée ; sinon les
 *  phrases simples, sur cette machine) et pourquoi Gemini n'a pas servi. */
export interface InstructionPreview {
  translation: Translation;
  understanding: "model" | "phrases" | "none";
  notice: string;
}

/** Accueil sans fichier (L3). */
export interface ProgrammeResult {
  kind: "generated" | "starter" | "none";
  project: Project;
  programme: Record<string, unknown> | null;
  notes: string[];
  understanding: "model" | "phrases";
  notice: string;
}

/** Réglages de l'accueil retenus pour la machine (L2). */
export interface HomeSettings {
  gemini_text: boolean;
  /** Un modèle distant est configuré (clé) : la case n'apparaît qu'alors. */
  remote_model: boolean;
  destination: string | null;
}

export const homeApi = {
  settings: () => request<HomeSettings>("/home/settings"),
  setGemini: (on: boolean) =>
    request<HomeSettings>("/home/settings", {
      ...json({ gemini_text: on }),
      method: "PUT",
    }),
};

export const instructionApi = {
  /** Ce que les phrases simples comprennent, avant tout projet (ligne « Compris : … »). */
  preview: (text: string, useModel = false) =>
    request<InstructionPreview>(
      "/instruction/preview",
      json({ text, use_model: useModel }),
    ),
  /** Traduit et applique la consigne (un pas d'historique annulable). */
  apply: (
    pid: string,
    lid: string,
    roomId: string,
    text: string,
    useModel = true,
    answers?: GuidedAnswers,
  ) =>
    request<EditResult & InstructionOutcome>(
      `/projects/${pid}/levels/${lid}/rooms/${roomId}/instruction`,
      json({ text, use_model: useModel, answers: answers ?? null }),
    ),
};

/** Ce que « Vues automatiques » a fait (V1). */
export interface AutoViews {
  added: string[];
  kept: string[];
  skipped: string[];
  aspect: string;
  notes: string[];
}

export type Aspect = "4:3" | "4:5";

export const renderApi = {
  /** Trois vues automatiques (V1) : posées en un pas d'historique annulable. */
  autoViews: (pid: string, lid: string, aspect: Aspect | null = null) =>
    request<EditResult & { views: AutoViews }>(
      `/projects/${pid}/levels/${lid}/views/auto`,
      json({ aspect }),
    ),
  /** « Rendre toutes les vues » (V1) : une tâche de fond, vue après vue. */
  renderAll: (
    pid: string,
    lid: string,
    samples: number,
    gpu: boolean,
    daylight: string | null = null,
  ) =>
    request<JobStatus>(
      `/projects/${pid}/levels/${lid}/render/all?samples=${samples}&gpu=${gpu ? "true" : "false"}${daylight ? `&daylight=${daylight}` : ""}`,
      { method: "POST" },
    ),
  /** « Re-rendre les n vues » périmées (P2) : chacune refaite comme son dernier rendu. */
  renderViews: (pid: string, lid: string, cameraIds: string[]) =>
    request<JobStatus>(
      `/projects/${pid}/levels/${lid}/render/views`,
      json({ camera_ids: cameraIds }),
    ),
  /** Vue d'architecte proposée (D3) : l'opération à appliquer, rien n'est écrit. */
  architectView: (pid: string, lid: string) =>
    request<Op>(`/projects/${pid}/levels/${lid}/cameras/architect`),
  /** Perspective de coupe (E3b) : l'opération à appliquer, rien n'est écrit. */
  sectionView: (pid: string, lid: string, axis: "x" | "y", value: number) =>
    request<Op>(
      `/projects/${pid}/levels/${lid}/cameras/section?axis=${axis}&value=${value.toFixed(2)}`,
    ),
  estimate: (pid: string, lid: string, samples: number) =>
    request<RenderEstimate>(
      `/projects/${pid}/levels/${lid}/render/estimate?samples=${samples}`,
    ),
  /** 202 : la tâche tourne côté serveur ; suivre par `inspirationApi.job`. */
  start: (
    pid: string,
    lid: string,
    cameraId: string,
    samples: number,
    gpu: boolean,
    daylight: string | null = null,
    section: { axis: string; value: number } | null = null,
  ) =>
    request<JobStatus>(
      `/projects/${pid}/levels/${lid}/cameras/${cameraId}/render?samples=${samples}&gpu=${gpu ? "true" : "false"}${daylight ? `&daylight=${daylight}` : ""}${section ? `&section_axis=${section.axis}&section_value=${section.value.toFixed(2)}` : ""}`,
      { method: "POST" },
    ),
  list: (pid: string) => request<RenderEntry[]>(`/projects/${pid}/renders`),
  /** P4 : l'élément vu en (x, y) dans un rendu (coordonnées relatives à l'image). */
  pick: (pid: string, runId: string, x: number, y: number) =>
    request<PickResult>(
      `/projects/${pid}/renders/${runId}/pick?x=${x.toFixed(4)}&y=${y.toFixed(4)}`,
    ),
  /** P4b : contour de l'élément désigné, PNG transparent à poser sur l'image. */
  outlineUrl: (pid: string, runId: string, x: number, y: number) =>
    `${BASE}/projects/${pid}/renders/${runId}/outline.png?x=${x.toFixed(4)}&y=${y.toFixed(4)}`,
  imageUrl: (pid: string, entry: RenderEntry) =>
    `${BASE}/projects/${pid}/runs/${entry.run_id}/blender/${entry.image}`,
  finishedUrl: (pid: string, entry: RenderEntry) =>
    `${BASE}/projects/${pid}/runs/${entry.run_id}/blender/${entry.finished?.image ?? "finished.png"}`,
  inspirationUrl: (pid: string, entry: RenderEntry) =>
    `${BASE}/projects/${pid}/runs/${entry.run_id}/blender/${entry.inspiration?.image ?? "inspiration.png"}`,
  catalogueUrl: (pid: string, entry: RenderEntry) =>
    `${BASE}/projects/${pid}/runs/${entry.run_id}/blender/${entry.catalogue?.image ?? "catalogue.png"}`,
  /** « Version catalogue » à la demande (V1.7b) : tâche de fond, suivie comme un rendu. */
  catalogue: (pid: string, runId: string) =>
    request<JobStatus>(`/projects/${pid}/renders/${runId}/catalogue`, {
      method: "POST",
    }),
  /** Galerie (US2) : renommer (titre "" = nom du point de vue) et/ou fixer les favoris. */
  update: (
    pid: string,
    runId: string,
    patch: { title?: string | null; favorites?: RenderVariant[] },
  ) =>
    request<RenderEntry>(`/projects/${pid}/renders/${runId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  /** Corbeille douce : le rendu peut être remis pendant `purge_after_s` secondes. */
  remove: (pid: string, runId: string) =>
    request<{ run_id: string; purge_after_s: number }>(
      `/projects/${pid}/renders/${runId}`,
      { method: "DELETE" },
    ),
  restore: (pid: string, runId: string) =>
    request<RenderEntry>(`/projects/${pid}/renders/${runId}/restore`, {
      method: "POST",
    }),
  favoritesZipUrl: (pid: string) =>
    `${BASE}/projects/${pid}/renders/favorites.zip`,
  /** L'URL d'une variante d'un rendu. */
  variantUrl: (
    pid: string,
    entry: RenderEntry,
    variant: RenderVariant,
  ): string =>
    variant === "finished"
      ? renderApi.finishedUrl(pid, entry)
      : variant === "inspiration"
        ? renderApi.inspirationUrl(pid, entry)
        : variant === "catalogue"
          ? renderApi.catalogueUrl(pid, entry)
          : renderApi.imageUrl(pid, entry),
  /** Finition IA (D5) : tâche de fond, suivie comme un rendu. */
  finish: (
    pid: string,
    runId: string,
    body: { strength: number; style: FinishStyle | null; seed?: number },
  ) =>
    request<JobStatus>(`/projects/${pid}/renders/${runId}/finish`, json(body)),
};

export const aiApi = {
  status: () => request<AiStatus>("/ai"),
  perceive: (pid: string, imageId: string) =>
    request<PerceptionRun>(`/projects/${pid}/images/${imageId}/perceive`, {
      method: "POST",
    }),
  /** Aperçu exact de ce qui partirait au modèle (texte seul) : aucun envoi. */
  briefPreview: (pid: string, lid: string, body: IntentionBody) =>
    request<IntentionPreview>(
      `/projects/${pid}/levels/${lid}/brief/preview`,
      json(body),
    ),
  /** Brief par zones (V1) d'après l'ambiance de la pièce ; rien n'est placé. */
  zoneBrief: (pid: string, lid: string, roomId: string | null) =>
    request<LayoutBrief>(
      `/projects/${pid}/levels/${lid}/brief/zones`,
      json({ room_id: roomId }),
    ),
  /** Rôle Intention : souhait → brief structuré (aucune position). */
  brief: (pid: string, lid: string, body: IntentionBody) =>
    request<IntentionRun>(`/projects/${pid}/levels/${lid}/brief`, json(body)),
  intentionArtifactUrl: (pid: string, runId: string, name: string) =>
    `${BASE}/projects/${pid}/runs/${runId}/intention/${name}`,
  networkLog: (pid: string) =>
    request<NetworkLogEntry[]>(`/projects/${pid}/network-log`),
  artifactUrl: (pid: string, runId: string, name: string) =>
    `${BASE}/projects/${pid}/runs/${runId}/perception/${name}`,
};

/* Tableau du projet (B1) : disposition gardée à part de la géométrie (board.json). */
export type BoardKind =
  | "contextual"
  | "requirements"
  | "photo"
  | "render"
  | "note"
  | "plan"
  | "frame"
  | "file"
  | "video"
  | "pdf"
  | "link";
export type BoardRole =
  | "moodboard"
  | "prompt"
  | "photos"
  | "inspirations"
  | "renders"
  | "inspiration"
  | "files";

/** KAN-65 : les huit couleurs d'un post-it (comme `NoteColor` côté serveur). */
export type NoteColor =
  "yellow" | "orange" | "pink" | "violet" | "blue" | "green" | "gray" | "white";

export interface BoardItem {
  id: string;
  kind: BoardKind;
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
  locked: boolean;
  frame: string | null;
  ref: string | null;
  text: string;
  role: BoardRole | null;
  /** Adresse d'un lien (C1), http ou https ; la page n'est jamais récupérée. */
  url?: string | null;
  /** Coché « Référence » (A2) : cette image guide l'allure des rendus contextualisés. */
  reference?: boolean;
  /** « À conserver » (A3) : variante retenue. */
  kept?: boolean;
  /** Commentaire attaché à la carte (A3). */
  comment?: string;
  /** KAN-65 : couleur d'un post-it ; absente (note d'avant) = jaune. */
  color?: NoteColor | null;
  /** B2c : heure et acteur de la dernière écriture de cet élément, posés par le serveur. */
  updated_at?: string | null;
  updated_by?: string | null;
  /** KAN-91 : étiquettes de matière ou de style désactivées (`m:lin`, `s:japandi`). */
  ignored_tags?: string[];
}

/** KAN-91 : les trois liens entre cartes (le type se déduit des deux cartes). */
export type LinkKind = "feeds_plan" | "guides_style" | "renders_view";

export interface BoardLink {
  id: string;
  source: string;
  target: string;
  kind: LinkKind;
  /** Posé par PlanDesign (zéro geste), pas tiré à la main. */
  auto?: boolean;
  updated_at?: string | null;
  updated_by?: string | null;
}

/** B2c : les écritures d'un geste ; la dernière écriture gagne, élément par élément. */
export interface BoardPatch {
  set: BoardItem[];
  delete: string[];
  view?: BoardView;
  /** KAN-91 : liens posés ou modifiés, liens coupés (leur paire est retenue). */
  links_set?: BoardLink[];
  links_delete?: string[];
}

/** KAN-91 : ce que lit la génération d'une carte, tel que le serveur le lira (C1). */
export interface GenerationInputs {
  target: string;
  kind: "plan" | "renders" | "none";
  photos: string[];
  photo_card: string | null;
  plan_level: string | null;
  plan_item: string | null;
  inspirations: {
    item: string;
    kind: string;
    ref: string | null;
    card: string;
    ignored_tags: string[];
  }[];
  inspiration_cards: string[];
  can_render: boolean;
  /** « Reliez un plan pour rendre » (D2). */
  reason: string | null;
}

/** KAN-91 : la bulle de survol d'un lien. */
export interface LinkSummary {
  id: string;
  kind: LinkKind;
  sentence: string;
  auto: boolean;
  updated_by: string | null;
  rights: string;
}

export interface BoardView {
  x: number;
  y: number;
  zoom: number;
}

export interface Board {
  version: 1;
  /** Révision enregistrée : un envoi parti d'une révision périmée est refusé (409). */
  revision: number;
  items: BoardItem[];
  view: BoardView;
  dismissed: string[];
  updated_at: string;
  /** KAN-91 : liens entre cartes, et paires coupées à la main (jamais recréées). */
  links?: BoardLink[];
  cut_links?: string[];
}

export interface MediaRef {
  id: string;
  name: string;
  media_type: string;
  size: number;
  path: string;
  imported_at: string;
  /** C1 : ce qu'il est d'après ses premiers octets. */
  kind?: "image" | "video" | "pdf" | "file";
  /** Vignette rendue sur la machine (première page d'un PDF). */
  thumb?: string | null;
  /** Envoyé par l'espace de travail, qui pose lui-même sa carte (25 oct.). */
  placed?: boolean;
}

/** KAN-51 : un point de l'espace de travail ; sur un élément, `at` est relatif à son coin et
 *  `last` sa dernière position connue (le fil y reste, « détaché », s'il est supprimé). */
export interface CommentTarget {
  at: { x: number; y: number };
  item_id?: string | null;
  last?: { x: number; y: number } | null;
}

export interface CommentMessage {
  id: string;
  author_name: string;
  author_color: string;
  mine: boolean;
  text: string;
  created_at: string;
  edited_at: string | null;
}

export interface CommentThread {
  id: string;
  target: CommentTarget;
  resolved: boolean;
  resolved_at: string | null;
  messages: CommentMessage[];
  can_delete: boolean;
}

/** KAN-51 : commentaires de l'espace de travail (mêmes routes en local et avec les comptes). */
export const commentsApi = {
  list: (pid: string) => request<CommentThread[]>(`/projects/${pid}/comments`),
  create: (pid: string, target: CommentTarget, text: string) =>
    request<CommentThread>(`/projects/${pid}/comments`, json({ target, text })),
  reply: (pid: string, tid: string, text: string) =>
    request<CommentThread>(
      `/projects/${pid}/comments/${tid}/replies`,
      json({ text }),
    ),
  edit: (pid: string, tid: string, mid: string, text: string) =>
    request<CommentThread>(`/projects/${pid}/comments/${tid}/messages/${mid}`, {
      method: "PATCH",
      body: JSON.stringify({ text }),
    }),
  removeMessage: (pid: string, tid: string, mid: string) =>
    request<void>(`/projects/${pid}/comments/${tid}/messages/${mid}`, {
      method: "DELETE",
    }),
  resolve: (pid: string, tid: string) =>
    request<CommentThread>(`/projects/${pid}/comments/${tid}/resolve`, {
      method: "POST",
    }),
  reopen: (pid: string, tid: string) =>
    request<CommentThread>(`/projects/${pid}/comments/${tid}/reopen`, {
      method: "POST",
    }),
  removeThread: (pid: string, tid: string) =>
    request<void>(`/projects/${pid}/comments/${tid}`, { method: "DELETE" }),
};

/** KAN-52 : une ligne de la cloche (un geste, ou des modifications regroupées). */
export type BellType =
  | "comment.created"
  | "comment.replied"
  | "comment.resolved"
  | "comment.reopened"
  | "member.joined"
  | "render.done"
  | "board.changed"
  | "plan.changed"
  | "reaction.added";

export interface BellLine {
  id: string;
  type: BellType;
  /** `local` ou l'id du compte (filtre « par personne » du journal d'activité). */
  actor_id: string;
  actor_name: string;
  actor_color: string;
  mine: boolean;
  project_id: string;
  project_name: string;
  at: string;
  count: number;
  unread: boolean;
  thread_id: string | null;
  item_id: string | null;
  level_id: string | null;
  run_id: string | null;
  label: string | null;
  /** KAN-58 : l'élément touché s'il n'y en a qu'un, et le détail des modifications. */
  item_kind: string | null;
  added: number;
  changed: number;
  removed: number;
  /** KAN-65 : parmi les modifiés, ceux dont seule la couleur a changé. */
  recolored?: number;
  /** KAN-78 : l'emoji d'une réaction (absent si la ligne en regroupe plusieurs). */
  emoji?: string | null;
}

export type ActivityKind =
  "comments" | "board" | "plan" | "members" | "renders" | "reactions";

/* KAN-78 : réactions aux éléments de l'espace de travail (six emojis fixes). */
export type ReactionKey = "up" | "heart" | "love" | "think" | "down" | "fire";

/** L'ordre du sélecteur et des pastilles (le même que le serveur). */
export const REACTIONS: { key: ReactionKey; emoji: string; label: string }[] = [
  { key: "up", emoji: "👍", label: "J'aime" },
  { key: "heart", emoji: "❤️", label: "J'adore" },
  { key: "love", emoji: "😍", label: "Coup de cœur" },
  { key: "think", emoji: "🤔", label: "À voir" },
  { key: "down", emoji: "👎", label: "Je n'aime pas" },
  { key: "fire", emoji: "🔥", label: "Génial" },
];

/** Une pastille : un emoji sur un élément, combien, si j'en suis, et qui (« Vous » d'abord). */
export interface ReactionPill {
  item_id: string;
  key: ReactionKey;
  emoji: string;
  count: number;
  mine: boolean;
  names: string[];
}

export const reactionsApi = {
  list: (pid: string) => request<ReactionPill[]>(`/projects/${pid}/reactions`),
  add: (pid: string, itemId: string, key: ReactionKey) =>
    request<ReactionPill[]>(`/projects/${pid}/reactions/${itemId}/${key}`, {
      method: "PUT",
    }),
  remove: (pid: string, itemId: string, key: ReactionKey) =>
    request<ReactionPill[]>(`/projects/${pid}/reactions/${itemId}/${key}`, {
      method: "DELETE",
    }),
};

/** KAN-58 : le journal d'activité d'un projet (tout membre le lit). */
export const activityApi = {
  list: (
    pid: string,
    filters: { who?: string | null; kind?: ActivityKind | null },
    before?: string | null,
  ) => {
    const query = new URLSearchParams();
    if (filters.who) query.set("who", filters.who);
    if (filters.kind) query.set("kind", filters.kind);
    if (before) query.set("before", before);
    const q = query.toString();
    return request<{ items: BellLine[]; next: string | null }>(
      `/projects/${pid}/activity${q ? `?${q}` : ""}`,
    );
  },
};

export interface BellPage {
  items: BellLine[];
  next: string | null;
  unread: number;
}

/** KAN-52 : la cloche (les projets dont je suis membre ; en local, ceux de la machine). */
export const bellApi = {
  list: (tab: "unread" | "all", before?: string | null) =>
    request<BellPage>(
      `/notifications?tab=${tab}${before ? `&before=${encodeURIComponent(before)}` : ""}`,
    ),
  count: () => request<{ unread: number }>("/notifications/count"),
  read: (eventId: string) =>
    request<void>("/notifications/read", json({ event_id: eventId })),
  readAll: () => request<void>("/notifications/read-all", { method: "POST" }),
};

/** B2d : bail d'édition d'un plan (comptes seulement). « Prendre la main » ne réussit que sur
 *  un plan libre ou dont le bail a expiré ; « Demander la main » prévient le seul tenant. */
export const leaseApi = {
  take: (pid: string, levelId: string) =>
    request<unknown>(`/projects/${pid}/levels/${levelId}/lease`, {
      method: "POST",
    }),
  give: (pid: string, levelId: string) =>
    request<void>(`/projects/${pid}/levels/${levelId}/lease`, {
      method: "DELETE",
    }),
  ask: (pid: string, levelId: string) =>
    request<void>(`/projects/${pid}/levels/${levelId}/lease/request`, {
      method: "POST",
    }),
};

/** Une écriture qui part même quand la page se ferme (`keepalive`), sans attendre de réponse. */
function keepalive(method: string, path: string, body: unknown): void {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = readToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  // B2c : comme toute écriture, sans s'annoncer à soi-même.
  if (liveConn) headers["X-Live-Conn"] = liveConn;
  void fetch(`${BASE}${path}`, {
    method,
    headers,
    body: JSON.stringify(body),
    keepalive: true,
  }).catch(() => undefined);
}

export const boardApi = {
  get: (pid: string) => request<Board>(`/projects/${pid}/board`),
  /** B2c : les écritures d'un geste (éléments posés, modifiés, retirés ; vue). Ne touche
   *  jamais la géométrie du plan. Le PUT du tableau entier ne sert plus qu'aux onglets d'avant
   *  B2c. */
  patch: (pid: string, patch: BoardPatch) =>
    request<Board>(`/projects/${pid}/board/items`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  media: (pid: string) => request<MediaRef[]>(`/projects/${pid}/media`),
  upload: (pid: string, file: File) => {
    const refused = tooBig(pid, file);
    if (refused) return Promise.reject(refused);
    const body = new FormData();
    body.append("file", file);
    // L'espace de travail pose lui-même la carte du fichier : la synchronisation ne doit pas
    // en faire une copie dans « Fichiers » entre l'envoi et l'écriture de la carte (25 oct.).
    return request<MediaRef>(`/projects/${pid}/media?placed=true`, {
      method: "POST",
      body,
    });
  },
  mediaUrl: (pid: string, mediaId: string) =>
    `${BASE}/projects/${pid}/media/${mediaId}/file`,
  mediaThumbUrl: (pid: string, mediaId: string) =>
    `${BASE}/projects/${pid}/media/${mediaId}/thumb`,
  removeMedia: (pid: string, mediaId: string) =>
    request<void>(`/projects/${pid}/media/${mediaId}`, { method: "DELETE" }),
  /** B4 : dernier envoi quand la page se cache ou se ferme ; `keepalive` laisse la
   *  requête partir même si l'onglet disparaît. Pas de réponse lue. */
  patchKeepalive: (pid: string, patch: BoardPatch): void =>
    keepalive("PATCH", `/projects/${pid}/board/items`, patch),
  /** KAN-43 : MA vue de l'espace de travail (tout membre, lecteur compris). */
  putView: (pid: string, view: BoardView) =>
    request<BoardView>(`/projects/${pid}/view`, {
      method: "PUT",
      body: JSON.stringify(view),
    }),
  putViewKeepalive: (pid: string, view: BoardView): void =>
    keepalive("PUT", `/projects/${pid}/view`, view),
  /** KAN-91 : ce que lit la génération d'un plan ou d'une carte Rendus (C1). */
  inputs: (pid: string, targetId: string) =>
    request<GenerationInputs>(`/projects/${pid}/board/inputs/${targetId}`),
  /** KAN-91 : la phrase de survol d'un lien, recalculée depuis les membres. */
  link: (pid: string, linkId: string) =>
    request<LinkSummary>(`/projects/${pid}/board/links/${linkId}`),
};

/* Rendu contextualisé (A1) : une source du tableau et une intention → prompt enrichi par le
 * plan, montré et modifiable, puis des variantes ; le plan et la 3D ne bougent pas. */
export type ContextualVariant = "conservatrice" | "creative" | "radicale";
export type ContextualBudget = "economique" | "standard" | "haut_de_gamme";

export interface ContextualSource {
  kind: "photo" | "render";
  id: string;
  label?: string;
}

export interface ContextualPromptResult {
  prompt: string;
  /** La même chose en français, en lecture seule (le modèle lit l'anglais). */
  prompt_fr: string;
  /** Jetons CLIP estimés (le modèle coupe à 77). */
  tokens: number;
  context: string[];
  estimated: boolean;
  understood: string[];
  /** Ce que le lecteur a compris, en mots simples et en français. */
  summary: string[];
  fields: Record<string, unknown>;
  reader: "model" | "phrases" | "none";
  notice: string;
}

/** Une image du tableau cochée « Référence » (A2) : son allure guide la génération. */
export interface ContextualReference {
  kind: "photo" | "render" | "contextual" | "file";
  ref: string;
  label: string;
}

export interface ContextualEntry {
  run_id: string;
  variant: ContextualVariant;
  image: string;
  prompt: string;
  intention: string;
  budget: ContextualBudget;
  source: ContextualSource;
  context: string[];
  estimated: boolean;
  strength: number;
  control_scale: number;
  seed: number;
  steps: number;
  model_ids: string[];
  guided_by?: string[];
  references?: string[];
  reference_scale?: number | null;
  duration_s: number;
  created_at: string;
  label: string;
}

export const contextualApi = {
  prompt: (
    pid: string,
    lid: string,
    body: {
      intention: string;
      budget: ContextualBudget;
      source: ContextualSource;
    },
  ) =>
    request<ContextualPromptResult>(
      `/projects/${pid}/levels/${lid}/contextual/prompt`,
      json(body),
    ),
  start: (
    pid: string,
    lid: string,
    body: {
      intention: string;
      budget: ContextualBudget;
      source: ContextualSource;
      prompt: string;
      variants: ContextualVariant[];
      seed: number;
      references: ContextualReference[];
      reference_scale: number;
    },
  ) =>
    request<JobStatus>(`/projects/${pid}/levels/${lid}/contextual`, json(body)),
  list: (pid: string) =>
    request<ContextualEntry[]>(`/projects/${pid}/contextual`),
  imageUrl: (pid: string, runId: string, name: string) =>
    `${BASE}/projects/${pid}/runs/${runId}/contextual/${name}`,
};
