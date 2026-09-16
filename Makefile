SHELL := /bin/bash
CONFIG_DIR ?= $(HOME)/.config/spot
COMPOSE := docker compose --env-file $(CONFIG_DIR)/.env

.PHONY: help bootstrap db-up db-down migrate dbt-parse dbt-build dbt-test dbt-profile test lint format \
	launchd-install launchd-uninstall refresh-status musicbrainz load-export resolve-tracks report-01 report-02 report-03 publish notebook worktree worktree-remove

help:
	@echo "bootstrap    one command to make this checkout fully operational (idempotent)"
	@echo "db-up        start the shared Postgres container (port 5433)"
	@echo "db-down      stop it — affects EVERY checkout, not just this one"
	@echo "migrate      apply numbered raw migrations + create this session's schemas"
	@echo "dbt-parse    dbt parse with SPOT_SESSION taken from .session"
	@echo "dbt-profile  append the spot profile to ~/.dbt/profiles.yml (backs it up first)"
	@echo "musicbrainz  fetch MusicBrainz genres and tags for every ISRC in raw (resumable, ~1 req/s)"
	@echo "load-export PROFILE=marc   unzip + load that profile's Extended Streaming History (re-run inserts 0)"
	@echo "resolve-tracks PROFILE=marc  DRY RUN: count export tracks still to look up (add --run via uv run spot)"
	@echo "notebook     start Jupyter Lab (uv run jupyter lab)"
	@echo "report-01    execute and render notebooks/01_data_inventory.ipynb to reports/"
	@echo "report-02 PROFILE=marc  render notebooks/02_listening_patterns.ipynb for one profile"
	@echo "report-03 PROFILE=marc  render notebooks/03_history_profile.ipynb for one profile"
	@echo "publish PROFILE=marc     regenerate published/*.csv from the marts (uploads nothing)"
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

# Dry run only: prints how many GET /tracks/{id} calls a real run would make. Running them is deliberate:
#   uv run spot resolve-tracks --profile marc --run [--limit N]
resolve-tracks:
	uv run spot resolve-tracks --profile "$(PROFILE)"

# nb2report is attached per run from a local checkout, never recorded in uv.lock: a path dependency
# in the lock breaks `uv sync --locked` for anyone without that sibling directory, CI included (R-008).
NB2REPORT ?= ../nb2report
report-01:
	uv run jupyter nbconvert --to notebook --execute --inplace \
		--ExecutePreprocessor.timeout=600 notebooks/01_data_inventory.ipynb
	mkdir -p reports
	uv run --with-editable $(NB2REPORT) nb2report notebooks/01_data_inventory.ipynb \
		--author "Marc Alexander" --toc-depth 3 -o reports/01_data_inventory.html
	uv run nbstripout notebooks/01_data_inventory.ipynb
	@echo "Report: reports/01_data_inventory.html (notebook outputs stripped again)"

# One profile per run (R-009): SPOT_PROFILE reaches setup() inside the kernel, so the notebook names
# nobody. The slug is checked against spot_meta.profile_registry before anything executes.
report-02:
	@test -n "$(PROFILE)" || { echo "usage: make report-02 PROFILE=<slug>" >&2; exit 2; }
	SPOT_PROFILE="$(PROFILE)" uv run python -m spotify_lakehouse.notebook
	SPOT_PROFILE="$(PROFILE)" uv run jupyter nbconvert --to notebook --execute --inplace \
		--ExecutePreprocessor.timeout=900 notebooks/02_listening_patterns.ipynb
	mkdir -p reports
	uv run --with-editable $(NB2REPORT) nb2report notebooks/02_listening_patterns.ipynb \
		--author "Marc Alexander" --toc-depth 3 -o "reports/02_listening_patterns_$(PROFILE).html"
	uv run nbstripout notebooks/02_listening_patterns.ipynb
	@echo "Report: reports/02_listening_patterns_$(PROFILE).html (notebook outputs stripped again)"

# The export's own profile: shape, distributions and oddities (R-050). Same PROFILE mechanism as 02.
report-03:
	@test -n "$(PROFILE)" || { echo "usage: make report-03 PROFILE=<slug>" >&2; exit 2; }
	SPOT_PROFILE="$(PROFILE)" uv run python -m spotify_lakehouse.notebook
	SPOT_PROFILE="$(PROFILE)" uv run jupyter nbconvert --to notebook --execute --inplace \
		--ExecutePreprocessor.timeout=900 notebooks/03_history_profile.ipynb
	mkdir -p reports
	uv run --with-editable $(NB2REPORT) nb2report notebooks/03_history_profile.ipynb \
		--author "Marc Alexander" --toc-depth 3 -o "reports/03_history_profile_$(PROFILE).html"
	uv run nbstripout notebooks/03_history_profile.ipynb
	@echo "Report: reports/03_history_profile_$(PROFILE).html (notebook outputs stripped again)"

# Flat extracts for Tableau Public, which cannot source a database (CLAUDE.md §4, R-007).
# published/ is gitignored: the files are derived and personal even though a workbook built on
# them is destined to be public. This target writes files and uploads nothing.
publish:
	@test -n "$(PROFILE)" || { echo "usage: make publish PROFILE=<slug>" >&2; exit 2; }
	uv run spot publish --profile "$(PROFILE)"

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
