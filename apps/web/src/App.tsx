import { useCallback, useEffect, useMemo, useState } from "react";
import {
  authApi,
  openReadSession,
  setAccountsMode,
  type Me,
} from "./api/client";
import { Editor } from "./pages/Editor";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { NewFromSite } from "./pages/NewFromSite";
import type { Step } from "./panels/Toolbar";
import { AccountContext, type Account } from "./state/account";
import { loadHosting } from "./state/hosting";

interface Route {
  projectId: string | null;
  step: Step | undefined;
  /** `#/nouveau?…` (B1d) : paramètres du formulaire du site ; null hors de cette route. */
  nouveau: string | null;
}

/** `#/projects/<id>` ouvre l'atelier ; `#/projects/<id>/import` l'ouvre sur « Ajouter une
 *  source », `/board` sur l'espace de travail ; `#/nouveau?…` crée l'espace prérempli
 *  depuis le site. */
function routeFromHash(): Route {
  const hash = window.location.hash;
  const nouveau = hash.match(/^#\/nouveau(?:\?(.*))?$/);
  if (nouveau)
    return { projectId: null, step: undefined, nouveau: nouveau[1] ?? "" };
  const m = hash.match(/^#\/projects\/([0-9a-f-]{36})(?:\/(import|board))?$/);
  return {
    projectId: m?.[1] ?? null,
    step: (m?.[2] as Step | undefined) ?? undefined,
    nouveau: null,
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

export function App() {
  const [route, setRoute] = useState<Route>(routeFromHash);
  const [session, setSession] = useState<Session>({ kind: "loading" });

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    let alive = true;
    void loadHosting().then(async (hosting) => {
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
    });
    return () => {
      alive = false;
      window.removeEventListener("hashchange", onHash);
    };
  }, []);

  const open = useCallback((id: string, step?: Step) => {
    const target = `#/projects/${id}${step === "import" || step === "board" ? `/${step}` : ""}`;
    // Depuis #/nouveau, l'adresse est remplacée : « Précédent » ne recrée pas d'espace.
    if (window.location.hash.startsWith("#/nouveau"))
      window.location.replace(target);
    else window.location.hash = target;
  }, []);
  const home = () => {
    window.location.hash = "";
  };

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

  if (session.kind === "loading") return null;
  if (session.kind === "signed-out")
    return <Login onSignedIn={(me) => setSession({ kind: "signed-in", me })} />;

  return (
    <AccountContext.Provider value={account}>
      {route.nouveau !== null ? (
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
