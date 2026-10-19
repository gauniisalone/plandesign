// B1d-b : avec l'option de build PUBLIC_LOGIN_WINDOW=1, copie login/login.js et
// login/login.css dans dist/ (après `astro build`). Sans l'option, rien : dist/ reste celui
// d'avant, octet pour octet (aucun fichier de login/ n'est dans public/).
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";

const CONTACT_URL = "https://plandesign.fr/contact/";

if (process.env.PUBLIC_LOGIN_WINDOW === "1") {
  for (const name of ["login.js", "login.css", "contact.js"]) {
    copyFileSync(new URL(`../login/${name}`, import.meta.url), new URL(`../dist/${name}`, import.meta.url));
  }
  // B2b : la page /contact/ n'existe qu'avec l'option ; son entrée du sitemap aussi.
  const sitemap = new URL("../dist/sitemap.xml", import.meta.url);
  const xml = readFileSync(sitemap, "utf8");
  if (!xml.includes(CONTACT_URL)) {
    writeFileSync(sitemap, xml.replace("</urlset>", `  <url>\n    <loc>${CONTACT_URL}</loc>\n  </url>\n</urlset>`));
  }
  console.log("login-assets : fenêtre « Se connecter » et contact copiés (login.js, login.css, contact.js), /contact/ au sitemap");
}
