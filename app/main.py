from __future__ import annotations

import asyncio
import csv
import json
import math
import random
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
DATA_DIR = ROOT_DIR / "data"
CSV_FILE = DATA_DIR / "obd-trouble-codes.csv"
PROJECT_NAME = "ScanMatic AutoTech Learning"

DEFAULT_SENSOR_ORDER = [
    "rpm", "vehicle_speed", "app", "throttle", "map", "maf", "iat", "ect", "cooling_fan",
    "o2_b1s1", "o2_b1s2", "stft", "ltft", "battery_voltage", "fuel_pressure",
    "rail_pressure", "boost", "egr_position", "inj_cmd_ms", "inj_dead_time_ms",
    "inj_ms", "spark_advance", "calculated_load", "ckp_sync", "cmp_sync",
]

FAULT_LABELS = {
    "maf_low": "MAF bajo",
    "maf_dirty": "MAF sucio",
    "vacuum_leak": "Falso aire",
    "ect_biased_cold": "ECT falsa en frío",
    "o2_slow": "O2 lenta",
    "o2_intermittent": "O2 intermitente",
    "misfire_cyl1": "Misfire cil.1",
    "ckp_intermittent": "CKP intermitente",
    "cmp_fault": "CMP sin señal",
    "rail_pressure_low": "Riel bajo",
    "boost_low": "Turbo bajo",
    "egr_stuck_open": "EGR atascada",
    "map_slow": "MAP con retardo",
    "throttle_lag": "Throttle con retardo",
    "idle_hunt": "Ralentí inestable",
}


@dataclass
class FaultRecord:
    code: str
    description: str
    status: str
    category: str
    first_seen_ts: float
    source_fault: str
    freeze_frame: Dict[str, Any] = field(default_factory=dict)


def load_dtc_database() -> Dict[str, str]:
    if not CSV_FILE.exists():
        return {}
    out: Dict[str, str] = {}
    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            code = str(row[0]).strip().upper() if len(row) >= 1 else ""
            description = str(row[1]).strip() if len(row) >= 2 else ""
            if not code or code in {"CODE", "DTC", "TROUBLE CODE"}:
                continue
            out[code] = description or "Descripción no disponible en la base de datos"
    return out


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


DTC_DB = load_dtc_database()
DTC_DESCRIPTION_OVERRIDES = {
    "P0201": "Injector Circuit/Open - Cylinder 1",
    "P0202": "Injector Circuit/Open - Cylinder 2",
    "P0203": "Injector Circuit/Open - Cylinder 3",
    "P0204": "Injector Circuit/Open - Cylinder 4",
    "P0300": "Random/Multiple Cylinder Misfire Detected",
    "P0301": "Cylinder 1 Misfire Detected",
    "P0302": "Cylinder 2 Misfire Detected",
    "P0303": "Cylinder 3 Misfire Detected",
    "P0304": "Cylinder 4 Misfire Detected",
    "P0351": "Ignition Coil Primary/Secondary Circuit - Cylinder 1",
    "P0352": "Ignition Coil Primary/Secondary Circuit - Cylinder 2",
    "P0353": "Ignition Coil Primary/Secondary Circuit - Cylinder 3",
    "P0354": "Ignition Coil Primary/Secondary Circuit - Cylinder 4",
}


