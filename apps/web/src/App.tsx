import { useCallback, useEffect, useMemo, useState } from "react";
import {
  authApi,
  openReadSession,
  setAccountsMode,
  type Me,
} from "./api/client";
import { AcceptInvitation } from "./pages/AcceptInvitation";
import { Editor } from "./pages/Editor";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { NewFromSite } from "./pages/NewFromSite";
import type { Step } from "./panels/Toolbar";
import { AccountContext, type Account } from "./state/account";
import { refreshCapabilities, watchCapabilities } from "./state/capabilities";
import { loadHosting } from "./state/hosting";
import { APP_NAME } from "./brand";
import { IconPlan } from "./ui/icons";

interface Route {
  projectId: string | null;
  step: Step | undefined;
  /** `#/nouveau?…` (B1d) : paramètres du formulaire du site ; null hors de cette route. */
  nouveau: string | null;
  /** `#/invitation/<jeton>` (B1e) : le jeton d'un lien d'invitation ; null hors de cette route. */
  invitation: string | null;
}

/** `#/projects/<id>` ouvre l'atelier ; `#/projects/<id>/import` l'ouvre sur « Ajouter une
 *  source », `/board` sur l'espace de travail ; `#/nouveau?…` crée l'espace prérempli
 *  depuis le site ; `#/invitation/<jeton>` ouvre un lien d'invitation (B1e). */
function routeFromHash(): Route {
  const hash = window.location.hash;
  const nouveau = hash.match(/^#\/nouveau(?:\?(.*))?$/);
  if (nouveau)
    return {
      projectId: null,
      step: undefined,
      nouveau: nouveau[1] ?? "",
      invitation: null,
    };
  const invited = hash.match(/^#\/invitation\/([A-Za-z0-9_-]{16,128})$/);
  if (invited)
    return {
      projectId: null,
      step: undefined,
      nouveau: null,
      invitation: invited[1]!,
    };
  const m = hash.match(/^#\/projects\/([0-9a-f-]{36})(?:\/(import|board))?$/);
  return {
    projectId: m?.[1] ?? null,
    step: (m?.[2] as Step | undefined) ?? undefined,
    nouveau: null,
    invitation: null,
  };
}

/** B1d : sans comptes, l'application comme avant ; avec, d'abord la session (une seule
 *  réponse 401 au plus au chargement), sinon la page de connexion et rien d'autre. */
/** La session lue une seule fois par chargement (le mode strict de React monte deux fois
 *  en développement) : une seule réponse 401 au plus. */
let firstMe: Promise<Me | null> | null = null;
function readSessionOnce(): Promise<Me | null> {
  firstMe ??= authApi.me().catch(() => null);
  return firstMe;
}

type Session =
  | { kind: "loading" }
  | { kind: "local" }
  | { kind: "signed-out" }
  | { kind: "signed-in"; me: Me };

/** Pendant la lecture de /api/health : la barre du haut et le fond, jamais une page blanche ;
 *  au-delà de 10 s sans réponse, une ligne courte dit ce qu'on attend. */
const BOOT_SLOW_MS = 10_000;
function BootSkeleton() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), BOOT_SLOW_MS);
    return () => window.clearTimeout(timer);
  }, []);
  return (
    <div className="boards" aria-busy="true">
      <header className="boards-top">
        <span className="brand-mark">
          <IconPlan />
        </span>
        <span className="boards-brand">{APP_NAME}</span>
      </header>
      {slow && (
        <p className="boot-wait" role="status">
          Connexion au serveur…
        </p>
      )}
    </div>
  );
}

export function App() {
  const [route, setRoute] = useState<Route>(routeFromHash);
  const [session, setSession] = useState<Session>({ kind: "loading" });

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    let alive = true;
    // Ce que le serveur sait calculer, lu avant d'afficher quoi que ce soit, puis relu
    // régulièrement (un poste de calcul peut se connecter en cours de session).
    void Promise.all([loadHosting(), refreshCapabilities({ boot: true })]).then(
      async ([hosting]) => {
        watchCapabilities();
        if (!hosting.accounts) {
          if (!alive) return;
          setSession({ kind: "local" });
          // API exposée par jeton : un jeton déjà connu de l'onglet pose le cookie de lecture.
          void openReadSession();
          return;
        }
        setAccountsMode(true, () => setSession({ kind: "signed-out" }));
        const me = await readSessionOnce();
        if (alive)
          setSession(me ? { kind: "signed-in", me } : { kind: "signed-out" });
      },
    );
    return () => {
      alive = false;
      window.removeEventListener("hashchange", onHash);
    };
  }, []);

  const open = useCallback((id: string, step?: Step) => {
    const target = `#/projects/${id}${step === "import" || step === "board" ? `/${step}` : ""}`;
    // Depuis #/nouveau ou #/invitation, l'adresse est remplacée : « Précédent » ne recrée
    // pas d'espace, et le jeton d'invitation ne reste pas dans l'historique.
    const hash = window.location.hash;
    if (hash.startsWith("#/nouveau") || hash.startsWith("#/invitation"))
      window.location.replace(target);
    else window.location.hash = target;
  }, []);
  const home = useCallback(() => {
    window.location.hash = "";
  }, []);

  const signedOut = useCallback(() => setSession({ kind: "signed-out" }), []);
  const account = useMemo<Account | null>(
    () =>
      session.kind === "signed-in"
        ? {
            me: session.me,
            signOut: () => authApi.logout().finally(signedOut),
            signOutAll: () => authApi.logoutAll().finally(signedOut),
          }
        : null,
    [session, signedOut],
  );

  // Repère des e2e : l'application est affichée, plus seulement son squelette.
  const ready = session.kind !== "loading";
  useEffect(() => {
    if (ready) document.documentElement.dataset.appReady = "";
  }, [ready]);

  if (session.kind === "loading") return <BootSkeleton />;
  if (session.kind === "signed-out")
    return (
      <Login
        invitation={route.invitation ?? undefined}
        onSignedIn={(me) => setSession({ kind: "signed-in", me })}
      />
    );

  return (
    <AccountContext.Provider value={account}>
      {route.invitation !== null && account ? (
        <AcceptInvitation
          token={route.invitation}
          onOpen={open}
          onHome={home}
        />
      ) : route.nouveau !== null ? (
        <NewFromSite query={route.nouveau} onOpen={open} />
      ) : route.projectId ? (
        <Editor
          key={route.projectId}
          projectId={route.projectId}
          initialStep={route.step}
          onHome={home}
        />
      ) : (
        <Home onOpen={open} />
      )}
    </AccountContext.Provider>
  );
}
