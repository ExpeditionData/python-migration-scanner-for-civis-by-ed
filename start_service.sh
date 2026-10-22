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
set -euo pipefail

cd "$(dirname "$0")"
exec gunicorn --bind "0.0.0.0:${CIVIS_SERVICE_PORT:-3838}" --workers 2 "app:create_app()"
