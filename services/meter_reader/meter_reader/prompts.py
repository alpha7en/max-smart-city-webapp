"""Prompts for the vision model.

The prompt is assembled from shared parts (intro, reading rules, identification, problems, JSON schema).
Without a meter type it is the universal prompt (tuned on the water dataset); with a known type a
"Known meter type" block replaces the type-detection section and the reading rules become type-specific.
"""
from typing import Optional

METER_TYPES = ("cold_water", "hot_water", "electricity", "gas", "heat")

# Fixed issue codes: this is a contract with the bot, do not rename.
ISSUE_CODES = (
    "no_meter",
    "wrong_type",
    "digits_not_visible",
    "blurry",
    "glare",
    "too_dark",
    "angle",
    "partially_covered",
    "multiple_meters",
    "serial_not_visible",
    "display_off",
    "other",
)

SYSTEM_PROMPT = (
    "You are an expert at reading Russian utility meters (water, electricity, gas, heat) from photos. "
    "You read digits precisely, one drum/segment at a time, and never invent digits you cannot see. "
    "You always answer with a single JSON object and nothing else."
)

_INTRO = (
    "Analyze the utility meter in the photo. "
    "The photo may be rotated, blurry, taken at an angle or with glare."
)

# ---- 1. Meter type -------------------------------------------------------------------------

_TYPE_DETECT = """1. Meter type:
   - "hot_water": water meter with red body/ring/markings, "90°"/"ГВ"/"горячая" labels, red "Г" in the model name (e.g. СГВ, ВСГ, СВК-15Г).
   - "cold_water": water meter with blue body/ring/markings, "30°"/"40°"/"ХВ"/"холодная" labels, "Х" in the model name (e.g. СХВ, ВСХ).
   - "electricity": kWh units, LCD or drum display, labels like "Меркурий", "Энергомера", "Нева", "кВт·ч".
   - "gas": m³ drum counter on a gas meter body, labels like "BK-G4", "СГМН", "Гранд", "газ".
   - "heat": heat meter / heat cost allocator with LCD in Гкал, MWh or GJ.
   - "unknown": no meter or cannot tell.
   Water-meter color markings: red means hot, blue means cold. Some universal meters have no color; decide by the most reliable cue."""

_TYPE_NAMES = {
    "cold_water": "cold water meter (ХВС)",
    "hot_water": "hot water meter (ГВС)",
    "electricity": "electricity meter",
    "gas": "gas meter",
    "heat": "heat meter (теплосчётчик) or heat cost allocator",
}

_KNOWN_TYPE = """1. Meter type: the user already selected it, set "meter_type": "{t}".
   - Change it ONLY if the photo clearly shows a different kind of meter; then set the type you see and add "wrong_type" to "issues".{extra}
   - No meter in the photo: "meter_type": "unknown", "readable": false, issue "no_meter"."""

_WATER_TYPE_EXTRA = (
    "\n   - Hot and cold water meters are built the same. Do NOT re-decide hot vs cold from body/ring color "
    "(universal meters have none). Report \"wrong_type\" for water only on explicit opposite text "
    "(\"ГВ\"/\"горячая\" vs \"ХВ\"/\"холодная\") or if it is not a water meter at all."
)

# ---- 2. Reading ------------------------------------------------------------------------------

_DRUM_LIST = """   - Go through the drums strictly left to right and list EVERY drum in "drums" with its digit and color ("black" or "red"). Do not skip leading zeros and do not skip the last red drum, even if it is partly covered, dim or rolling.
   - If a drum is between two digits (rolling), use the lower digit, except when it has visibly almost completed the transition."""

_WATER_GAS_DRUMS = (
    "   - Water/gas meters have a row of rotating drums. Whole cubic meters are on black drums (or white digits "
    "on black), fractions are on red drums (or digits in red-framed windows). Typical Russian water meters have "
    "5 black drums followed by 3 red drums, but count what you actually see.\n"
    + _DRUM_LIST
    + "\n   - Ignore the small round dials/pointers, the star/rotor indicator and any numbers printed on the dial "
    "face (serial, year, Qn, class): read only the drum counter."
)

