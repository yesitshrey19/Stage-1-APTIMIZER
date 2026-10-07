# ---- build ----------------------------------------------------------------
FROM node:20-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./

# One quirk in this project's dependency tree, verified:
#   1. react-day-picker's peer range conflicts with the installed date-fns, so a
#      strict `npm ci` refuses. --legacy-peer-deps is required.
RUN npm install --legacy-peer-deps --no-audit --no-fund

COPY . .

# Empty value on purpose: api.js builds `${REACT_APP_BACKEND_URL}/api`, so an empty
# string yields the relative path /api. nginx below proxies that to the API on the
# same origin, which removes CORS from the picture entirely.
ENV REACT_APP_BACKEND_URL=""
ENV CI=false
RUN npm run build

# ---- serve ----------------------------------------------------------------
FROM nginx:1.27-alpine

# The nginx image expands /etc/nginx/templates/*.template into conf.d on start, using
# only the variables that exist in the environment. Both of these are therefore runtime
# settings: pointing the container at a different API needs a redeploy, not a rebuild.
# PORT is the one Render injects (10000); 80 keeps docker compose working unchanged.
ENV PORT=80 \
    API_UPSTREAM=http://backend:8000

COPY --from=build /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT}/" >/dev/null || exit 1

EXPOSE 80
