// Aucun lien vers l'application ni vers le dépôt tant que les comptes ne sont pas ouverts
// (décision d'augus, 16 oct. 2026). Contrat futur : voir README.md.

// Limites du formulaire (à garder alignées avec l'app le jour où il sera branché).
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
