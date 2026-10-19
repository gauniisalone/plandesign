# ============================================================
# OpenPlan Studio — commandes de développement
# ATTENTION : les recettes utilisent des TABULATIONS, pas des espaces.
# Pré-requis : uv (Python) et Node LTS (front). Voir docs/contributing/setup.md
# ============================================================

SHELL := /bin/bash
UV    ?= uv
# Extras du venv partagé (le serveur local et les vérifications) : `uv run` les installe
# s'ils manquent, sans retirer les autres (synchronisation non exacte). « comptes » y est
# pour que les tests des comptes tournent toujours ; le mode local ne l'importe jamais.
EXTRAS := --extra api --extra vision --extra ai --extra monitoring --extra comptes
RUN   := $(UV) run $(EXTRAS)
WEB   := apps/web
HAS_WEB := $(wildcard $(WEB)/package.json)

.DEFAULT_GOAL := help
.PHONY: help setup setup-neural start dev api test lint typecheck format build check clean lint-imports web-types e2e

## Afficher l'aide
help:
	@echo "Commandes disponibles :"
	@echo "  make setup      - uv sync (toutes les extras) + npm ci dans $(WEB)"
	@echo "  make start      - construit le front et sert tout sur http://localhost:8000/ (une commande)"
	@echo "  make api        - API locale avec rechargement (127.0.0.1:8000)"
	@echo "  make dev        - serveur de dev web (127.0.0.1:5173, proxy /api)"
	@echo "  make web-types  - régénère apps/web/src/api/types.gen.ts"
	@echo "  make test       - pytest avec couverture"
	@echo "  make lint       - ruff + import-linter + eslint"
	@echo "  make typecheck  - mypy --strict + tsc"
	@echo "  make format     - ruff format + ruff --fix"
	@echo "  make build      - wheel/sdist + build web"
	@echo "  make check      - lint + typecheck + test + build"
	@echo "  make clean      - artefacts de build et caches"

## Installation (sans torch : voir setup-neural)
setup:
	$(UV) sync $(EXTRAS)
ifneq ($(HAS_WEB),)
	cd $(WEB) && if [ -f package-lock.json ]; then npm ci; else npm install; fi
endif

## Modèles neuronaux locaux : torch + transformers (plusieurs Go), puis poids à la demande
setup-neural:
	$(UV) sync $(EXTRAS) --extra neural
	@echo "Poids : uv run openplan models list ; uv run openplan models pull <id>"

## Tout-en-un pour essayer : front construit puis servi par l'API sur un seul port (boucle
## locale, sans jeton). Fonctionne aussi depuis un shell de connexion non interactif
## (`wsl -d Debian -- bash -lc "... && make start"`) : uv et nvm sont mis sur le PATH ici.
start:
	@export PATH="$$HOME/.local/bin:$$PATH"; \
	if [ -s "$$HOME/.nvm/nvm.sh" ]; then . "$$HOME/.nvm/nvm.sh"; fi; \
	if [ ! -d $(WEB)/node_modules ]; then (cd $(WEB) && npm ci); fi; \
	(cd $(WEB) && npm run build) && $(RUN) openplan serve --web

## Développement
api:
	$(RUN) openplan serve --reload

dev:
	@echo "API : make api (127.0.0.1:8000) — Web : cd $(WEB) && npm run dev (127.0.0.1:5173)"
	cd $(WEB) && npm run dev

## Types TypeScript générés depuis les JSON Schema Pydantic
web-types:
	$(RUN) openplan schemas $(WEB)/schemas
	cd $(WEB) && npm run gen-types

## Tests
test:
	$(RUN) pytest

## Lint
lint-imports:
	$(RUN) lint-imports

lint: lint-imports
	$(RUN) ruff check .
	$(RUN) ruff format --check .
ifneq ($(HAS_WEB),)
	cd $(WEB) && npm run lint
endif

## Types
typecheck:
	$(RUN) mypy
ifneq ($(HAS_WEB),)
	cd $(WEB) && npm run typecheck
endif

## Formatage
format:
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

## Build : front, puis wheel/sdist avec le front et les ressources embarqués (openplan/_bundled)
build:
ifneq ($(HAS_WEB),)
	cd $(WEB) && npm run build
endif
	rm -rf openplan/_bundled
	mkdir -p openplan/_bundled/models openplan/_bundled/assets openplan/_bundled/blender/scripts
	cp models/registry.yaml openplan/_bundled/models/
	cp assets/catalog.yaml openplan/_bundled/assets/
	cp -r assets/furniture openplan/_bundled/assets/
	cp -r assets/materials openplan/_bundled/assets/
	cp blender/scripts/build_scene.py openplan/_bundled/blender/scripts/
	if [ -d $(WEB)/dist ]; then cp -r $(WEB)/dist openplan/_bundled/web; fi
	$(UV) build
	rm -rf openplan/_bundled

## Tests de bout en bout dans Chromium headless (Playwright ; lance API + Vite, données jetables)
## Prérequis une fois : cd apps/web && npx playwright install chromium (et install-deps sous Debian)
e2e:
	cd $(WEB) && rm -rf .e2e-data && npm run e2e && npm run e2e:accounts && npm run e2e:online && npm run e2e:site

## Pipeline complète (les e2e sont à part : plus lents, navigateur requis)
check: lint typecheck test build

## Nettoyage (artefacts uniquement, jamais les données utilisateur)
clean:
	rm -rf dist/ build/ *.egg-info/ .pytest_cache/ .ruff_cache/ .mypy_cache/ htmlcov/ .coverage openplan/_bundled/
	rm -rf $(WEB)/dist $(WEB)/node_modules/.vite $(WEB)/node_modules/.tmp
	find . -type d -name "__pycache__" -not -path "./.venv/*" -prune -exec rm -rf {} + 2>/dev/null || true