class SimulationState:
    def __init__(self) -> None:
        self.mode = "gasoline"
        self.engine_on = False
        self.stage = "key_on"
        self.coolant_warm = True
        self.cold_start_active = False
        self.fan_on = False
        self.students_connected = 0
        self.instructors_connected = 0
        self.session_name = "Clase 1"
        self.session_code = "SCANMATIC"
        self.vehicle_profile = "Gasolina 1.6 MPI"
        self.vin = "SMATLNGSIM000001"
        self.whatsapp_number = ""
        self.last_mode08_note = "Modo 08 disponible. Sin actuadores comandados desde el panel 1 en este momento."
        self._broadcast_version = 0
        self._lock = asyncio.Lock()
        self._start_ts = time.time()
        self._t = 0.0
        self._o2_flip = False
        self._monitor_frame_source: Dict[str, float] = {}
        self._last_stage = "key_on"
        self._o2_flip = False
        self.faults: Set[str] = set()
        self.manual_overrides: Dict[str, float] = {}
        self.dtc_records: Dict[str, FaultRecord] = {}
        self.monitor_counters: Dict[str, int] = {}
        self.expected_sensors: Dict[str, float] = {}
        self.student_notes: List[str] = []
        self.mil_on = False
        self.last_clear_ts = 0.0
        self.engine_shutdown_alert = False
        self.engine_shutdown_message = ""
        self.scenario_name = "Normal / referencia"
        self.current_preset = "normal"
        self.sensors = self._default_sensor_set()
        self.actuators = self._default_actuators()
        self.reset_runtime()

    def _default_sensor_set(self) -> Dict[str, float]:
        return {
            "rpm": 0.0,
            "vehicle_speed": 0.0,
            "app": 0.0,
            "throttle": 0.0,
            "map": 100.0,
            "maf": 0.0,
            "iat": 29.0,
            "ect": 88.0,
            "cooling_fan": 0.0,
            "o2_b1s1": 0.45,
            "o2_b1s2": 0.45,
            "stft": 0.0,
            "ltft": 0.0,
            "battery_voltage": 12.3,
            "fuel_pressure": 350.0,
            "rail_pressure": 300.0,
            "boost": 101.0,
            "egr_position": 0.0,
            "inj_cmd_ms": 0.0,
            "inj_dead_time_ms": 0.0,
            "inj_ms": 0.0,
            "spark_advance": 0.0,
            "calculated_load": 0.0,
            "ckp_sync": 1.0,
            "cmp_sync": 1.0,
        }


    def _default_actuators(self) -> Dict[str, Any]:
        return {
            "fuel_pump": True,
            "cooling_fan_mode": "auto",
            "egr_cmd": False,
            "purge_cmd": False,
            "injector_cyl1": True,
            "injector_cyl2": True,
            "injector_cyl3": True,
            "injector_cyl4": True,
            "coil_cyl1": True,
            "coil_cyl2": True,
            "coil_cyl3": True,
            "coil_cyl4": True,
        }

    @property
    def uptime(self) -> float:
        return time.time() - self._start_ts

    def _student_link(self) -> str:
        return f"http://{get_local_ip()}:8000/student"

    def _whatsapp_link(self) -> str:
        text = f"Accede al panel 2 de {PROJECT_NAME}: {self._student_link()}"
        encoded = text.replace(" ", "%20").replace(":", "%3A").replace("/", "%2F")
        base = "https://wa.me/"
        number = "".join(ch for ch in self.whatsapp_number if ch.isdigit())
        return f"{base}{number}?text={encoded}" if number else f"https://wa.me/?text={encoded}"

    def snapshot(self) -> Dict[str, Any]:
        live_dtcs = [asdict(record) for record in self.dtc_records.values()]
        live_dtcs.sort(key=lambda x: x["code"])
        return {
            "mode": self.mode,
            "engine_on": self.engine_on,
            "stage": self.stage,
            "coolant_warm": self.coolant_warm,
            "session_name": self.session_name,
            "session_code": self.session_code,
            "vehicle_profile": self.vehicle_profile,
            "vin": self.vin,
            "students_connected": self.students_connected,
            "instructors_connected": self.instructors_connected,
            "faults": sorted(self.faults),
            "fault_labels": {k: FAULT_LABELS.get(k, k) for k in sorted(self.faults)},
            "dtcs": live_dtcs,
            "sensors": self.sensors,
            "expected_sensors": self.expected_sensors,
            "manual_overrides": self.manual_overrides,
            "actuators": self.actuators,
            "mode08_summary": self.mode08_summary(),
            "monitor_results": self.monitor_results(),
            "mil_on": self.mil_on,
            "scenario_name": self.scenario_name,
            "current_preset": self.current_preset,
            "uptime": self.uptime,
            "broadcast_version": self._broadcast_version,
            "engine_shutdown_alert": self.engine_shutdown_alert,
            "engine_shutdown_message": self.engine_shutdown_message,
            "connection": {
                "ip": get_local_ip(),
                "student_link": self._student_link(),
                "whatsapp_number": self.whatsapp_number,
                "whatsapp_link": self._whatsapp_link(),
            },
        }

    async def apply_command(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with self._lock:
            if action == "set_mode":
                new_mode = payload.get("mode", "gasoline")
                if new_mode in {"gasoline", "diesel"}:
                    self.mode = new_mode
                    self.vehicle_profile = "Gasolina 1.6 MPI" if new_mode == "gasoline" else "Diésel 2.0 Common Rail"
                    self.reset_runtime()
            elif action == "toggle_engine":
                target = bool(payload.get("engine_on", not self.engine_on))
                self.engine_on = target
                if self.engine_on and self.stage == "key_on":
                    self.stage = "idle"
                elif not self.engine_on:
                    self.stage = "key_on"
            elif action == "set_stage":
                stage = payload.get("stage", "key_on")
                if stage in {"key_on", "crank", "idle", "part_load", "high_load", "decel"}:
                    self.stage = stage
                    self.engine_on = stage != "key_on"
            elif action == "set_sensor":
                key = payload.get("key")
                value = payload.get("value")
                if key in self.sensors and isinstance(value, (int, float)):
                    self.manual_overrides[key] = float(value)
            elif action == "clear_sensor_override":
                key = str(payload.get("key", ""))
                self.manual_overrides.pop(key, None)
            elif action == "toggle_fault":
                name = str(payload.get("fault", "")).strip()
                if name:
                    if name in self.faults:
                        self.faults.remove(name)
                    else:
                        self.faults.add(name)
            elif action == "apply_preset":
                self.apply_preset(payload.get("preset"))
            elif action == "reset_all":
                self.reset_runtime()
            elif action == "clear_dtcs":
                self.clear_dtcs()
            elif action == "set_session":
                self.session_name = str(payload.get("session_name") or self.session_name)
                code = str(payload.get("session_code") or self.session_code).strip().upper()
                self.session_code = code or "SCANMATIC"
            elif action == "set_whatsapp_number":
                self.whatsapp_number = "".join(ch for ch in str(payload.get("number", "")) if ch.isdigit())
            elif action == "set_actuator":
                key = str(payload.get("key", "")).strip()
                value = payload.get("value")
                if key in self.actuators:
                    if key == "cooling_fan_mode":
                        if value in {"auto", "on", "off"}:
                            self.actuators[key] = value
                    else:
                        self.actuators[key] = bool(value)
                    self._update_mode08_note()
            elif action == "add_note":
                text = str(payload.get("text", "")).strip()
                if text:
                    self.student_notes.append(text[:200])
            self.update(0.0)
            self._broadcast_version += 1
            return {"ok": True, "state": self.snapshot()}

    def reset_runtime(self) -> None:
        self.faults.clear()
        self.manual_overrides.clear()
        self.dtc_records.clear()
        self.monitor_counters.clear()
        self.stage = "key_on"
        self.engine_on = False
        self.mil_on = False
        self.scenario_name = "Normal / referencia"
        self.current_preset = "normal"
        self.cold_start_active = False
        self.fan_on = False
        self.coolant_warm = True
        self._t = 0.0
        self.sensors = self._default_sensor_set()
        self.expected_sensors = self._default_sensor_set()
        self.actuators = self._default_actuators()
        self._update_mode08_note()
        self.update(0.0)


    def _update_mode08_note(self) -> None:
        active = []
        labels = {
            "fuel_pump": "bomba de combustible",
            "egr_cmd": "EGR comandada",
            "purge_cmd": "purga EVAP",
            "cooling_fan_mode": "electroventilador",
        }
        if not self.actuators.get("fuel_pump", True):
            active.append(labels["fuel_pump"])
        if self.actuators.get("egr_cmd", False):
            active.append(labels["egr_cmd"])
        if self.actuators.get("purge_cmd", False):
            active.append(labels["purge_cmd"])
        if self.actuators.get("cooling_fan_mode") in {"on", "off"}:
            active.append(labels["cooling_fan_mode"])
        inj_off = sum(1 for i in range(1, 5) if not self.actuators.get(f"injector_cyl{i}", True))
        coil_off = sum(1 for i in range(1, 5) if not self.actuators.get(f"coil_cyl{i}", True))
        if inj_off:
            active.append(f"{inj_off} inyector(es)")
        if coil_off:
            active.append(f"{coil_off} bobina(s)")

        base = (
            "El Modo 08 corresponde al control bidireccional o prueba de actuadores. "
            "En esta plataforma educativa, el mando se realiza solo desde el Panel 1 del instructor para simular fallas y obligar al análisis en el Panel 2. "
            "Por esa razón, los actuadores específicos no se muestran aquí por el momento."
        )
        if active:
            self.last_mode08_note = base + " Actualmente hay una prueba o alteración comandada desde el Panel 1 que puede modificar el comportamiento del motor."
        else:
            self.last_mode08_note = base + " En este momento no hay actuadores comandados desde el Panel 1."

    def mode08_summary(self) -> Dict[str, Any]:
        active_count = 0
        if not self.actuators.get("fuel_pump", True):
            active_count += 1
        if self.actuators.get("egr_cmd", False):
            active_count += 1
        if self.actuators.get("purge_cmd", False):
            active_count += 1
        if self.actuators.get("cooling_fan_mode") in {"on", "off"}:
            active_count += 1
        active_count += sum(1 for i in range(1, 5) if not self.actuators.get(f"injector_cyl{i}", True))
        active_count += sum(1 for i in range(1, 5) if not self.actuators.get(f"coil_cyl{i}", True))
        return {
            "title": "Modo 08 · Control de actuadores",
            "message": self.last_mode08_note,
            "active_hidden_tests": active_count,
            "engine_behavior": "normal" if active_count == 0 else "alterado por prueba comandada desde el Panel 1",
        }

    def _root_cause_cylinder_flags(self) -> Set[str]:
        flags: Set[str] = set()
        for cyl in range(1, 5):
            if f"misfire_cyl{cyl}" in self.faults:
                flags.add(f"misfire_cyl{cyl}")
            if not self.actuators.get(f"injector_cyl{cyl}", True):
                flags.add(f"misfire_cyl{cyl}")
            if not self.actuators.get(f"coil_cyl{cyl}", True):
                flags.add(f"misfire_cyl{cyl}")
        return flags

    def clear_dtcs(self) -> None:
        self.last_clear_ts = time.time()
        self.dtc_records.clear()
        self.monitor_counters.clear()
        self.mil_on = False
        if len(self._root_cause_cylinder_flags()) <= 2:
            self.engine_shutdown_alert = False
            self.engine_shutdown_message = ""

    def apply_preset(self, preset: str | None) -> None:
        self.reset_runtime()
        preset = (preset or "normal").strip()
        self.current_preset = preset or "normal"
        if self.current_preset == "normal":
            self.scenario_name = "Normal / referencia"
            self.faults.clear()
            self.manual_overrides = {k: v for k, v in self.manual_overrides.items() if k == "app"}
            self.dtc_records.clear()
            self.monitor_counters.clear()
            self.mil_on = False
            self.engine_on = False
            self.stage = "key_on"
            self.cold_start_active = False
            self.fan_on = False
            self.coolant_warm = True
            self.sensors = self._default_sensor_set()
            self.expected_sensors = self._default_sensor_set()
            return
        if preset == "cold_start":
            self.scenario_name = "Arranque en frío"
            self.cold_start_active = True
            self.coolant_warm = False
            self.fan_on = False
            self.engine_on = False
            self.stage = "key_on"
            self.sensors = self._default_sensor_set()
            self.expected_sensors = self._default_sensor_set()
            self.sensors["ect"] = 18.0
            self.sensors["iat"] = 18.0
            self.expected_sensors["ect"] = 18.0
            self.expected_sensors["iat"] = 18.0
            self.sensors["cooling_fan"] = 0.0
            self.expected_sensors["cooling_fan"] = 0.0
            return
        self.cold_start_active = False
        self.fan_on = False
        self.coolant_warm = True
        self.engine_on = True
        self.stage = "idle"
        if preset == "lean_maf":
            self.scenario_name = "Escenario: mezcla pobre por MAF + falso aire"
            self.faults.update({"maf_low", "vacuum_leak"})
        elif preset == "rich_ect":
            self.scenario_name = "Escenario: ECT falsa en frío"
            self.faults.update({"ect_biased_cold"})
        elif preset == "misfire":
            self.scenario_name = "Escenario: misfire cilindro 1"
            self.faults.update({"misfire_cyl1"})
        elif preset == "diesel_rail":
            self.scenario_name = "Escenario: diésel con riel bajo"
            self.mode = "diesel"
            self.vehicle_profile = "Diésel 2.0 Common Rail"
            self.faults.update({"rail_pressure_low"})
        elif preset == "ckp_dropout":
            self.scenario_name = "Escenario: CKP intermitente"
            self.faults.update({"ckp_intermittent"})
        elif preset == "o2_lazy":
            self.scenario_name = "Escenario: O2 lenta"
            self.faults.update({"o2_slow"})
        elif preset == "dirty_maf":
            self.scenario_name = "Escenario: MAF sucio con respuesta lenta"
            self.faults.update({"maf_dirty"})
        elif preset == "slow_map":
            self.scenario_name = "Escenario: MAP con retardo"
            self.faults.update({"map_slow"})
        elif preset == "throttle_lag":
            self.scenario_name = "Escenario: throttle con retardo"
            self.faults.update({"throttle_lag"})
        elif preset == "idle_hunt":
            self.scenario_name = "Escenario: ralentí inestable"
            self.faults.update({"idle_hunt"})
        elif preset == "o2_intermittent":
            self.scenario_name = "Escenario: O2 intermitente"
            self.faults.update({"o2_intermittent"})
        elif preset == "turbo_low":
            self.scenario_name = "Escenario: turbo bajo"
            self.mode = "diesel"
            self.vehicle_profile = "Diésel 2.0 Common Rail"
            self.faults.update({"boost_low"})
        elif preset == "egr_stuck":
            self.scenario_name = "Escenario: EGR atascada abierta"
            self.faults.update({"egr_stuck_open"})
        self.update(0.0)

    def freeze_frame(self, source: Dict[str, float] | None = None) -> Dict[str, Any]:
        values = source or self.sensors
        keys = [
            "rpm", "vehicle_speed", "app", "throttle", "map", "maf", "ect", "iat", "cooling_fan",
            "o2_b1s1", "o2_b1s2", "stft", "ltft", "fuel_pressure", "rail_pressure",
            "boost", "inj_cmd_ms", "inj_dead_time_ms", "inj_ms", "spark_advance", "calculated_load", "battery_voltage",
            "ckp_sync", "cmp_sync",
        ]
        frame = {key: round(float(values.get(key, 0.0)), 3) for key in keys}
        frame["stage"] = self.stage
        frame["mode"] = self.mode
        frame["engine_on"] = self.engine_on
        frame["scenario_name"] = self.scenario_name
        frame["timestamp"] = round(time.time(), 3)
        return frame

    def ensure_dtc(self, code: str, category: str, source_fault: str, status: str) -> None:
        desc = DTC_DESCRIPTION_OVERRIDES.get(code) or DTC_DB.get(code, "Descripción no disponible en la base de datos")
        if code not in self.dtc_records:
            self.dtc_records[code] = FaultRecord(
                code=code,
                description=desc,
                status=status,
                category=category,
                first_seen_ts=time.time(),
                source_fault=source_fault,
                freeze_frame=self.freeze_frame(self._monitor_frame_source or self.sensors),
            )
        else:
            record = self.dtc_records[code]
            record.status = status
            record.category = category
            record.source_fault = source_fault

    def _stage_default_app(self) -> float:
        return {
            "key_on": 0.0,
            "crank": 6.0,
            "idle": 0.0,
            "part_load": 28.0,
            "high_load": 68.0,
            "decel": 3.0,
        }.get(self.stage, 0.0)

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _healthy_reference_active(self) -> bool:
        manual_fault_input = any(k != "app" for k in self.manual_overrides)
        cylinder_actuator_fault = any(not self.actuators.get(f"injector_cyl{i}", True) or not self.actuators.get(f"coil_cyl{i}", True) for i in range(1,5))
        actuator_fault_input = (not self.actuators.get("fuel_pump", True) or cylinder_actuator_fault or self.actuators.get("egr_cmd", False) or self.actuators.get("cooling_fan_mode") != "auto")
        return self.current_preset == "normal" and not self.faults and not manual_fault_input and not actuator_fault_input

    def _run_monitor(self, monitor_id: str, condition: bool, code: str, category: str, source_fault: str, *, pending_after: int = 2, confirm_after: int = 4) -> None:
        counter = self.monitor_counters.get(monitor_id, 0)
        prev_status = self.dtc_records[code].status if code in self.dtc_records else None
        if condition:
            counter += 1
            self.monitor_counters[monitor_id] = counter
            status = "pending" if counter < confirm_after else "confirmed"
            if counter >= pending_after:
                first_capture = code not in self.dtc_records
                self.ensure_dtc(code, category, source_fault, status)
                record = self.dtc_records.get(code)
                if record and first_capture and not record.freeze_frame:
                    record.freeze_frame = self.freeze_frame(self._monitor_frame_source or self.sensors)
        else:
            self.monitor_counters[monitor_id] = 0
            record = self.dtc_records.get(code)
            if record and record.status in {"pending", "confirmed"}:
                record.status = "history"

    def _compute_gasoline_expected(self, app_pct: float) -> Dict[str, float]:
        throttle_target = self._clamp(app_pct * 0.84 + (2.0 if app_pct > 0 else 0.0), 0.0, 88.0)
        idle_rpm = 810.0
        rpm = idle_rpm + app_pct * 34.0
        map_kpa = self._clamp(28.0 + throttle_target * 0.95 + app_pct * 0.12, 22.0, 98.0)
        maf = max(1.8, 1.75 + (rpm / 1000.0) * (0.82 + throttle_target / 30.0))
        load = self._clamp(11.0 + throttle_target * 1.06, 7.0, 96.0)
        speed = 0.0 if self.stage in {"crank", "idle"} else self._clamp(app_pct * 1.0 + rpm / 80.0, 0.0, 150.0)
        battery = 13.85 + 0.06 * math.sin(self._t)
        fuel_pressure = 350.0
        dead_time = self._clamp(0.96 - (battery - 12.0) * 0.17, 0.45, 0.95)
        # Valor de escáner: tiempo de inyección comandado por la ECU.
        inj_cmd_ms = self._clamp(1.22 + load * 0.012 + (rpm - idle_rpm) / 6000.0 * 0.35, 1.2, 10.8)
        # Valor físico total: tiempo comandado + retardo de apertura del inyector (dead time).
        inj_open_ms = self._clamp(inj_cmd_ms + dead_time, 1.7, 11.8)
        spark = self._clamp(8.0 + throttle_target / 4.2, 0.0, 36.0)
        return {
            "rpm": rpm,
            "vehicle_speed": speed,
            "app": app_pct,
            "throttle": throttle_target,
            "map": map_kpa,
            "maf": maf,
            "iat": 29.0 + 0.3 * math.sin(self._t / 3.0),
            "ect": 88.0 if self.coolant_warm else 24.0 + self._t * 0.15,
            "cooling_fan": 0.0,
            "o2_b1s1": 0.48 + 0.18 * math.sin(self._t * 4.0),
            "o2_b1s2": 0.62 + 0.03 * math.sin(self._t * 2.0),
            "stft": 0.0,
            "ltft": self.sensors.get("ltft", 0.0) * 0.8,
            "battery_voltage": battery,
            "fuel_pressure": fuel_pressure,
            "rail_pressure": 300.0,
            "boost": 101.0,
            "egr_position": 0.0 if app_pct < 8 else 6.0,
            "inj_cmd_ms": inj_cmd_ms,
            "inj_dead_time_ms": dead_time,
            "inj_ms": inj_open_ms,
            "spark_advance": spark,
            "calculated_load": load,
            "ckp_sync": 1.0,
            "cmp_sync": 1.0,
        }

    def _compute_diesel_expected(self, app_pct: float) -> Dict[str, float]:
        rpm = 760.0 + app_pct * 30.0
        throttle_virtual = self._clamp(app_pct * 0.45, 0.0, 35.0)
        map_kpa = self._clamp(98.0 + app_pct * 0.95, 95.0, 225.0)
        boost = 101.0 + app_pct * 0.85
        maf = max(4.2, 4.0 + (rpm / 950.0) * (1.15 + app_pct / 26.0))
        rail = 320.0 + app_pct * 15.5
        speed = 0.0 if self.stage in {"crank", "idle"} else self._clamp(app_pct * 0.95 + rpm / 82.0, 0.0, 150.0)
        load = self._clamp(15.0 + app_pct * 1.12, 10.0, 99.0)
        battery = 13.9 + 0.06 * math.sin(self._t)
        dead_time = self._clamp(0.42 - (battery - 12.0) * 0.05, 0.20, 0.42)
        inj_cmd_ms = self._clamp(0.42 + load * 0.020 + app_pct / 190.0, 0.45, 6.6)
        inj_open_ms = self._clamp(inj_cmd_ms + dead_time, 0.7, 7.0)
        return {
            "rpm": rpm,
            "vehicle_speed": speed,
            "app": app_pct,
            "throttle": throttle_virtual,
            "map": map_kpa,
            "maf": maf,
            "iat": 30.0 + 0.25 * math.sin(self._t / 3.0),
            "ect": 86.0 if self.coolant_warm else 23.0 + self._t * 0.12,
            "cooling_fan": 0.0,
            "o2_b1s1": 0.86 - 0.02 * math.sin(self._t * 2.0),
            "o2_b1s2": 0.84 - 0.015 * math.sin(self._t * 1.2),
            "stft": 0.0,
            "ltft": 0.0,
            "battery_voltage": battery,
            "fuel_pressure": 75.0,
            "rail_pressure": rail,
            "boost": boost,
            "egr_position": 14.0 if app_pct < 25 else 6.0,
            "inj_cmd_ms": inj_cmd_ms,
            "inj_dead_time_ms": dead_time,
            "inj_ms": inj_open_ms,
            "spark_advance": 0.0,
            "calculated_load": load,
            "ckp_sync": 1.0,
            "cmp_sync": 1.0,
        }

    def _apply_rpm_coupling(self, actual: Dict[str, float], expected: Dict[str, float]) -> None:
        expected_rpm = max(expected.get("rpm", 0.0), 1.0)
        actual_rpm = max(actual.get("rpm", 0.0), 0.0)
        rpm_ratio = self._clamp(actual_rpm / expected_rpm, 0.35, 1.85)

        if self.stage in {"idle", "crank"}:
            actual["vehicle_speed"] = 0.0 if actual_rpm < 1200 else self._clamp((actual_rpm - 900.0) / 35.0, 0.0, 35.0)
        else:
            base_speed = expected.get("vehicle_speed", 0.0)
            actual["vehicle_speed"] = self._clamp(base_speed * rpm_ratio, 0.0, 180.0)

        actual["calculated_load"] = self._clamp(expected.get("calculated_load", 0.0) * (0.70 + 0.30 * rpm_ratio), 0.0, 100.0)
        actual["throttle"] = self._clamp(expected.get("throttle", 0.0) * (0.92 + 0.08 * rpm_ratio), 0.0, 100.0)
        actual["battery_voltage"] = self._clamp(12.8 + actual_rpm / 6000.0 + 0.04 * math.sin(self._t), 12.0, 14.4)

        if self.mode == "gasoline":
            actual["maf"] = max(0.0, actual.get("maf", 0.0) * (0.55 + 0.45 * rpm_ratio))
            actual["map"] = self._clamp(actual.get("map", 0.0) * (0.88 + 0.12 * rpm_ratio), 15.0, 102.0)
            actual["inj_cmd_ms"] = self._clamp(actual.get("inj_cmd_ms", 0.0) * (0.68 + 0.32 * rpm_ratio), 1.0, 12.0)
            actual["inj_dead_time_ms"] = self._clamp(expected.get("inj_dead_time_ms", 0.6) + (13.8 - actual.get("battery_voltage", 13.8)) * 0.10, 0.40, 1.10)
            actual["inj_ms"] = self._clamp(actual["inj_cmd_ms"] + actual["inj_dead_time_ms"], 1.5, 18.0)
            actual["spark_advance"] = self._clamp(expected.get("spark_advance", 0.0) + (actual_rpm - expected_rpm) / 220.0, -5.0, 45.0)
            actual["fuel_pressure"] = self._clamp(expected.get("fuel_pressure", 350.0) * (0.97 + 0.03 * rpm_ratio), 250.0, 430.0)
            actual["boost"] = 101.0
            actual["rail_pressure"] = 300.0
        else:
            actual["maf"] = max(0.0, actual.get("maf", 0.0) * (0.60 + 0.40 * rpm_ratio))
            actual["map"] = self._clamp(actual.get("map", 0.0) * (0.90 + 0.10 * rpm_ratio), 85.0, 260.0)
            actual["boost"] = self._clamp(actual.get("boost", 101.0) * (0.90 + 0.10 * rpm_ratio), 98.0, 260.0)
            actual["rail_pressure"] = self._clamp(actual.get("rail_pressure", 300.0) * (0.85 + 0.15 * rpm_ratio), 180.0, 2000.0)
            actual["inj_cmd_ms"] = self._clamp(actual.get("inj_cmd_ms", 0.0) * (0.70 + 0.30 * rpm_ratio), 0.35, 8.0)
            actual["inj_dead_time_ms"] = self._clamp(expected.get("inj_dead_time_ms", 0.30) + (13.8 - actual.get("battery_voltage", 13.8)) * 0.04, 0.15, 0.45)
            actual["inj_ms"] = self._clamp(actual["inj_cmd_ms"] + actual["inj_dead_time_ms"], 0.5, 12.0)
            actual["fuel_pressure"] = self._clamp(expected.get("fuel_pressure", 75.0) * (0.95 + 0.05 * rpm_ratio), 40.0, 110.0)

    def _smooth_value(self, previous: float, target: float, alpha: float) -> float:
        return previous + (target - previous) * self._clamp(alpha, 0.02, 1.0)

    def _blend_actual_with_target(self, previous: Dict[str, float], target: Dict[str, float], app_pct: float) -> Dict[str, float]:
        actual = dict(target)
        healthy = self._healthy_reference_active()
        dynamic_alpha = {
            "rpm": 0.65 if healthy and self.stage not in {"idle", "crank"} else (0.22 if healthy else (0.14 if self.stage in {"idle", "crank"} else 0.20)),
            "vehicle_speed": 0.24 if healthy else 0.10,
            "throttle": 0.85 if healthy else 0.20,
            "map": 0.55 if healthy else 0.18,
            "maf": 0.52 if healthy else 0.18,
            "iat": 0.04,
            "ect": 0.03,
            "o2_b1s1": 0.32 if healthy else 0.22,
            "o2_b1s2": 0.18 if healthy else 0.14,
            "stft": 0.28 if healthy else 0.20,
            "ltft": 0.05,
            "battery_voltage": 0.18,
            "fuel_pressure": 0.28 if healthy else 0.16,
            "rail_pressure": 0.30 if healthy else 0.18,
            "boost": 0.24 if healthy else 0.16,
            "egr_position": 0.18 if healthy else 0.10,
            "inj_cmd_ms": 0.34 if healthy else 0.18,
            "inj_dead_time_ms": 0.30 if healthy else 0.22,
            "inj_ms": 0.34 if healthy else 0.18,
            "spark_advance": 0.32 if healthy else 0.22,
            "calculated_load": 0.34 if healthy else 0.14,
            "ckp_sync": 0.80 if healthy else 0.60,
            "cmp_sync": 0.80 if healthy else 0.60,
            "app": 0.95 if healthy else 0.70,
        }
        if "throttle_lag" in self.faults:
            dynamic_alpha["throttle"] = 0.06
            dynamic_alpha["rpm"] *= 0.65
            dynamic_alpha["maf"] *= 0.75
        if "map_slow" in self.faults:
            dynamic_alpha["map"] = 0.05
        if "maf_dirty" in self.faults:
            dynamic_alpha["maf"] = 0.08
        if self.stage == "idle" and app_pct < 4.0 and not healthy:
            dynamic_alpha["rpm"] *= 0.75
        for key, target_val in target.items():
            prev = previous.get(key, target_val)
            actual[key] = self._smooth_value(prev, target_val, dynamic_alpha.get(key, 0.16))
        return actual

    def _apply_fault_models(self, actual: Dict[str, float], expected: Dict[str, float], previous: Dict[str, float], app_pct: float) -> None:
        severity = 0.0
        if "maf_low" in self.faults:
            actual["maf"] *= 0.58
            severity += 0.35
        if "maf_dirty" in self.faults:
            dirty_target = expected["maf"] * (0.78 + 0.04 * math.sin(self._t * 0.7))
            actual["maf"] = self._smooth_value(previous.get("maf", dirty_target), dirty_target, 0.05)
            severity += 0.18
        if "vacuum_leak" in self.faults:
            actual["map"] += 10.0 + 2.5 * math.sin(self._t * 1.8)
            actual["maf"] *= 0.94
            severity += 0.22
        if "ect_biased_cold" in self.faults:
            actual["ect"] = self._smooth_value(previous.get("ect", 12.0), 12.0, 0.12)
            severity += 0.20
        if "o2_slow" in self.faults:
            target_o2 = self._clamp(0.55 + 0.08 * math.sin(self._t * 0.60), 0.20, 0.90)
            actual["o2_b1s1"] = self._smooth_value(previous.get("o2_b1s1", target_o2), target_o2, 0.04)
            severity += 0.10
        if "o2_intermittent" in self.faults:
            if random.random() < 0.12:
                actual["o2_b1s1"] = 0.20 if random.random() < 0.5 else 0.90
            severity += 0.12
        if "misfire_cyl1" in self.faults:
            drop = 80.0 + 35.0 * abs(math.sin(self._t * 6.2))
            actual["rpm"] -= drop
            actual["map"] += 7.0
            actual["maf"] *= 0.92
            severity += 0.38
        if "ckp_intermittent" in self.faults:
            actual["ckp_sync"] = 0.0 if random.random() < 0.30 else 1.0
            if actual["ckp_sync"] < 0.5:
                actual["rpm"] *= 0.60
            severity += 0.22
        if "cmp_fault" in self.faults:
            actual["cmp_sync"] = 0.0
            severity += 0.20
        if "rail_pressure_low" in self.faults:
            actual["rail_pressure"] *= 0.52
            actual["rpm"] -= 90.0
            actual["boost"] *= 0.84
            severity += 0.34
        if "boost_low" in self.faults:
            actual["boost"] *= 0.72
            severity += 0.20
        if "egr_stuck_open" in self.faults:
            actual["egr_position"] = max(actual.get("egr_position", 0.0), 42.0)
            actual["maf"] *= 0.84
            actual["map"] += 6.0
            actual["rpm"] -= 50.0
            severity += 0.20
        if "map_slow" in self.faults:
            map_target = expected["map"]
            actual["map"] = self._smooth_value(previous.get("map", map_target), map_target, 0.05)
            severity += 0.12
        if "throttle_lag" in self.faults:
            throttle_target = expected["throttle"] * 0.88
            actual["throttle"] = self._smooth_value(previous.get("throttle", throttle_target), throttle_target, 0.06)
            severity += 0.14
        if "idle_hunt" in self.faults and self.stage == "idle":
            severity += 0.18

        # Manual overrides must behave like disturbed measured values, not just static numbers
        for key, value in self.manual_overrides.items():
            if key in actual and key != "app":
                target_override = float(value)
                alpha = 0.35 if key in {"rpm", "map", "maf", "throttle", "rail_pressure", "boost"} else 0.18
                actual[key] = self._smooth_value(previous.get(key, target_override), target_override, alpha)
                mismatch = abs(target_override - expected.get(key, target_override)) / max(abs(expected.get(key, 1.0)), 1.0)
                severity += min(0.45, mismatch * 0.35)

        # Engine behavior consequences even before DTC
        maf_ratio = abs(actual["maf"] - expected["maf"]) / max(expected["maf"], 0.1)
        map_ratio = abs(actual["map"] - expected["map"]) / max(expected["map"], 1.0)
        ect_ratio = abs(actual["ect"] - expected["ect"]) / max(abs(expected["ect"]), 1.0)
        throttle_ratio = abs(actual["throttle"] - expected["throttle"]) / max(expected["throttle"], 1.0) if expected["throttle"] > 0.5 else 0.0
        instability = min(0.85, severity + maf_ratio * 0.45 + map_ratio * 0.32 + ect_ratio * 0.16 + throttle_ratio * 0.18)
        if self.stage == "idle":
            idle_hunt = (20.0 + 85.0 * instability) * math.sin(self._t * (2.2 + 4.0 * instability))
            actual["rpm"] += idle_hunt
        else:
            actual["rpm"] -= 120.0 * instability
        actual["calculated_load"] = self._clamp(actual.get("calculated_load", 0.0) + instability * (10.0 if self.stage == "idle" else 4.0), 0.0, 100.0)


    def monitor_results(self) -> List[Dict[str, Any]]:
        sensors = self.sensors
        expected = self.expected_sensors or sensors
        dtc_codes = {rec.code: rec.status for rec in self.dtc_records.values()}
        faults = set(self.faults)

        def clamp(v: float, lo: float, hi: float) -> float:
            return max(lo, min(hi, v))

        def row(tid: str, label: str, current: float, min_v: float, max_v: float, unit: str, fail: bool = False, note: str = "") -> Dict[str, Any]:
            status = "fail" if fail or current < min_v or current > max_v else "ok"
            center = (min_v + max_v) / 2.0
            span = max((max_v - min_v) / 2.0, 1e-6)
            wear = clamp(abs(current - center) / span * 100.0, 0.0, 199.0)
            if status == "fail":
                wear = max(wear, 100.0)
            return {
                "tid": tid,
                "label": label,
                "current": round(float(current), 3),
                "min": round(float(min_v), 3),
                "max": round(float(max_v), 3),
                "unit": unit,
                "status": status,
                "wear_pct": round(float(wear), 1),
                "note": note,
            }

        load = float(sensors.get("calculated_load", 0.0))
        rpm = float(sensors.get("rpm", 0.0))
        app = float(sensors.get("app", 0.0))
        throttle = float(sensors.get("throttle", 0.0))
        map_kpa = float(sensors.get("map", 0.0))
        maf = float(sensors.get("maf", 0.0))
        ect = float(sensors.get("ect", 0.0))
        o2s1 = float(sensors.get("o2_b1s1", 0.0))
        o2s2 = float(sensors.get("o2_b1s2", 0.0))
        stft = float(sensors.get("stft", 0.0))
        ltft = float(sensors.get("ltft", 0.0))
        egr = float(sensors.get("egr_position", 0.0))
        rail = float(sensors.get("rail_pressure", 0.0))
        boost = float(sensors.get("boost", 0.0))
        fuel_p = float(sensors.get("fuel_pressure", 0.0))
        fan = float(sensors.get("cooling_fan", 0.0))
        batt = float(sensors.get("battery_voltage", 0.0))
        ckp = float(sensors.get("ckp_sync", 1.0))
        cmpv = float(sensors.get("cmp_sync", 1.0))
        closed_loop = self.mode == "gasoline" and self.engine_on and ect >= 70.0 and self.stage != "crank"

        expected_map = float(expected.get("map", map_kpa))
        expected_maf = float(expected.get("maf", maf))
        expected_throttle = float(expected.get("throttle", throttle))
        expected_rail = float(expected.get("rail_pressure", rail))
        expected_boost = float(expected.get("boost", boost))
        expected_rpm = float(expected.get("rpm", rpm))

        cat_ratio = 1.0 - min(1.0, abs(o2s2 - 0.65) / 0.25)
        o2_switch_activity = 0.55 if closed_loop and 0.2 <= o2s1 <= 0.9 else 0.1
        if "o2_slow" in faults:
            o2_switch_activity = 0.16
        if "o2_intermittent" in faults:
            o2_switch_activity = 0.08

        misfire_drop = max(0.0, expected_rpm - rpm)
        misfire_idx = clamp(1.0 - (misfire_drop / 220.0), 0.0, 1.2)
        cyl_results = []
        for cyl in range(1, 5):
            inj_ok = bool(self.actuators.get(f"injector_cyl{cyl}", True))
            coil_ok = bool(self.actuators.get(f"coil_cyl{cyl}", True))
            cyl_fault = (f"misfire_cyl{cyl}" in faults) or (not inj_ok) or (not coil_ok)
            current = 0.95 if not cyl_fault else max(0.0, 0.55 - 0.1 * cyl)
            fail = cyl_fault or (f"P030{cyl}" in dtc_codes)
            cyl_results.append(row(f"$06_MIS_C{cyl}", f"Modo 06 · Misfire cilindro {cyl}", current, 0.78, 1.02, "ratio", fail=fail, note="Índice de combustión por cilindro"))

        rows = [
            row("$06_CAT", "Modo 06 · Eficiencia catalizador", cat_ratio, 0.62, 1.02, "ratio", fail=("P0420" in dtc_codes) or (cat_ratio < 0.62), note="Relación de actividad O2 tras catalizador"),
            row("$06_O2_RESP", "Modo 06 · Respuesta O2 B1S1", o2_switch_activity, 0.28, 0.95, "ratio", fail=("P0133" in dtc_codes) or ("o2_slow" in faults) or ("o2_intermittent" in faults), note="Velocidad de cruce rico/pobre"),
            row("$06_O2_HEAT", "Modo 06 · Calefactor O2", batt if self.engine_on else 0.0, 11.5, 15.2, "V", fail=(self.engine_on and batt < 11.5), note="Alimentación disponible al calentador"),
            row("$06_FUEL_TRIM", "Modo 06 · Balance combustible", abs(stft + ltft), 0.0, 18.0, "%", fail=(closed_loop and abs(stft + ltft) > 18.0) or ("P0171" in dtc_codes) or ("P0172" in dtc_codes), note="Suma de correcciones STFT + LTFT"),
            row("$06_MAF_RAT", "Modo 06 · Racionalidad MAF", abs(maf - expected_maf) / max(expected_maf, 0.1), 0.0, 0.22, "ratio", fail=("P0101" in dtc_codes) or ("maf_low" in faults) or ("maf_dirty" in faults), note="Desviación frente al aire esperado"),
            row("$06_MAP_RAT", "Modo 06 · Respuesta MAP", abs(map_kpa - expected_map) / max(expected_map, 1.0), 0.0, 0.18, "ratio", fail=("P0106" in dtc_codes) or ("vacuum_leak" in faults) or ("map_slow" in faults), note="Desviación MAP respecto al cálculo"),
            row("$06_TPS_RAT", "Modo 06 · Seguimiento throttle", abs(throttle - expected_throttle), 0.0, 8.0, "%", fail=("P0121" in dtc_codes) or ("throttle_lag" in faults), note="Error entre throttle real y esperado"),
            row("$06_EGR", "Modo 06 · Flujo EGR", egr, 0.0, 18.0 if app < 15 else 32.0, "%", fail=("P0402" in dtc_codes) or ("egr_stuck_open" in faults), note="Apertura EGR comparada con la carga actual"),
            row("$06_CKP_CMP", "Modo 06 · Sincronía CKP/CMP", min(ckp, cmpv), 0.95, 1.02, "ratio", fail=("P0335" in dtc_codes) or ("P0340" in dtc_codes) or ckp < 0.95 or cmpv < 0.95, note="Índice de sincronía entre CKP y CMP"),
            row("$06_EVAP_PURGE", "Modo 06 · Purga EVAP", 1.0 if not self.actuators.get("purge_cmd", False) else 0.82, 0.78, 1.02, "ratio", fail=False, note="Resultado del autotest de purga"),
            row("$06_COOL_FAN", "Modo 06 · Control electroventilador", fan, 0.0, 1.0, "state", fail=(self.engine_on and ect >= 96.0 and fan < 0.5), note="Debe activarse por encima de 96 °C"),
        ]

        if self.mode == "diesel":
            rows.extend([
                row("$06_RAIL", "Modo 06 · Presión de riel", abs(rail - expected_rail) / max(expected_rail, 1.0), 0.0, 0.22, "ratio", fail=("P0087" in dtc_codes) or ("rail_pressure_low" in faults), note="Error relativo de presión de riel"),
                row("$06_BOOST", "Modo 06 · Control de turbo", abs(boost - expected_boost) / max(expected_boost, 1.0), 0.0, 0.18, "ratio", fail=("P0299" in dtc_codes) or ("boost_low" in faults), note="Error relativo de sobrealimentación"),
            ])
        else:
            rows.extend([
                row("$06_FUEL_P", "Modo 06 · Presión de combustible", fuel_p, 280.0, 420.0, "kPa", fail=(self.engine_on and not self.actuators.get("fuel_pump", True)), note="Prueba de suministro de combustible"),
                row("$06_IDLE_CTL", "Modo 06 · Control de ralentí", abs(rpm - expected_rpm), 0.0, 90.0, "rpm", fail=(self.stage == "idle" and abs(rpm - expected_rpm) > 90.0) or ("idle_hunt" in faults), note="Error entre ralentí objetivo y real"),
            ])

        rows.extend(cyl_results)
        rows.sort(key=lambda x: x["tid"])
        return rows

    def latest_freeze_frames(self) -> List[Dict[str, Any]]:
        frames = []
        for record in sorted(self.dtc_records.values(), key=lambda r: r.first_seen_ts, reverse=True):
            frames.append({
                "code": record.code,
                "description": record.description,
                "status": record.status,
                "category": record.category,
                "source_fault": record.source_fault,
                "freeze_frame": record.freeze_frame,
            })
        return frames


    def _apply_temperature_model(self, expected: Dict[str, float], previous: Dict[str, float], app_pct: float) -> None:
        prev_ect = float(previous.get("ect", expected.get("ect", 88.0)))
        prev_iat = float(previous.get("iat", expected.get("iat", 29.0)))

        fan_mode = self.actuators.get("cooling_fan_mode", "auto")
        ambient = float(self.manual_overrides.get("iat", prev_iat if self.engine_on else previous.get("iat", 28.0)))

        if not self.engine_on or self.stage == "key_on":
            ect = float(self.manual_overrides.get("ect", prev_ect))
            iat = float(self.manual_overrides.get("iat", ambient))
            self.fan_on = fan_mode == "on"
            expected["ect"] = self._clamp(ect, -20.0, 120.0)
            expected["iat"] = self._clamp(iat, -20.0, 80.0)
            expected["cooling_fan"] = 1.0 if self.fan_on else 0.0
            self.coolant_warm = expected["ect"] >= 70.0
            return

        baseline_by_stage = {
            "crank": 24.0 if self.cold_start_active else 42.0,
            "idle": 22.0 if self.cold_start_active else 78.0,
            "part_load": 32.0 if self.cold_start_active else 84.0,
            "high_load": 38.0 if self.cold_start_active else 88.0,
            "decel": 34.0 if self.cold_start_active else 82.0,
        }
        baseline = baseline_by_stage.get(self.stage, 80.0)
        ect = max(prev_ect, baseline)

        if fan_mode == "on":
            self.fan_on = True
        elif fan_mode == "off":
            self.fan_on = False

        stage_heat = {
            "crank": 0.08,
            "idle": 0.14,
            "part_load": 0.22,
            "high_load": 0.34,
            "decel": 0.09,
        }.get(self.stage, 0.14)
        if self.mode == "diesel":
            stage_heat *= 0.92

        # Calentamiento normal del motor hasta temperatura de operación.
        if not self.fan_on:
            ect += stage_heat + app_pct * (0.0024 if self.mode == "gasoline" else 0.0020)
            ect = min(96.0, ect)
        else:
            ect -= 0.24 + app_pct * 0.0015
            ect = max(90.0, ect)

        if fan_mode == "auto":
            if ect >= 96.0:
                self.fan_on = True
            elif ect <= 90.0:
                self.fan_on = False

        iat_target = ambient + min(16.0, max(0.0, (ect - ambient) * 0.10))
        iat = self._smooth_value(prev_iat, iat_target, 0.05)

        if "ect" in self.manual_overrides:
            ect = float(self.manual_overrides["ect"])
            if fan_mode == "auto":
                if ect >= 96.0:
                    self.fan_on = True
                elif ect <= 90.0:
                    self.fan_on = False
        if "iat" in self.manual_overrides:
            iat = float(self.manual_overrides["iat"])

        expected["ect"] = self._clamp(ect, -20.0, 120.0)
        expected["iat"] = self._clamp(iat, -20.0, 80.0)
        expected["cooling_fan"] = 1.0 if self.fan_on else 0.0
        self.coolant_warm = expected["ect"] >= 70.0

        if self.mode == "gasoline":
            cold_factor = self._clamp((70.0 - expected["ect"]) / 55.0, 0.0, 1.0)
        else:
            cold_factor = self._clamp((65.0 - expected["ect"]) / 50.0, 0.0, 1.0)

        # Ralentí alto en frío y retorno a ralentí mínimo cuando APP <= 3%.
        if app_pct <= 3.0:
            warm_idle = 780.0 if self.mode == "gasoline" else 760.0
            cold_bonus = (260.0 if self.mode == "gasoline" else 150.0) * cold_factor
            expected["rpm"] = warm_idle + cold_bonus
            expected["vehicle_speed"] = 0.0 if self.stage in {"idle", "crank", "decel"} else expected.get("vehicle_speed", 0.0)
            expected["throttle"] = max(expected.get("throttle", 0.0), 2.0 + cold_factor * 1.6)
            expected["map"] = self._clamp(expected.get("map", 0.0) + cold_factor * 2.0, 18.0, 260.0)

        if cold_factor > 0.0:
            expected["inj_cmd_ms"] *= 1.0 + ((0.28 if self.mode == "gasoline" else 0.20) * cold_factor)
            expected["inj_ms"] = expected["inj_cmd_ms"] + expected.get("inj_dead_time_ms", 0.6)

    def update(self, dt: float = 0.25) -> None:

        self._t += dt

        app_pct = self._clamp(float(self.manual_overrides.get("app", self._stage_default_app() if self.engine_on else 0.0)), 0.0, 100.0)

        if not self.engine_on or self.stage == "key_on":
            self.expected_sensors = self._default_sensor_set()
            self.expected_sensors["app"] = round(app_pct, 2)
            prev = dict(self.sensors)
            self.sensors = self._default_sensor_set()
            self.sensors["app"] = round(app_pct, 2)
            self.sensors["battery_voltage"] = round(self._smooth_value(prev.get("battery_voltage", 12.4), 12.35, 0.10), 3)
            self.sensors["map"] = 100.0
            self.sensors["maf"] = 0.0
            self.sensors["throttle"] = round(app_pct * 0.18, 3)
            if self.cold_start_active:
                self.sensors["ect"] = round(float(self.manual_overrides.get("ect", prev.get("ect", 18.0))), 3)
                self.sensors["iat"] = round(float(self.manual_overrides.get("iat", prev.get("iat", 18.0))), 3)
                self.sensors["cooling_fan"] = 1.0 if self.actuators.get("cooling_fan_mode") == "on" else 0.0
            else:
                if "ect" in self.manual_overrides:
                    self.sensors["ect"] = round(float(self.manual_overrides["ect"]), 3)
                else:
                    self.sensors["ect"] = round(self._smooth_value(prev.get("ect", 88.0), self.sensors["ect"], 0.03), 3)
                if "iat" in self.manual_overrides:
                    self.sensors["iat"] = round(float(self.manual_overrides["iat"]), 3)
                self.sensors["cooling_fan"] = 1.0 if self.actuators.get("cooling_fan_mode") == "on" else 0.0
            self.expected_sensors["ect"] = self.sensors["ect"]
            self.expected_sensors["iat"] = self.sensors["iat"]
            self.expected_sensors["cooling_fan"] = self.sensors["cooling_fan"]

            self.mil_on = any(record.status == "confirmed" for record in self.dtc_records.values())
            self._last_stage = self.stage
            self._broadcast_version += 1
            return

        expected = self._compute_gasoline_expected(app_pct) if self.mode == "gasoline" else self._compute_diesel_expected(app_pct)
        previous = dict(self.sensors)
        self._apply_temperature_model(expected, previous, app_pct)

        manual_misfire_flags = {f for f in self.faults if f.startswith("misfire_cyl")}
        effective_misfire_flags = set(manual_misfire_flags)

        if not self.actuators.get("fuel_pump", True):
            expected["fuel_pressure"] = 40.0
            if self.mode == "diesel":
                expected["rail_pressure"] = min(expected["rail_pressure"], 180.0)
            expected["inj_cmd_ms"] *= 0.55
            expected["inj_ms"] = expected["inj_cmd_ms"] + expected.get("inj_dead_time_ms", 0.5)
        if self.actuators.get("egr_cmd", False):
            expected["egr_position"] = max(expected.get("egr_position", 0.0), 22.0)
            expected["map"] = min(102.0, expected["map"] + 8.0)
            expected["maf"] = max(0.6, expected["maf"] * 0.88)

        if self._last_stage != self.stage:
            previous = {**expected, **previous}
            previous["rpm"] = previous.get("rpm", expected["rpm"])
        actual = self._blend_actual_with_target(previous, expected, app_pct)
        self._apply_fault_models(actual, expected, previous, app_pct)

        injector_fault_count = 0
        coil_fault_count = 0
        for cyl in range(1, 5):
            inj_ok = self.actuators.get(f"injector_cyl{cyl}", True)
            coil_ok = self.actuators.get(f"coil_cyl{cyl}", True)
            if not inj_ok:
                injector_fault_count += 1
                actual["inj_cmd_ms"] *= 0.93
                actual["inj_ms"] = actual["inj_cmd_ms"] + actual.get("inj_dead_time_ms", 0.6)
                actual["maf"] *= 0.992
                actual["rpm"] -= 85.0
                actual["map"] += 2.8
                actual["calculated_load"] *= 0.98
                effective_misfire_flags.add(f"misfire_cyl{cyl}")
            if not coil_ok:
                coil_fault_count += 1
                actual["rpm"] -= 95.0
                actual["map"] += 3.4
                actual["maf"] *= 0.991
                actual["calculated_load"] *= 0.98
                effective_misfire_flags.add(f"misfire_cyl{cyl}")
        if not self.actuators.get("fuel_pump", True):
            actual["fuel_pressure"] = self._smooth_value(previous.get("fuel_pressure", 350.0), 50.0, 0.25)
            if self.mode == "diesel":
                actual["rail_pressure"] = self._smooth_value(previous.get("rail_pressure", 300.0), 160.0, 0.25)
            actual["rpm"] -= 180.0
            actual["map"] += 5.0

        # Al reactivar actuadores, la dinámica debe regresar a la normalidad del escenario actual.
        actuator_disturbance_active = (
            (not self.actuators.get("fuel_pump", True))
            or self.actuators.get("egr_cmd", False)
            or self.actuators.get("purge_cmd", False)
            or self.actuators.get("cooling_fan_mode") != "auto"
            or any(not self.actuators.get(f"injector_cyl{i}", True) or not self.actuators.get(f"coil_cyl{i}", True) for i in range(1, 5))
        )
        if not actuator_disturbance_active:
            for key, alpha in {
                "rpm": 0.55, "map": 0.45, "maf": 0.45, "fuel_pressure": 0.50,
                "rail_pressure": 0.50, "boost": 0.40, "inj_cmd_ms": 0.45,
                "inj_ms": 0.45, "calculated_load": 0.35, "egr_position": 0.40,
            }.items():
                if key in actual and key in expected:
                    actual[key] = self._smooth_value(actual[key], expected[key], alpha)

        # Las banderas de misfire por actuador se manejan de forma efectiva por ciclo,
        # para que desaparezcan al reparar la causa sin quedar pegadas en self.faults.
        self._apply_rpm_coupling(actual, expected)

        active_cylinders = max(0, 4 - len(effective_misfire_flags))
        severe_shutdown = active_cylinders <= 1
        if severe_shutdown:
            self.engine_on = False
            self.stage = "key_on"
            self.engine_shutdown_alert = True
            self.engine_shutdown_message = "⚠ Motor apagado por falla"
            for key in ("rpm", "vehicle_speed", "maf", "inj_cmd_ms", "inj_ms", "calculated_load"):
                actual[key] = 0.0
            actual["map"] = 100.0
            actual["stft"] = 0.0
            actual["ltft"] = 0.0
            actual["o2_b1s1"] = 0.45
            actual["o2_b1s2"] = 0.45
            effective_misfire_flags = set(self._root_cause_cylinder_flags())
        elif not self._root_cause_cylinder_flags():
            self.engine_shutdown_alert = False
            self.engine_shutdown_message = ""

        airflow_error = 0.0
        if expected["maf"] > 0.1:
            airflow_error = (expected["maf"] - actual["maf"]) / expected["maf"]
        pressure_error = (actual["map"] - expected["map"]) / max(expected["map"], 1.0)
        rpm_error = (expected["rpm"] - actual["rpm"]) / max(expected["rpm"], 1.0)

        closed_loop_ready = False
        if self.mode == "gasoline":
            trim_bias = airflow_error * 34.0 + pressure_error * 18.0 + rpm_error * 8.0
            if actual["ect"] < expected["ect"] - 25:
                trim_bias -= 12.0

            manual_misfire_count = len(manual_misfire_flags)
            if manual_misfire_count:
                trim_bias -= 10.0 * manual_misfire_count
            if injector_fault_count:
                trim_bias += 18.0 * injector_fault_count
            if coil_fault_count:
                trim_bias -= 18.0 * coil_fault_count

            closed_loop_ready = actual["ect"] >= 70.0 and self.stage in {"idle", "part_load", "high_load", "decel"} and not severe_shutdown
            if closed_loop_ready:
                if injector_fault_count or coil_fault_count:
                    trim_target = (18.0 * injector_fault_count) - (18.0 * coil_fault_count) - (8.0 * manual_misfire_count)
                else:
                    trim_target = trim_bias
                trim_target += 0.7 * math.sin(self._t * 3.0)
                actual["stft"] = self._clamp(self._smooth_value(previous.get("stft", 0.0), trim_target, 0.34), -25.0, 25.0)
                actual["ltft"] = self._clamp(self._smooth_value(previous.get("ltft", 0.0), actual["stft"] * 0.60, 0.08), -25.0, 25.0)
            else:
                actual["stft"] = 0.0
                actual["ltft"] = self._clamp(previous.get("ltft", 0.0), -25.0, 25.0)
            if "o2_slow" not in self.faults and "o2_b1s1" not in self.manual_overrides and "o2_intermittent" not in self.faults:
                if closed_loop_ready:
                    if injector_fault_count or coil_fault_count or manual_misfire_count:
                        # Con falla la señal narrowband sigue oscilando, pero sesgada a rico o pobre.
                        dominant_rich = (coil_fault_count * 0.22) + (manual_misfire_count * 0.14)
                        dominant_lean = injector_fault_count * 0.22
                        o2_center = self._clamp(0.50 + dominant_rich - dominant_lean, 0.22, 0.78)
                        o2_swing = 0.18
                        o2_shape = math.sin(self._t * 4.4) + 0.18 * math.sin(self._t * 8.8)
                        o2_target = self._clamp(o2_center + o2_swing * o2_shape, 0.10, 0.90)
                        smooth_alpha = 0.72
                    else:
                        # Narrowband sana: onda redondeada que cruza y sobrepasa ligeramente
                        # las guías de 0.20 y 0.80 V, sin verse cuadrada ni serruchada.
                        lambda_shift = self._clamp((-actual["stft"] / 25.0) * 0.015, -0.015, 0.015)
                        rich_bias = self._clamp(app_pct / 100.0 * 0.006, 0.0, 0.006)
                        fundamental = math.sin(self._t * 4.2)
                        harmonic = 0.12 * math.sin(self._t * 8.4)
                        o2_target = 0.50 + 0.33 * fundamental + harmonic + lambda_shift + rich_bias
                        o2_target = self._clamp(o2_target, 0.12, 0.88)
                        smooth_alpha = 0.80
                else:
                    warmup_factor = self._clamp((70.0 - actual["ect"]) / 70.0, 0.0, 1.0)
                    o2_target = self._clamp(0.76 + 0.10 * warmup_factor + 0.02 * math.sin(self._t * 1.6), 0.20, 0.90)
                    smooth_alpha = 0.14
                actual["o2_b1s1"] = self._smooth_value(previous.get("o2_b1s1", o2_target), o2_target, smooth_alpha)
                actual["o2_b1s1"] = self._clamp(actual["o2_b1s1"], 0.20, 0.90)
            if "o2_b1s2" not in self.manual_overrides:
                if closed_loop_ready:
                    o2s2_target = self._clamp(0.66 + (actual["o2_b1s1"] - 0.55) * 0.12, 0.58, 0.82)
                else:
                    o2s2_target = self._clamp(actual["o2_b1s1"] - 0.04, 0.58, 0.82)
                actual["o2_b1s2"] = self._smooth_value(previous.get("o2_b1s2", o2s2_target), o2s2_target, 0.12)
                actual["o2_b1s2"] = self._clamp(actual["o2_b1s2"], 0.58, 0.82)
        else:
            rail_error = (expected["rail_pressure"] - actual["rail_pressure"]) / max(expected["rail_pressure"], 1.0)
            boost_error = (expected["boost"] - actual["boost"]) / max(expected["boost"], 1.0)
            actual["stft"] = 0.0
            actual["ltft"] = 0.0
            if "o2_b1s1" not in self.manual_overrides:
                o2_target = self._clamp(0.86 - boost_error * 0.03 - rail_error * 0.02, 0.72, 0.95)
                actual["o2_b1s1"] = self._smooth_value(previous.get("o2_b1s1", o2_target), o2_target, 0.18)
            if "o2_b1s2" not in self.manual_overrides:
                o2s2_target = self._clamp(actual["o2_b1s1"] - 0.015, 0.70, 0.94)
                actual["o2_b1s2"] = self._smooth_value(previous.get("o2_b1s2", o2s2_target), o2s2_target, 0.14)

        self._monitor_frame_source = dict(actual)

        maf_dev = abs(actual["maf"] - expected["maf"]) / max(expected["maf"], 0.1)
        map_dev = abs(actual["map"] - expected["map"]) / max(expected["map"], 1.0)
        ect_dev = abs(actual["ect"] - expected["ect"])
        rail_dev = abs(actual["rail_pressure"] - expected["rail_pressure"]) / max(expected["rail_pressure"], 1.0)
        boost_dev = abs(actual["boost"] - expected["boost"]) / max(expected["boost"], 1.0)
        throttle_dev = abs(actual["throttle"] - expected["throttle"]) / max(expected["throttle"], 1.0) if expected["throttle"] > 0.5 else 0.0

        has_manual_fault_input = any(k != "app" for k in self.manual_overrides)
        cylinder_actuator_fault = any(not self.actuators.get(f"injector_cyl{i}", True) or not self.actuators.get(f"coil_cyl{i}", True) for i in range(1,5))
        actuator_fault_input = (not self.actuators.get("fuel_pump", True) or cylinder_actuator_fault or self.actuators.get("egr_cmd", False) or self.actuators.get("cooling_fan_mode") != "auto")
        diagnostic_enabled = bool(self.faults) or has_manual_fault_input or actuator_fault_input or self.current_preset != "normal"

        if self._healthy_reference_active():
            self.dtc_records.clear()
            self.monitor_counters.clear()
            self.mil_on = False
            diagnostic_enabled = False

        if not diagnostic_enabled:
            self.monitor_counters.clear()
            self.mil_on = False
        else:
            stable_load = app_pct > 3.0 or self.stage in {"part_load", "high_load"}
            self._run_monitor("maf_range", stable_load and maf_dev > 0.32 and app_pct > 10.0, "P0101", "air", "maf_low")
            self._run_monitor("map_range", stable_load and map_dev > 0.26 and app_pct > 8.0, "P0106", "air", "vacuum_leak")
            self._run_monitor("ect_range", ect_dev > 25.0, "P0115", "sensor", "ect_biased_cold")
            self._run_monitor("o2_slow", (("o2_slow" in self.faults) or ("o2_intermittent" in self.faults) or ("o2_b1s1" in self.manual_overrides)) and app_pct > 5.0, "P0133", "sensor", "o2_slow")
            self._run_monitor("lean_mix", closed_loop_ready and actual["stft"] > 14.0 and actual["ltft"] > 6.0 and app_pct > 8.0, "P0171", "fuel", "vacuum_leak")
            self._run_monitor("rich_mix", closed_loop_ready and actual["stft"] < -14.0 and actual["ltft"] < -6.0 and app_pct > 5.0, "P0172", "fuel", "ect_biased_cold")
            misfire_sources = []
            for cyl in range(1, 5):
                flag = f"misfire_cyl{cyl}" in effective_misfire_flags
                inj_fault = not self.actuators.get(f"injector_cyl{cyl}", True)
                coil_fault = not self.actuators.get(f"coil_cyl{cyl}", True)
                manual_bias = abs(float(self.manual_overrides.get("rpm", actual["rpm"])) - actual["rpm"]) if "rpm" in self.manual_overrides else 0.0
                active = flag or (flag and manual_bias > 40.0)
                if active:
                    misfire_sources.append(cyl)
                self._run_monitor(f"misfire_c{cyl}", active, f"P030{cyl}", "misfire", f"misfire_cyl{cyl}", pending_after=1 if (inj_fault or coil_fault) else 2, confirm_after=2 if (inj_fault or coil_fault) else 4)
                self._run_monitor(f"injector_c{cyl}", inj_fault, f"P020{cyl}", "actuator", f"injector_cyl{cyl}", pending_after=1, confirm_after=2)
                self._run_monitor(f"coil_c{cyl}", coil_fault, f"P035{cyl}", "actuator", f"coil_cyl{cyl}", pending_after=1, confirm_after=2)
            self._run_monitor("misfire_multi", len(misfire_sources) >= 2, "P0300", "misfire", "misfire_cyl1")
            self._run_monitor("ckp_sync", actual["ckp_sync"] < 0.5, "P0335", "sensor", "ckp_intermittent")
            self._run_monitor("cmp_sync", actual["cmp_sync"] < 0.5, "P0340", "sensor", "cmp_fault")
            self._run_monitor("throttle_range", throttle_dev > 0.24 and app_pct > 12.0, "P0121", "sensor", "throttle_lag")

            if self.mode == "diesel":
                self._run_monitor("rail_pressure", rail_dev > 0.34 and app_pct > 12.0, "P0087", "fuel", "rail_pressure_low")
                self._run_monitor("boost_low", boost_dev > 0.22 and app_pct > 18.0, "P0299", "air", "boost_low")
                self._run_monitor("egr_flow", actual["egr_position"] > 35.0 and app_pct > 5.0, "P0402", "air", "egr_stuck_open")
            else:
                for code in ("P0087", "P0299", "P0402"):
                    rec = self.dtc_records.get(code)
                    if rec and rec.status in {"pending", "confirmed"}:
                        rec.status = "history"

        if not diagnostic_enabled:
            self.dtc_records.clear()
        else:
            for code in list(self.dtc_records.keys()):
                rec = self.dtc_records.get(code)
                if rec and rec.status == "history" and code.startswith(("P020", "P035", "P030")):
                    active_misfire_codes = {f"P030{cyl}" for cyl in range(1, 5) if f"misfire_cyl{cyl}" in effective_misfire_flags}
                    active_injector_codes = {f"P020{cyl}" for cyl in range(1, 5) if not self.actuators.get(f"injector_cyl{cyl}", True)}
                    active_coil_codes = {f"P035{cyl}" for cyl in range(1, 5) if not self.actuators.get(f"coil_cyl{cyl}", True)}
                    if code not in active_misfire_codes and code not in active_injector_codes and code not in active_coil_codes and not (code == "P0300" and len(effective_misfire_flags) >= 2):
                        self.dtc_records.pop(code, None)

        self.mil_on = any(record.status == "confirmed" for record in self.dtc_records.values())
        self.expected_sensors = {k: round(v, 3) for k, v in expected.items()}
        self.sensors = {k: round(actual.get(k, 0.0), 3) for k in DEFAULT_SENSOR_ORDER}
        self._last_stage = self.stage
        self._broadcast_version += 1

    def search_dtc(self, query: str, limit: int = 20) -> List[Dict[str, str]]:
        query = (query or "").strip().upper()
        out = []
        for code, desc in DTC_DB.items():
            if not query or query in code or query in desc.upper():
                out.append({"code": code, "description": desc})
            if len(out) >= limit:
                break
        return out


state = SimulationState()
app = FastAPI(title=PROJECT_NAME)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


class WSManager:
    def __init__(self) -> None:
        self.clients: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, data: Dict[str, Any]) -> None:
        if not self.clients:
            return
        message = json.dumps(data)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = WSManager()


