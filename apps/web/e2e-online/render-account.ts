/** KAN-89 : le compte autorisé aux rendus réalistes (créé avant le serveur par
 *  seed_render_account.py, uuid connu d'avance) et le jeton du poste simulé. Propres à la base
 *  jetable des e2e-online, sur la boucle locale : ce ne sont pas des secrets. */
export const RENDER_ACCOUNT = {
  id: "8a9e0c1e-5b7d-4c2a-9f3e-0e2b6c4d8a91",
  email: "rendus@example.org",
};
export const E2E_WORKER_TOKEN = "e2e-online-poste-de-rendu-jeton-de-test-0001";
