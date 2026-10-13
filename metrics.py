"""Berechnete Batterie- und Verbrauchskennzahlen.

Vorzeichen-Konvention des GoodWe-ET (empirisch verifiziert mit
battery_mode_label): pbattery1 < 0 = Laden, pbattery1 > 0 = Entladen.
"""


def compute_metrics(runtime: dict, user_cfg: dict) -> dict:
    capacity = float(user_cfg["battery_capacity_kwh"])
    threshold = float(user_cfg["warning_threshold_w"])

    metrics = {
        "battery_capacity_kwh": capacity,
        "warning_threshold_w": threshold,
    }

    soc = runtime.get("battery_soc")
    p_bat = runtime.get("pbattery1")
    house = runtime.get("house_consumption")
    ppv = runtime.get("ppv")

    if isinstance(soc, (int, float)) and not isinstance(soc, bool):
        soc = float(soc)
        metrics["battery_soc_percent"] = soc
        metrics["battery_stored_kwh"] = round(capacity * soc / 100.0, 3)
        metrics["battery_remaining_kwh"] = round(capacity * (100.0 - soc) / 100.0, 3)

    if isinstance(p_bat, (int, float)) and not isinstance(p_bat, bool):
        p = float(p_bat)
        metrics["battery_power_w"] = p
        if p < -5:  # ET: negativ = Laden
            metrics["battery_charging"] = True
            metrics["battery_charge_power_w"] = abs(p)
            if "battery_remaining_kwh" in metrics:
                # Ladezeit bis voll: verbleibende kWh / Ladeleistung
                metrics["charge_time_seconds"] = int(
                    metrics["battery_remaining_kwh"] / (abs(p) / 1000.0) * 3600
                )
        elif p > 5:  # ET: positiv = Entladen
            metrics["battery_charging"] = False
            metrics["battery_discharge_power_w"] = p
            if "battery_stored_kwh" in metrics:
                # Entladezeit bis leer: gespeicherte kWh / Entladeleistung
                metrics["discharge_time_seconds"] = int(
                    metrics["battery_stored_kwh"] / (p / 1000.0) * 3600
                )
        else:
            metrics["battery_charging"] = None

    if isinstance(house, (int, float)) and not isinstance(house, bool):
        metrics["house_power_w"] = float(house)

    # Netzbezug/Einspeisung (live): Zähler-Leistung + Zustandslabel des
    # Wechselrichters bestimmen die Richtung zuverlässig.
    grid_power = runtime.get("meter_active_power_total")
    if not isinstance(grid_power, (int, float)) or isinstance(grid_power, bool):
        grid_power = runtime.get("active_power")
    if isinstance(grid_power, (int, float)) and not isinstance(grid_power, bool):
        grid_power = abs(float(grid_power))
        label = runtime.get("grid_in_out_label") or runtime.get("grid_mode_label")
        metrics["grid_power_w"] = grid_power
        metrics["grid_state"] = label
        if label == "Importing":
            metrics["grid_import_w"] = grid_power
            metrics["grid_export_w"] = 0.0
        elif label == "Exporting":
            metrics["grid_import_w"] = 0.0
            metrics["grid_export_w"] = grid_power

    # Autonomie: wie lange reicht der gespeicherte Strom bei aktuellem
    # Netto-Verbrauch (Haus minus PV)?
    if isinstance(house, (int, float)) and "battery_stored_kwh" in metrics:
        pv = ppv if isinstance(ppv, (int, float)) and not isinstance(ppv, bool) else 0.0
        net = float(house) - float(pv)
        metrics["net_consumption_w"] = net
        if net > 10:
            metrics["autonomy_time_seconds"] = int(
                metrics["battery_stored_kwh"] / (net / 1000.0) * 3600
            )
            metrics["autonomy_covered"] = False
        else:
            # PV deckt den Verbrauch (oder Überschuss) → Speicher wird nicht geleert
            metrics["autonomy_time_seconds"] = None
            metrics["autonomy_covered"] = True

    return metrics
