// KAN-102 : la logique du module navigateur « Ajouter à PlanDesign » (apps/extension/lib.js),
// lancée avec les autres tests unitaires du front (node --test, sans navigateur).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  apiRequest,
  cleanToken,
  errorMessage,
  imageBody,
  pickDefault,
  readChoice,
  webUrl,
} from "../../extension/lib.js";

const TOKEN = `pde_${"a1".repeat(16)}_${"Zz-_9".repeat(9)}`;

test("seules les adresses http(s) d'une image partent au serveur", () => {
  assert.equal(webUrl("https://i.pinimg.com/a.jpg"), "https://i.pinimg.com/a.jpg");
  assert.equal(webUrl("  http://exemple.fr/b.png "), "http://exemple.fr/b.png");
  for (const bad of [
    "data:image/png;base64,iVBOR",
    "blob:https://exemple.fr/1234",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "https://exemple.fr/a b.png",
    `https://exemple.fr/${"x".repeat(2000)}`,
    "",
    null,
  ])
    assert.equal(webUrl(bad), null, String(bad).slice(0, 40));
});

test("la fenêtre de choix lit l'image et sa page dans son adresse", () => {
  const query = new URLSearchParams({
    image: "https://i.pinimg.com/a.jpg",
    page: "https://www.pinterest.fr/pin/1/",
  });
  assert.deepEqual(readChoice(`?${query}`), {
    raw: "https://i.pinimg.com/a.jpg",
    image: "https://i.pinimg.com/a.jpg",
    page: "https://www.pinterest.fr/pin/1/",
  });
  const inline = readChoice("?image=data%3Aimage%2Fpng%3Bbase64%2CAAAA&page=chrome%3A%2F%2Fnewtab");
  assert.equal(inline.image, null);
  assert.equal(inline.page, null);
});

test("le jeton collé est nettoyé, et refusé s'il n'a pas sa forme", () => {
  assert.equal(cleanToken(`  ${TOKEN.slice(0, 20)}\n${TOKEN.slice(20)} `), TOKEN);
  assert.equal(cleanToken("pde_court"), null);
  assert.equal(cleanToken("pd_session=abc"), null);
  assert.equal(cleanToken(undefined), null);
});

test("la requête porte le jeton, jamais de cookie, et ne vise que l'API du module", () => {
  const get = apiRequest("https://app.plandesign.fr/", TOKEN, "/targets");
  assert.equal(get.url, "https://app.plandesign.fr/api/extension/targets");
  assert.equal(get.init.method, "GET");
  assert.equal(get.init.credentials, "omit");
  assert.equal(get.init.referrerPolicy, "no-referrer");
  assert.deepEqual(get.init.headers, { Authorization: `Bearer ${TOKEN}` });
  assert.equal("body" in get.init, false);
  const body = imageBody("node-1", "https://i.pinimg.com/a.jpg", null);
  const post = apiRequest("https://app.plandesign.fr", TOKEN, "/projects/p-1/images", body);
  assert.equal(post.url, "https://app.plandesign.fr/api/extension/projects/p-1/images");
  assert.equal(post.init.method, "POST");
  assert.equal(post.init.credentials, "omit");
  assert.deepEqual(JSON.parse(post.init.body as string), {
    node_id: "node-1",
    image_url: "https://i.pinimg.com/a.jpg",
    page_url: null,
  });
});

test("le dernier choix est repris s'il existe encore, sinon le premier Moodboard", () => {
  const projects = [
    { id: "p1", name: "Vide", moodboards: [] },
    { id: "p2", name: "Salon", moodboards: [{ id: "m1", title: "Moodboard" }] },
    {
      id: "p3",
      name: "Grenier",
      moodboards: [
        { id: "m2", title: "Moodboard" },
        { id: "m3", title: "Bois" },
      ],
    },
  ];
  assert.deepEqual(pickDefault(projects, { projectId: "p3", nodeId: "m3" }), {
    projectId: "p3",
    nodeId: "m3",
  });
  assert.deepEqual(pickDefault(projects, { projectId: "p3", nodeId: "parti" }), {
    projectId: "p2",
    nodeId: "m1",
  });
  assert.deepEqual(pickDefault(projects, null), { projectId: "p2", nodeId: "m1" });
  assert.equal(pickDefault([projects[0]], null), null);
});

test("chaque refus a son message", () => {
  assert.match(errorMessage(401), /Jeton refusé/);
  assert.match(errorMessage(403), /pas éditeur/);
  assert.match(errorMessage(404), /introuvable/);
  assert.match(errorMessage(429), /une minute/);
  assert.match(errorMessage(0), /ne répond pas/);
  assert.equal(errorMessage(413, "Image trop lourde : 10 Mo au plus."), "Image trop lourde : 10 Mo au plus.");
  assert.equal(errorMessage(502), "Erreur 502.");
});

test("permissions minimales, aucune page lue en tâche de fond", () => {
  const manifest = JSON.parse(
    readFileSync(new URL("../../extension/manifest.json", import.meta.url), "utf8"),
  );
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual([...manifest.permissions].sort(), ["activeTab", "contextMenus", "storage"]);
  assert.deepEqual(manifest.host_permissions, ["https://app.plandesign.fr/*"]);
  assert.equal("content_scripts" in manifest, false);
  assert.equal("optional_permissions" in manifest, false);
  const config = readFileSync(new URL("../../extension/config.js", import.meta.url), "utf8");
  assert.match(config, /APP_ORIGIN = "https:\/\/app\.plandesign\.fr"/);
  const background = readFileSync(
    new URL("../../extension/background.js", import.meta.url),
    "utf8",
  );
  assert.match(background, /contexts: \["image"\]/);
  assert.doesNotMatch(background, /fetch\(|executeScript|tabs\./);
});
