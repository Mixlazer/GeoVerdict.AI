.PHONY: up down backend frontend llmops release-clean publish-check

up:
	docker compose up --build

down:
	docker compose down

backend:
	docker compose up backend

frontend:
	docker compose up frontend

llmops:
	docker compose up llmops

local-up:
	wsl.exe bash -lc 'cd /home/mixli/GeoVerdict.AI && bash scripts/start_wsl_services.sh'

local-down:
	wsl.exe bash -lc 'cd /home/mixli/GeoVerdict.AI && bash scripts/stop_wsl_services.sh'

release-clean:
	wsl.exe bash -lc 'cd /home/mixli/GeoVerdict.AI && bash scripts/release_clean.sh'

publish-check:
	wsl.exe bash -lc 'cd /home/mixli/GeoVerdict.AI && bash scripts/github_preflight.sh'
