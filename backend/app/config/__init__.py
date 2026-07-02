"""Central configuration for the Handwriting Notes Digitizer backend.

Exposes the application settings singleton and the deterministic-seeding
helpers used across recognition and training so that behaviour is
reproducible (Req 11.1, 11.3).
"""

from app.config.settings import (
    Settings,
    get_settings,
    seed_everything,
    configure_determinism,
)

__all__ = [
    "Settings",
    "get_settings",
    "seed_everything",
    "configure_determinism",
]
