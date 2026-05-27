"""Gen 2 (Pokemon Gold/Silver/Crystal) shiny detection.

Shiny status in Gen 2 is determined entirely by the Pokemon's DVs.
There is no separate "shiny flag" — the game derives shininess by
inspecting the DVs at draw time.

The DV format is 2 bytes:

    byte 0 (high):  AAAA DDDD  (Attack DV, Defense DV)
    byte 1 (low):   SSSS PPPP  (Speed DV, Special DV)

i.e. each DV is a 4-bit nibble.

A Pokemon is shiny iff:
    - Attack DV in {2, 3, 6, 7, 10, 11, 14, 15}  (bit 1 of attack DV set)
    - Defense DV == 10
    - Speed DV   == 10
    - Special DV == 10

This module provides:

* :func:`decode_dvs`     — extract the four DVs from the 2-byte block
* :func:`is_shiny`       — apply the shiny formula
* :func:`detect_shiny`   — read everything from an emulator/reader and
                           return a structured result
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from pokemon_agent.emulator import Emulator
from pokemon_agent.memory.gold import (
    ADDR_BATTLE_MODE,
    ADDR_ENEMY_DVS,
    ADDR_ENEMY_SPECIES,
    SPECIES_NAMES,
)


SHINY_ATTACK_DVS = frozenset({2, 3, 6, 7, 10, 11, 14, 15})


@dataclass(frozen=True)
class DVs:
    """The four individual DVs decoded from a wEnemyMonDVs / wPartyMon*DVs block."""

    attack: int
    defense: int
    speed: int
    special: int

    @property
    def raw(self) -> int:
        """Reconstruct the original 16-bit value (high byte first)."""
        return (
            ((self.attack & 0xF) << 12)
            | ((self.defense & 0xF) << 8)
            | ((self.speed & 0xF) << 4)
            | (self.special & 0xF)
        )


@dataclass(frozen=True)
class ShinyResult:
    """Outcome of inspecting an active wild encounter for shininess."""

    in_battle: bool
    species_id: int
    species: str
    dvs: Optional[DVs]
    is_shiny: bool


def decode_dvs(byte0: int, byte1: int) -> DVs:
    """Decode 4 DVs (4 bits each) from a 2-byte DV block.

    Parameters
    ----------
    byte0 : int
        High byte: ``AAAA DDDD`` (attack high nibble, defense low nibble).
    byte1 : int
        Low byte: ``SSSS PPPP`` (speed high nibble, special low nibble).

    Returns
    -------
    DVs
        The four decoded 4-bit DVs.
    """
    byte0 &= 0xFF
    byte1 &= 0xFF
    return DVs(
        attack=(byte0 >> 4) & 0x0F,
        defense=byte0 & 0x0F,
        speed=(byte1 >> 4) & 0x0F,
        special=byte1 & 0x0F,
    )


def decode_dvs_u16(value: int) -> DVs:
    """Decode DVs from a packed 16-bit value (high byte = byte0).

    Equivalent to ``decode_dvs(value >> 8, value & 0xFF)``.
    """
    return decode_dvs((value >> 8) & 0xFF, value & 0xFF)


def is_shiny(dvs: DVs) -> bool:
    """Apply the Gen 2 shiny rule to a decoded DV set.

    A Pokemon is shiny iff:

    * Attack DV is in ``{2, 3, 6, 7, 10, 11, 14, 15}`` (i.e. bit 1 set),
    * Defense, Speed, and Special DVs are all exactly ``10``.
    """
    return (
        dvs.attack in SHINY_ATTACK_DVS
        and dvs.defense == 10
        and dvs.speed == 10
        and dvs.special == 10
    )


def detect_shiny(emulator: Emulator) -> ShinyResult:
    """Inspect the current wild encounter on *emulator* for shininess.

    Reads ``wBattleMode``, ``wEnemyMonSpecies``, and ``wEnemyMonDVs``
    directly from WRAM. If no battle is active, returns a
    :class:`ShinyResult` with ``in_battle=False`` and ``dvs=None``.
    """
    in_battle = emulator.read_u8(ADDR_BATTLE_MODE) != 0
    species_id = emulator.read_u8(ADDR_ENEMY_SPECIES)
    species_name = SPECIES_NAMES.get(species_id, f"???({species_id})")

    if not in_battle:
        return ShinyResult(
            in_battle=False,
            species_id=species_id,
            species=species_name,
            dvs=None,
            is_shiny=False,
        )

    byte0 = emulator.read_u8(ADDR_ENEMY_DVS)
    byte1 = emulator.read_u8(ADDR_ENEMY_DVS + 1)
    dvs = decode_dvs(byte0, byte1)

    return ShinyResult(
        in_battle=True,
        species_id=species_id,
        species=species_name,
        dvs=dvs,
        is_shiny=is_shiny(dvs),
    )


__all__ = [
    "DVs",
    "ShinyResult",
    "SHINY_ATTACK_DVS",
    "decode_dvs",
    "decode_dvs_u16",
    "is_shiny",
    "detect_shiny",
]
