"""Energie-Bilanz & Autarkie (EnergyCalculator).

Zwei Datenquellen:
  1. Zähler-Deltas des Wechselrichters (e_day, e_day_imp, e_day_exp,
     e_load_day, e_bat_charge_day, e_bat_discharge_day) – bevorzugt, weil
     es die firmeneigenen Energiemessungen sind. Der Kern persistiert die
     Deltas pro Poll in SQLite (Tabelle energy_delta).
  2. Trapezregel über die Leistungs-Historie – Fallback für kurze Zeiträume,
     wenn (noch) keine Deltas vorhanden sind.

Alle Ergebnisse sind Schätzungen (estimate), weil Messfehler (±2 %) und
Datenlücken (Offline-Zeiten) nie ganz vermeidbar sind.
"""

from datetime import datetime, timedelta

from datastore import DataStore

PERIODS = ("today", "week", "month", "year")

COUNTER_FIELDS = {
    "pv_wh": "e_day",
    "import_wh": "e_day_imp",
    "export_wh": "e_day_exp",
    "load_wh": "e_load_day",
    "bat_charge_wh": "e_bat_charge_day",
    "bat_discharge_wh": "e_bat_discharge_day",
}


def _local_now() -> datetime:
    return datetime.now().astimezone()


def get_period_range(period: str) -> tuple[datetime, datetime]:
    """Zeitbereich für today/week/month/year in lokaler Zeitzone."""
    now = _local_now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return midnight, now
    if period == "week":
        return midnight - timedelta(days=midnight.weekday()), now  # Montag 00:00
    if period == "month":
        return midnight.replace(day=1), now
    if period == "year":
        return midnight.replace(month=1, day=1), now
    raise ValueError(f"Unbekannter Zeitraum: {period} (erlaubt: {', '.join(PERIODS)})")


def _integrate_power(store: DataStore, field: str, from_ts: datetime, to_ts: datetime) -> float:
    """Trapezregel: integriert Leistung (W) über Zeit zu Wh."""
    pts = [
        (p["ts"], p["values"][field])
        for p in store.history()
        if from_ts.timestamp() <= p["ts"] <= to_ts.timestamp()
        and isinstance(p["values"].get(field), (int, float))
        and not isinstance(p["values"].get(field), bool)
    ]
    total_wh = 0.0
    for i in range(1, len(pts)):
        t0, p0 = pts[i - 1]
        t1, p1 = pts[i]
        total_wh += (p0 + p1) / 2.0 * ((t1 - t0) / 3600.0)
    return total_wh


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


