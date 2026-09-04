"""Constanten voor de ETAPpro integratie.

De registerkaart zelf staat in `device.py`, als het registermodel dat de
Modbus-library leest.
"""

DOMAIN = "etappro"
VERSION = "2.0.0"
DEFAULT_PORT = 502
SCAN_INTERVAL_FAST = 10    # seconden — stroom, vermogen, modus
SCAN_INTERVAL_MEDIUM = 30  # seconden — spanning, temperatuur
SCAN_INTERVAL_SLOW = 60    # seconden — energieteller, max stroom

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
