FROM node:20-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# VITE_API_URL must be baked in at build time (Vite replaces import.meta.env.* statically).
# Pass it via docker-compose build args. Defaults to empty string (relative URLs).
ARG VITE_API_URL=""
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build

# Unprivileged nginx variant: runs as non-root uid 101 and ships a main
# nginx.conf whose default server listens on 8080 (see nginx.conf). It writes
# its pid and temp paths under /tmp, so it works with a read-only root fs when
# /tmp is an emptyDir. Pinned to the matching upstream nginx version.
FROM nginxinc/nginx-unprivileged:1.27-alpine

# The base image's default user is 101; COPY as root so the image build can
# write into /etc/nginx and /usr/share/nginx, then drop back to non-root.
USER root
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER 101

# Serves on 8080 because non-root nginx cannot bind privileged port 80.
EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
