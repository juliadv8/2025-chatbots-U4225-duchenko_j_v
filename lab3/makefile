SHELL := /bin/bash
SERVICE := bot

.PHONY: up down restart logs ps stats build pull sh health

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart $(SERVICE)

logs:
	docker compose logs -f $(SERVICE)

ps:
	docker compose ps

stats:
	docker compose stats

build:
	docker compose build --no-cache

pull:
	docker compose pull

sh:
	docker compose exec $(SERVICE) sh

health:
	docker inspect --format='{{json .State.Health}}' museum-bot | jq .
