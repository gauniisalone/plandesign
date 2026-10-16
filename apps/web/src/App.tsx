import { useEffect, useState } from "react";
import { openReadSession } from "./api/client";
import { Editor } from "./pages/Editor";
import { Home } from "./pages/Home";
import type { Step } from "./panels/Toolbar";

interface Route {
  projectId: string | null;
  step: Step | undefined;
}

/** `#/projects/<id>` ouvre l'atelier ; `#/projects/<id>/import` l'ouvre sur « Ajouter une source ». */
function routeFromHash(): Route {
  const m = window.location.hash.match(
    /^#\/projects\/([0-9a-f-]{36})(\/import)?$/,
  );
  return { projectId: m?.[1] ?? null, step: m?.[2] ? "import" : undefined };
}

export function App() {
  const [route, setRoute] = useState<Route>(routeFromHash);

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    // API exposée : un jeton déjà connu de l'onglet pose le cookie de lecture dès l'arrivée.
    void openReadSession();
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const open = (id: string, step?: Step) => {
    window.location.hash = `#/projects/${id}${step === "import" ? "/import" : ""}`;
  };
  const home = () => {
    window.location.hash = "";
  };

  return route.projectId ? (
    <Editor
      key={route.projectId}
      projectId={route.projectId}
      initialStep={route.step}
      onHome={home}
    />
  ) : (
    <Home onOpen={open} />
  );
}
