// KAN-102 : le menu « Ajouter à PlanDesign » sur les images, et rien d'autre. Aucune page
// n'est lue : le navigateur donne l'adresse de l'image et de sa page au clic, la fenêtre de
// choix (choose.html) fait le reste. Script classique (sans import) : service worker dans
// Chrome, script d'arrière-plan dans Firefox.

const MENU_ID = "plandesign-add-image";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_ID,
    title: "Ajouter à PlanDesign",
    contexts: ["image"],
  });
});

chrome.contextMenus.onClicked.addListener((info) => {
  if (info.menuItemId !== MENU_ID) return;
  const query = new URLSearchParams({
    image: info.srcUrl ?? "",
    page: info.pageUrl ?? "",
  });
  void chrome.windows.create({
    url: `${chrome.runtime.getURL("choose.html")}?${query.toString()}`,
    type: "popup",
    width: 440,
    height: 600,
  });
});
