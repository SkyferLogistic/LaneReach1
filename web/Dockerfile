# LaneReach console: build the static app, serve it with nginx, proxy the API.
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build
# Precompressed copies for nginx's gzip_static (best compression, no work per request).
RUN find dist -type f \( -name '*.js' -o -name '*.css' -o -name '*.svg' -o -name '*.json' -o -name '*.html' \) -size +1k -exec gzip -9 -k {} +

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz || exit 1
