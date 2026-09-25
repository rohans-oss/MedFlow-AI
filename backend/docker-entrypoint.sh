#!/bin/sh
set -e
echo "Running database migrations…"
alembic upgrade head

bootstrap() {
  if [ "${SEED_DEMO:-false}" = "true" ]; then
    python -m app.seed
  fi
  if [ "${TRAIN_ON_START:-true}" = "true" ]; then
    echo "Training forecasting models where none exist…"
    python -m app.ml.train --if-missing || echo "Forecast training skipped (see message above)."
  fi
}

# Hosted platforms (Render, Railway…) fail a deploy whose port does not open within a few minutes; seeding and
# training the demo can take longer on small instances. BOOTSTRAP_IN_BACKGROUND=true starts the API first and
# seeds/trains alongside it. Training resumes on the next start if interrupted; an interrupted seed leaves a partial
# demo — recreate it with `python -m app.seed --reset && python -m app.ml.train`.
if [ "${BOOTSTRAP_IN_BACKGROUND:-false}" = "true" ]; then
  bootstrap &
else
  bootstrap
fi
exec "$@"
