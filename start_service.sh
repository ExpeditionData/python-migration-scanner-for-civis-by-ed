#!/bin/bash
# Starts the web app in the civisanalytics/civis-services-flask:0.1.0 image.
#
# The image's own /entrypoint.sh changes to "${APP_DIR}/${REPO_PATH_DIR}" (the
# repo root, because the Service's path is left blank) and runs
# ./start_service.sh directly, so this file must keep its executable bit and
# LF line endings (tests/test_deploy.py checks both).
#
# It installs nothing. The app uses only the packages already in the image
# (Flask, gunicorn, civis 2.7.1), so the Service never contacts a package index.
#
# Two worker processes with four threads each (gthread): a slow first page
# load, which reads result tables through Civis queries, holds one thread, not
# a whole worker, so /healthz still answers. With gthread, a slow request does
# not trip --timeout (the worker keeps checking in while its threads wait);
# --timeout 120 restarts a worker only if the whole worker stops responding.
# Each worker keeps its own result cache (app/backends/cache.py).
set -euo pipefail

cd "$(dirname "$0")"
exec gunicorn --bind "0.0.0.0:${CIVIS_SERVICE_PORT:-3838}" --worker-class gthread --workers 2 --threads 4 --timeout 120 "app:create_app()"
