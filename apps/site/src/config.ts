// Liens vers l'application. Contrat de passage : voir README.md (« Contrat /nouveau »).
export const APP_URL = "https://app.plandesign.fr/";
export const NEW_PROJECT_URL = "https://app.plandesign.fr/nouveau";
export const REPO_URL = "https://github.com/gauniisalone/plandesign";
export const ISSUES_URL = "https://github.com/gauniisalone/plandesign/issues";

// Limites du formulaire (doivent rester alignées avec la route /nouveau de l'app).
export const DESCRIPTION_MAX = 1000;
export const SURFACE_MIN = 1;
export const SURFACE_MAX = 1000;

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
