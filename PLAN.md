# Pokemon Gold — Shiny Farming Bot

**Project:** `projects/pokemon-agent` (forked from NousResearch/pokemon-agent)
**ROM:** `roms/pokemon_gold.gbc`
**Emulator:** PyBoy (headless for development, GUI on woothoot for play)
**Design principle:** RAM-first, deterministic code. Vision used minimally or not at all.

---

## ✅ Phase 1 — Gen 2 Memory Reader *(in progress)*

Create a Gen 2 memory reader (`memory/gold.py`) that maps Pokemon Gold's WRAM addresses for all gameplay-relevant state.

- [x] Fork/clone repo, install deps, verify ROM loads
- [x] Research Gen 2 memory map from pret/pokegold and pret/pokecrystal disassembly
- [x] Implement `memory/gold.py` (626 lines): GoldReader(GameMemoryReader) — player, party, battle, bag, dialog, map, flags
- [x] Add `"gold"` game type to `_detect_game_type()` in `cli.py` and `server.py`
- [x] Register `.gbc`/`.gb` → `"gold"` mapping (was `"red"`)
- [x] Wire GoldReader into server startup routing
- [x] Verify: 53 tests pass (1ec1257)

### RAM addresses needed (Gen 2 WRAM):

| Region | Address | Notes |
|--------|---------|-------|
| Player X | `0xDCB8` | wXCoord |
| Player Y | `0xDCB7` | wYCoord |
| Map group | `0xDA00` | wMapGroup |
| Map number | `0xDA01` | wMapNumber |
| Game state | `0xD0EB` | Rough marker (overworld/battle/menu) |
| Party count | `0xDCD7` | 6 max |
| Party species | `0xDCD8`+ | Indexed list, 0xFF terminated |
| Party mon struct | `0xDCDF`+ | 0x30 (48) bytes per slot |
| Enemy species | `0xD0ED` | wEnemyMon + 0x00 |
| Enemy DVs | `0xD0F3` | wEnemyMon + 0x06 — **shiny check target** |
| Battle mode | `0xD22D` | 0=none, 1=wild, 2=trainer |
| Dialog lock | `0xD730` | Bit 5 = joypad disabled |
| Johto badges | `0xD857` | Bitmask |
| Kanto badges | `0xD858` | Bitmask |
| Player money | `0xD84E` | 3 bytes BCD big-endian |
| Play time | `0xD4C4`+ | Hours BE + minutes + seconds + frames |
| Items pocket | `0xD892`+ | (id, qty) pairs, 0xFF terminated |
| Balls pocket | `0xD8D7`+ | (id, qty) pairs |

*Note: These addresses come from the Pokemon Crystal disassembly. Gold/Silver memory maps are nearly identical but should be verified against Gold's actual layout.*

---

## ✅ Phase 2 — Shiny Detection *(in progress)*

Pure RAM-based detection using Gen 2's DV system.

- [x] Implement `pokemon_agent/shiny.py` — `decode_dvs()`, `is_shiny()`, `detect_shiny()`
- [x] DV decoding from 2-byte wEnemyMonDVs (at ADDR_ENEMY_DVS = 0xD0ED + 0x06)
- [x] 8 parametrized shiny attack DV tests + off-by-one edge cases for def/spd/spc
- [x] Gen 2 formula: Attack DV ∈ {2,3,6,7,10,11,14,15} AND Defense=Speed=Special=10
- [x] Verify: all 53 tests pass (including 32 shiny parametrized cases)
- [ ] Verify against known encounters (requires in-game wild encounter)

**Gen 2 shiny formula (crystal-clear):**
```
shiny = (atk_dv in {2,3,6,7,10,11,14,15}) and (def_dv == 10) and (spd_dv == 10) and (spc_dv == 10)
```

*DVs read from wEnemyMon + 0x06 (0xD0F3 for Gold US) during wild encounter.*

---

## ✅ Phase 3 — Farming State Machine (Step 3a: Shiny Starter Reset)

### Step 3a — Shiny Starter Reset *(complete — shiny_starter.py on branch shiny-starter-reset)*

- [x] GoldReader and shiny detection ready (Phases 1 & 2)
- [x] Create `shiny_starter.py` — loads save state, runs starter sequence at max speed with GUI
- [x] Button sequence: select Pokeball → choose Totodile → nickname "Kiwi" → advance dialog
- [x] Read party DVs after dialog clears
- [x] Shiny → save state + alert. Not shiny → reset and repeat.
- [x] Verify RNG progression via frame jitter + DV printing per attempt
- [ ] **Test:** master creates save state and runs the script

### Step 3b — Wild Encounter Farming (future)

**States:**
1. **NAVIGATE** — walk through encounter grass, avoid obstacles, use Repel if active
2. **ENCOUNTER_WAIT** — detect wild battle start via `wBattleType` or `wIsInBattle` flag
3. **SHINY_CHECK** — read enemy DVs, determine shiny
4. **SHINY_ACTION** — if shiny: save state, throw balls. If not: RUN
5. **HEAL_CHECK** — monitor party HP, return to nearest Pokecenter when low
6. **BOX_CHECK** — monitor party/box capacity, deposit if needed

---

## 🔲 Phase 4 — Optional: Vision Layer

If RAM-only proves unreliable for screen classification (e.g., distinguishing evolution animation from dialog), add minimal vision.

- [ ] Screen capture at key decision points
- [ ] Lightweight classifier (template matching or small model)
- [ ] Only used as fallback when RAM state is ambiguous

---

## 🔲 Phase 5 — Optional: Server + Dashboard

Reuse the existing FastAPI server and WebSocket dashboard for live viewing.

- [ ] Wire Gen 2 reader into `/state` endpoint
- [ ] Wire shiny detection into `/state` 
- [ ] Dashboard live-view of farming session
- [ ] Deployment to woothoot (bluefin, GUI PyBoy window)

---

## Architecture Decisions

- **Emulator layer:** Keep existing `emulator.py` ABC + PyBoyEmulator as-is
- **Server layer:** Keep existing FastAPI server, add gold reader routing
- **Memory layer:** New `memory/gold.py` (GoldReader), completely separate from `red.py`
- **Farming loop:** Synchronous, tick-based, no async. Fast as PyBoy can run.
- **Shiny detection:** RAM-only. DV decoding from 2 bytes at `wEnemyMonDVs`.