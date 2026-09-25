#!/bin/sh
# MedFlow AI - start the full stack on macOS/Linux (needs Docker running)
set -e
cd "$(dirname "$0")"
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and retry."; exit 1; }
[ -f .env ] || cp .env.example .env
docker compose up --build -d
echo "Waiting for the app..."
until curl -s -o /dev/null http://localhost:3000/login; do sleep 5; done
echo "MedFlow AI is running: http://localhost:3000  (admin@sunrise.demo / Demo@1234)"
