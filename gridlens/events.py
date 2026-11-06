"""Catalog of smart-meter event codes.

Codes follow the shape of the IDIS / DLMS standard event log (power, tamper and
voltage-quality events). The numbers here are the subset this project uses.
"""

CATALOG: dict[int, str] = {
    1: "power_failure",
    2: "power_restored",
    4: "time_changed",
    40: "terminal_cover_opened",
    41: "terminal_cover_closed",
    44: "main_cover_opened",
    47: "strong_dc_field_detected",
    48: "magnetic_field_cleared",
    63: "neutral_missing",
    76: "voltage_sag",
    77: "voltage_swell",
    91: "reverse_energy",
    200: "communication_lost",
    201: "communication_restored",
}

POWER_LOSS = {"power_failure", "neutral_missing"}
POWER_BACK = {"power_restored"}
TAMPER = {"terminal_cover_opened", "main_cover_opened", "strong_dc_field_detected", "reverse_energy"}
VOLTAGE = {"voltage_sag", "voltage_swell"}
COMMS = {"communication_lost", "communication_restored"}


def name_of(code: int) -> str:
    return CATALOG.get(code, f"unknown_{code}")
