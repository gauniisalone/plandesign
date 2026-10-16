# OpenPlan Studio, image « légère » : API + front construit, servis sur un seul port.
# Sans torch (profondeur, combles depuis la profondeur, image d'inspiration) ni Blender :
# ces fonctions répondent par un message clair. Pour elles, installer en local (make setup-neural).
# Rien ne sort du conteneur : pas de téléchargement de poids (HF_HUB_OFFLINE=1), pas de télémétrie.

# --- 1. Front (Vite) -----------------------------------------------------------------
FROM node:24-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

# --- 2. API (uv, Python 3.12) ----------------------------------------------------------
FROM python:3.12-slim AS api
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    OPENPLAN_DATA_DIR=/app/data \
    OPENPLAN_MODELS_DIR=/app/models/weights \
    OPENPLAN_WEB_DIR=/app/apps/web/dist
WORKDIR /app
# libgl/libglib : OpenCV headless n'en a pas besoin, mais reportlab/pillow veulent zlib/jpeg déjà présents.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project --extra api --extra vision --extra ai --extra monitoring
COPY openplan/ ./openplan/
COPY models/registry.yaml ./models/registry.yaml
COPY assets/catalog.yaml ./assets/catalog.yaml
COPY assets/furniture ./assets/furniture
COPY assets/materials ./assets/materials
COPY blender/scripts/build_scene.py ./blender/scripts/build_scene.py
RUN uv sync --frozen --no-dev --extra api --extra vision --extra ai --extra monitoring
COPY --from=web /web/dist ./apps/web/dist
RUN useradd --create-home --uid 1000 openplan && mkdir -p /app/data /app/models/weights \
    && chown -R openplan:openplan /app
USER openplan
EXPOSE 8000
# 127.0.0.1 par défaut : avec network_mode: host (docker-compose.yml) l'API n'est joignable que
# depuis la machine, sans jeton. Avec un port publié classique, passer --host 0.0.0.0 et un
# OPENPLAN_API_TOKEN (voir docker-compose.yml, variante commentée).
CMD ["uv", "run", "--no-sync", "openplan", "serve", "--web"]
