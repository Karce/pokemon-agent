"""pokemon_agent — AI-driven Pokemon gameplay agent."""

__version__ = "0.1.0"

from pokemon_agent.shiny import (
    DVs,
    ShinyResult,
    decode_dvs,
    decode_dvs_u16,
    detect_shiny,
    is_shiny,
)

__all__ = [
    "__version__",
    "DVs",
    "ShinyResult",
    "decode_dvs",
    "decode_dvs_u16",
    "detect_shiny",
    "is_shiny",
]
