FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91 AS runtime

# B-only build entrypoint: A CI/delivery continues to use the root Dockerfile.
LABEL org.momcozy.release-target="north-america-staging"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY --chown=app:app alembic.ini alembic.ini
COPY --chown=app:app migrations migrations
COPY --chown=app:app app app
COPY --chown=app:app evals evals
COPY --chown=app:app docs/contracts docs/contracts
COPY --chown=app:app docs/runtime-contract-catalog.generated.json docs/runtime-contract-catalog.generated.json
COPY --chown=app:app scripts scripts

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
