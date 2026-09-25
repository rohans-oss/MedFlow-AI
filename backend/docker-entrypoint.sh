#!/bin/sh
set -e
echo "Running database migrations…"
alembic upgrade head
if [ "${SEED_DEMO:-false}" = "true" ]; then
  python -m app.seed
fi
if [ "${TRAIN_ON_START:-true}" = "true" ]; then
  echo "Training forecasting models where none exist…"
  python -m app.ml.train --if-missing || echo "Forecast training skipped (see message above)."
fi
exec "$@"
