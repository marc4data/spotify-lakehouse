SHELL := /bin/bash
CONFIG_DIR ?= $(HOME)/.config/spot
COMPOSE := docker compose --env-file $(CONFIG_DIR)/.env

.PHONY: help bootstrap db-up db-down migrate dbt-parse dbt-build dbt-test dbt-profile test lint format \
	launchd-install launchd-uninstall refresh-status

help:
	@echo "bootstrap    one command to make this checkout fully operational (idempotent)"
	@echo "db-up        start the shared Postgres container (port 5433)"
	@echo "db-down      stop it — affects EVERY checkout, not just this one"
	@echo "migrate      apply numbered raw migrations + create this session's schemas"
	@echo "dbt-parse    dbt parse with SPOT_SESSION taken from .session"
	@echo "dbt-profile  append the spot profile to ~/.dbt/profiles.yml (backs it up first)"
	@echo "test / lint / format"

bootstrap:
	@scripts/bootstrap.sh

db-up:
	$(COMPOSE) up -d --wait postgres

db-down:
	@echo "Stopping spot-postgres. Every worktree shares this container."
	$(COMPOSE) down

migrate:
	uv run spot migrate

dbt-parse:
	scripts/dbt.sh parse

dbt-build:
	scripts/dbt.sh build

dbt-test:
	scripts/dbt.sh test

launchd-install:
	scripts/launchd.sh install

launchd-uninstall:
	scripts/launchd.sh uninstall

refresh-status:
	uv run spot refresh --status

dbt-profile:
	scripts/install_dbt_profile.sh

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .
