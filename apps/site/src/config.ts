// Aucun lien vers l'application ni vers le dépôt tant que les comptes ne sont pas ouverts
// (décision d'augus, 16 oct. 2026). Contrat /nouveau v1 : voir README.md.

// B1d-b : la fenêtre « Se connecter » n'existe qu'avec l'option de build
// PUBLIC_LOGIN_WINDOW=1 (désactivée par défaut, donc en production tant qu'augus n'a pas
// décrété le MVP). Sans elle, la sortie est celle d'avant, octet pour octet.
export const LOGIN_WINDOW = import.meta.env.PUBLIC_LOGIN_WINDOW === "1";
export const APP_ORIGIN = (
  import.meta.env.PUBLIC_APP_ORIGIN || "https://app.plandesign.fr"
).replace(/\/+$/, "");

// Limites du formulaire. Avec la fenêtre, celles de l'app (consigne gardée : 300
// caractères ; surface des réponses guidées : 8 à 400 m²) ; sans elle, celles d'avant (le
// formulaire ne s'envoie pas).
export const DESCRIPTION_MAX = LOGIN_WINDOW ? 300 : 1000;
export const SURFACE_MIN = LOGIN_WINDOW ? 8 : 1;
export const SURFACE_MAX = LOGIN_WINDOW ? 400 : 1000;

// Types de pièce proposés : valeurs identiques à openplan/schemas/assistant.py
// (ROOM_TYPES), pour que l'app les reprenne sans table de correspondance.
export const ROOM_TYPES: { value: string; label: string }[] = [
  { value: "living_room", label: "Séjour" },
  { value: "bedroom", label: "Chambre" },
  { value: "kitchen", label: "Cuisine" },
  { value: "bathroom", label: "Salle de bain" },
  { value: "office", label: "Bureau" },
  { value: "attic", label: "Combles" },
  { value: "hallway", label: "Entrée, couloir" },
  { value: "other", label: "Autre" },
];
