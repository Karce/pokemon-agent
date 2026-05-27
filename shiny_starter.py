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
    ADDR_GAME_STATE,
    ADDR_JOY_LOCK,
    ADDR_PARTY_COUNT,
    ADDR_PARTY_MON1,
    ADDR_PARTY_NICKS,
    ADDR_PARTY_SPECIES,
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

# ── Run mode ────────────────────────────────────────────────────────────
# SPEED:
#   "FAST" — emulation_speed=0, no per-press debug output. This is the
#            mode the actual farming loop runs in.
#   "SLOW" — emulation_speed=1 (normal speed), prints raw joy_lock /
#            text_delay / party_count values around each press so you
#            can watch where detection is failing.
# DUMP_MEMORY:
#   True   — dump a few dozen bytes around the party / dialog WRAM
#            region whenever party_count first becomes > 0, and also on
#            phase-1 timeout. Pairs well with SLOW for diagnosing the
#            "party never fills" failure.
SPEED = "FAST"
DUMP_MEMORY = False

# Bounds for each phase.  These are pressed-with-dialog-aware-waits, so
# a "press" here means "wait for joy_lock to clear, then tap A".  Counts
# are generous; extras after we've already reached the next state are
# either harmless (no-op on a stable menu) or self-correct on the next
# iteration of the outer loop.
MAX_PRESSES_TO_PARTY_FILL = 80     # Pokeball interact + "want this?" YES
# cry / "received TOTODILE" / "give a nickname?" YES — bumped from 4
# because the original count never made it to the keyboard reliably
# (cry + receive text + Y/N prompt = >4 advanceable beats in practice).
PRESSES_PARTY_TO_KEYBOARD = 8
PRESSES_POST_NICKNAME = 60         # Elm's "TOTODILE, eh?  ..." chain

# Input timing.
A_HOLD = 3
# Gen-2 menu cursor needs the D-pad held noticeably longer than A —
# 3 frames was unreliable and the cursor stayed at (0,0) on the
# naming screen, producing "AAAA" every attempt.
DPAD_HOLD = 10
PRESS_GAP = 8

# Minimum frames between consecutive presses, even when the dialog
# detector says the coast is clear.  Stops the script from mashing A
# faster than the game can update WRAM (cry animation, party-fill, etc.)
# and prevents the "joy_lock=0 forever, press every 11 frames" failure
# mode observed on Gold US.
MIN_PRESS_INTERVAL = 16

# Extra settle frames after the nickname is confirmed with START.
# Elm's "TOTODILE, eh?" chain takes a moment to begin and the party
# struct (including DVs) is not finalised until then.
POST_NICKNAME_SETTLE = 60

# Max frames to wait for dialog to become input-ready before forcing a press.
DIALOG_WAIT_MAX = 240

