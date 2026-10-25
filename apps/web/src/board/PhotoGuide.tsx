import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Libellé du lien qui ouvre le guide (KAN-91 L5) : le même partout. */
export const PHOTO_GUIDE_LINK = "Guide de prise de photo";

/** KAN-91 L5 : ce que montre l'alerte « gros plan » après l'import (ton neutre, mots d'augus). */
export const CLOSE_UP_TEXT =
  "Cette photo montre un détail (un meuble, un coin) : prenez la pièce entière, ou ajoutez une mesure de référence.";

const GESTURES: { key: string; title: string; text: string; art: ReactNode }[] =
  [
    {
      key: "loin",
      title: "Reculez au maximum",
      text: "Dos au mur ou dans un coin, toute la pièce dans le cadre.",
      art: (
        <svg viewBox="0 0 200 120" aria-hidden="true">
          <rect x="10" y="20" width="180" height="90" className="pg-wall" />
          <path d="M170,92 L20,40 L60,108Z" className="pg-cone" />
          <circle cx="170" cy="92" r="6" className="pg-cam" />
        </svg>
      ),
    },
    {
      key: "angle",
      title: "Grand angle",
      text: "Choisissez 0,5× si votre téléphone le propose.",
      art: (
        <svg viewBox="0 0 200 120" aria-hidden="true">
          <rect
            x="55"
            y="12"
            width="90"
            height="100"
            rx="12"
            className="pg-phone"
          />
          <rect
            x="63"
            y="24"
            width="74"
            height="70"
            rx="4"
            className="pg-screen"
          />
          <circle cx="80" cy="80" r="14" className="pg-zoom-on" />
          <text x="80" y="84" className="pg-zoom-on-text">
            0,5×
          </text>
          <circle cx="118" cy="80" r="11" className="pg-zoom-off" />
          <text x="118" y="84" className="pg-zoom-off-text">
            1×
          </text>
        </svg>
      ),
    },
    {
      key: "haut",
      title: "Appareil à ~1,50 m",
      text: "À hauteur de poitrine, téléphone droit, ni vers le sol ni vers le plafond.",
      art: (
        <svg viewBox="0 0 200 120" aria-hidden="true">
          <line x1="20" y1="110" x2="180" y2="110" className="pg-ground" />
          <circle cx="80" cy="30" r="9" className="pg-person-head" />
          <path
            d="M80,40 V80 M80,80 L70,110 M80,80 L90,110 M80,52 L104,44"
            className="pg-person"
          />
          <rect
            x="104"
            y="38"
            width="10"
            height="14"
            rx="2"
            className="pg-cam"
          />
          <path
            d="M140,46 V110 M134,46 h12 M134,110 h12"
            className="pg-measure"
          />
          <text x="150" y="82" className="pg-measure-text">
            1,50 m
          </text>
        </svg>
      ),
    },
    {
      key: "coins",
      title: "Deux coins et le sol",
      text: "Deux angles de murs et le bas des murs visibles sur la photo.",
      art: (
        <svg viewBox="0 0 200 120" aria-hidden="true">
          <polygon points="0,0 60,30 60,85 0,120" className="pg-side" />
          <polygon points="200,0 140,30 140,85 200,120" className="pg-side" />
          <rect x="60" y="30" width="80" height="55" className="pg-back" />
          <polygon points="0,120 60,85 140,85 200,120" className="pg-floor" />
          <circle cx="60" cy="85" r="6" className="pg-corner" />
          <circle cx="140" cy="85" r="6" className="pg-corner" />
        </svg>
      ),
    },
  ];

/** Plusieurs photos d'une pièce (25 oct.) : ce qui permet de les recouper. */
export const SERIES_RULE =
  "Prenez chaque photo dos à un mur, en montrant le mur d'en face en entier, d'un coin à l'autre. Deux photos qui se suivent doivent montrer au moins un même coin et ses deux murs : c'est ce qui permet de les recouper. Évitez les gros plans, chaque photo doit voir le fond de la pièce.";

const AVOID = [
  "gros plan d'un meuble",
  "photo floue ou de nuit",
  "appareil penché vers le haut",
];

/** KAN-91 L5 : le guide de prise de photo. Il ne s'ouvre jamais tout seul (décision d'augus,
 *  D4) : seulement par le lien « Guide de prise de photo » du cadre Photos ou de l'alerte
 *  « gros plan ». Échap, le fond ou « Fermer » le ferment ; « Importer des photos » ouvre le
 *  choix de fichiers du cadre quand il y en a un. */
export function PhotoGuide({
  onClose,
  onImport,
}: {
  onClose: () => void;
  onImport?: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  return createPortal(
    <div className="photo-guide-backdrop" onPointerDown={onClose}>
      <div
        className="photo-guide"
        role="dialog"
        aria-modal="true"
        aria-label={PHOTO_GUIDE_LINK}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <h2>{PHOTO_GUIDE_LINK}</h2>
        <p className="photo-guide-lead">
          Une photo prise de loin donne un plan juste ; un gros plan ne donne
          rien à mesurer.
        </p>
        <ol className="photo-guide-cards">
          {GESTURES.map((g) => (
            <li key={g.key} className="photo-guide-card">
              {g.art}
              <h3>{g.title}</h3>
              <p>{g.text}</p>
            </li>
          ))}
        </ol>
        <p className="photo-guide-next">
          Puis une deuxième photo, prise depuis l'angle opposé.
        </p>
        {/* Consigne du PO (25 oct.), mot pour mot : ce qui permet de recouper les photos. */}
        <p className="photo-guide-next">{SERIES_RULE}</p>
        <p className="photo-guide-avoid">
          <span>À éviter :</span>
          {AVOID.map((a) => (
            <span key={a} className="photo-guide-no">
              {a}
            </span>
          ))}
        </p>
        <div className="photo-guide-actions">
          <button type="button" ref={closeRef} onClick={onClose}>
            Fermer
          </button>
          {onImport && (
            <button
              type="button"
              className="primary"
              onClick={() => {
                onClose();
                onImport();
              }}
            >
              Importer des photos
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
