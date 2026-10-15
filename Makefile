.PHONY: docs-check docs-screenshots doctor dev-backend dev-frontend test-backend test-frontend test docker-up docker-down docker-logs docker-smoke

doctor:
	backend/.venv/bin/python scripts/doctor.py

dev-backend:
	cd backend && .venv/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000

dev-frontend:
	cd frontend && npm run dev -- --host 127.0.0.1 --port 5173

test-backend:
	cd backend && PYTHONPATH=.. .venv/bin/python3 -m pytest -q

test-frontend:
	cd frontend && npm test

test: test-frontend test-backend

docker-up:
	docker compose up --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f --tail=120

docker-smoke:
	docker compose up -d --build --wait --wait-timeout 120
	backend/.venv/bin/python scripts/doctor.py

# Documentation checks need only Python 3; capture also needs frontend/Playwright.
PYTHON ?= python3

docs-check:
	$(PYTHON) scripts/docs.py check

docs-screenshots:
	$(PYTHON) scripts/docs.py capture
