// KAN-102 : la fenêtre ouverte par le clic droit : choisir le projet et le node Moodboard, puis
// envoyer l'adresse de l'image au serveur, qui la récupère lui-même (garde anti-SSRF).
import { APP_ORIGIN } from "./config.js";
import {
  LAST_CHOICE_KEY,
  TOKEN_KEY,
  apiRequest,
  errorMessage,
  imageBody,
  pickDefault,
  readChoice,
} from "./lib.js";

const $ = (id) => document.getElementById(id);
const status = (text, kind = "") => {
  $("status").textContent = text;
  $("status").className = kind;
};

async function call(token, path, body) {
  const { url, init } = apiRequest(APP_ORIGIN, token, path, body);
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

function fill(select, entries) {
  select.replaceChildren(
    ...entries.map(([value, label, disabled]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.disabled = !!disabled;
      return option;
    }),
  );
}

async function main() {
  $("settings").addEventListener("click", () => chrome.runtime.openOptionsPage());
  const choice = readChoice(location.search);
  if (!choice.image) {
    status(
      "Cette image n'a pas d'adresse web (image intégrée à la page) : elle ne peut pas être envoyée. Ouvrez-la dans un onglet, puis réessayez.",
      "error",
    );
    return;
  }
  $("preview").src = choice.image;
  $("preview").hidden = false;
  $("source").textContent = choice.page ? `Depuis ${new URL(choice.page).host}` : "";
  const stored = await chrome.storage.local.get([TOKEN_KEY, LAST_CHOICE_KEY]);
  const token = stored[TOKEN_KEY];
  if (!token) {
    status(
      "Collez d'abord le jeton de l'extension (créé dans PlanDesign, Réglages › Compte).",
      "error",
    );
    $("settings").hidden = false;
    return;
  }
  status("Lecture de vos projets…");
  let targets;
  try {
    targets = await call(token, "/targets");
  } catch (e) {
    status(e.message, "error");
    $("settings").hidden = false;
    return;
  }
  const projects = targets.projects;
  const chosen = pickDefault(projects, stored[LAST_CHOICE_KEY]);
  if (!chosen) {
    status(
      "Aucun de vos projets n'a de Moodboard où vous êtes éditeur : ajoutez un node Moodboard dans PlanDesign.",
      "error",
    );
    return;
  }
  const showNodes = (projectId) => {
    const project = projects.find((p) => p.id === projectId);
    fill(
      $("node"),
      (project?.moodboards ?? []).map((m) => [m.id, m.title]),
    );
  };
  fill(
    $("project"),
    projects.map((p) => [
      p.id,
      p.moodboards.length ? p.name : `${p.name} (sans Moodboard)`,
      !p.moodboards.length,
    ]),
  );
  $("project").value = chosen.projectId;
  showNodes(chosen.projectId);
  $("node").value = chosen.nodeId;
  $("project").addEventListener("change", () => showNodes($("project").value));
  $("form").hidden = false;
  status("");
  $("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const projectId = $("project").value;
    const nodeId = $("node").value;
    $("add").disabled = true;
    status("Envoi : PlanDesign récupère l'image…");
    try {
      const added = await call(
        token,
        `/projects/${encodeURIComponent(projectId)}/images`,
        imageBody(nodeId, choice.image, choice.page),
      );
      await chrome.storage.local.set({ [LAST_CHOICE_KEY]: { projectId, nodeId } });
      status(`« ${added.name} » est dans le Moodboard.`, "ok");
      setTimeout(() => window.close(), 1500);
    } catch (e) {
      status(e.message, "error");
      $("add").disabled = false;
    }
  });
}

void main();
