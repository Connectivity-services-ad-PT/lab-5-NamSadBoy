.PHONY: install lint compose-up compose-down logs test-compose readiness

install:
	npm install

lint:
	npm run lint:openapi

compose-up:
	docker compose up -d --build --wait

compose-down:
	docker compose down -v

logs:
	docker compose logs -f

test-compose:
	npm run test:all

readiness:
	docker compose ps
	curl http://localhost:8000/health
	curl http://localhost:9000/health
	curl http://localhost:9100/health
