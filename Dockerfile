# --- Stage 1: build the frontend -------------------------------------------
# Produces frontend/dist, which app/server.py serves as static files when
# present (same-origin as the API - see the StaticFiles mount at the bottom
# of app/server.py for why that matters: it avoids the SameSite cross-site
# cookie problem entirely, so this one image + one Render service is the
# whole deployment, no separate frontend host needed).
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: the app itself -------------------------------------------------
FROM python:3.12-slim AS runtime
WORKDIR /srv
ENV PYTHONPATH=/srv \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# psycopg[binary] and fastembed's onnxruntime ship self-contained manylinux
# wheels - no extra system packages (no libpq-dev, no build-essential)
# needed to install requirements.txt on this base image.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY data/ ./data/
COPY --from=frontend-build /frontend/dist/ ./frontend/dist/

EXPOSE 8787

# claude_agent_sdk spawns its bundled `claude` CLI as a subprocess per agent
# session - that binary comes from the platform-specific wheel `pip install`
# resolved above for linux/amd64, nothing extra to install for it here.
# Render (and most PaaS hosts) inject $PORT at runtime rather than using a
# fixed port - bind to it if set, falling back to 8787 for `docker run`.
CMD ["sh", "-c", "python -m uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-8787}"]
