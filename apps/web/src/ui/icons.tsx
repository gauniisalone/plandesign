import type { SVGProps } from "react";

/** Icônes en traits (24 × 24, courant), dans l'esprit de la maquette : pas de dépendance. */
type IconProps = SVGProps<SVGSVGElement>;

function Svg({ children, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IconCursor = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 4l7.5 16 2.2-6.3L20 11.5 4 4z" />
  </Svg>
);

export const IconHand = (p: IconProps) => (
  <Svg {...p}>
    <path d="M18 11V6a2 2 0 0 0-4 0v5" />
    <path d="M14 10V4a2 2 0 0 0-4 0v6" />
    <path d="M10 10.5V6a2 2 0 0 0-4 0v8" />
    <path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15" />
  </Svg>
);

export const IconWall = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 20 20 4" />
    <path d="M4 20h4v-4" />
    <path d="M20 4h-4v4" />
  </Svg>
);

export const IconDoor = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 20V5a1 1 0 0 1 1-1h9v16" />
    <path d="M3 20h18" />
    <path d="M15 4a8 8 0 0 1 4 7" />
    <circle cx="12" cy="12" r=".6" fill="currentColor" />
  </Svg>
);

export const IconWindow = (p: IconProps) => (
  <Svg {...p}>
    <rect x="4" y="5" width="16" height="14" rx="1.5" />
    <path d="M12 5v14M4 12h16" />
  </Svg>
);

export const IconRooms = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="3" width="18" height="18" rx="2" />
    <path d="M3 12h9M12 3v18M12 15h9" />
  </Svg>
);

export const IconSofa = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 11V8a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v3" />
    <path d="M3 13a2 2 0 0 1 2-2 2 2 0 0 1 2 2v2h10v-2a2 2 0 0 1 4 0v5H3v-5z" />
    <path d="M5 18v2M19 18v2" />
  </Svg>
);

export const IconSparkles = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4" />
    <path d="m12 7 1.6 3.4L17 12l-3.4 1.6L12 17l-1.6-3.4L7 12l3.4-1.6L12 7z" />
  </Svg>
);

export const IconGear = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
  </Svg>
);

/** KAN-58 : le journal d'activité (une horloge). */
export const IconActivity = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </Svg>
);

/** KAN-52 : la cloche des notifications. */
export const IconBell = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
    <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
  </Svg>
);

export const IconSliders = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" />
    <circle cx="16" cy="6" r="2" />
    <circle cx="10" cy="12" r="2" />
    <circle cx="18" cy="18" r="2" />
  </Svg>
);

export const IconAlert = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3 2.5 20h19L12 3z" />
    <path d="M12 9v5M12 17.5v.5" />
  </Svg>
);

export const IconUndo = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 14 4 9l5-5" />
    <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
  </Svg>
);

export const IconRedo = (p: IconProps) => (
  <Svg {...p}>
    <path d="m15 14 5-5-5-5" />
    <path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13" />
  </Svg>
);

export const IconTrash = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 6h18" />
    <path d="M8 6V4h8v2" />
    <path d="M19 6l-1 14H6L5 6" />
    <path d="M10 11v6M14 11v6" />
  </Svg>
);

export const IconFit = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 3H5a2 2 0 0 0-2 2v3" />
    <path d="M16 3h3a2 2 0 0 1 2 2v3" />
    <path d="M8 21H5a2 2 0 0 1-2-2v-3" />
    <path d="M16 21h3a2 2 0 0 0 2-2v-3" />
  </Svg>
);

export const IconMinus = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 12h14" />
  </Svg>
);

export const IconPlus = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 12h14M12 5v14" />
  </Svg>
);

export const IconCube = (p: IconProps) => (
  <Svg {...p}>
    <path d="m21 16-9 5-9-5V8l9-5 9 5v8z" />
    <path d="m3.3 8.5 8.7 4.8 8.7-4.8" />
    <path d="M12 13.3V21" />
  </Svg>
);

export const IconDownload = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3v12" />
    <path d="m7 10 5 5 5-5" />
    <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
  </Svg>
);

export const IconShare = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="18" cy="5" r="2.5" />
    <circle cx="6" cy="12" r="2.5" />
    <circle cx="18" cy="19" r="2.5" />
    <path d="m8.2 10.8 7.6-4.6M8.2 13.2l7.6 4.6" />
  </Svg>
);

export const IconArrowLeft = (p: IconProps) => (
  <Svg {...p}>
    <path d="M19 12H5" />
    <path d="m12 19-7-7 7-7" />
  </Svg>
);

export const IconUpload = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <circle cx="8.5" cy="9.5" r="1.5" />
    <path d="m21 15-4.5-4.5L9 18" />
    <path d="M16 19v-6M13.5 15.5 16 13l2.5 2.5" />
  </Svg>
);

export const IconClose = (p: IconProps) => (
  <Svg {...p}>
    <path d="M18 6 6 18M6 6l12 12" />
  </Svg>
);