@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(simulation_loop())


async def simulation_loop() -> None:
    while True:
        state.update(0.25)
        await ws_manager.broadcast({"type": "state", "payload": state.snapshot()})
        await asyncio.sleep(0.25)


@app.get("/", response_class=HTMLResponse)
async def root() -> RedirectResponse:
    return RedirectResponse("/student")


@app.get("/instructor", response_class=HTMLResponse)
async def instructor_view(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("instructor.html", {"request": request, "project_name": PROJECT_NAME})


@app.get("/student", response_class=HTMLResponse)
async def student_view(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("student.html", {"request": request, "project_name": PROJECT_NAME})


@app.get("/api/state")
async def api_state() -> Dict[str, Any]:
    return state.snapshot()


@app.get("/api/connection-info")
async def connection_info() -> Dict[str, Any]:
    return state.snapshot()["connection"]


@app.get("/api/dtc-search")
async def api_dtc_search(q: str = "", limit: int = 20) -> List[Dict[str, str]]:
    return state.search_dtc(query=q, limit=limit)


@app.post("/api/instructor/command")
async def instructor_command(request: Request) -> JSONResponse:
    payload = await request.json()
    result = await state.apply_command(payload.get("action"), payload)
    return JSONResponse(result)


@app.post("/api/student/action")
async def student_action(request: Request) -> JSONResponse:
    payload = await request.json()
    action = payload.get("action")
    if action == "read_dtcs":
        return JSONResponse({"ok": True, "dtcs": [asdict(x) for x in state.dtc_records.values()]})
    if action == "clear_dtcs":
        await state.apply_command("clear_dtcs", {})
        return JSONResponse({"ok": True, "dtcs": [asdict(x) for x in state.dtc_records.values()]})
    if action == "live_data":
        return JSONResponse({"ok": True, "sensors": state.sensors, "mil_on": state.mil_on})
    if action == "freeze_frame":
        return JSONResponse({"ok": True, "freeze_frames": state.latest_freeze_frames(), "current": state.freeze_frame(state.sensors)})
    if action == "mode05":
        return JSONResponse({"ok": True, "o2_tests": {
            "b1s1_voltage": state.sensors.get("o2_b1s1", 0.0),
            "b1s2_voltage": state.sensors.get("o2_b1s2", 0.0),
            "closed_loop_ready": state.sensors.get("ect", 0.0) >= 70.0 and state.engine_on,
            "heater_ready": state.sensors.get("battery_voltage", 0.0) > 12.0,
        }})
    if action == "mode06":
        return JSONResponse({"ok": True, "monitor_results": state.monitor_results()})
    if action == "mode07":
        return JSONResponse({"ok": True, "dtcs": [asdict(x) for x in state.dtc_records.values() if x.status == "pending"]})
    if action == "mode08":
        return JSONResponse({"ok": True, "mode08": state.mode08_summary()})
    if action == "mode09":
        return JSONResponse({"ok": True, "ecu_info": {
            "vin": state.vin,
            "vehicle_profile": state.vehicle_profile,
            "session_name": state.session_name,
            "mode": state.mode,
            "stage": state.stage,
            "protocol": "OBD2 Simulado",
        }})
    return JSONResponse({"ok": False, "error": "Acción no soportada"}, status_code=400)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws_manager.connect(ws)
    role = ws.query_params.get("role", "student")
    if role == "instructor":
        state.instructors_connected += 1
    else:
        state.students_connected += 1
    await ws.send_text(json.dumps({"type": "state", "payload": state.snapshot()}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(ws)
        if role == "instructor":
            state.instructors_connected = max(0, state.instructors_connected - 1)
        else:
            state.students_connected = max(0, state.students_connected - 1)
