"""Constanten voor de ETAPpro integratie.

Registeradressen zijn gebaseerd op de officiële documentatie:
https://github.com/EV-Chargeking/etap-modbus
"""

DOMAIN = "etappro"
DEFAULT_PORT = 502
SCAN_INTERVAL_FAST = 10    # seconden — stroom, vermogen, modus
SCAN_INTERVAL_MEDIUM = 30  # seconden — spanning, temperatuur
SCAN_INTERVAL_SLOW = 60    # seconden — energieteller, max stroom

# ── Energie-meter registers (slave 1) ───────────────────────
REG_VOLTAGE_L1      = 306   # FLOAT32, 2 regs — Spanning L1-N (V)
REG_VOLTAGE_L2      = 308   # FLOAT32, 2 regs — Spanning L2-N (V)
REG_VOLTAGE_L3      = 310   # FLOAT32, 2 regs — Spanning L3-N (V)
REG_CURRENT_L1      = 320   # FLOAT32, 2 regs — Stroom L1 (A)
REG_CURRENT_L2      = 322   # FLOAT32, 2 regs — Stroom L2 (A)
REG_CURRENT_L3      = 324   # FLOAT32, 2 regs — Stroom L3 (A)
REG_CURRENT_SUM     = 326   # FLOAT32, 2 regs — Totale stroom (A)
REG_POWER_L1        = 338   # FLOAT32, 2 regs — Actief vermogen L1 (W)
REG_POWER_L2        = 340   # FLOAT32, 2 regs — Actief vermogen L2 (W)
REG_POWER_L3        = 342   # FLOAT32, 2 regs — Actief vermogen L3 (W)
REG_POWER_SUM       = 344   # FLOAT32, 2 regs — Totaal vermogen (W)
REG_ENERGY          = 374   # FLOAT64, 4 regs — Energieteller (Wh)

# ── Status registers (slave 1) ───────────────────────────────
REG_MAX_CURRENT     = 1100  # FLOAT32, 2 regs — Hardware max stroom (A)
REG_TEMP_BOARD      = 1102  # FLOAT32, 2 regs — Boardtemperatuur (°C)
REG_TEMP_EV_PLUG    = 1106  # FLOAT32, 2 regs — Stekker auto-zijde (°C)
REG_TEMP_GRID_PLUG  = 1108  # FLOAT32, 2 regs — Stekker net-zijde (°C)
REG_AVAILABILITY    = 1200  # INT16,   1 reg  — Beschikbaarheid (0/1)
REG_MODE            = 1201  # STRING,  5 regs — IEC 61851 modus (bijv. "C2")
REG_APPLIED_CURRENT = 1206  # FLOAT32, 2 regs — Toegepaste max stroom (A)
REG_SETPOINT        = 1210  # FLOAT32, 2 regs — Laadstroom setpoint R/W (A)
REG_SETPOINT_STATUS = 1214  # UINT16,  1 reg  — Setpoint actief (0/1)
REG_PHASES          = 1215  # UINT16,  1 reg  — Laadphasen R/W (1 of 3)
REG_STARTED_BY      = 1236  # STRING,  5 regs — Gestart door (RFID/APP/...)

# ── IEC 61851 modus → leesbare status ───────────────────────
MODE_LABELS = {
    "A": "Vrij",
    "A1": "Vrij",
    "A2": "Vrij",
    "B": "Auto verbonden",
    "B1": "Auto verbonden",
    "B2": "Auto verbonden",
    "C": "Aan het laden",
    "C1": "Aan het laden",
    "C2": "Aan het laden",
    "E": "Fout",
    "F": "Buiten dienst",
}

# Minimum laadstroom (wettelijk vereist voor IEC 61851 Type 2)
MIN_CURRENT_A = 6.0
