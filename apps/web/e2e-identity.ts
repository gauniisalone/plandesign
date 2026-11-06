import { execSync } from "node:child_process";

/**
 * KAN-152 : quelle copie et quel commit ces e2e testent-ils ? Deux e2e peuvent tourner en même
 * temps sur deux copies (`pd-lock e2e`, une paire de ports par place) : le journal doit dire de
 * quelle copie vient le serveur, pour qu'aucun résultat ne soit attribué à la mauvaise.
 * - une ligne au chargement de la configuration (processus principal seulement) ;
 * - la même ligne en tête de la sortie du serveur d'API (`echo` avant `openplan serve`) ;
 * - les mêmes champs dans `metadata` (repris par les rapports json et html).
 */
function git(args: string): string {
  try {
    return execSync(`git ${args}`, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return "";
  }
}

export interface E2eIdentity {
  copie: string;
  sha: string;
  ports: string;
  /** La ligne du journal : « [e2e] copie … @ sha ; API 8100, web 5273 ». */
  line: string;
  /** Fragment de commande shell qui écrit `line` sur la sortie d'erreur (toujours relayée par
   * Playwright, même sans `stdout: "pipe"`) ; apostrophes retirées. */
  echo: string;
  metadata: Record<string, string>;
}

export function e2eIdentity(ports: Record<string, string>): E2eIdentity {
  const copie = git("rev-parse --show-toplevel") || process.cwd();
  const sha = git("rev-parse --short=12 HEAD") || "inconnu";
  const dirty = git("status --porcelain --untracked-files=no") ? " (+ modifications non commitées)" : "";
  const list = Object.entries(ports)
    .map(([name, port]) => `${name} ${port}`)
    .join(", ");
  const line = `[e2e] copie ${copie} @ ${sha}${dirty} ; ${list}`;
  if (!process.env.TEST_WORKER_INDEX) console.log(line);
  const safe = line.replaceAll("'", "");
  return {
    copie,
    sha: sha + (dirty ? "+" : ""),
    ports: list,
    line,
    echo: `echo '${safe}' >&2`,
    metadata: { copie, sha: sha + (dirty ? "+" : ""), ports: list },
  };
}
