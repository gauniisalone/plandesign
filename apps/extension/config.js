// KAN-102 : l'hôte de l'application, le seul que l'extension appelle (comme `host_permissions`
// du manifeste). `python3 apps/extension/build.py --origin …` le remplace dans le zip.
export const APP_ORIGIN = "https://app.plandesign.fr";
