// B1d-b : avec l'option de build PUBLIC_LOGIN_WINDOW=1, copie login/login.js et
// login/login.css dans dist/ (après `astro build`). Sans l'option, rien : dist/ reste celui
// d'avant, octet pour octet (aucun fichier de login/ n'est dans public/).
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";

// B2b : /contact/ ; KAN-133 : /cgu/. Ces pages n'existent qu'avec l'option ; leurs entrées du
// sitemap aussi.
const OPTION_URLS = ["https://plandesign.fr/contact/", "https://plandesign.fr/cgu/"];

if (process.env.PUBLIC_LOGIN_WINDOW === "1") {
  for (const name of ["login.js", "login.css", "contact.js"]) {
    copyFileSync(new URL(`../login/${name}`, import.meta.url), new URL(`../dist/${name}`, import.meta.url));
  }
  const sitemap = new URL("../dist/sitemap.xml", import.meta.url);
  let xml = readFileSync(sitemap, "utf8");
  for (const url of OPTION_URLS) {
    if (!xml.includes(url)) xml = xml.replace("</urlset>", `  <url>\n    <loc>${url}</loc>\n  </url>\n</urlset>`);
  }
  writeFileSync(sitemap, xml);
  console.log("login-assets : fenêtre « Se connecter » et contact copiés (login.js, login.css, contact.js), /contact/ et /cgu/ au sitemap");
}
