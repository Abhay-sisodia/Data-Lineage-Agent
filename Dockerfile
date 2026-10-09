# The review bundle's app image: analyser, lineage graph tools, agent harness and the
# Streamlit UI. PostgreSQL only. Run it with docker-compose.review.yml (see REVIEW.md).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them. The same hashed pins as
# development; Streamlit is the temporary UI's only unpinned dependency (frontend/README.md).
COPY requirements.txt requirements-kg.txt ./
RUN pip install --require-hashes -r requirements.txt \
 && pip install --require-hashes -r requirements-kg.txt -c requirements.txt \
 && pip install streamlit==1.63.0

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

COPY config ./config
COPY frontend ./frontend
COPY scripts/pg_seed.py scripts/pg_exec_suite.py ./scripts/
COPY stress ./stress
COPY docker ./docker

# A non-root user with a home: the Claude agent keeps its settings under ~/.claude.
RUN useradd --create-home app && mkdir -p /app/.lineage_cache && chown -R app /app
USER app

# PostgreSQL only in this bundle; Ask needs an API key (no Claude Code login in a container).
ENV LINEAGE_DIALECTS=postgres \
    LINEAGE_REQUIRE_API_KEY=1

EXPOSE 8501
ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
