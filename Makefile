.PHONY: help install test lint demo serve keygen deploy verify-stack

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n", $$1, $$2}'

install:  ## Install dependencies including dev extras
	uv sync --extra dev

test:  ## Run the test suite (no GCP or model access needed)
	ANBU_STORE_BACKEND=memory uv run pytest -q

lint:  ## Lint
	uv run ruff check anbu_care tests scripts

demo:  ## Run the end-to-end spine with no model in the loop
	ANBU_STORE_BACKEND=memory uv run python scripts/demo_spine.py

serve:  ## Serve the agent API and ADK dev UI on :8080
	uv run uvicorn anbu_care.server:app --host 0.0.0.0 --port 8080 --reload

chat:  ## Talk to the coordinator agent in the terminal
	uv run adk run anbu_care

keygen:  ## Mint a stable Ed25519 signing key for the receipt chain
	uv run python -m anbu_care.provenance.keygen

verify-stack:  ## Confirm Vertex, Firestore, and the model are actually reachable
	uv run python scripts/verify_stack.py

deploy:  ## Deploy to Cloud Run
	./infra/deploy_cloud_run.sh

preflight:  ## Check the state that silently ruins a recording (add FIX=1 to clear what is safe)
	@ANBU_URL=$${ANBU_URL:-https://anbu-care-37j4eofpwq-el.a.run.app} \
	 ./.venv/bin/python scripts/preflight.py $(if $(FIX),--fix,)

booking-mode:  ## Show or set whether the booker submits for real (MODE=dry|live)
	@bash .claude/skills/booking-mode/scripts/booking-mode.sh $(or $(MODE),status)
