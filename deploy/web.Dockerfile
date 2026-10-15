# Production image for the React app: build once, serve the static files with nginx.
# (The development image in frontend/Dockerfile runs the Vite dev server and is not used on the server.)
# Build context is the repository root (see docker-compose.prod.yml).
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json ./
RUN npm install
COPY frontend/ .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
