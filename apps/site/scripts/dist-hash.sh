#!/usr/bin/env bash
# KAN-109 : empreinte du site construit (dist/), pour le garde des passages. Elle remplace la
# comparaison avec un ancien build de référence : le site public change exprès (page de
# confidentialité, mesure d'audience), et l'empreinte attendue est écrite à côté
# (expected-dist.sha256), relue à chaque passage. Toute modification voulue du site met ce
# fichier à jour dans le même commit ; une modification non voulue fait échouer le passage.
#
# Usage : scripts/dist-hash.sh            → affiche l'empreinte de dist/
#         scripts/dist-hash.sh --check    → compare avec expected-dist.sha256 (code 1 sinon)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d dist ] || { echo "dist/ absent : construire le site d'abord (npm run build)" >&2; exit 2; }
hash=$(cd dist && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
if [ "${1:-}" = "--check" ]; then
  want=$(tr -d '[:space:]' < expected-dist.sha256)
  if [ "$hash" = "$want" ]; then echo "site : empreinte attendue ($hash)"; exit 0; fi
  echo "site : empreinte $hash ≠ attendue $want (changement du site non voulu ?)" >&2
  exit 1
fi
echo "$hash"
