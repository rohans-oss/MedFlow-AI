"""Audit regression (V0–V10 audit, bug S-1): production must refuse the development/placeholder JWT secret."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

WEAK = ["change-me-in-production-use-a-long-random-string", "change-me", "dev-only-change-me-0f3c9a7e5b1d4c2a", "short"]


@pytest.mark.parametrize("secret", WEAK)
def test_production_refuses_default_or_weak_jwt_secret(secret):
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(ENVIRONMENT="production", JWT_SECRET=secret)


def test_production_accepts_a_strong_secret_and_development_keeps_the_default():
    strong = "k3P9x-" + "a7Q2" * 10
    assert Settings(ENVIRONMENT="production", JWT_SECRET=strong).JWT_SECRET == strong
    assert Settings(ENVIRONMENT="development").JWT_SECRET  # dev/test behaviour unchanged
