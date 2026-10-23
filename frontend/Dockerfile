# The web app: built once, then served as plain static files by nginx.
#
# The API's address is baked into the build (Vite reads VITE_* values when it builds), so build one image per environment:
#   docker build --build-arg VITE_API_BASE_URL=https://api.example.com --build-arg VITE_MAIL_VIEWER_URL=https://mail.google.com/mail/u/0 -t ar-frontend .

FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_MAIL_VIEWER_URL=http://localhost:8025
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL VITE_MAIL_VIEWER_URL=$VITE_MAIL_VIEWER_URL
RUN npm run build


# nginx that runs as a normal user (it listens on 8080, not 80)
FROM nginxinc/nginx-unprivileged:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
