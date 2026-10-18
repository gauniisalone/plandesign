// B1d-b : avec l'option de build PUBLIC_LOGIN_WINDOW=1, copie login/login.js et
// login/login.css dans dist/ (après `astro build`). Sans l'option, rien : dist/ reste celui
// d'avant, octet pour octet (aucun fichier de login/ n'est dans public/).
import { copyFileSync } from "node:fs";

if (process.env.PUBLIC_LOGIN_WINDOW === "1") {
  for (const name of ["login.js", "login.css", "contact.js"]) {
    copyFileSync(new URL(`../login/${name}`, import.meta.url), new URL(`../dist/${name}`, import.meta.url));
  }
  console.log("login-assets : fenêtre « Se connecter » et contact copiés (login.js, login.css, contact.js)");
}
