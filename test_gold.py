"""Phase 1 & 2 tests for the Gen 2 (Pokemon Gold) port.

Covers:

1. ROM loading via PyBoy + cartridge title check.
2. GoldReader returns plausible (non-garbage) values on a fresh boot.
3. Gen 2 shiny DV decoding logic on a battery of known cases.
4. cli/server game-type detection routes .gbc -> "gold".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pokemon_agent.shiny import DVs, decode_dvs, decode_dvs_u16, is_shiny


ROM_PATH = Path(__file__).parent / "roms" / "pokemon_gold.gbc"


# ---------------------------------------------------------------------------
# 1. ROM loading
# ---------------------------------------------------------------------------

def test_rom_file_exists():
    assert ROM_PATH.exists(), f"ROM not found at {ROM_PATH}"
    assert ROM_PATH.stat().st_size > 0


def test_pyboy_loads_and_title_is_pokemon_gold():
    pyboy = pytest.importorskip("pyboy").PyBoy(str(ROM_PATH), window="null")
    try:
        # PyBoy strips the cartridge title to its non-null prefix
        assert pyboy.cartridge_title == "POKEMON_GLDAAU"
    finally:
        pyboy.stop(save=False)


# ---------------------------------------------------------------------------
# 2. GoldReader smoke test
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def booted_emulator():
    """Boot the ROM, tick past the bootrom/cartridge intro, return emulator."""
    from pokemon_agent.emulator import create_emulator

    emu = create_emulator(str(ROM_PATH))
    # Run a couple hundred frames so the cartridge logo finishes drawing
    # and WRAM is initialised by the game's main loop.
    emu.tick(600)
    yield emu
    emu.close()


def test_gold_reader_instantiates(booted_emulator):
    from pokemon_agent.memory.gold import GoldReader

    reader = GoldReader(booted_emulator)
    assert reader.game_name.startswith("Pokemon Gold")


def test_gold_reader_methods_return_dicts(booted_emulator):
    """Every abstract method should return a sensible structure, not crash."""
    from pokemon_agent.memory.gold import GoldReader

    reader = GoldReader(booted_emulator)

    player = reader.read_player()
    assert isinstance(player, dict)
    assert "position" in player
    assert "money" in player
    assert "badges" in player

    party = reader.read_party()
    assert isinstance(party, list)
    assert len(party) <= 6

    bag = reader.read_bag()
    assert isinstance(bag, list)

    battle = reader.read_battle()
    assert isinstance(battle, dict)
    assert "in_battle" in battle

    dialog = reader.read_dialog()
    assert isinstance(dialog, dict)
    assert "active" in dialog

    map_info = reader.read_map_info()
    assert isinstance(map_info, dict)
    assert "map_id" in map_info

    flags = reader.read_flags()
    assert isinstance(flags, dict)
    assert "badge_count" in flags


def test_memory_reads_arent_pure_garbage(booted_emulator):
    """A freshly-booted ROM shouldn't have every byte stuck at 0x00 or 0xFF.

    This guards against the situation where the reader is pointing at an
    un-mapped memory region and gets uniform bus-pull values.
    """
    sample = booted_emulator.read_range(0xC000, 0x2000)  # 8 KiB of WRAM
    distinct = len(set(sample))
    assert distinct > 4, (
        f"Only {distinct} distinct byte values across 8KiB of WRAM — "
        "the emulator probably hasn't initialised RAM yet."
    )


# ---------------------------------------------------------------------------
# 3. Shiny DV decoding
# ---------------------------------------------------------------------------

def test_decode_dvs_basic():
    # byte0 = 0xAB -> attack=0xA, defense=0xB
    # byte1 = 0xCD -> speed=0xC, special=0xD
    d = decode_dvs(0xAB, 0xCD)
    assert d == DVs(attack=0xA, defense=0xB, speed=0xC, special=0xD)


def test_decode_dvs_u16_packs_high_byte_first():
    # 0xABCD should decompose the same way as decode_dvs(0xAB, 0xCD)
    assert decode_dvs_u16(0xABCD) == decode_dvs(0xAB, 0xCD)


def test_dvs_raw_roundtrips():
    d = DVs(attack=10, defense=10, speed=10, special=10)  # all 0xA
    assert d.raw == 0xAAAA
    assert decode_dvs_u16(d.raw) == d


# Canonical Gen 2 shiny: atk in {2,3,6,7,10,11,14,15}, def=spd=spc=10
@pytest.mark.parametrize("atk", sorted({2, 3, 6, 7, 10, 11, 14, 15}))
def test_is_shiny_true_for_canonical_shinies(atk):
    d = DVs(attack=atk, defense=10, speed=10, special=10)
    assert is_shiny(d)


@pytest.mark.parametrize("atk", [0, 1, 4, 5, 8, 9, 12, 13])
def test_is_shiny_false_when_attack_dv_lacks_bit_1(atk):
    """All non-shiny attack DVs have bit 1 clear."""
    d = DVs(attack=atk, defense=10, speed=10, special=10)
    assert not is_shiny(d)


@pytest.mark.parametrize("dv", [0, 1, 2, 5, 9, 11, 15])
def test_is_shiny_false_when_defense_not_10(dv):
    d = DVs(attack=10, defense=dv, speed=10, special=10)
    assert not is_shiny(d)


@pytest.mark.parametrize("dv", [0, 1, 2, 5, 9, 11, 15])
def test_is_shiny_false_when_speed_not_10(dv):
    d = DVs(attack=10, defense=10, speed=dv, special=10)
    assert not is_shiny(d)


@pytest.mark.parametrize("dv", [0, 1, 2, 5, 9, 11, 15])
def test_is_shiny_false_when_special_not_10(dv):
    d = DVs(attack=10, defense=10, speed=10, special=dv)
    assert not is_shiny(d)


def test_shiny_known_value_all_a():
    # The all-A DV pattern 0xAAAA is the textbook "perfect shiny":
    # atk=10, def=10, spd=10, spc=10.
    d = decode_dvs_u16(0xAAAA)
    assert d.attack == 10 and d.defense == 10 and d.speed == 10 and d.special == 10
    assert is_shiny(d)


def test_shiny_known_value_non_shiny():
    # 0x0000 — all DVs are 0, definitely not shiny.
    assert not is_shiny(decode_dvs_u16(0x0000))


def test_shiny_known_value_atk_15_others_10():
    # atk=15 (in shiny set), def/spd/spc=10 — should be shiny.
    d = decode_dvs_u16(0xFAAA)
    assert d.attack == 15
    assert is_shiny(d)


def test_shiny_known_value_def_off_by_one():
    # atk=10, def=11, spd=10, spc=10 — not shiny because def != 10.
    d = decode_dvs_u16(0xABAA)
    assert d == DVs(attack=10, defense=11, speed=10, special=10)
    assert not is_shiny(d)


# ---------------------------------------------------------------------------
# 4. Game-type detection
# ---------------------------------------------------------------------------

def test_cli_detect_game_type_gbc():
    from pokemon_agent.cli import _detect_game_type
    assert _detect_game_type("anything.gbc") == "gold"
    assert _detect_game_type("anything.gb") == "gold"


def test_server_detect_game_type_gbc():
    from pokemon_agent.server import _detect_game_type
    assert _detect_game_type("anything.gbc") == "gold"
    assert _detect_game_type("anything.gb") == "gold"


def test_cli_detect_game_type_gba_unchanged():
    from pokemon_agent.cli import _detect_game_type
    assert _detect_game_type("anything.gba") == "firered"


# ---------------------------------------------------------------------------
# 5. Public API export
# ---------------------------------------------------------------------------

def test_top_level_imports():
    import pokemon_agent
    assert pokemon_agent.is_shiny is is_shiny
    assert pokemon_agent.decode_dvs is decode_dvs
