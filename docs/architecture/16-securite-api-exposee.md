# 16 · API exposée : jeton, cookie de lecture, modèle de menace

Complète l'incrément 4.6 (jetons nommés, journal des accès, en-têtes) pour app.plandesign.fr
(déploiement D2 de l'infra) : l'atelier doit rester utilisable hors de la boucle locale, où
chaque `/api` exige `Authorization: Bearer`. Décision PO du 15 octobre 2026.

## Le problème

Le client met le Bearer sur tous ses `fetch` (JSON, envois, `keepalive`), mais ≈ 45 points du
front servent une URL nue que le navigateur charge sans en-tête : `<img src>` (photos, rendus,
variantes, vignettes, plan SVG, médias, aperçus du catalogue, contours), `<a download>`
(exports SVG/PDF/DXF/GLB/archive/JSON, zip des favoris, artefacts), `window.open`, et le GLB du
viewer 3D. Hors boucle locale, tous recevaient 401.

## La solution : un cookie de lecture, GET seulement

- `POST /api/session` (avec le Bearer) pose `openplan_read` : **HttpOnly, Secure,
  SameSite=Lax, Path=/api, 12 h** ; le front l'appelle dès qu'il connaît le jeton (arrivée,
  invite 401) et le renouvelle toutes les 6 h. `DELETE /api/session` l'efface.
- Valeur : `nom:HMAC-SHA256(secret du serveur, nom:jeton)`. Jamais le jeton brut ; le secret
  (`<data>/read_cookie.secret`, 32 octets, 600) est créé une fois, hors dépôt. Changer ou
  retirer un jeton invalide le cookie (le HMAC ne correspond plus).
- L'intergiciel n'accepte le cookie que pour **GET et HEAD**, et jamais sur les routes GET qui
  écrivent (`COOKIE_DENIED` : la liste des projets, qui renomme les anciens noms de fichier,
  et la désignation dans un rendu, qui note le journal local). Toute mutation exige le Bearer.
- Un test parcourt toutes les routes GET/HEAD sous `/api`, cherche dans leur source les motifs
  d'écriture (`store.save`, `write_*`, `mkdir`, `history.commit`, `apply_op`, `jobs.submit`,
  `journal.record`, `unlink`, `rename`, `shutil`) et exige que chaque route trouvée soit dans
  `COOKIE_DENIED` ; un autre prouve qu'un `POST` avec le cookie seul rend 401.
- Repli : sur le tableau, photos et rendus passent par `fetch` + `blob` avec l'en-tête dès
  qu'un jeton est connu de l'onglet (`AuthedImg`), ce qui marche même si le navigateur bloque
  le cookie. Boucle locale : rien ne change (pas de jeton, pas de cookie, `<img>` ordinaire).
- Pas d'URL signée : elle fuirait dans l'historique, les journaux et les partages de lien.

## Modèle de menace (en dix lignes)

1. **Site tiers qui forge une requête (CSRF)** : le cookie ne vaut que pour GET/HEAD, et les
   GET qui écrivent le refusent ; une mutation sans Bearer rend 401. SameSite=Lax empêche de
   plus l'envoi du cookie sur les sous-requêtes venues d'un autre site.
2. **Script injecté (XSS)** : CSP stricte (`'self'` seulement), cookie HttpOnly (illisible
   par un script), jeton en `sessionStorage` de l'onglet seulement.
3. **Vol du cookie** : il ne contient pas le jeton ; il meurt en 12 h ou dès que le jeton
   change ; il ne permet que des lectures.
4. **Lien partagé par mégarde** : aucune URL ne porte de secret (ni jeton ni signature).
5. **Écoute du réseau** : Secure (HTTPS seulement, Caddy + Let's Encrypt, HSTS).
6. **Secret du serveur compromis** : il ne permet que de fabriquer un cookie pour un jeton
   que l'on connaît déjà ; le régénérer (supprimer le fichier) invalide tous les cookies.
7. **Journal des accès** : acteur `user:<nom>` identique via Bearer ou cookie, jamais la
   valeur de l'un ni de l'autre.
8. **Boucle locale** : inchangée, sans jeton ; la mention « tout reste sur cette machine »
   ne concerne pas un serveur exposé (voir note 14 § 9).
9. **Hors périmètre** : comptes et rôles par utilisateur (B1), limitation de débit (Caddy ou
   fail2ban sur 401 répétés, infra).
10. **À revoir** si une route GET devient écrivante : le test l'attrape et exige son ajout à
    `COOKIE_DENIED`.