class EnergyCalculator:
    def __init__(self, store: DataStore):
        self.store = store

    def _raw_sums(self, from_ts: datetime, to_ts: datetime) -> dict:
        """Summiert Wh je Fluss + Abdeckung.

        Für 'today' werden bevorzugt die Live-Tageszähler des Wechselrichters
        verwendet (sofort aktuell, 0,1-kWh-Auflösung); für längere Zeiträume
        die persistierten Zähler-Deltas. Fehlen Zähler, greift die
        Trapez-Integration über die Messpunkt-Historie.
        """
        today_now = _local_now().replace(hour=0, minute=0, second=0, microsecond=0)
        # Nur wenn der Zeitraum exakt 'heute' ist (from = Mitternacht):
        is_today = from_ts >= today_now

        if is_today:
            runtime = self.store.snapshot().get("runtime", {})
            counters = {}
            for k, field in COUNTER_FIELDS.items():
                v = runtime.get(field)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    counters[k] = float(v) * 1000.0  # kWh → Wh
            if len(counters) == len(COUNTER_FIELDS):
                return {
                    "sums": counters,
                    "data_completeness": 1.0,
                    "source": "inverter_counters",
                }

        rows = self.store.query_energy_deltas(from_ts.timestamp(), to_ts.timestamp())
        if rows:
            sums = {k: 0.0 for k in COUNTER_FIELDS}
            covered = 0.0
            for r in rows:
                covered += r["dt"]
                for k in COUNTER_FIELDS:
                    sums[k] += r.get(k, 0.0)
            duration = max(1e-6, (to_ts - from_ts).total_seconds())
            completeness = round(_clamp(covered / duration, 0.0, 1.0), 3)
            return {
                "sums": sums,
                "data_completeness": completeness,
                "source": "energy_deltas",
            }

        # Fallback: Trapezregel über die Messpunkt-Historie.
        # Nur für 'heute' sinnvoll – die Historie reicht nur 24 h zurück.
        if not is_today:
            return {
                "sums": {k: 0.0 for k in COUNTER_FIELDS},
                "data_completeness": 0.0,
                "source": "no_data",
            }
        sums = {k: 0.0 for k in COUNTER_FIELDS}
        sums["pv_wh"] = _integrate_power(self.store, "ppv", from_ts, to_ts)
        grid = _integrate_power(self.store, "active_power", from_ts, to_ts)  # + = Bezug
        sums["import_wh"] = max(grid, 0.0)
        sums["export_wh"] = max(-grid, 0.0)
        sums["load_wh"] = _integrate_power(self.store, "house_consumption", from_ts, to_ts)
        bat = _integrate_power(self.store, "pbattery1", from_ts, to_ts)  # ET: - = Laden
        sums["bat_charge_wh"] = max(-bat, 0.0)
        sums["bat_discharge_wh"] = max(bat, 0.0)

        pts = [
            p for p in self.store.history()
            if from_ts.timestamp() <= p["ts"] <= to_ts.timestamp()
        ]
        covered = (pts[-1]["ts"] - pts[0]["ts"]) if len(pts) > 1 else 0.0
        duration = max(1e-6, (to_ts - from_ts).total_seconds())
        return {
            "sums": sums,
            "data_completeness": round(_clamp(covered / duration, 0.0, 1.0), 3),
            "source": "power_integration",
        }

    def compute_balance(self, period: str = "today") -> dict:
        from_ts, to_ts = get_period_range(period)
        raw = self._raw_sums(from_ts, to_ts)
        s = raw["sums"]

        production = s["pv_wh"] / 1000.0
        import_kwh = s["import_wh"] / 1000.0
        export_kwh = s["export_wh"] / 1000.0
        load = s["load_wh"] / 1000.0
        bat_charge = s["bat_charge_wh"] / 1000.0
        bat_discharge = s["bat_discharge_wh"] / 1000.0

        # PV-Direktverbrauch: Der Export-Zähler des ET enthält auch
        # Batterie-Einspeisung. Rückrechnung:
        #   direkt = Produktion − Export + Batterie-Entladung
        # (gedeckelt auf [0, Produktion]; Schätzung, da Ladung teils aus
        #  dem Netz kommen kann).
        direct = _clamp(production - export_kwh + bat_discharge, 0.0, production if production > 0 else 0.0)

        if load > 0:
            autarky = round(_clamp((load - import_kwh) / load * 100.0), 1)
        else:
            # Kein Verbrauch gemessen → Autarkie unbekannt (nicht 100 % anzeigen)
            autarky = None
        self_consumption = round(_clamp(direct / production * 100.0), 1) if production > 0 else 0.0
        round_trip = round(_clamp(bat_discharge / bat_charge * 100.0, 0.0, 100.0), 1) if bat_charge > 0 else None

        return {
            "period": period,
            "from": from_ts.isoformat(),
            "to": to_ts.isoformat(),
            "estimate": True,
            "data_completeness": raw["data_completeness"],
            "source": raw["source"],
            "pv": {
                "production_kwh": round(production, 3),
                "direct_use_kwh": round(direct, 3),
                "battery_charge_kwh": round(bat_charge, 3),
                "grid_export_kwh": round(export_kwh, 3),
            },
            "consumption": {
                "total_kwh": round(load, 3),
                "from_pv_kwh": round(direct, 3),
                "from_battery_kwh": round(bat_discharge, 3),
                "from_grid_kwh": round(import_kwh, 3),
            },
            "battery": {
                "charged_kwh": round(bat_charge, 3),
                "discharged_kwh": round(bat_discharge, 3),
                "net_kwh": round(bat_charge - bat_discharge, 3),
                # Kein Verlust, sondern Ladestand-Differenz + Verluste – Schätzung
                "round_trip_percent": round_trip,
            },
            "grid": {
                "imported_kwh": round(import_kwh, 3),
                "exported_kwh": round(export_kwh, 3),
                "net_kwh": round(import_kwh - export_kwh, 3),
            },
            "self_consumption_percent": self_consumption,
            "autarky_percent": autarky,
            "grid_dependency_percent": round(100.0 - autarky, 1) if autarky is not None else None,
        }

    def compute_autarky(self, period: str = "today") -> dict:
        bal = self.compute_balance(period)
        return {
            "period": period,
            "from": bal["from"],
            "to": bal["to"],
            "consumption_kwh": bal["consumption"]["total_kwh"],
            "grid_import_kwh": bal["grid"]["imported_kwh"],
            "pv_direct_kwh": bal["pv"]["direct_use_kwh"],
            "battery_discharge_kwh": bal["battery"]["discharged_kwh"],
            "autarky_percent": bal["autarky_percent"],
            "grid_dependency_percent": bal["grid_dependency_percent"],
            "data_completeness": bal["data_completeness"],
            "estimate": True,
        }