# WRAM bank register (GBC). Bank 1 holds most Gen-2 game state; if a
# read of party_count returns garbage, check whether SVBK matches.
ADDR_SVBK = 0xFF70


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

    if SPEED not in ("FAST", "SLOW"):
        print(f"ERROR: SPEED must be 'FAST' or 'SLOW', got {SPEED!r}", file=sys.stderr)
        return 1
    slow = SPEED == "SLOW"

    pyboy = PyBoy(str(ROM), window="SDL2")
    # FAST: 0 = unthrottled.  SLOW: 1 = real-time, so you can watch the
    # game and the debug stream side-by-side.
    pyboy.set_emulation_speed(0 if not slow else 1)

    # Per-attempt frame counter, reset each load_state().  Used only to
    # tag debug lines so timing between events is visible.
    frame_count = [0]

    # -- low-level helpers --------------------------------------------------

    def tick(n: int = 1) -> None:
        for _ in range(n):
            if not pyboy.tick():
                sys.exit(0)
            frame_count[0] += 1

    def press(button: str, hold: int = A_HOLD, gap: int = PRESS_GAP) -> None:
        pyboy.button_press(button)
        tick(hold)
        pyboy.button_release(button)
        tick(gap)

    def read_u8(addr: int) -> int:
        return pyboy.memory[addr] & 0xFF

    def dbg(msg: str) -> None:
        if slow:
            print(f"[f={frame_count[0]:>6}] {msg}", flush=True)

    def dump_diagnostic(label: str) -> None:
        """Hex-dump WRAM regions we care about. Always prints (gated by caller)."""
        svbk = read_u8(ADDR_SVBK) & 0x07
        print(f"  ── DUMP: {label} ── (frame={frame_count[0]}, SVBK={svbk})", flush=True)
        print(
            f"  ADDR_PARTY_COUNT @ 0x{ADDR_PARTY_COUNT:04X} = "
            f"0x{read_u8(ADDR_PARTY_COUNT):02X}",
            flush=True,
        )
        # 64 bytes centred a bit before wPartyCount so we see the
        # surrounding state.
        base = ADDR_PARTY_COUNT - 8
        for off in range(0, 64, 16):
            line = " ".join(f"{read_u8(base + off + i):02X}" for i in range(16))
            print(f"  0x{base + off:04X}: {line}", flush=True)
        # First party slot (48 bytes) — should be all-zero until filled.
        print(f"  ── party slot 0 @ 0x{ADDR_PARTY_MON1:04X} ──", flush=True)
        for off in range(0, PARTY_MON_SIZE, 16):
            line = " ".join(
                f"{read_u8(ADDR_PARTY_MON1 + off + i):02X}" for i in range(16)
            )
            print(f"  0x{ADDR_PARTY_MON1 + off:04X}: {line}", flush=True)
        jl = read_u8(ADDR_JOY_LOCK)
        print(
            f"  JOY_LOCK   @ 0x{ADDR_JOY_LOCK:04X} = "
            f"0x{jl:02X} (bit4={bool(jl & 0x10)} "
            f"bit6={bool(jl & 0x40)} bit7={bool(jl & 0x80)})",
            flush=True,
        )
        print(
            f"  TEXT_DELAY @ 0x{ADDR_TEXT_DELAY:04X} = "
            f"0x{read_u8(ADDR_TEXT_DELAY):02X}",
            flush=True,
        )
        print(
            f"  GAME_STATE @ 0x{ADDR_GAME_STATE:04X} = "
            f"0x{read_u8(ADDR_GAME_STATE):02X}",
            flush=True,
        )
        print(
            f"  PARTY_SPECIES @ 0x{ADDR_PARTY_SPECIES:04X} = "
            + " ".join(f"{read_u8(ADDR_PARTY_SPECIES + i):02X}" for i in range(7)),
            flush=True,
        )

    # -- state queries ------------------------------------------------------

    def party_count() -> int:
        return read_u8(ADDR_PARTY_COUNT)

    def dialog_active() -> bool:
        # Mirrors GoldReader.read_dialog(): in Gold, wJoypadDisable
        # (0xD8BA) uses bits 4/6/7 — treat any nonzero byte as "input
        # disabled". Also treat text-delay > 0 as "still animating".
        return read_u8(ADDR_JOY_LOCK) != 0 or read_u8(ADDR_TEXT_DELAY) != 0

    def wait_input_ready(max_frames: int = DIALOG_WAIT_MAX) -> None:
        """Tick until the game is no longer animating text / locked out of input."""
        for _ in range(max_frames):
            if not dialog_active():
                return
            tick(1)
        # If we fell through, dialog stayed "active" the whole window —
        # surface this in SLOW mode because it usually means our
        # detection bits are wrong, not that the game is really busy.
        if slow:
            dbg(
                f"wait_input_ready EXHAUSTED max={max_frames} "
                f"joy=0x{read_u8(ADDR_JOY_LOCK):02X} "
                f"txt=0x{read_u8(ADDR_TEXT_DELAY):02X}"
            )

    def press_a_when_ready() -> None:
        wait_input_ready()
        press("a")
        # Even if the dialog detector said "ready", the game often
        # needs a handful of frames after a press to write its next
        # state (party-fill, text-delay-frames re-arming, joy-lock
        # toggling for the cry animation, ...).  Without this floor the
        # script mashes A at ~11-frame intervals and out-runs the
        # game's bookkeeping.
        tick(MIN_PRESS_INTERVAL)

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

    # Frames to wait between releasing one D-pad direction and pressing
    # the next.  PRESS_GAP (8) was empirically too tight on the Gen-2
    # naming keyboard.
    DPAD_GAP = 12

    def move_cursor(dr: int, dc: int) -> None:
        for _ in range(dr):
            press("down", hold=DPAD_HOLD, gap=DPAD_GAP)
        for _ in range(-dr):
            press("up", hold=DPAD_HOLD, gap=DPAD_GAP)
        for _ in range(dc):
            press("right", hold=DPAD_HOLD, gap=DPAD_GAP)
        for _ in range(-dc):
            press("left", hold=DPAD_HOLD, gap=DPAD_GAP)

    def type_kiwi() -> None:
        cur_r, cur_c = 0, 0
        for _ch, r, c in KIWI_KEYS:
            move_cursor(r - cur_r, c - cur_c)
            press("a")
            # Give the keyboard a beat to register the letter before
            # the next cursor move.
            tick(MIN_PRESS_INTERVAL)
            cur_r, cur_c = r, c
        # START confirms the name on the Gen-2 naming screen.  Pressing
        # A here would just type a fifth character.
        press("start")
        # The game needs time to dismiss the keyboard, return to the
        # overworld dialog flow, and start writing the final party
        # struct.  Reading DVs immediately gives stale/garbage.
        tick(POST_NICKNAME_SETTLE)

    # -- main loop ----------------------------------------------------------

    attempt = 0
    try:
        while True:
            attempt += 1
            frame_count[0] = 0
            load_state()
            dbg(f"=== attempt {attempt} (SPEED={SPEED}, DUMP_MEMORY={DUMP_MEMORY}) ===")

            # Per-attempt jitter so the divider/RNG state shifts between
            # runs and we actually roll different DVs each time.  Without
            # this every attempt would be byte-for-byte identical and we'd
            # be stuck with the same DVs forever.
            tick((attempt * 13) % 240)

            # Phase 1: Pokeball interact + "WANT THIS TOTODILE?" YES.
            filled = False
            for i in range(MAX_PRESSES_TO_PARTY_FILL):
                pc = party_count()
                dbg(
                    f"phase1 press={i:>2} party_count=0x{pc:02X} "
                    f"joy=0x{read_u8(ADDR_JOY_LOCK):02X} "
                    f"txt=0x{read_u8(ADDR_TEXT_DELAY):02X}"
                )
                if pc > 0:
                    filled = True
                    break
                press_a_when_ready()
            if not filled:
                print(
                    f"[{attempt}] timed out waiting for party_count > 0",
                    file=sys.stderr,
                )
                if DUMP_MEMORY:
                    dump_diagnostic("phase1 TIMEOUT")
                continue
            if DUMP_MEMORY:
                dump_diagnostic("party_count > 0")

            # Phase 2: advance past the cry / "received TOTODILE" text and
            # confirm YES on the nickname Y/N, landing on the keyboard.
            for i in range(PRESSES_PARTY_TO_KEYBOARD):
                dbg(
                    f"phase2 press={i:>2} "
                    f"joy=0x{read_u8(ADDR_JOY_LOCK):02X} "
                    f"txt=0x{read_u8(ADDR_TEXT_DELAY):02X}"
                )
                press_a_when_ready()

            # Phase 3: type KIWI and press START to confirm.
            dbg("phase3 type_kiwi")
            type_kiwi()

            # Phase 4: clear Elm's "TOTODILE, eh?" follow-up dialog.
            for i in range(PRESSES_POST_NICKNAME):
                if slow and i % 10 == 0:
                    dbg(
                        f"phase4 press={i:>2} "
                        f"joy=0x{read_u8(ADDR_JOY_LOCK):02X} "
                        f"txt=0x{read_u8(ADDR_TEXT_DELAY):02X}"
                    )
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
