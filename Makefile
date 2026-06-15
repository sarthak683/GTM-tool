.PHONY: local ps logs rebuild-frontend rebuild-backend rebuild-backend-all migrate test-backend smoke frontend-build backend-compile seed-demo seed-pipeline

local:
	docker compose up -d --build

ps:
	docker compose ps

logs:
	docker compose logs --tail=150 backend frontend worker beat

rebuild-frontend:
	docker compose up -d --build frontend

rebuild-backend:
	docker compose up -d --build backend

rebuild-backend-all:
	docker compose up -d --build backend worker beat

migrate:
	docker compose exec -T backend alembic upgrade head

test-backend:
	docker compose exec -T backend pytest

smoke:
	scripts/smoke/local-health.sh

frontend-build:
	scripts/smoke/frontend-build.sh

backend-compile:
	scripts/smoke/backend-pycompile.sh

seed-demo:
	docker compose cp scripts/seed_dev_data.py backend:/tmp/seed_dev_data.py
	docker compose exec -T backend python /tmp/seed_dev_data.py

seed-pipeline:
	docker compose cp scripts/seed_pipeline_deals.py backend:/tmp/seed_pipeline_deals.py
	docker compose exec -T backend python /tmp/seed_pipeline_deals.py

