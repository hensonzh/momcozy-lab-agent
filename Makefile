LOCAL_ENV_FILE ?= env/local.env
DEPLOY_ENVIRONMENT ?= staging
DEPLOY_ENV_FILE ?= env/$(DEPLOY_ENVIRONMENT).env
PYTHON ?= .venv/bin/python
DEPLOY_IMAGE ?= ghcr.io/hensonzh/momcozy-lab-agent@sha256:0000000000000000000000000000000000000000000000000000000000000000
DEPLOY_RELEASE_ID ?= 0000000000000000000000000000000000000000
LOCAL_COMPOSE = MOMCOZY_AGENT_ENV_FILE=$(LOCAL_ENV_FILE) docker compose -f docker-compose.local.yml
DEPLOY_COMPOSE = MOMCOZY_AGENT_IMAGE=$(DEPLOY_IMAGE) MOMCOZY_AGENT_RELEASE_ID=$(DEPLOY_RELEASE_ID) MOMCOZY_AGENT_ENV_FILE=$(DEPLOY_ENV_FILE) docker compose --env-file $(DEPLOY_ENV_FILE) -f docker-compose.deploy.yml

.PHONY: \
	agent-local-build agent-local-up agent-local-down agent-local-logs \
	agent-deploy-validate agent-staging-config agent-production-config \
	agent-deploy-ps agent-deploy-logs agent-test agent-lint

agent-local-build:
	$(LOCAL_COMPOSE) build api worker migrate

agent-local-up:
	$(LOCAL_COMPOSE) up -d postgres redis minio minio-init
	$(LOCAL_COMPOSE) --profile tools run --rm migrate
	$(LOCAL_COMPOSE) up -d --force-recreate api worker

agent-local-down:
	$(LOCAL_COMPOSE) down

agent-local-logs:
	$(LOCAL_COMPOSE) logs --follow api worker

# Deployment mutation is intentionally owned by the protected agent-delivery workflow.
agent-deploy-validate:
	$(DEPLOY_COMPOSE) config --quiet

agent-staging-config:
	$(MAKE) agent-deploy-validate DEPLOY_ENVIRONMENT=staging DEPLOY_ENV_FILE=env/staging.env.example

agent-production-config:
	$(MAKE) agent-deploy-validate DEPLOY_ENVIRONMENT=production DEPLOY_ENV_FILE=env/production.env.example

agent-deploy-ps:
	$(DEPLOY_COMPOSE) ps

agent-deploy-logs:
	$(DEPLOY_COMPOSE) logs --follow api worker

agent-test:
	$(PYTHON) -m pytest -q

agent-lint:
	$(PYTHON) -m ruff check app tests scripts
	$(PYTHON) -m mypy app
