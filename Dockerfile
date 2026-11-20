# syntax=docker/dockerfile:1
# Builds the static site (site/) and serves it with nginx.
#   docker build -t scry .                                   image that serves the site on port 80
#   docker build --target site --output site .               write the static folder to ./site
FROM python:3.12-slim AS build
WORKDIR /src
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scry.py page.html worker.mjs ./
# Downloads Pyodide and sqlite-wasm from the npm registry and checks the pinned hashes.
RUN python scry.py --site /site

FROM scratch AS site
COPY --from=build /site /

FROM nginx:1.27-alpine AS serve
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /site /usr/share/nginx/html
