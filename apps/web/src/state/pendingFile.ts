/** Fichier déposé sur l'accueil à remettre à l'atelier du projet qui vient d'être créé
 *  (photo ou plan) : l'atelier l'importe et construit le plan. Vit le temps d'une
 *  navigation, en mémoire seulement. */
let pending: File | null = null;

export function setPendingFile(file: File): void {
  pending = file;
}

export function takePendingFile(): File | null {
  const f = pending;
  pending = null;
  return f;
}
