# Local testing only. Never used in Civis and never pushed to a registry.
#
# The web app Service's image, pinned by digest:
# civisanalytics/civis-services-flask:0.1.0 (linux/amd64)
FROM civisanalytics/civis-services-flask@sha256:e8d7a631ab46598caa6735e2dd657763c95eb5c76af7a57510f3d2370f02a20f

# 1. Fail if the image's Python or package versions differ from requirements-service.txt.
# 2. Add pytest (the same pin as requirements-dev-service.txt) and nothing else.
# 3. Fail if any package that came with the image changed version.
COPY requirements-service.txt docker/check_image.py /tmp/cpvs-build/
RUN set -eu; \
    cd /tmp/cpvs-build; \
    python check_image.py versions requirements-service.txt; \
    pip freeze --all > before.txt; \
    pip install --no-cache-dir --disable-pip-version-check pytest==9.1.1; \
    pip freeze --all > after.txt; \
    python check_image.py unchanged before.txt after.txt; \
    cd /; \
    rm -rf /tmp/cpvs-build

# Civis clones the repo to /app. .dockerignore keeps Context/, .env files,
# virtual environments, dry-run output, .git, and caches out of this copy.
# The image's own start command (/entrypoint.sh, run from /) is unchanged;
# the smoke test in CLAUDE.md runs it with APP_DIR=/app.
WORKDIR /app
COPY . /app
ENV CPVS_TEST_IMAGE=service \
    PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]
