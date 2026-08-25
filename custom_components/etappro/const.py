"""Constants for the ETAPpro integration.

Register addresses are based on the official documentation:
https://github.com/EV-Chargeking/etap-modbus
"""

DOMAIN = "etappro"
VERSION = "1.1.0"
DEFAULT_PORT = 502
SCAN_INTERVAL_FAST = 10    # seconds — current, power, mode
SCAN_INTERVAL_MEDIUM = 30  # seconds — voltage, temperature
SCAN_INTERVAL_SLOW = 60    # seconds — energy counter, max current

# ── Energy meter registers (slave 1) ─────────────────────────
REG_VOLTAGE_L1      = 306   # FLOAT32, 2 regs — Voltage L1-N (V)
REG_VOLTAGE_L2      = 308   # FLOAT32, 2 regs — Voltage L2-N (V)
REG_VOLTAGE_L3      = 310   # FLOAT32, 2 regs — Voltage L3-N (V)
REG_CURRENT_L1      = 320   # FLOAT32, 2 regs — Current L1 (A)
REG_CURRENT_L2      = 322   # FLOAT32, 2 regs — Current L2 (A)
REG_CURRENT_L3      = 324   # FLOAT32, 2 regs — Current L3 (A)
REG_CURRENT_SUM     = 326   # FLOAT32, 2 regs — Total current (A)
REG_POWER_L1        = 338   # FLOAT32, 2 regs — Active power L1 (W)
REG_POWER_L2        = 340   # FLOAT32, 2 regs — Active power L2 (W)
REG_POWER_L3        = 342   # FLOAT32, 2 regs — Active power L3 (W)
REG_POWER_SUM       = 344   # FLOAT32, 2 regs — Total power (W)
REG_ENERGY          = 374   # FLOAT64, 4 regs — Energy counter (Wh)

# ── Status registers (slave 1) ───────────────────────────────
REG_MAX_CURRENT     = 1100  # FLOAT32, 2 regs — Hardware max current (A, see note)
REG_TEMP_BOARD      = 1102  # FLOAT32, 2 regs — Board temperature (°C)
REG_TEMP_EV_PLUG    = 1106  # FLOAT32, 2 regs — Plug, car side (°C)
REG_TEMP_GRID_PLUG  = 1108  # FLOAT32, 2 regs — Plug, grid side (°C)
REG_AVAILABILITY    = 1200  # INT16,   1 reg  — Availability (0/1)
REG_MODE            = 1201  # STRING,  5 regs — IEC 61851 mode (e.g. "C2")
REG_APPLIED_CURRENT = 1206  # FLOAT32, 2 regs — Applied max current (A)
REG_SETPOINT        = 1210  # FLOAT32, 2 regs — Charging current setpoint R/W (A)
REG_SETPOINT_STATUS = 1214  # UINT16,  1 reg  — Setpoint accounted for (0/1)
REG_PHASES          = 1215  # UINT16,  1 reg  — Charging phases R/W (1 or 3)
REG_STARTED_BY      = 1236  # STRING,  5 regs — Started by (RFID/APP/...)

# Note on REG_MAX_CURRENT: the vendor docs state amperes, but some firmware
# returns milliamperes (e.g. 16000 for 16 A). See _decode() in modbus_client.py.

# ── IEC 61851 mode → readable status ─────────────────────────
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

# Minimum charging current (required by IEC 61851 Type 2)
MIN_CURRENT_A = 6.0
