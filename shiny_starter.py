"""Pokemon Gold shiny-starter farm.

Loads a save state placed right before the Totodile Pokeball pick,
drives the starter sequence, reads party slot 0 DVs as soon as the
Pokemon lands in WRAM, and either halts (shiny) or reloads the state
and retries (not shiny).

The key optimisation: we read DVs from party WRAM the instant the
Pokemon appears — no need to advance dialog or do the nickname screen
on non-shiny attempts. Each cycle takes ~150 frames at max speed.

Run with the project venv active:
    source .venv/bin/activate
    python shiny_starter.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from pyboy import PyBoy

from pokemon_agent.memory.gold import (
    ADDR_PARTY_COUNT,
    ADDR_PARTY_MON1,
    PARTY_MON_SIZE,
    PARTYMON_OFF_DVS,
    PARTYMON_OFF_SPECIES,
    SPECIES_NAMES,
)
from pokemon_agent.shiny import DVs, decode_dvs, is_shiny

ROOT = Path(__file__).resolve().parent
ROM = ROOT / "roms" / "pokemon_gold.gbc"
STATE = ROOT / "roms" / "pokemon_gold.gbc.state"
SHINY_STATE = ROOT / "roms" / "shiny_totodile_kiwi.state"

# Target species ID for Totodile in Gen 2
TOTODILE_ID = 158

# Frame timing
HOLD = 2
GAP = 4
PRESS_TOTAL = HOLD + GAP  # 6 frames per full button press

# Max iterations for detecting party slot 0 filling
MAX_PICK_ITERS = 600


def main() -> int:
    if not STATE.exists():
        print(f"ERROR: save state not found: {STATE}", file=sys.stderr)
        print(file=sys.stderr)
        print("Create one before running this script:", file=sys.stderr)
        print("  1. python play.py", file=sys.stderr)
        print("  2. In the PyBoy window, get to right before the starter pick", file=sys.stderr)
        print("     (player aligned with the Totodile Pokeball in Elm's lab).", file=sys.stderr)
        print("  3. Press Shift+1 to save state to slot 1.", file=sys.stderr)
        print("  4. Close the window, then:", file=sys.stderr)
        print(f"     mv roms/pokemon_gold.gbc.state1 {STATE}", file=sys.stderr)
        return 1

    pyboy = PyBoy(str(ROM), window="SDL2")
    pyboy.set_emulation_speed(0)  # unlimited speed

    def tick(n: int = 1) -> None:
        for _ in range(n):
            if not pyboy.tick():
                sys.exit(0)

    def press(button: str, hold: int = HOLD, gap: int = GAP) -> None:
        pyboy.button_press(button)
        tick(hold)
        pyboy.button_release(button)
        tick(gap)

    def load_state() -> None:
        with open(STATE, "rb") as f:
            pyboy.load_state(f)
        tick(12)  # let the emulator settle after load

    def read_u8(addr: int) -> int:
        return pyboy.memory[addr] & 0xFF

    def party_count() -> int:
        return read_u8(ADDR_PARTY_COUNT)

    def party_slot(slot: int = 0) -> tuple[int, DVs]:
        """Return (species_id, dvs) for the given party slot."""
        base = ADDR_PARTY_MON1 + slot * PARTY_MON_SIZE
        species = read_u8(base + PARTYMON_OFF_SPECIES)
        b0 = read_u8(base + PARTYMON_OFF_DVS)
        b1 = read_u8(base + PARTYMON_OFF_DVS + 1)
        dvs = decode_dvs(b0, b1)
        return species, dvs

    def mash_a_until(condition, max_iters: int) -> bool:
        for _ in range(max_iters):
            if condition():
                return True
            press("a")
        return condition()

    def find_char(ch: str):
        UPPER = [
            "ABCDEFGHI",
            "JKLMNOPQR",
            "STUVWXYZ ",
        ]
        for r, row in enumerate(UPPER):
            for c, x in enumerate(row):
                if x == ch.upper():
                    return r, c
        raise ValueError(f"char {ch!r} not in keyboard table")

    def grid_move(dr: int, dc: int):
        """Move cursor (dr, dc) cells using D-pad.  dr, dc can be negative."""
        for _ in range(max(0, dr)):
            press("down")
        for _ in range(max(0, -dr)):
            press("up")
        for _ in range(max(0, dc)):
            press("right")
        for _ in range(max(0, -dc)):
            press("left")

    def type_kiwi():
        """Type 'KIWI' on the Gen 2 naming screen (all caps, no case toggle).
        
        Keyboard grid (uppercase), cursor starts at (0, 0) = 'A':
            Row 0: A B C D E F G H I
            Row 1: J K L M N O P Q R
            Row 2: S T U V W X Y Z _
        """
        # KIWI: K(1,1) I(0,8) W(2,4) I(0,8)
        sequence = [(1, 1), (0, 8), (2, 4), (0, 8)]
        cur_r, cur_c = 0, 0
        for r, c in sequence:
            grid_move(r - cur_r, c - cur_c)
            cur_r, cur_c = r, c
            press("a")
        press("start")  # confirm name

    attempt = 0
    try:
        while True:
            attempt += 1
            load_state()

            # Mash A until Pokemon lands in party slot 0
            got = mash_a_until(lambda: party_count() > 0, MAX_PICK_ITERS)
            if not got:
                print(f"[{attempt}] timed out waiting for party slot 0", file=sys.stderr)
                continue

            species, dvs = party_slot(0)
            shiny = is_shiny(dvs)
            name = SPECIES_NAMES.get(species, f"???({species})")

            print(
                f"[{attempt:>4}] {name:>12}  "
                f"ATK={dvs.attack:2d} DEF={dvs.defense:2d} "
                f"SPD={dvs.speed:2d} SPC={dvs.special:2d}  "
                f"{'✨ SHINY' if shiny else 'not shiny'}"
            )

            if species != TOTODILE_ID:
                print(f"    └─ wrong species! expected Totodile ({TOTODILE_ID})", file=sys.stderr)
                # Reset immediately — don't waste time on dialog
                continue

            if not shiny:
                # Reload immediately — DVs are already known, no need for dialog
                continue

            # ── SHINY FOUND ──────────────────────────────────────────────
            print()
            print("=" * 50)
            print(f"  ✨ SHINY TOTODILE on attempt {attempt}!  ✨")
            print(f"  DVs: ATK={dvs.attack} DEF={dvs.defense} SPD={dvs.speed} SPC={dvs.special}")
            print("=" * 50)
            print()
            print("Advancing through dialog to nickname screen...")

            # Advance through Elm's "give it a nickname?" dialog
            for _ in range(60):
                press("a")

            type_kiwi()

            # Advance through remaining Elm dialog
            for _ in range(200):
                press("a")

            # Save a state right here so master can reload the shiny
            with open(SHINY_STATE, "wb") as f:
                pyboy.save_state(f)

            print(f"Saved shiny state to {SHINY_STATE}")
            print("Window stays open. Close it or Ctrl+C to exit.")

            while pyboy.tick():
                pass
            return 0

    except KeyboardInterrupt:
        print(file=sys.stderr)
    finally:
        pyboy.stop(save=False)

    return 0


if __name__ == "__main__":
    sys.exit(main())
