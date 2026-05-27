"""Pokemon Gold shiny-starter farm for Totodile.

Repeatedly loads a save state placed in front of Prof Elm's starter
Pokeballs, picks Totodile, advances through the FULL dialog sequence
INCLUDING the nickname keyboard (typing "KIWI" every attempt), reads
party-slot-0 DVs, and either halts on a shiny or reloads and retries.

Why type the nickname on non-shiny attempts too?  Two reasons:
  1. We want a single, well-defined dialog flow that always ends in
     the same overworld state — easier to reason about than branching
     "if shiny do A, else do B".
  2. Mashing A blindly through the nickname Y/N would accept the
     default name TOTODILE, and the slight timing variance between
     attempts (different jitter, different keyboard timing) is what
     lets the Gen-2 RNG land on different DVs.  Skipping the keyboard
     would mean identical DVs forever.

Run with the project venv active:
    source .venv/bin/activate
    python shiny_starter.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from pyboy import PyBoy

from pokemon_agent.memory.gold import (
    ADDR_JOY_LOCK,
    ADDR_PARTY_COUNT,
    ADDR_PARTY_MON1,
    ADDR_PARTY_NICKS,
    ADDR_TEXT_DELAY,
    GEN2_ENCODING,
    NAME_SIZE,
    PARTY_MON_SIZE,
    PARTYMON_OFF_DVS,
    PARTYMON_OFF_SPECIES,
    SPECIES_NAMES,
)
from pokemon_agent.shiny import decode_dvs, is_shiny

ROOT = Path(__file__).resolve().parent
ROM = ROOT / "roms" / "pokemon_gold.gbc"
STATE = ROOT / "roms" / "pokemon_gold.gbc.state"
SHINY_STATE = ROOT / "roms" / "shiny_totodile_kiwi.state"

TOTODILE_ID = 158

# Bounds for each phase.  These are pressed-with-dialog-aware-waits, so
# a "press" here means "wait for joy_lock to clear, then tap A".  Counts
# are generous; extras after we've already reached the next state are
# either harmless (no-op on a stable menu) or self-correct on the next
# iteration of the outer loop.
MAX_PRESSES_TO_PARTY_FILL = 80     # Pokeball interact + "want this?" YES
PRESSES_PARTY_TO_KEYBOARD = 4      # cry / "received TOTODILE" / nickname-Y/N YES
PRESSES_POST_NICKNAME = 60         # Elm's "TOTODILE, eh?  ..." chain

# Input timing.
A_HOLD = 3
DPAD_HOLD = 3
PRESS_GAP = 8

# Max frames to wait for dialog to become input-ready before forcing a press.
DIALOG_WAIT_MAX = 240


def main() -> int:
    if not STATE.exists():
        print(f"ERROR: save state not found at {STATE}", file=sys.stderr)
        print(file=sys.stderr)
        print("Create one before running this script:", file=sys.stderr)
        print("  1. python play.py", file=sys.stderr)
        print("  2. Walk into Elm's lab and stand facing Totodile's", file=sys.stderr)
        print("     Pokeball, ready to press A.", file=sys.stderr)
        print("  3. In the PyBoy window, Shift+1 to save to slot 1.", file=sys.stderr)
        print("  4. Close the window, then:", file=sys.stderr)
        print(f"     mv roms/pokemon_gold.gbc.state1 {STATE}", file=sys.stderr)
        return 1

    pyboy = PyBoy(str(ROM), window="SDL2")
    pyboy.set_emulation_speed(0)

    # -- low-level helpers --------------------------------------------------

    def tick(n: int = 1) -> None:
        for _ in range(n):
            if not pyboy.tick():
                sys.exit(0)

    def press(button: str, hold: int = A_HOLD, gap: int = PRESS_GAP) -> None:
        pyboy.button_press(button)
        tick(hold)
        pyboy.button_release(button)
        tick(gap)

    def read_u8(addr: int) -> int:
        return pyboy.memory[addr] & 0xFF

    # -- state queries ------------------------------------------------------

    def party_count() -> int:
        return read_u8(ADDR_PARTY_COUNT)

    def dialog_active() -> bool:
        # Mirrors GoldReader.read_dialog(): joy_lock bit 5 set OR text scrolling.
        return bool(read_u8(ADDR_JOY_LOCK) & 0x20) or read_u8(ADDR_TEXT_DELAY) != 0

    def wait_input_ready(max_frames: int = DIALOG_WAIT_MAX) -> None:
        """Tick until the game is no longer animating text / locked out of input."""
        for _ in range(max_frames):
            if not dialog_active():
                return
            tick(1)

    def press_a_when_ready() -> None:
        wait_input_ready()
        press("a")

    def load_state() -> None:
        with open(STATE, "rb") as f:
            pyboy.load_state(f)
        tick(4)

    # -- DV / nickname readers ---------------------------------------------

    def slot0_species_and_dvs():
        base = ADDR_PARTY_MON1
        species = read_u8(base + PARTYMON_OFF_SPECIES)
        b0 = read_u8(base + PARTYMON_OFF_DVS)
        b1 = read_u8(base + PARTYMON_OFF_DVS + 1)
        return species, decode_dvs(b0, b1)

    def slot0_nickname() -> str:
        chars = []
        for i in range(NAME_SIZE):
            b = read_u8(ADDR_PARTY_NICKS + i)
            if b == 0x50:
                break
            chars.append(GEN2_ENCODING.get(b, "?"))
        return "".join(chars)

    # -- keyboard navigation -----------------------------------------------

    # 9x6 uppercase grid.  Cursor starts at row 0, col 0 = 'A'.
    # Per the task spec:
    #   K -> (1, 1)
    #   I -> (0, 8)
    #   W -> (2, 4)
    #   I -> (0, 8)
    KIWI_KEYS = [
        ("K", 1, 1),
        ("I", 0, 8),
        ("W", 2, 4),
        ("I", 0, 8),
    ]

    def move_cursor(dr: int, dc: int) -> None:
        for _ in range(dr):
            press("down", hold=DPAD_HOLD)
        for _ in range(-dr):
            press("up", hold=DPAD_HOLD)
        for _ in range(dc):
            press("right", hold=DPAD_HOLD)
        for _ in range(-dc):
            press("left", hold=DPAD_HOLD)

    def type_kiwi() -> None:
        cur_r, cur_c = 0, 0
        for _ch, r, c in KIWI_KEYS:
            move_cursor(r - cur_r, c - cur_c)
            press("a")
            cur_r, cur_c = r, c
        # START confirms the name on the Gen-2 naming screen.  Pressing
        # A here would just type a fifth character.
        press("start")

    # -- main loop ----------------------------------------------------------

    attempt = 0
    try:
        while True:
            attempt += 1
            load_state()

            # Per-attempt jitter so the divider/RNG state shifts between
            # runs and we actually roll different DVs each time.  Without
            # this every attempt would be byte-for-byte identical and we'd
            # be stuck with the same DVs forever.
            tick((attempt * 13) % 240)

            # Phase 1: Pokeball interact + "WANT THIS TOTODILE?" YES.
            filled = False
            for _ in range(MAX_PRESSES_TO_PARTY_FILL):
                if party_count() > 0:
                    filled = True
                    break
                press_a_when_ready()
            if not filled:
                print(
                    f"[{attempt}] timed out waiting for party_count > 0",
                    file=sys.stderr,
                )
                continue

            # Phase 2: advance past the cry / "received TOTODILE" text and
            # confirm YES on the nickname Y/N, landing on the keyboard.
            for _ in range(PRESSES_PARTY_TO_KEYBOARD):
                press_a_when_ready()

            # Phase 3: type KIWI and press START to confirm.
            type_kiwi()

            # Phase 4: clear Elm's "TOTODILE, eh?" follow-up dialog.
            for _ in range(PRESSES_POST_NICKNAME):
                press_a_when_ready()

            # Phase 5: read what we got.
            species, dvs = slot0_species_and_dvs()
            nick = slot0_nickname()
            shiny = is_shiny(dvs)
            species_name = SPECIES_NAMES.get(species, f"???({species})")

            print(
                f"[{attempt:>4}] {species_name:>10}  "
                f"nick={nick!r:<14} "
                f"ATK={dvs.attack:2d} DEF={dvs.defense:2d} "
                f"SPD={dvs.speed:2d} SPC={dvs.special:2d}  "
                f"{'*** SHINY ***' if shiny else 'not shiny'}"
            )

            if species != TOTODILE_ID:
                print(
                    f"  └─ wrong species; expected Totodile ({TOTODILE_ID})",
                    file=sys.stderr,
                )
                continue

            if not shiny:
                continue

            # ── Shiny found ───────────────────────────────────────────
            print()
            print("=" * 60)
            print(f"  ✨  SHINY TOTODILE  ✨   on attempt {attempt}")
            print(
                f"  DVs:  ATK={dvs.attack}  DEF={dvs.defense}  "
                f"SPD={dvs.speed}  SPC={dvs.special}"
            )
            print(f"  Nickname: {nick!r}")
            print("=" * 60)
            print()

            with open(SHINY_STATE, "wb") as f:
                pyboy.save_state(f)
            print(f"Saved shiny state to {SHINY_STATE}")
            print("Window stays open.  Close it (or Ctrl+C) to exit.")

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