export const IconPanel = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M15 4v16" />
  </Svg>
);

export const IconPlan = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 4h16v16H4z" />
    <path d="M4 10h7v10M11 10h9" />
  </Svg>
);

export const IconPlay = (p: IconProps) => (
  <Svg {...p}>
    <path d="m7 5 12 7-12 7V5z" />
  </Svg>
);

/* ---- Barres compactes (étape b) : vues, coupes, affichage, outils ajoutés ---- */

export const IconChat = (p: IconProps) => (
  <Svg {...p}>
    <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1.2-4.4A8 8 0 1 1 21 12z" />
    <path d="M8 12h.01M12 12h.01M16 12h.01" />
  </Svg>
);

export const IconRuler = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 12h18" />
    <path d="M3 8v8M21 8v8" />
    <path d="M6 10l-3 2 3 2M18 10l3 2-3 2" />
  </Svg>
);

export const IconRoom = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 4h16v16H4z" />
    <path d="M4 14h7v6" />
    <circle cx="16" cy="9" r="1.5" fill="currentColor" />
  </Svg>
);

export const IconStairs = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 20h4v-4h4v-4h4V8h4V4" />
    <path d="M3 20v-4" />
  </Svg>
);

/** Vue de dessus : le plan vu d'en haut, le nord marqué. */
export const IconViewTop = (p: IconProps) => (
  <Svg {...p}>
    <rect x="5" y="5" width="14" height="14" rx="1" />
    <path d="M5 12h14M12 5v14" strokeDasharray="2 2" />
    <path d="M12 2v2" />
  </Svg>
);

/** Vue de face : le pignon, toit en pointe et porte au centre. */
export const IconViewFront = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 20V9l8-5 8 5v11z" />
    <path d="M4 20h16" />
    <rect x="10" y="13" width="4" height="7" />
  </Svg>
);

/** Vue de côté : le long pan, toit en trapèze et deux fenêtres (distincte du pignon). */
export const IconViewSide = (p: IconProps) => (
  <Svg {...p}>
    <path d="M2 20h20" />
    <path d="M4 20v-9h16v9" />
    <path d="M4 11l3-5h10l3 5" />
    <path d="M7.5 14h3v3h-3zM13.5 14h3v3h-3z" />
  </Svg>
);

export const IconViewIso = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" />
    <path d="M12 12l8-4.5M12 12L4 7.5M12 12v9" />
  </Svg>
);

export const IconSectionH = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" strokeOpacity={0.45} />
    <path d="M2 12.5h20" strokeWidth={2.25} />
    <path d="M5 12.5l2-1.5M19 12.5l-2-1.5" />
  </Svg>
);

export const IconSectionV = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" strokeOpacity={0.45} />
    <path d="M12 1.5v21" strokeWidth={2.25} />
    <path d="M12 5l1.5 2M12 19l1.5-2" />
  </Svg>
);

export const IconSectionFlip = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 12h18" strokeWidth={2.25} />
    <path d="M8 8a5 5 0 0 1 8 0" />
    <path d="M16 8l.5-3M16 8l-3-.5" />
    <path d="M16 16a5 5 0 0 1-8 0" />
    <path d="M8 16l-.5 3M8 16l3 .5" />
  </Svg>
);

export const IconRoofOff = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 11l9-7 9 7" strokeDasharray="2.5 2" />
    <path d="M5 11v9h14v-9" />
    <path d="M3 3l18 18" />
  </Svg>
);

export const IconXray = (p: IconProps) => (
  <Svg {...p}>
    <rect x="4" y="6" width="16" height="12" rx="1" strokeDasharray="2 2" />
    <path d="M8 12h8M10 9l-2 3 2 3M14 9l2 3-2 3" />
  </Svg>
);

export const IconDollhouse = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 20V9l8-5v16" />
    <path d="M12 4l8 5v11" />
    <path d="M4 20h16" />
    <path d="M12 12h8" strokeOpacity={0.45} />
  </Svg>
);

export const IconCeilings = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 9h18" />
    <path d="M5 9v11h14V9" />
    <path d="M8 6h8" strokeOpacity={0.45} />
    <path d="M6 4h12" strokeOpacity={0.3} />
  </Svg>
);

export const IconMaterials = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 4a8 8 0 0 0 0 16z" fill="currentColor" fillOpacity={0.25} />
    <path d="M6 8l12 8" strokeOpacity={0.45} />
  </Svg>
);

/** Galerie (US2) : favori (étoile, pleine si `filled`), renommer, comparer, agrandir. */
export const IconStar = ({
  filled,
  ...p
}: IconProps & { filled?: boolean }) => (
  <Svg {...p} fill={filled ? "currentColor" : "none"}>
    <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9L12 3.5z" />
  </Svg>
);

export const IconPencil = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4" />
  </Svg>
);

export const IconCompare = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="M12 3v18" />
  </Svg>
);

export const IconExpand = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
  </Svg>
);
