# ETAPpro EV Charger — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)

Local Modbus TCP integration for the **ETAPpro EV charger by EVchargeking**.  
No cloud, no API key — only the local IP address of your charger is required.

Based on the official register map: [EV-Chargeking/etap-modbus](https://github.com/EV-Chargeking/etap-modbus)

Disclaimer: This integration was made with support of AI tools
---

## Installation via HACS

1. Go to **HACS → Integrations** in Home Assistant
2. Click the three dots (⋮) in the top right → **Custom repositories**
3. Add: `https://github.com/YOUR-USERNAME/ha-etappro`  
   Category: **Integration**
4. Search for "ETAPpro" and click **Download**
5. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **ETAPpro**
3. Enter the **local IP address** of your ETAPpro  
   *(find it in the ETAPpro app or in your router's device list)*
4. Click **Submit** — the integration will test the connection automatically

## Entities

### Sensors (read-only)

| Entity | Unit | Description |
|--------|------|-------------|
| Status | — | Human-readable status (Free / Car connected / Charging / Error) |
| Mode (IEC 61851) | — | Raw IEC mode code (A1, B2, C2, E, ...) |
| Voltage L1 / L2 / L3 | V | Grid voltage per phase |
| Current L1 / L2 / L3 | A | Charging current per phase |
| Total current | A | Sum of all phases |
| Power L1 / L2 / L3 | W | Active power per phase |
| Total power | W | Total active power |
| Total energy | kWh | Cumulative energy counter |
| Board temperature | °C | Internal board temperature |
| EV plug temperature | °C | Temperature at the vehicle connector |
| Grid plug temperature | °C | Temperature at the grid connector |
| Applied max current | A | Currently enforced current limit |
| Started by | — | RFID / APP / MODBUS / FREE |

### Configurable (write)

| Entity | Range | Description |
|--------|-------|-------------|
| Current setpoint | 6 – *hardware max* A | Slider in the UI |
| Charging phases | 1 or 3 | Single-phase or three-phase charging |

## Options

After installation, click the **gear icon (⚙)** on the integration card to adjust the polling interval (default: 10 seconds).

## Troubleshooting

**Entities show as "Unavailable":**
- Verify the IP address is correct
- Make sure the ETAPpro and Home Assistant are on the same local network
- Check that the ETAPpro is powered on and connected to Wi-Fi

**Enable verbose logging:**
```yaml
# configuration.yaml
logger:
  logs:
    custom_components.etappro: debug
```
