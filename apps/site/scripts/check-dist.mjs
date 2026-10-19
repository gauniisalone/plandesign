// Vérifie la sortie statique : compatible CSP stricte, aucun appel vers des tiers.
// Lancé après `astro build`. Sort en erreur au premier problème.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, extname } from "node:path";

const DIST = new URL("../dist/", import.meta.url).pathname;
const ALLOWED_HOSTS = new Set([
  "plandesign.fr",
  "schema.org",
  "www.apache.org",
  "www.w3.org",
  "www.sitemaps.org",
]);
// B1d-b : option de build PUBLIC_LOGIN_WINDOW=1 (fenêtre « Se connecter »). Sans elle
// (production tant qu'augus n'a pas décrété le MVP) : aucune mention de l'app, aucun
// script, aucun fichier de la fenêtre. Avec : le seul script /login.js, la seule origine
// de l'app (PUBLIC_APP_ORIGIN, https://app.plandesign.fr par défaut).
const LOGIN = process.env.PUBLIC_LOGIN_WINDOW === "1";
const APP_HOST = new URL(process.env.PUBLIC_APP_ORIGIN || "https://app.plandesign.fr").hostname;
if (LOGIN) ALLOWED_HOSTS.add(APP_HOST);

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
      if (/type="application\/ld\+json"/.test(attrs)) continue;
      if (!/\ssrc=/.test(attrs)) problems.push(`${rel} : script en ligne exécutable`);
      else if (!LOGIN) problems.push(`${rel} : script sans l'option de la fenêtre de connexion`);
      else if (!/\ssrc="\/(login|contact)\.js"/.test(attrs)) {
        problems.push(`${rel} : script autre que /login.js ou /contact.js`);
      }
    }
    if (!LOGIN && /data-app-origin|<dialog\b/i.test(text)) {
      problems.push(`${rel} : fenêtre de connexion sans l'option de build`);
    }
    if (/\son[a-z]+\s*=/i.test(text)) problems.push(`${rel} : gestionnaire d'événement en ligne`);
  }
  // Décision d'augus (16 oct. 2026) : ni lien ni mention de l'app ou du dépôt tant que
  // les comptes ne sont pas ouverts.
  if (!LOGIN && /app\.plandesign/i.test(text)) problems.push(`${rel} : mention de app.plandesign.fr`);
  if (/github/i.test(text)) problems.push(`${rel} : mention de GitHub`);
  for (const m of text.matchAll(/https?:\/\/([a-z0-9.-]+)/gi)) {
    if (!ALLOWED_HOSTS.has(m[1].toLowerCase())) problems.push(`${rel} : hôte externe ${m[1]}`);
  }
}

walk(DIST);
for (const name of ["login.js", "login.css", "contact.js", "contact/index.html"]) {
  const there = existsSync(join(DIST, name));
  if (!LOGIN && there) problems.push(`${name} : présent sans l'option de build`);
  if (LOGIN && !there) problems.push(`${name} : absent avec l'option de build`);
}
// B2b : /contact/ au sitemap avec l'option seulement, comme la page elle-même.
const inSitemap = readFileSync(join(DIST, "sitemap.xml"), "utf8").includes("https://plandesign.fr/contact/");
if (!LOGIN && inSitemap) problems.push("sitemap.xml : /contact/ sans l'option de build");
if (LOGIN && !inSitemap) problems.push("sitemap.xml : /contact/ absent avec l'option de build");
if (problems.length) {
  console.error("check-dist : problèmes trouvés\n" + [...new Set(problems)].join("\n"));
  process.exit(1);
}
console.log(
  `check-dist : OK${LOGIN ? ` avec la fenêtre de connexion (${APP_HOST})` : ""} (${(total / 1024).toFixed(1)} Kio au total)`,
);