_ELECTRICITY_UNIVERSAL = (
    "   - For electricity meters: list the displayed kWh digits in \"drums\" (use \"black\" for whole kWh, "
    "\"red\" for digits after the decimal point); if the display shows a tariff (T1/T2/T3) put it in \"tariff\"."
)

_READING_UNIVERSAL = "2. Reading of the main counter:\n" + _WATER_GAS_DRUMS + "\n" + _ELECTRICITY_UNIVERSAL

_READING_WATER = (
    "2. Reading of the main counter (m³):\n"
    + _WATER_GAS_DRUMS
    + "\n   - Some meters have only 2 red drums plus a pointer dial for liters: list only the drums, "
    "never add a digit from the pointer."
    + "\n   - Do NOT read: serial number, year, Qn/Q3, class, pressure/temperature marks."
)

_READING_GAS = (
    "2. Reading of the main counter (m³):\n"
    "   - Gas meters (BK-G4, СГМН, Гранд, Elster...) have a drum counter: whole m³ on black drums (usually 5), "
    "fractions on red drums or on digits with white background in a red frame, often after a comma "
    "(BK-G4: last 3 drums red). Mark every fraction digit \"red\".\n"
    + _DRUM_LIST
    + "\n   - Do NOT read: temperature, pressure (kPa, mbar), Qmax/Qmin, year, serial number, electronic pulse "
    "display settings."
)

_READING_ELECTRICITY = (
    "2. Reading of the main counter (kWh):\n"
    "   - LCD or drum display with usually 5-6 whole kWh digits and 1-2 digits after the decimal point. On LCD "
    "the decimals are separated by a point or shown in another color/frame; on drums they are red.\n"
    "   - List every displayed digit left to right in \"drums\": \"black\" for whole kWh, \"red\" for digits "
    "after the decimal point. Keep leading zeros.\n"
    "   - Put the tariff shown next to the value into \"tariff\" (\"T1\", \"T2\", \"T3\"; \"total\" for a sum/Σ "
    "value); null if no tariff indicator is shown.{tariffs}\n"
    "   - Do NOT read: date, time, voltage (V), current (A), power (kW), frequency, screen/parameter code "
    "(e.g. \"1.8.1\", \"C 01\"), meter constant (imp/kWh), serial number."
)

_TARIFFS_MULTI = (
    "\n   - This is a {n}-tariff meter: the display cycles T1..T{n} (sometimes a total). Read the value shown "
    "now and its tariff indicator. If no tariff indicator is visible, set \"tariff\": null and add issue "
    "\"other\" with a note that the tariff is not visible."
)

_READING_HEAT = (
    "2. Reading of the main counter (heat energy):\n"
    "   - LCD heat meter / allocator. Its screen cycles through parameters; read ONLY the accumulated heat "
    "energy (Q, \"Гкал\"/Gcal, sometimes MWh or GJ) and set \"unit\" accordingly.\n"
    "   - List every displayed digit left to right in \"drums\": \"black\" before the decimal point, \"red\" "
    "after it. Keep leading zeros.\n"
    "   - Do NOT read: volume/flow (m³, m³/h), temperatures (°C, t1/t2, Δt), power (kW), operating time/hours, "
    "date, time, error codes, serial number.\n"
    "   - If the screen shows another parameter (not energy), set \"readable\": false, \"drums\": [] and "
    "issue \"other\" with a note which parameter is shown."
)

# One line per type: what the serial number looks like and what it is NOT (added to the reading block).
_SERIAL_BY_TYPE = {
    "water": (
        "   - \"serial_number\": usually 8 digits, often with a 2-digit year prefix (\"18-123456\"), sometimes "
        "manufacturer letters; it is NOT the year of manufacture, GOST, Qn/Q3, DN, class or a seal number."
    ),
    "electricity": (
        "   - \"serial_number\": a long digit string near the barcode (Меркурий 8 digits, Энергомера 12-15, "
        "Нева 8+); it is NOT the GOST, accuracy class, imp/kWh constant, V/A rating, year or a seal number."
    ),
    "gas": (
        "   - \"serial_number\": usually 7-8 digits, sometimes with letters, on the nameplate; it is NOT the model "
        "(BK-G4), Qmax/Qmin, year, GOST or a seal number."
    ),
    "heat": (
        "   - \"serial_number\": usually 6-10 digits on the case or LCD; it is NOT the year, DN, flow rating, "
        "GOST or a seal number."
    ),
}

