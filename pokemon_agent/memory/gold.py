"""Pokemon Gold / Silver (USA) memory reader.

All RAM addresses come from the *pokegold* decomp project
(https://github.com/pret/pokegold), cross-referenced against pokecrystal
(https://github.com/pret/pokecrystal). This module targets the Gold/Silver
US release; Crystal uses slightly different offsets.

Gen 2 changes vs Gen 1:
  - Party slot size is 0x30 (48) bytes, not 44.
  - Map is identified by (group, number) instead of a single byte.
  - Two badge bytes (Johto + Kanto) instead of one.
  - Bag is split into pockets (items, key items, balls, TM/HM).
  - DVs are stored as 2 bytes per Pokemon: byte0=AAAA DDDD, byte1=SSSS PPPP
    where A/D/S/P are the 4-bit Attack/Defense/Speed/Special DVs.

Gen 2 text encoding is mostly compatible with Gen 1 (terminator 0x50,
uppercase 0x80-0x99, lowercase 0xA0-0xB9, digits 0xF6-0xFF).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pokemon_agent.emulator import Emulator
from pokemon_agent.memory.reader import GameMemoryReader


# ===================================================================
# RAM addresses (WRAM) — Pokemon Gold (US)
# ===================================================================
# All addresses below were verified against the pret/pokegold symbols
# branch (pokegold.sym). Crystal uses different offsets — do not
# cross-reference pokecrystal here.

# -- Player block (wPlayerData) --
ADDR_PLAYER_ID     = 0xD1A1   # 2 bytes BE (wPlayerID)
ADDR_PLAYER_NAME   = 0xD1A3   # 11 bytes (wPlayerName)
ADDR_RIVAL_NAME    = 0xD1B9   # 11 bytes (wRivalName)
ADDR_MONEY         = 0xD573   # 3 bytes BCD BE (wMoney)
ADDR_JOHTO_BADGES  = 0xD57C   # 1 byte bitmask (wJohtoBadges)
ADDR_KANTO_BADGES  = 0xD57D   # 1 byte bitmask (wKantoBadges)

# -- Map / position --
ADDR_MAP_GROUP     = 0xDA00   # current map group (wMapGroup)
ADDR_MAP_NUMBER    = 0xDA01   # current map number within group (wMapNumber)
ADDR_PLAYER_Y      = 0xDA02   # wYCoord
ADDR_PLAYER_X      = 0xDA03   # wXCoord
ADDR_PLAYER_DIR    = 0xD205   # wPlayerDirection (0=down,4=up,8=left,0xC=right)

# -- Party --
ADDR_PARTY_COUNT   = 0xDA22   # wPartyCount
ADDR_PARTY_SPECIES = 0xDA23   # 6 bytes + 0xFF terminator (wPartySpecies)
ADDR_PARTY_MON1    = 0xDA2A   # 48 bytes × 6 (wPartyMon1)
ADDR_PARTY_OTS     = 0xDB4A   # 11 bytes × 6 (wPartyMonOTs)
ADDR_PARTY_NICKS   = 0xDB8C   # 11 bytes × 6 (wPartyMonNicknames)

PARTY_MON_SIZE     = 0x30     # 48 bytes per party slot
NAME_SIZE          = 11

# Offsets within a PartyMon struct (size 0x30):
#   0x00  species
#   0x01  held item
#   0x02-0x05  moves
#   0x06-0x07  OT ID (BE)
#   0x08-0x0A  experience (3 bytes BE)
#   0x0B-0x0C  HP EV
#   0x0D-0x0E  Atk EV
#   0x0F-0x10  Def EV
#   0x11-0x12  Spd EV
#   0x13-0x14  Spc EV
#   0x15-0x16  DVs (2 bytes)
#   0x17-0x1A  PP (4 bytes)
#   0x1B       happiness / friendship
#   0x1C       pokerus
#   0x1D-0x1E  caught data (2 bytes)
#   0x1F       level
#   0x20       status condition
#   0x21       unused
#   0x22-0x23  current HP (BE)
#   0x24-0x25  max HP (BE)
#   0x26-0x27  attack (BE)
#   0x28-0x29  defense (BE)
#   0x2A-0x2B  speed (BE)
#   0x2C-0x2D  spc attack (BE)
#   0x2E-0x2F  spc defense (BE)
PARTYMON_OFF_SPECIES   = 0x00
PARTYMON_OFF_ITEM      = 0x01
PARTYMON_OFF_MOVES     = 0x02
PARTYMON_OFF_OT_ID     = 0x06
PARTYMON_OFF_EXP       = 0x08
PARTYMON_OFF_DVS       = 0x15
PARTYMON_OFF_PP        = 0x17
PARTYMON_OFF_HAPPINESS = 0x1B
PARTYMON_OFF_LEVEL     = 0x1F
PARTYMON_OFF_STATUS    = 0x20
PARTYMON_OFF_HP        = 0x22
PARTYMON_OFF_MAX_HP    = 0x24
PARTYMON_OFF_ATK       = 0x26
PARTYMON_OFF_DEF       = 0x28
PARTYMON_OFF_SPD       = 0x2A
PARTYMON_OFF_SPC_ATK   = 0x2C
PARTYMON_OFF_SPC_DEF   = 0x2E

# -- Bag (Item Pocket only — Gen 2 has separate balls/key items/TM pockets) --
ADDR_NUM_ITEMS     = 0xD5B7   # wNumItems
ADDR_ITEMS         = 0xD5B8   # (item, qty) pairs, 0xFF terminator

ADDR_NUM_KEY_ITEMS = 0xD5E1   # wNumKeyItems (key items have no qty)
ADDR_KEY_ITEMS     = 0xD5E2

ADDR_NUM_BALLS     = 0xD5FC   # wNumBalls
ADDR_BALLS         = 0xD5FD

# -- Battle --
# wBattleMode: 0=none, 1=wild, 2=trainer
# wBattleType: 0=normal, 1=can_lose, 2=debug, ... up to ~10 (legendary/shiny/etc)
ADDR_BATTLE_MODE   = 0xD116
ADDR_BATTLE_TYPE   = 0xD119

# BattleMon (active enemy in battle) struct: ~28 bytes.
# Layout (BattleMon, no exp/EVs):
#   0x00  species
#   0x01  held item
#   0x02-0x05 moves
#   0x06-0x07 DVs (2 bytes)
#   0x08-0x0B PP
#   0x0C  happiness
#   0x0D  level
#   0x0E  status
#   0x0F  unused
#   0x10-0x11 current HP (BE)
#   0x12-0x13 max HP (BE)
#   0x14-0x15 attack (BE)
#   0x16-0x17 defense (BE)
#   0x18-0x19 speed (BE)
#   0x1A-0x1B spc atk (BE)
#   0x1C-0x1D spc def (BE)
ADDR_ENEMY_MON           = 0xD0EF   # wEnemyMon (BattleMon struct base)
ENEMY_MON_OFF_SPECIES    = 0x00
ENEMY_MON_OFF_ITEM       = 0x01
ENEMY_MON_OFF_MOVES      = 0x02
ENEMY_MON_OFF_DVS        = 0x06
ENEMY_MON_OFF_LEVEL      = 0x0D
ENEMY_MON_OFF_STATUS     = 0x0E
ENEMY_MON_OFF_HP         = 0x10
ENEMY_MON_OFF_MAX_HP     = 0x12

# Convenience addresses derived from base
ADDR_ENEMY_SPECIES = ADDR_ENEMY_MON + ENEMY_MON_OFF_SPECIES
ADDR_ENEMY_DVS     = ADDR_ENEMY_MON + ENEMY_MON_OFF_DVS
ADDR_ENEMY_LEVEL   = ADDR_ENEMY_MON + ENEMY_MON_OFF_LEVEL
ADDR_ENEMY_HP      = ADDR_ENEMY_MON + ENEMY_MON_OFF_HP
ADDR_ENEMY_MAX_HP  = ADDR_ENEMY_MON + ENEMY_MON_OFF_MAX_HP
ADDR_ENEMY_STATUS  = ADDR_ENEMY_MON + ENEMY_MON_OFF_STATUS

# -- Dialog / input lock --
ADDR_TEXT_DELAY    = 0xCEE9   # wTextDelayFrames — nonzero while text scrolling
# wJoypadDisable in Gold: bits 4, 6, 7 can disable input (faint anim, SGB
# transfer, scripted). Any nonzero value means joypad is locked.
ADDR_JOY_LOCK      = 0xD8BA   # wJoypadDisable

# -- Pokedex --
ADDR_DEX_OWNED     = 0xDBE4   # 32 bytes (251 species + padding) (wPokedexCaught)
ADDR_DEX_SEEN      = 0xDC04   # wPokedexSeen

# -- Play time --
# wGameTimeHours is 2 bytes BE. The "wGameTimeCap" byte sits 1 before it.
ADDR_PLAYTIME_H    = 0xD1EB   # 2 bytes BE (wGameTimeHours)
ADDR_PLAYTIME_M    = 0xD1ED   # 1 byte (wGameTimeMinutes)
ADDR_PLAYTIME_S    = 0xD1EE   # 1 byte (wGameTimeSeconds)
ADDR_PLAYTIME_F    = 0xD1EF   # 1 byte (wGameTimeFrames)

# -- Game state flag --
# Gold has no exact analog of Crystal's wGameLogicPaused at 0xD0EB.
# wBattleMode is the most reliable "are we in battle vs overworld" marker.
ADDR_GAME_STATE    = 0xD116   # wBattleMode (0=overworld/menu, 1=wild, 2=trainer)


# ===================================================================
# Gen 2 text encoding — mostly inherited from Gen 1
# ===================================================================

def _build_encoding_table() -> Dict[int, str]:
    """Build the Gen-2 text lookup table.

    Identical to Gen 1 for the basic alphabet/digits; the
    terminator is still 0x50.
    """
    t: Dict[int, str] = {}
    for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        t[0x80 + i] = c
    for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
        t[0xA0 + i] = c
    for i, c in enumerate("0123456789"):
        t[0xF6 + i] = c
    t[0x7F] = " "
    t[0xE0] = "'"
    t[0xE1] = "P"
    t[0xE2] = "M"
    t[0xE3] = "-"
    t[0xE6] = "?"
    t[0xE7] = "!"
    t[0xE8] = "."
    t[0xF0] = "¥"
    t[0xF1] = "×"
    t[0xF3] = "/"
    t[0xF4] = ","
    t[0xF5] = "♀"
    t[0x50] = ""
    t[0x4F] = "\n"
    t[0x51] = "\n"
    t[0x55] = "\n"
    return t

GEN2_ENCODING: Dict[int, str] = _build_encoding_table()


# ===================================================================
# Name tables — Gen 2 species (251 Pokemon)
# ===================================================================

# Gen 1 species 1-151 retain their indices in Gen 2.  Gen 2 adds 152-251.
SPECIES_NAMES: Dict[int, str] = {
    0: "MissingNo.",
    # --- Gen 1 (1-151) ---
    1: "Bulbasaur", 2: "Ivysaur", 3: "Venusaur",
    4: "Charmander", 5: "Charmeleon", 6: "Charizard",
    7: "Squirtle", 8: "Wartortle", 9: "Blastoise",
    10: "Caterpie", 11: "Metapod", 12: "Butterfree",
    13: "Weedle", 14: "Kakuna", 15: "Beedrill",
    16: "Pidgey", 17: "Pidgeotto", 18: "Pidgeot",
    19: "Rattata", 20: "Raticate",
    21: "Spearow", 22: "Fearow",
    23: "Ekans", 24: "Arbok",
    25: "Pikachu", 26: "Raichu",
    27: "Sandshrew", 28: "Sandslash",
    29: "Nidoran♀", 30: "Nidorina", 31: "Nidoqueen",
    32: "Nidoran♂", 33: "Nidorino", 34: "Nidoking",
    35: "Clefairy", 36: "Clefable",
    37: "Vulpix", 38: "Ninetales",
    39: "Jigglypuff", 40: "Wigglytuff",
    41: "Zubat", 42: "Golbat",
    43: "Oddish", 44: "Gloom", 45: "Vileplume",
    46: "Paras", 47: "Parasect",
    48: "Venonat", 49: "Venomoth",
    50: "Diglett", 51: "Dugtrio",
    52: "Meowth", 53: "Persian",
    54: "Psyduck", 55: "Golduck",
    56: "Mankey", 57: "Primeape",
    58: "Growlithe", 59: "Arcanine",
    60: "Poliwag", 61: "Poliwhirl", 62: "Poliwrath",
    63: "Abra", 64: "Kadabra", 65: "Alakazam",
    66: "Machop", 67: "Machoke", 68: "Machamp",
    69: "Bellsprout", 70: "Weepinbell", 71: "Victreebel",
    72: "Tentacool", 73: "Tentacruel",
    74: "Geodude", 75: "Graveler", 76: "Golem",
    77: "Ponyta", 78: "Rapidash",
    79: "Slowpoke", 80: "Slowbro",
    81: "Magnemite", 82: "Magneton",
    83: "Farfetch'd",
    84: "Doduo", 85: "Dodrio",
    86: "Seel", 87: "Dewgong",
    88: "Grimer", 89: "Muk",
    90: "Shellder", 91: "Cloyster",
    92: "Gastly", 93: "Haunter", 94: "Gengar",
    95: "Onix",
    96: "Drowzee", 97: "Hypno",
    98: "Krabby", 99: "Kingler",
    100: "Voltorb", 101: "Electrode",
    102: "Exeggcute", 103: "Exeggutor",
    104: "Cubone", 105: "Marowak",
    106: "Hitmonlee", 107: "Hitmonchan",
    108: "Lickitung",
    109: "Koffing", 110: "Weezing",
    111: "Rhyhorn", 112: "Rhydon",
    113: "Chansey", 114: "Tangela", 115: "Kangaskhan",
    116: "Horsea", 117: "Seadra",
    118: "Goldeen", 119: "Seaking",
    120: "Staryu", 121: "Starmie",
    122: "Mr. Mime", 123: "Scyther",
    124: "Jynx", 125: "Electabuzz", 126: "Magmar",
    127: "Pinsir", 128: "Tauros",
    129: "Magikarp", 130: "Gyarados",
    131: "Lapras", 132: "Ditto",
    133: "Eevee", 134: "Vaporeon", 135: "Jolteon", 136: "Flareon",
    137: "Porygon",
    138: "Omanyte", 139: "Omastar",
    140: "Kabuto", 141: "Kabutops",
    142: "Aerodactyl", 143: "Snorlax",
    144: "Articuno", 145: "Zapdos", 146: "Moltres",
    147: "Dratini", 148: "Dragonair", 149: "Dragonite",
    150: "Mewtwo", 151: "Mew",
    # --- Gen 2 (152-251) ---
    152: "Chikorita", 153: "Bayleef", 154: "Meganium",
    155: "Cyndaquil", 156: "Quilava", 157: "Typhlosion",
    158: "Totodile", 159: "Croconaw", 160: "Feraligatr",
    161: "Sentret", 162: "Furret",
    163: "Hoothoot", 164: "Noctowl",
    165: "Ledyba", 166: "Ledian",
    167: "Spinarak", 168: "Ariados",
    169: "Crobat",
    170: "Chinchou", 171: "Lanturn",
    172: "Pichu", 173: "Cleffa", 174: "Igglybuff",
    175: "Togepi", 176: "Togetic",
    177: "Natu", 178: "Xatu",
    179: "Mareep", 180: "Flaaffy", 181: "Ampharos",
    182: "Bellossom",
    183: "Marill", 184: "Azumarill",
    185: "Sudowoodo",
    186: "Politoed",
    187: "Hoppip", 188: "Skiploom", 189: "Jumpluff",
    190: "Aipom",
    191: "Sunkern", 192: "Sunflora",
    193: "Yanma",
    194: "Wooper", 195: "Quagsire",
    196: "Espeon", 197: "Umbreon",
    198: "Murkrow",
    199: "Slowking",
    200: "Misdreavus",
    201: "Unown",
    202: "Wobbuffet",
    203: "Girafarig",
    204: "Pineco", 205: "Forretress",
    206: "Dunsparce",
    207: "Gligar",
    208: "Steelix",
    209: "Snubbull", 210: "Granbull",
    211: "Qwilfish",
    212: "Scizor",
    213: "Shuckle",
    214: "Heracross",
    215: "Sneasel",
    216: "Teddiursa", 217: "Ursaring",
    218: "Slugma", 219: "Magcargo",
    220: "Swinub", 221: "Piloswine",
    222: "Corsola",
    223: "Remoraid", 224: "Octillery",
    225: "Delibird",
    226: "Mantine",
    227: "Skarmory",
    228: "Houndour", 229: "Houndoom",
    230: "Kingdra",
    231: "Phanpy", 232: "Donphan",
    233: "Porygon2",
    234: "Stantler",
    235: "Smeargle",
    236: "Tyrogue",
    237: "Hitmontop",
    238: "Smoochum",
    239: "Elekid",
    240: "Magby",
    241: "Miltank",
    242: "Blissey",
    243: "Raikou", 244: "Entei", 245: "Suicune",
    246: "Larvitar", 247: "Pupitar", 248: "Tyranitar",
    249: "Lugia", 250: "Ho-Oh",
    251: "Celebi",
}


TYPE_NAMES: Dict[int, str] = {
    0: "Normal", 1: "Fighting", 2: "Flying", 3: "Poison",
    4: "Ground", 5: "Rock", 6: "Bug", 7: "Ghost", 8: "Steel",
    20: "Fire", 21: "Water", 22: "Grass", 23: "Electric",
    24: "Psychic", 25: "Ice", 26: "Dragon", 27: "Dark",
}


FACING_NAMES: Dict[int, str] = {
    0x00: "down",
    0x04: "up",
    0x08: "left",
    0x0C: "right",
}


JOHTO_BADGE_NAMES = [
    "Zephyr", "Hive", "Plain", "Fog",
    "Storm", "Mineral", "Glacier", "Rising",
]
KANTO_BADGE_NAMES = [
    "Boulder", "Cascade", "Thunder", "Rainbow",
    "Soul", "Marsh", "Volcano", "Earth",
]


# Status byte layout: bits 0-2 = sleep counter, 3=psn, 4=brn, 5=frz, 6=par
def _decode_status(status_byte: int) -> str:
    if status_byte == 0:
        return "OK"
    parts: List[str] = []
    sleep = status_byte & 0x07
    if sleep:
        parts.append(f"SLP({sleep})")
    if status_byte & 0x08:
        parts.append("PSN")
    if status_byte & 0x10:
        parts.append("BRN")
    if status_byte & 0x20:
        parts.append("FRZ")
    if status_byte & 0x40:
        parts.append("PAR")
    return "/".join(parts) if parts else "OK"


# ===================================================================
# Reader implementation
# ===================================================================

class GoldReader(GameMemoryReader):
    """Memory reader for *Pokemon Gold* and *Pokemon Silver* (USA).

    Address constants come from the pret/pokegold disassembly. Crystal
    uses slightly different offsets; if support is needed, subclass
    this and override the address constants.
    """

    @property
    def game_name(self) -> str:
        return "Pokemon Gold/Silver (USA)"

    # -- helpers ------------------------------------------------------------

    def _decode_text(self, addr: int, max_len: int = NAME_SIZE) -> str:
        return self.read_string(addr, max_len, GEN2_ENCODING, terminator=0x50)

    def _read_party_mon(self, base: int, nick_addr: int) -> Dict[str, Any]:
        """Parse a 48-byte party Pokemon struct at *base*."""
        data = self.emu.read_range(base, PARTY_MON_SIZE)
        species_id = data[PARTYMON_OFF_SPECIES]
        species_name = SPECIES_NAMES.get(species_id, f"???({species_id})")
        nickname = self._decode_text(nick_addr, NAME_SIZE)

        moves: List[Dict[str, Any]] = []
        for i in range(4):
            mid = data[PARTYMON_OFF_MOVES + i]
            if mid != 0:
                pp_byte = data[PARTYMON_OFF_PP + i]
                moves.append({
                    "id": mid,
                    "pp": pp_byte & 0x3F,
                    "pp_up": (pp_byte >> 6) & 0x03,
                })

        dvs_hi = data[PARTYMON_OFF_DVS]
        dvs_lo = data[PARTYMON_OFF_DVS + 1]
        atk_dv = (dvs_hi >> 4) & 0x0F
        def_dv = dvs_hi & 0x0F
        spd_dv = (dvs_lo >> 4) & 0x0F
        spc_dv = dvs_lo & 0x0F

        return {
            "species_id": species_id,
            "species": species_name,
            "nickname": nickname,
            "level": data[PARTYMON_OFF_LEVEL],
            "held_item": data[PARTYMON_OFF_ITEM],
            "hp": (data[PARTYMON_OFF_HP] << 8) | data[PARTYMON_OFF_HP + 1],
            "max_hp": (data[PARTYMON_OFF_MAX_HP] << 8) | data[PARTYMON_OFF_MAX_HP + 1],
            "status": _decode_status(data[PARTYMON_OFF_STATUS]),
            "moves": moves,
            "stats": {
                "attack":  (data[PARTYMON_OFF_ATK] << 8) | data[PARTYMON_OFF_ATK + 1],
                "defense": (data[PARTYMON_OFF_DEF] << 8) | data[PARTYMON_OFF_DEF + 1],
                "speed":   (data[PARTYMON_OFF_SPD] << 8) | data[PARTYMON_OFF_SPD + 1],
                "spc_atk": (data[PARTYMON_OFF_SPC_ATK] << 8) | data[PARTYMON_OFF_SPC_ATK + 1],
                "spc_def": (data[PARTYMON_OFF_SPC_DEF] << 8) | data[PARTYMON_OFF_SPC_DEF + 1],
            },
            "dvs": {
                "attack": atk_dv,
                "defense": def_dv,
                "speed": spd_dv,
                "special": spc_dv,
            },
            "ot_id": (data[PARTYMON_OFF_OT_ID] << 8) | data[PARTYMON_OFF_OT_ID + 1],
            "happiness": data[PARTYMON_OFF_HAPPINESS],
        }

    # -- public interface ---------------------------------------------------

    def read_player(self) -> Dict[str, Any]:
        name = self._decode_text(ADDR_PLAYER_NAME, NAME_SIZE)
        rival = self._decode_text(ADDR_RIVAL_NAME, NAME_SIZE)
        money = self.read_bcd(ADDR_MONEY, 3)

        johto = self.emu.read_u8(ADDR_JOHTO_BADGES)
        kanto = self.emu.read_u8(ADDR_KANTO_BADGES)
        johto_list = [JOHTO_BADGE_NAMES[i] for i in range(8) if johto & (1 << i)]
        kanto_list = [KANTO_BADGE_NAMES[i] for i in range(8) if kanto & (1 << i)]

        map_y = self.emu.read_u8(ADDR_PLAYER_Y)
        map_x = self.emu.read_u8(ADDR_PLAYER_X)
        facing_byte = self.emu.read_u8(ADDR_PLAYER_DIR)
        facing = FACING_NAMES.get(facing_byte, f"unknown(0x{facing_byte:02X})")

        hours = self.emu.read_u16(ADDR_PLAYTIME_H)
        minutes = self.emu.read_u8(ADDR_PLAYTIME_M)
        seconds = self.emu.read_u8(ADDR_PLAYTIME_S)

        return {
            "name": name,
            "rival_name": rival,
            "money": money,
            "badges": johto_list + kanto_list,
            "johto_badges": johto_list,
            "kanto_badges": kanto_list,
            "badge_count": len(johto_list) + len(kanto_list),
            "position": {"y": map_y, "x": map_x},
            "facing": facing,
            "play_time": f"{hours}:{minutes:02d}:{seconds:02d}",
        }

    def read_party(self) -> List[Dict[str, Any]]:
        count = self.emu.read_u8(ADDR_PARTY_COUNT)
        count = min(count, 6)
        party: List[Dict[str, Any]] = []
        for i in range(count):
            base = ADDR_PARTY_MON1 + i * PARTY_MON_SIZE
            nick_addr = ADDR_PARTY_NICKS + i * NAME_SIZE
            party.append(self._read_party_mon(base, nick_addr))
        return party

    def read_bag(self) -> List[Dict[str, Any]]:
        """Read item-pocket contents (regular usable items)."""
        count = self.emu.read_u8(ADDR_NUM_ITEMS)
        count = min(count, 20)
        items: List[Dict[str, Any]] = []
        for i in range(count):
            item_id = self.emu.read_u8(ADDR_ITEMS + i * 2)
            qty = self.emu.read_u8(ADDR_ITEMS + i * 2 + 1)
            if item_id == 0xFF:
                break
            items.append({"id": item_id, "quantity": qty})
        return items

    def read_balls(self) -> List[Dict[str, Any]]:
        """Read ball-pocket contents."""
        count = self.emu.read_u8(ADDR_NUM_BALLS)
        count = min(count, 12)
        balls: List[Dict[str, Any]] = []
        for i in range(count):
            ball_id = self.emu.read_u8(ADDR_BALLS + i * 2)
            qty = self.emu.read_u8(ADDR_BALLS + i * 2 + 1)
            if ball_id == 0xFF:
                break
            balls.append({"id": ball_id, "quantity": qty})
        return balls

    def read_battle(self) -> Dict[str, Any]:
        battle_mode = self.emu.read_u8(ADDR_BATTLE_MODE)
        battle_type_byte = self.emu.read_u8(ADDR_BATTLE_TYPE)
        type_name = {0: "none", 1: "wild", 2: "trainer"}.get(
            battle_mode, f"unknown({battle_mode})"
        )
        result: Dict[str, Any] = {
            "in_battle": battle_mode != 0,
            "type": type_name,
            "battle_type_flag": battle_type_byte,
        }
        if battle_mode != 0:
            species_id = self.emu.read_u8(ADDR_ENEMY_SPECIES)
            dvs_hi = self.emu.read_u8(ADDR_ENEMY_DVS)
            dvs_lo = self.emu.read_u8(ADDR_ENEMY_DVS + 1)
            level = self.emu.read_u8(ADDR_ENEMY_LEVEL)
            hp = (self.emu.read_u8(ADDR_ENEMY_HP) << 8) | self.emu.read_u8(ADDR_ENEMY_HP + 1)
            max_hp = (self.emu.read_u8(ADDR_ENEMY_MAX_HP) << 8) | self.emu.read_u8(ADDR_ENEMY_MAX_HP + 1)
            status = _decode_status(self.emu.read_u8(ADDR_ENEMY_STATUS))

            atk_dv = (dvs_hi >> 4) & 0x0F
            def_dv = dvs_hi & 0x0F
            spd_dv = (dvs_lo >> 4) & 0x0F
            spc_dv = dvs_lo & 0x0F

            result["enemy"] = {
                "species_id": species_id,
                "species": SPECIES_NAMES.get(species_id, f"???({species_id})"),
                "level": level,
                "hp": hp,
                "max_hp": max_hp,
                "status": status,
                "dvs": {
                    "attack": atk_dv,
                    "defense": def_dv,
                    "speed": spd_dv,
                    "special": spc_dv,
                    "raw": (dvs_hi << 8) | dvs_lo,
                },
            }
        return result

    def read_dialog(self) -> Dict[str, Any]:
        """Return whether a dialog/text-box is currently locking joypad.

        Gold's wJoypadDisable disables input when *any* bit is set
        (bits 4/6/7 in practice — scripted, faint anim, SGB transfer).
        Treat the byte as a simple non-zero flag.
        """
        joy_lock = self.emu.read_u8(ADDR_JOY_LOCK)
        text_delay = self.emu.read_u8(ADDR_TEXT_DELAY)
        active = joy_lock != 0 or text_delay != 0
        return {
            "active": active,
            "joy_lock": joy_lock,
            "text_delay": text_delay,
        }

    def read_map_info(self) -> Dict[str, Any]:
        group = self.emu.read_u8(ADDR_MAP_GROUP)
        number = self.emu.read_u8(ADDR_MAP_NUMBER)
        return {
            "map_id": (group, number),
            "map_group": group,
            "map_number": number,
            "map_name": f"Map {group}.{number}",
        }

    def read_flags(self) -> Dict[str, Any]:
        johto = self.emu.read_u8(ADDR_JOHTO_BADGES)
        kanto = self.emu.read_u8(ADDR_KANTO_BADGES)
        johto_list = [JOHTO_BADGE_NAMES[i] for i in range(8) if johto & (1 << i)]
        kanto_list = [KANTO_BADGE_NAMES[i] for i in range(8) if kanto & (1 << i)]

        owned_bits = self.read_bits(ADDR_DEX_OWNED, 32)
        seen_bits = self.read_bits(ADDR_DEX_SEEN, 32)
        dex_owned = sum(owned_bits[:251])
        dex_seen = sum(seen_bits[:251])

        return {
            "johto_badges": johto_list,
            "kanto_badges": kanto_list,
            "badges": johto_list + kanto_list,
            "badge_count": len(johto_list) + len(kanto_list),
            "pokedex_owned": dex_owned,
            "pokedex_seen": dex_seen,
        }


# Alias used by server.py
PokemonGoldReader = GoldReader
