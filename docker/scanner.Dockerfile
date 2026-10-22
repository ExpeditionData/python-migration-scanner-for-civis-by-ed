# Local testing only. Never used in Civis and never pushed to a registry.
#
# The scanner job's image, pinned by digest:
# civisanalytics/datascience-python:9.1.1 (linux/amd64)
FROM civisanalytics/datascience-python@sha256:d6903fdcf6b13801417621ad7522c09a16e1d2c01d11eb398f75ad0f73d974ef

# 1. Fail if the image's Python or package versions differ from requirements-scanner.txt.
# 2. Add pytest (the same pin as requirements-dev.txt) and nothing else.
# 3. Fail if any package that came with the image changed version.
COPY requirements-scanner.txt docker/check_image.py /tmp/cpvs-build/
RUN set -eu; \
    cd /tmp/cpvs-build; \
    python check_image.py versions requirements-scanner.txt; \
    pip freeze --all > before.txt; \
    pip install --no-cache-dir --disable-pip-version-check pytest==9.1.1; \
    pip freeze --all > after.txt; \
    python check_image.py unchanged before.txt after.txt; \
    cd /; \
    rm -rf /tmp/cpvs-build

# Civis clones the repo to /app. .dockerignore keeps Context/, .env files,
# virtual environments, dry-run output, .git, and caches out of this copy.
WORKDIR /app
COPY . /app
ENV CPVS_TEST_IMAGE=scanner \
    PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]
