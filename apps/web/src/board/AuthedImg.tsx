import { useEffect, useState } from "react";
import { hasToken, readTokenForFetch } from "../api/client";

/** Image du tableau quand l'API est exposée : le cookie de lecture porte normalement les
 *  `<img src>` ; si un jeton est connu de l'onglet, l'image passe par fetch + blob avec
 *  l'en-tête, ce qui marche même quand le navigateur bloque le cookie. Sans jeton (boucle
 *  locale) : un `<img>` ordinaire, rien ne change. */

interface Loaded {
  /** L'adresse dont `blob` est l'image ; une autre adresse = chargement en cours. */
  src: string;
  blob: string | null;
  failed: boolean;
}

export function AuthedImg({
  src,
  alt,
  className,
  missing = "image indisponible",
}: {
  src: string;
  alt: string;
  className?: string;
  missing?: string;
}) {
  const authed = hasToken();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  useEffect(() => {
    if (!authed) return;
    let off = false;
    let url: string | null = null;
    fetch(src, { headers: { Authorization: `Bearer ${readTokenForFetch()}` } })
      .then((r) =>
        r.ok ? r.blob() : Promise.reject(new Error(String(r.status))),
      )
      .then((b) => {
        if (off) return;
        url = URL.createObjectURL(b);
        setLoaded({ src, blob: url, failed: false });
      })
      .catch(() => {
        if (!off) setLoaded({ src, blob: null, failed: true });
      });
    return () => {
      off = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [src, authed]);
  if (!authed)
    return <img src={src} alt={alt} className={className} draggable={false} />;
  const current = loaded && loaded.src === src ? loaded : null;
  if (current?.failed) return <span className="board-missing">{missing}</span>;
  return current?.blob ? (
    <img src={current.blob} alt={alt} className={className} draggable={false} />
  ) : (
    <span className="board-missing muted">…</span>
  );
}
