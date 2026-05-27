# Pokemon Gold — Shiny Farming Bot

**Project:** `projects/pokemon-agent` (forked from NousResearch/pokemon-agent)
**ROM:** `roms/pokemon_gold.gbc`
**Emulator:** PyBoy (headless for development, GUI on woothoot for play)
**Design principle:** RAM-first, deterministic code. Vision used minimally or not at all.

---

## ✅ Phase 1 — Gen 2 Memory Reader *(in progress)*

Create a Gen 2 memory reader (`memory/gold.py`) that maps Pokemon Gold's WRAM addresses for all gameplay-relevant state.

- [x] Fork/clone repo, install deps, verify ROM loads
- [ ] Research Gen 2 memory map from online sources (pret/pokecrystal disassembly)
- [ ] Implement `memory/gold.py`: GoldReader(GameMemoryReader)
- [ ] Add `"gold"` game type to `_detect_game_type()` in `cli.py` and `server.py`
- [ ] Register `.gbc` → `"gold"` mapping (was `"red"`)
- [ ] Wire GoldReader into the state builder
- [ ] Verify: `pokemon-agent info --rom roms/pokemon_gold.gbc` prints accurate game state

### RAM addresses needed (Gen 2 WRAM):

| Region | Address | Notes |
|--------|---------|-------|
| Player X | `0xD05D` | Map-dependent |
| Player Y | `0xD05C` | Map-dependent |
| Map ID | `0xD05B` | Current map number |
| Game state | `0xD056` | Overworld/battle/menu/etc |
| Party count | `0xD163` | Number of Pokemon in party |
| Party species | `0xD164`+ | 1 byte per party member |
| Party levels | `0xD18B`+ | 1 byte per party member |
| Party HP | `0xD16A`+ | 2 bytes per member (big-endian) |
| Party status | `0xD181`+ | 1 byte per member |
| Enemy species | `0xDCB9` | Wild encounter Pokemon ID |
| Enemy DVs | `0xDCB6`-`0xDCB7` | 2 bytes — **shiny check target** |
| Battle type | `0xD230` | Wild/trainer/no battle |
| Dialog flag | `0xD730` | Text box active |

*Note: These addresses come from the Pokemon Crystal disassembly. Gold/Silver memory maps are nearly identical but should be verified against Gold's actual layout.*

---

## ✅ Phase 2 — Shiny Detection *(in progress)*

Pure RAM-based detection using Gen 2's DV system.

- [ ] Implement `pokemon_agent/detect_shiny.py`
- [ ] During wild encounter, read `wEnemyMonDVs` (0xDCB6-0xDCB7)
- [ ] Decode 4 individual DVs from 2 bytes:
  - Attack DV: bits 10-11, 14-15 (across the two bytes)
  - Defense DV: bits 8-9, 12-13
  - Speed DV: bits 2-3, 6-7
  - Special DV: bits 0-1, 4-5
- [ ] Apply shiny formula: Attack DV ∈ {2,3,6,7,10,11,14,15} AND Defense=10 AND Speed=10 AND Special=10
- [ ] Verify against known encounters

**Gen 2 shiny formula (crystal-clear):**
```
shiny = (atk_dv in {2,3,6,7,10,11,14,15}) and (def_dv == 10) and (spd_dv == 10) and (spc_dv == 10)
```

---

## 🔲 Phase 3 — Farming State Machine

Deterministic state machine. No LLM calls on the hot path.

**States:**
1. **NAVIGATE** — walk through encounter grass, avoid obstacles, use Repel if active
2. **ENCOUNTER_WAIT** — detect wild battle start via `wBattleType` or `wIsInBattle` flag
3. **SHINY_CHECK** — read enemy DVs, determine shiny
4. **SHINY_ACTION** — if shiny: save state, throw balls. If not: RUN
5. **HEAL_CHECK** — monitor party HP, return to nearest Pokecenter when low
6. **BOX_CHECK** — monitor party/box capacity, deposit if needed

- [ ] Design state machine in `farm.py`
- [ ] Implement tick-driven loop (no async, no events)
- [ ] Implement NAVIGATE: patrol a route with encounter grass
- [ ] Implement ENCOUNTER_WAIT: detect battle start from RAM
- [ ] Implement SHINY_CHECK: call into detect_shiny.py
- [ ] Implement SHINY_ACTION: throw balls if shiny, Run if not
- [ ] Implement HEAL_CHECK: track party HP across encounters
- [ ] Add action logging (timestamp, encounter count, shiny found)

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