SHELL := /bin/bash
CONFIG_DIR ?= $(HOME)/.config/spot
COMPOSE := docker compose --env-file $(CONFIG_DIR)/.env

.PHONY: help bootstrap db-up db-down migrate dbt-parse dbt-build dbt-test dbt-profile test lint format \
	launchd-install launchd-uninstall refresh-status musicbrainz load-export report-01 notebook worktree worktree-remove

help:
	@echo "bootstrap    one command to make this checkout fully operational (idempotent)"
	@echo "db-up        start the shared Postgres container (port 5433)"
	@echo "db-down      stop it — affects EVERY checkout, not just this one"
	@echo "migrate      apply numbered raw migrations + create this session's schemas"
	@echo "dbt-parse    dbt parse with SPOT_SESSION taken from .session"
	@echo "dbt-profile  append the spot profile to ~/.dbt/profiles.yml (backs it up first)"
	@echo "musicbrainz  fetch MusicBrainz genres and tags for every ISRC in raw (resumable, ~1 req/s)"
	@echo "load-export PROFILE=marc   unzip + load that profile's Extended Streaming History (re-run inserts 0)"
	@echo "notebook     start Jupyter Lab (uv run jupyter lab)"
	@echo "report-01    execute and render notebooks/01_api_inventory.ipynb to reports/"
	@echo "worktree NAME=wtc          create ../spotify-wtc, bootstrap it, print what it provisioned"
	@echo "worktree-remove NAME=wtc   remove it, delete its branch if merged, drop its schemas"
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

musicbrainz:
	uv run spot resolve-musicbrainz

load-export:
	uv run spot load-export --profile "$(PROFILE)"

# nb2report is attached per run from a local checkout, never recorded in uv.lock: a path dependency
# in the lock breaks `uv sync --locked` for anyone without that sibling directory, CI included (R-008).
NB2REPORT ?= ../nb2report
report-01:
	uv run jupyter nbconvert --to notebook --execute --inplace \
		--ExecutePreprocessor.timeout=600 notebooks/01_api_inventory.ipynb
	mkdir -p reports
	uv run --with-editable $(NB2REPORT) nb2report notebooks/01_api_inventory.ipynb \
		--author "Marc Alexander" --toc-depth 3 -o reports/01_api_inventory.html
	uv run nbstripout notebooks/01_api_inventory.ipynb
	@echo "Report: reports/01_api_inventory.html (notebook outputs stripped again)"

notebook:
	uv run jupyter lab

worktree:
	@scripts/worktree.sh add "$(NAME)"

worktree-remove:
	@scripts/worktree.sh remove "$(NAME)"

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
