// KAN-102 : le jeton de l'extension, collé une fois, gardé dans le stockage LOCAL du
// navigateur (jamais synchronisé) ; vérifié auprès de l'application avant d'être gardé.
import { APP_ORIGIN } from "./config.js";
import { TOKEN_KEY, apiRequest, cleanToken, errorMessage } from "./lib.js";

const $ = (id) => document.getElementById(id);
const status = (text, kind = "") => {
  $("status").textContent = text;
  $("status").className = kind;
};

async function check(token) {
  const { url, init } = apiRequest(APP_ORIGIN, token, "/targets");
  let response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new Error(errorMessage(0));
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(response.status, data.detail));
  return data;
}

async function main() {
  $("origin").textContent = APP_ORIGIN;
  const stored = await chrome.storage.local.get(TOKEN_KEY);
  if (stored[TOKEN_KEY]) status("Un jeton est enregistré.", "ok");
  $("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const token = cleanToken($("token").value);
    if (!token) {
      status("Ce n'est pas un jeton de l'extension (il commence par « pde_ »).", "error");
      return;
    }
    status("Vérification…");
    try {
      const targets = await check(token);
      await chrome.storage.local.set({ [TOKEN_KEY]: token });
      $("token").value = "";
      const who = targets.name ? ` (${targets.name})` : "";
      status(`Jeton enregistré${who} : ${targets.projects.length} projet(s) où vous êtes éditeur.`, "ok");
    } catch (e) {
      status(e.message, "error");
    }
  });
  $("forget").addEventListener("click", async () => {
    await chrome.storage.local.remove(TOKEN_KEY);
    status("Jeton oublié par l'extension. Pensez à le révoquer dans PlanDesign.", "ok");
  });
}

void main();