_READING_BY_TYPE = {
    "cold_water": _READING_WATER,
    "hot_water": _READING_WATER,
    "gas": _READING_GAS,
    "electricity": _READING_ELECTRICITY,
    "heat": _READING_HEAT,
}

# ---- 3. Identification -----------------------------------------------------------------------

_IDENTIFICATION = """3. Identification: manufacturer/brand (e.g. "Норма", "Бетар", "Экомера", "Valtec", "Itelma", "Декаст", "Пульсар", "Меркурий"), model designation (e.g. "СВК-15Г", "СГВ-15", "ВСКМ 90-15"), and serial number if legible. Keep original Cyrillic spelling as printed. Use null for anything not legible."""

# ---- 4. Problems -----------------------------------------------------------------------------

_PROBLEMS = """4. Problems (be honest, never guess):
   - "readable": true if you can read every digit of the main reading. A rolling drum, minor glare, blur or angle that still lets you tell the digits: readable true (you may still list the issue).
   - If some digits of the reading are truly not visible (hidden, cut off, unreadably blurry, display off): "readable": false, "drums": [] (do not invent digits), and list the causes.
   - "issues": codes from this list only: "no_meter", "wrong_type", "digits_not_visible" (digits hidden or cut off), "blurry", "glare", "too_dark", "angle", "partially_covered", "multiple_meters", "serial_not_visible" (does not affect readable), "display_off" (LCD blank or not showing the reading), "other". Empty list if all is fine.
   - "issue_note": short explanation in Russian for the user (max 120 chars), e.g. "Блик закрывает последние цифры"; null if no issues."""

# ---- JSON schema -----------------------------------------------------------------------------

_SCHEMA = """Return JSON exactly in this shape:
{
  "meter_type": "hot_water" | "cold_water" | "electricity" | "gas" | "heat" | "unknown",
  "type_evidence": "short reason for the type",
  "drums": [{"digit": "0", "color": "black"}, ...],
  "unit": "m3" | "kWh" | "Gcal" | "MWh" | "GJ",
  "tariff": string or null,
  "brand": string or null,
  "model": string or null,
  "serial_number": string or null,
  "readable": true | false,
  "issues": [string, ...],
  "issue_note": string or null,
  "confidence": number from 0 to 1 for the reading
}"""


def _clamp_tariffs(tariffs: Optional[int]) -> int:
    try:
        return max(1, min(3, int(tariffs))) if tariffs is not None else 1
    except (TypeError, ValueError):
        return 1


def build_prompt(meter_type: Optional[str] = None, tariffs: Optional[int] = None) -> str:
    """Assemble the user prompt. Unknown/None meter_type gives the universal prompt."""
    if meter_type not in METER_TYPES:
        parts = [_INTRO, _TYPE_DETECT, _READING_UNIVERSAL]
    else:
        extra = _WATER_TYPE_EXTRA if meter_type in ("cold_water", "hot_water") else ""
        reading = _READING_BY_TYPE[meter_type]
        serial = _SERIAL_BY_TYPE["water" if meter_type in ("cold_water", "hot_water") else meter_type]
        reading = reading + "\n" + serial
        if meter_type == "electricity":
            n = _clamp_tariffs(tariffs)
            reading = reading.replace("{tariffs}", _TARIFFS_MULTI.format(n=n) if n > 1 else "")
        parts = [
            _INTRO,
            f"Known meter type: {_TYPE_NAMES[meter_type]}.",
            _KNOWN_TYPE.format(t=meter_type, extra=extra),
            reading,
        ]
    parts += [_IDENTIFICATION, _PROBLEMS, _SCHEMA]
    return "\n\n".join(parts)


# Universal prompt (no meter type given), kept for backward compatibility.
RECOGNIZE_PROMPT = build_prompt()
