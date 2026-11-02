# Matrice des droits de l'API (KAN-105, L0)

Fichier généré par `tests/accounts/access_matrix.py`, comparé par `test_kan105_access_matrix` :
ne pas l'éditer à la main. Colonnes : la règle déclarée (rôle minimal du membre, ou politique
globale), les maillons en plus (bail du plan B2d, place KAN-12, porte et débit du poste), les
décisions repérées dans le corps de la route, et les codes de refus qui y sont écrits.

232 routes ; par règle : admin 6, dans la route 2, extension 2, legacy 2, membre ≥ commenter 11, membre ≥ editor 88, membre ≥ owner 9, membre ≥ viewer 53, public 18, user 35, worker 6.

| méthode | chemin | applis | règle déclarée | maillons en plus | corps | refus |
|---|---|---|---|---|---|---|
| GET | /api/account/extension-tokens | comptes | user |  |  |  |
| POST | /api/account/extension-tokens | comptes | user |  |  | 409 |
| DELETE | /api/account/extension-tokens/{token_id} | comptes | user |  |  | 404 |
| GET | /api/admin/audience | comptes | admin |  |  |  |
| GET | /api/admin/health | comptes | admin |  |  |  |
| GET | /api/admin/usage | comptes | admin |  |  |  |
| POST | /api/admin/users/{user_id}/sessions/revoke | comptes | admin |  |  |  |
| GET | /api/ai | local+comptes | user |  |  |  |
| POST | /api/auth/email/start | comptes | public |  |  |  |
| POST | /api/auth/email/verify | comptes | public |  |  |  |
| POST | /api/auth/invitation/accept | comptes | user |  |  | 410 |
| GET | /api/auth/login | comptes | public |  |  |  |
| POST | /api/auth/login/code | comptes | public |  |  |  |
| POST | /api/auth/login/password | comptes | public |  |  |  |
| POST | /api/auth/login/verify | comptes | public |  |  |  |
| POST | /api/auth/logout | comptes | public |  |  |  |
| POST | /api/auth/logout-all | comptes | user |  |  |  |
| GET | /api/auth/me | comptes | user |  |  |  |
| PATCH | /api/auth/me | comptes | user |  |  |  |
| PUT | /api/auth/password | comptes | user |  |  | 403 |
| POST | /api/auth/password/forgot | comptes | public |  |  |  |
| POST | /api/auth/password/login | comptes | public |  |  | 401 429 |
| POST | /api/auth/password/reset | comptes | public |  |  |  |
| POST | /api/auth/reauth/start | comptes | user |  |  |  |
| POST | /api/auth/reauth/verify | comptes | user |  |  |  |
| GET | /api/auth/sessions | comptes | user |  |  |  |
| DELETE | /api/auth/sessions/{session_id} | comptes | user |  |  | 404 |
| POST | /api/auth/signup/start | comptes | public |  |  |  |
| POST | /api/auth/signup/verify | comptes | public |  |  |  |
| GET | /api/auth/usage | comptes | user |  | signed_in_user, place |  |
| GET | /api/blender | local+comptes | user |  |  |  |
| GET | /api/catalog | local+comptes | user |  |  |  |
| GET | /api/catalog/symbols | local+comptes | user |  |  |  |
| GET | /api/catalog/{asset_id}/model | local+comptes | user |  |  |  |
| GET | /api/catalog/{asset_id}/preview | local+comptes | user |  |  |  |
| POST | /api/contact | comptes | public |  |  |  |
| GET | /api/device | local+comptes | user |  |  |  |
| GET | /api/doctor | local+comptes | admin |  |  |  |
| POST | /api/extension/projects/{project_id}/images | comptes | extension | porte de l'extension ; membre ≥ editor ; place (413) |  | 404 429 |
| GET | /api/extension/targets | comptes | extension | porte de l'extension |  |  |
| GET | /api/health | local+comptes | public |  |  |  |
| HEAD | /api/health | local+comptes | public |  |  |  |
| GET | /api/home/settings | local+comptes | user |  |  |  |
| PUT | /api/home/settings | local+comptes | admin |  |  |  |
| POST | /api/instruction/preview | local+comptes | user |  |  |  |
| GET | /api/invitations | comptes | user |  |  |  |
| POST | /api/invitations/{invitation_id}/accept | comptes | user |  |  | 410 |
| POST | /api/invitations/{invitation_id}/decline | comptes | user |  |  | 410 |
| GET | /api/jobs/running | local+comptes | user |  |  |  |
| GET | /api/models | local+comptes | user |  |  |  |
| GET | /api/notifications | local+comptes | user |  |  |  |
| GET | /api/notifications/count | local+comptes | user |  |  |  |
| POST | /api/notifications/read | local+comptes | user |  |  | 404 |
| POST | /api/notifications/read-all | local+comptes | user |  |  |  |
| GET | /api/projects | local+comptes | user |  |  |  |
| POST | /api/projects | local+comptes | user |  |  |  |
| DELETE | /api/projects/{project_id} | local+comptes | membre ≥ owner |  |  |  |
| GET | /api/projects/{project_id} | local+comptes | membre ≥ viewer |  |  |  |
| PATCH | /api/projects/{project_id} | local+comptes | membre ≥ editor |  | require_role |  |
| GET | /api/projects/{project_id}/activity | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/assistant/thread | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/board | local+comptes | membre ≥ viewer |  |  |  |
| PUT | /api/projects/{project_id}/board | local+comptes | membre ≥ editor |  |  | 409 |
| GET | /api/projects/{project_id}/board/inputs/{target_id} | local+comptes | membre ≥ viewer |  |  | 404 |
| PATCH | /api/projects/{project_id}/board/items | local+comptes | membre ≥ editor |  |  |  |
| GET | /api/projects/{project_id}/board/links/{link_id} | local+comptes | membre ≥ viewer |  |  | 404 |
| GET | /api/projects/{project_id}/comments | local+comptes | membre ≥ viewer |  | role_of |  |
| POST | /api/projects/{project_id}/comments | local+comptes | membre ≥ commenter |  | role_of |  |
| DELETE | /api/projects/{project_id}/comments/{thread_id} | local+comptes | membre ≥ commenter |  | role_of | 403 |
| DELETE | /api/projects/{project_id}/comments/{thread_id}/messages/{message_id} | local+comptes | membre ≥ commenter |  | propriétaire | 403 404 |
| PATCH | /api/projects/{project_id}/comments/{thread_id}/messages/{message_id} | local+comptes | membre ≥ commenter |  | role_of, propriétaire | 403 404 |
| POST | /api/projects/{project_id}/comments/{thread_id}/reopen | local+comptes | membre ≥ commenter |  |  |  |
| POST | /api/projects/{project_id}/comments/{thread_id}/replies | local+comptes | membre ≥ commenter |  | role_of |  |
| POST | /api/projects/{project_id}/comments/{thread_id}/resolve | local+comptes | membre ≥ commenter |  |  |  |
| GET | /api/projects/{project_id}/contextual | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/cover | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/exports/archive | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/exports/dxf | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/exports/glb | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/exports/json | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/exports/pdf | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/exports/section-pdf | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/exports/svg | local+comptes | membre ≥ viewer |  |  |  |
| POST | /api/projects/{project_id}/images | local+comptes | membre ≥ editor | place (413) |  |  |
| DELETE | /api/projects/{project_id}/images/{image_id} | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/images/{image_id}/attic | local+comptes | membre ≥ editor | place (413) |  |  |
| POST | /api/projects/{project_id}/images/{image_id}/depth | local+comptes | membre ≥ editor | place (413) |  |  |
| GET | /api/projects/{project_id}/images/{image_id}/file | local+comptes | membre ≥ viewer |  |  |  |
| POST | /api/projects/{project_id}/images/{image_id}/inspire | local+comptes | membre ≥ editor | place (413) |  |  |
| POST | /api/projects/{project_id}/images/{image_id}/perceive | local+comptes | membre ≥ editor | place (413) |  | 409 |
| GET | /api/projects/{project_id}/images/{image_id}/quality | local+comptes | membre ≥ viewer |  |  |  |
| PUT | /api/projects/{project_id}/images/{image_id}/reference | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| DELETE | /api/projects/{project_id}/images/{image_id}/reference/{segment_id} | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| GET | /api/projects/{project_id}/invitations | comptes | membre ≥ owner |  |  |  |
| POST | /api/projects/{project_id}/invitations | comptes | membre ≥ owner |  |  |  |
| POST | /api/projects/{project_id}/invitations/email | comptes | membre ≥ owner |  |  | 429 |
| DELETE | /api/projects/{project_id}/invitations/{invitation_id} | comptes | membre ≥ owner |  |  | 404 |
| DELETE | /api/projects/{project_id}/jobs/{job_id} | local+comptes | membre ≥ editor |  |  |  |
| GET | /api/projects/{project_id}/jobs/{job_id} | local+comptes | membre ≥ viewer |  |  |  |
| DELETE | /api/projects/{project_id}/journal | local+comptes | membre ≥ owner |  |  |  |
| GET | /api/projects/{project_id}/journal | local+comptes | membre ≥ viewer |  |  |  |
| POST | /api/projects/{project_id}/journal/ui | local+comptes | membre ≥ viewer |  |  |  |
| POST | /api/projects/{project_id}/levels | local+comptes | membre ≥ editor |  |  |  |
| DELETE | /api/projects/{project_id}/levels/{level_id} | local+comptes | membre ≥ editor |  |  | 404 409 |
| POST | /api/projects/{project_id}/levels/{level_id}/area-guard | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/area-guard/undo | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/assistant/act | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/assistant/preview | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/assistant/say | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/attic | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/attic-fusion | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/autopilot | local+comptes | membre ≥ editor | place (413) |  |  |
| GET | /api/projects/{project_id}/levels/{level_id}/bake | local+comptes | membre ≥ viewer |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/blender | local+comptes | membre ≥ editor | place (413) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/brief | local+comptes | membre ≥ editor |  |  | 409 |
| POST | /api/projects/{project_id}/levels/{level_id}/brief/preview | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/brief/zones | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/calibrate | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| GET | /api/projects/{project_id}/levels/{level_id}/cameras/architect | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/levels/{level_id}/cameras/section | local+comptes | membre ≥ viewer |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/cameras/{camera_id}/render | local+comptes | membre ≥ editor | place (413) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/contextual | local+comptes | membre ≥ editor | place (413) |  | 409 |
| POST | /api/projects/{project_id}/levels/{level_id}/contextual/prompt | local+comptes | membre ≥ editor |  |  | 409 |
| GET | /api/projects/{project_id}/levels/{level_id}/derived | local+comptes | membre ≥ viewer |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/detect | local+comptes | membre ≥ editor | place (413) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/detect-depth | local+comptes | membre ≥ editor | place (413) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/elements/{element_id}/check | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/furniture/check | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/furniture/{element_id}/check | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/images/{image_id}/autoplan | local+comptes | membre ≥ editor | place (413) |  |  |
| PUT | /api/projects/{project_id}/levels/{level_id}/images/{image_id}/depth | local+comptes | membre ≥ editor | bail du plan (409) ; place (413) |  | 404 |
| POST | /api/projects/{project_id}/levels/{level_id}/images/{image_id}/openings | local+comptes | membre ≥ editor | place (413) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/images/{image_id}/reestimate | local+comptes | membre ≥ editor | bail du plan (409) |  | 404 |
| POST | /api/projects/{project_id}/levels/{level_id}/import-plan | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/import-plan/preview | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/import-plan/preview-image | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/layouts | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/layouts/apply | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/layouts/critique | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/layouts/critique/preview | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/layouts/render | local+comptes | membre ≥ editor | place (413) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/layouts/revise | local+comptes | membre ≥ editor |  |  |  |
| DELETE | /api/projects/{project_id}/levels/{level_id}/lease | comptes | membre ≥ editor |  | signed_in_user |  |
| POST | /api/projects/{project_id}/levels/{level_id}/lease | comptes | membre ≥ editor |  | signed_in_user |  |
| POST | /api/projects/{project_id}/levels/{level_id}/lease/request | comptes | membre ≥ editor |  | signed_in_user |  |
| POST | /api/projects/{project_id}/levels/{level_id}/ops | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/photo-fusion | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/programme | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/realistic | comptes | membre ≥ editor |  | allowed_user |  |
| POST | /api/projects/{project_id}/levels/{level_id}/realistic/from-photo | comptes | membre ≥ editor |  | allowed_user | 404 409 |
| POST | /api/projects/{project_id}/levels/{level_id}/recalibrate | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/recalibrate/dimensions | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/recalibrate/dimensions/preview | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/render | local+comptes | membre ≥ editor | place (413) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/render/all | local+comptes | membre ≥ editor | place (413) |  |  |
| GET | /api/projects/{project_id}/levels/{level_id}/render/estimate | local+comptes | membre ≥ viewer |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/render/views | local+comptes | membre ≥ editor | place (413) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/rooms/{room_id}/instruction | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/starter | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/views/auto | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/views/{camera_id}/adopt | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/levels/{level_id}/views/{camera_id}/rotate | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/measurements | local+comptes | membre ≥ editor |  |  |  |
| DELETE | /api/projects/{project_id}/measurements/{measurement_id} | local+comptes | membre ≥ editor |  |  |  |
| GET | /api/projects/{project_id}/media | local+comptes | membre ≥ viewer |  |  |  |
| POST | /api/projects/{project_id}/media | local+comptes | membre ≥ editor | place (413) |  | 413 |
| POST | /api/projects/{project_id}/media/link | local+comptes | membre ≥ editor |  |  | 429 |
| DELETE | /api/projects/{project_id}/media/{media_id} | local+comptes | membre ≥ editor |  |  | 404 |
| GET | /api/projects/{project_id}/media/{media_id}/file | local+comptes | membre ≥ viewer |  |  | 404 |
| GET | /api/projects/{project_id}/media/{media_id}/thumb | local+comptes | membre ≥ viewer |  |  | 404 |
| GET | /api/projects/{project_id}/members | comptes | membre ≥ owner |  |  |  |
| DELETE | /api/projects/{project_id}/members/{user_id} | comptes | membre ≥ owner |  |  | 404 409 |
| PATCH | /api/projects/{project_id}/members/{user_id} | comptes | membre ≥ owner |  |  | 404 409 |
| GET | /api/projects/{project_id}/metrics | local+comptes | membre ≥ viewer |  |  |  |
| POST | /api/projects/{project_id}/name/auto | local+comptes | membre ≥ editor |  |  |  |
| GET | /api/projects/{project_id}/network-log | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/reactions | local+comptes | membre ≥ viewer |  |  |  |
| DELETE | /api/projects/{project_id}/reactions/{item_id}/{key} | local+comptes | membre ≥ commenter |  |  |  |
| PUT | /api/projects/{project_id}/reactions/{item_id}/{key} | local+comptes | membre ≥ commenter |  |  |  |
| GET | /api/projects/{project_id}/realistic | comptes | membre ≥ viewer |  | member_user |  |
| DELETE | /api/projects/{project_id}/realistic/cover | comptes | membre ≥ editor |  | allowed_user |  |
| PUT | /api/projects/{project_id}/realistic/{request_id}/cover | comptes | membre ≥ editor |  | allowed_user, propriétaire |  |
| DELETE | /api/projects/{project_id}/realistic/{request_id}/favorite | comptes | membre ≥ editor |  | member_user | 404 |
| PUT | /api/projects/{project_id}/realistic/{request_id}/favorite | comptes | membre ≥ editor |  | member_user |  |
| GET | /api/projects/{project_id}/realistic/{request_id}/image | comptes | membre ≥ viewer |  | member_user | 404 |
| DELETE | /api/projects/{project_id}/realistic/{request_id}/reactions/{key} | comptes | membre ≥ commenter |  | member_user |  |
| PUT | /api/projects/{project_id}/realistic/{request_id}/reactions/{key} | comptes | membre ≥ commenter |  | member_user |  |
| POST | /api/projects/{project_id}/realistic/{request_id}/share-links | comptes | membre ≥ editor |  | allowed_user, propriétaire |  |
| POST | /api/projects/{project_id}/realistic/{request_id}/variant | comptes | membre ≥ editor |  | allowed_user, propriétaire |  |
| POST | /api/projects/{project_id}/redo | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| GET | /api/projects/{project_id}/renders | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/renders/favorites.zip | local+comptes | membre ≥ viewer |  |  |  |
| DELETE | /api/projects/{project_id}/renders/{run_id} | local+comptes | membre ≥ editor |  |  |  |
| PATCH | /api/projects/{project_id}/renders/{run_id} | local+comptes | membre ≥ editor |  |  |  |
| POST | /api/projects/{project_id}/renders/{run_id}/catalogue | local+comptes | membre ≥ editor | place (413) |  |  |
| POST | /api/projects/{project_id}/renders/{run_id}/finish | local+comptes | membre ≥ editor | place (413) |  |  |
| GET | /api/projects/{project_id}/renders/{run_id}/outline.png | local+comptes | membre ≥ viewer |  |  | 409 |
| GET | /api/projects/{project_id}/renders/{run_id}/pick | local+comptes | membre ≥ viewer |  |  | 409 |
| POST | /api/projects/{project_id}/renders/{run_id}/restore | local+comptes | membre ≥ editor |  |  |  |
| GET | /api/projects/{project_id}/role | local+comptes | membre ≥ viewer |  | role_of |  |
| GET | /api/projects/{project_id}/runs/{run_id}/attic/{name} | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/runs/{run_id}/blender/{name} | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/runs/{run_id}/contextual/{name} | local+comptes | membre ≥ viewer |  |  | 404 |
| GET | /api/projects/{project_id}/runs/{run_id}/depth/{name} | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/runs/{run_id}/detection/{name} | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/runs/{run_id}/inspiration/{name} | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/runs/{run_id}/intention/{name} | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/runs/{run_id}/layout/{name} | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/runs/{run_id}/openings/{name} | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/runs/{run_id}/perception/{name} | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/share-links | comptes | membre ≥ editor |  |  |  |
| DELETE | /api/projects/{project_id}/share-links/{link_id} | comptes | membre ≥ editor |  |  | 404 |
| GET | /api/projects/{project_id}/thumbnail | local+comptes | membre ≥ viewer |  |  |  |
| POST | /api/projects/{project_id}/undo | local+comptes | membre ≥ editor | bail du plan (409) |  |  |
| GET | /api/projects/{project_id}/validation | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/projects/{project_id}/view | local+comptes | membre ≥ viewer |  |  |  |
| PUT | /api/projects/{project_id}/view | local+comptes | membre ≥ viewer |  |  |  |
| GET | /api/realistic/status | comptes | user |  | signed_in_user |  |
| DELETE | /api/session | local | legacy |  |  |  |
| POST | /api/session | local | legacy |  |  |  |
| GET | /api/share/{token} | comptes | public |  |  |  |
| GET | /api/share/{token}/image | comptes | public |  |  |  |
| GET | /api/test/mail/last-code | comptes | public |  |  | 404 |
| GET | /api/web-models/depth | local+comptes | user |  |  | 404 |
| POST | /api/worker/claim | comptes | worker | porte du poste ; débit 1 / 5 s (429) |  |  |
| POST | /api/worker/jobs/{request_id}/beat | comptes | worker | porte du poste |  | 404 |
| POST | /api/worker/jobs/{request_id}/fail | comptes | worker | porte du poste |  | 404 |
| GET | /api/worker/jobs/{request_id}/files/{name} | comptes | worker | porte du poste |  | 404 410 413 |
| POST | /api/worker/jobs/{request_id}/result | comptes | worker | porte du poste |  | 404 410 413 |
| GET | /api/worker/jobs/{request_id}/style/{index} | comptes | worker | porte du poste |  | 404 410 |
| WS | /ws/me | comptes | dans la route |  |  |  |
| WS | /ws/projects/{project_id} | comptes | dans la route |  |  |  |
