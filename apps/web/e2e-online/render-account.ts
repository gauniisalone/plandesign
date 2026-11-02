/** KAN-89 : le compte autorisé aux rendus réalistes (créé avant le serveur par
 *  seed_render_account.py, uuid connu d'avance) et le jeton du poste simulé. Propres à la base
 *  jetable des e2e-online, sur la boucle locale : ce ne sont pas des secrets. */
export const RENDER_ACCOUNT = {
  id: "8a9e0c1e-5b7d-4c2a-9f3e-0e2b6c4d8a91",
  email: "rendus@example.org",
};
/** KAN-99 : un second compte autorisé, pour renders-compare.spec.ts seulement : 3 codes de
 *  connexion par adresse et par quart d'heure, et le premier compte en use déjà 3 dans les
 *  autres fichiers (realistic.spec.ts, renders-gallery.spec.ts). */
export const RENDER_ACCOUNT_KAN99 = {
  id: "3f1c7b2a-9d4e-4f6a-8b1c-5e7d9a0b2c43",
  email: "rendus-kan99@example.org",
};
export const E2E_WORKER_TOKEN = "e2e-online-poste-de-rendu-jeton-de-test-0001";
