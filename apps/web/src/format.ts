/** Nombres affichés (KAN-71) : virgule décimale partout où un texte est lu par une personne.
 *  Même règle que le serveur (`openplan/schemas/numbers.py`, mêmes noms, mêmes cas de test) :
 *  arrondi au nombre de décimales demandé, virgule, unité après une espace (« 1,20 m »,
 *  « 12,5 m² », « 50 cm », « 12,5 % »), « -0 » écrit « 0 ». Les formats techniques (JSON,
 *  attributs SVG, noms de fichiers, valeurs des champs de saisie envoyées au serveur) n'y
 *  passent pas. Aucun import : lu tel quel par `node --test`. */

export function decimal(value: number, digits = 2, signed = false): string {
  if (!Number.isFinite(value)) return "—";
  const text = Math.abs(value).toFixed(digits);
  const negative = value < 0 && /[1-9]/.test(text);
  const sign = negative ? "-" : signed ? "+" : "";
  return sign + text.replace(".", ",");
}

/** « 1,20 m ». */
export function metres(valueM: number, digits = 2, signed = false): string {
  return `${decimal(valueM, digits, signed)} m`;
}

/** Une longueur en mètres, écrite en centimètres : « 50 cm », « 2,5 cm ». */
export function centimetres(
  valueM: number,
  digits = 0,
  signed = false,
): string {
  return `${decimal(valueM * 100, digits, signed)} cm`;
}

/** « 12,5 m² ». */
export function area(valueM2: number, digits = 1): string {
  return `${decimal(valueM2, digits)} m²`;
}

/** Une fraction (0,125) écrite en pourcentage : « 12,5 % ». */
export function percent(fraction: number, digits = 0, signed = false): string {
  return `${decimal(fraction * 100, digits, signed)} %`;
}

/** L'unité d'affichage du projet : « 1,20 m », « 120 cm », « 1200 mm ». */
export function length(valueM: number, units: string = "m"): string {
  if (units === "cm") return centimetres(valueM);
  if (units === "mm") return `${decimal(valueM * 1000, 0)} mm`;
  return metres(valueM);
}

/** Un nombre tapé ou gardé tel quel (« 35.5 » → « 35,5 »), sans arrondi : pour réafficher
 *  une saisie. */
export function typed(value: number | string): string {
  return String(value).replace(".", ",");
}
