// Vérifie la sortie statique : compatible CSP stricte, aucun appel vers des tiers.
// Lancé après `astro build`. Sort en erreur au premier problème.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, extname } from "node:path";

const DIST = new URL("../dist/", import.meta.url).pathname;
const ALLOWED_HOSTS = new Set([
  "plandesign.fr",
  "schema.org",
  "www.apache.org",
  "www.w3.org",
  "www.sitemaps.org",
]);

const problems = [];
let total = 0;

function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p);
    else {
      total += st.size;
      check(p);
    }
  }
}

function check(file) {
  const ext = extname(file);
  if (![".html", ".css", ".js", ".xml", ".txt", ".webmanifest", ".svg"].includes(ext)) return;
  const rel = file.slice(DIST.length);
  if (rel.startsWith("fonts/")) return; // textes de licence OFL (liens non chargés)
  const text = readFileSync(file, "utf8");
  if (ext === ".html") {
    if (/\sstyle\s*=/i.test(text)) problems.push(`${rel} : attribut style en ligne`);
    if (/<style[\s>]/i.test(text)) problems.push(`${rel} : balise <style> en ligne`);
    for (const m of text.matchAll(/<script\b([^>]*)>/gi)) {
      const attrs = m[1];
      if (!/type="application\/ld\+json"/.test(attrs) && !/\ssrc=/.test(attrs)) {
        problems.push(`${rel} : script en ligne exécutable`);
      }
    }
    if (/\son[a-z]+\s*=/i.test(text)) problems.push(`${rel} : gestionnaire d'événement en ligne`);
  }
  // Décision d'augus (16 oct. 2026) : ni lien ni mention de l'app ou du dépôt tant que
  // les comptes ne sont pas ouverts.
  if (/app\.plandesign/i.test(text)) problems.push(`${rel} : mention de app.plandesign.fr`);
  if (/github/i.test(text)) problems.push(`${rel} : mention de GitHub`);
  for (const m of text.matchAll(/https?:\/\/([a-z0-9.-]+)/gi)) {
    if (!ALLOWED_HOSTS.has(m[1].toLowerCase())) problems.push(`${rel} : hôte externe ${m[1]}`);
  }
}

walk(DIST);
if (problems.length) {
  console.error("check-dist : problèmes trouvés\n" + [...new Set(problems)].join("\n"));
  process.exit(1);
}
console.log(`check-dist : OK (${(total / 1024).toFixed(1)} Kio au total)`);
