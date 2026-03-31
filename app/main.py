from __future__ import annotations

import asyncio
import csv
import json
import math
import random
import sqlite3
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
DATA_DIR = ROOT_DIR / "data"
CSV_FILE = DATA_DIR / "obd-trouble-codes.csv"
SESSION_DB_FILE = DATA_DIR / "session_registry.db"
SESSION_EXPORT_DIR = DATA_DIR / "session_exports"
PROJECT_NAME = "ScanMatic AutoTech Learning"
LOCAL_IP_CACHE_TTL_SECONDS = 15.0
_cached_local_ip = "127.0.0.1"
_cached_local_ip_expires_at = 0.0

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
    "ect_biased_cold": "ECT falsa en frÃ­o",
    "o2_slow": "O2 lenta",
    "o2_intermittent": "O2 intermitente",
    "misfire_cyl1": "Misfire cil.1",
    "ckp_intermittent": "CKP intermitente",
    "cmp_fault": "CMP sin seÃ±al",
    "rail_pressure_low": "Riel bajo",
    "boost_low": "Turbo bajo",
    "egr_stuck_open": "EGR atascada",
    "map_slow": "MAP con retardo",
    "throttle_lag": "Throttle con retardo",
    "idle_hunt": "RalentÃ­ inestable",
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
            out[code] = description or "DescripciÃ³n no disponible en la base de datos"
    return out


def ensure_session_registry_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(SESSION_DB_FILE)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_name TEXT NOT NULL,
                session_code TEXT NOT NULL,
                ended_at_ts REAL NOT NULL,
                student_count INTEGER NOT NULL,
                export_csv_path TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_log_id INTEGER NOT NULL,
                student_id TEXT NOT NULL,
                student_name TEXT NOT NULL,
                snapshot_json TEXT,
                FOREIGN KEY(session_log_id) REFERENCES session_logs(id)
            )
            """
        )
        try:
            conn.execute("ALTER TABLE session_students ADD COLUMN snapshot_json TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()


def get_local_ip(force_refresh: bool = False) -> str:
    global _cached_local_ip, _cached_local_ip_expires_at
    now = time.time()
    if not force_refresh and _cached_local_ip and now < _cached_local_ip_expires_at:
        return _cached_local_ip

    detected_ip = "127.0.0.1"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            detected_ip = s.getsockname()[0]
    except Exception:
        try:
            detected_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            detected_ip = "127.0.0.1"
    _cached_local_ip = detected_ip
    _cached_local_ip_expires_at = now + LOCAL_IP_CACHE_TTL_SECONDS
    return detected_ip


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

DTC_CAUSES_OVERRIDES: Dict[str, List[str]] = {
    "P0101": [
        "Sensor MAF sucio o fuera de calibracion.",
        "Entrada de aire no medida (fuga de vacio).",
        "Conector/cableado MAF con mala continuidad.",
    ],
    "P0106": [
        "Sensor MAP con lectura fuera de rango o lenta.",
        "Manguera de vacio fisurada/obstruida.",
        "Falla electrica en referencia de 5V o tierra.",
    ],
    "P0115": [
        "Sensor ECT defectuoso o sesgado.",
        "Conector ECT sulfatado/falso contacto.",
        "Termostato trabado que altera comportamiento termico.",
    ],
    "P0121": [
        "Sensor TPS/Throttle con rango incoherente.",
        "Cuerpo de aceleracion sucio o con atasco.",
        "Aprendizaje/adaptacion de mariposa no realizado.",
    ],
    "P0133": [
        "Sensor de oxigeno B1S1 envejecido (respuesta lenta).",
        "Escape con fugas antes del sensor.",
        "Mezcla fuera de control por MAF/MAP/inyeccion.",
    ],
    "P0171": [
        "Mezcla pobre por entrada de aire no medida.",
        "Presion de combustible baja o inyector restringido.",
        "MAF subestimando flujo real de aire.",
    ],
    "P0172": [
        "Mezcla rica por exceso de combustible.",
        "ECT sesgado en frio que enriquece de mas.",
        "Inyector goteando o presion de combustible alta.",
    ],
    "P0201": [
        "Circuito de inyector cilindro 1 abierto/corto.",
        "Conector o arnes del inyector daÃ±ado.",
        "Driver ECU de inyector con falla.",
    ],
    "P0202": [
        "Circuito de inyector cilindro 2 abierto/corto.",
        "Conector o arnes del inyector daÃ±ado.",
        "Driver ECU de inyector con falla.",
    ],
    "P0203": [
        "Circuito de inyector cilindro 3 abierto/corto.",
        "Conector o arnes del inyector daÃ±ado.",
        "Driver ECU de inyector con falla.",
    ],
    "P0204": [
        "Circuito de inyector cilindro 4 abierto/corto.",
        "Conector o arnes del inyector daÃ±ado.",
        "Driver ECU de inyector con falla.",
    ],
    "P0299": [
        "Presion de sobrealimentacion insuficiente.",
        "Fugas en ductos/intercooler o actuador turbo defectuoso.",
        "Geometria variable/wastegate sin control adecuado.",
    ],
    "P0300": [
        "Misfire multiple aleatorio por combustion inestable.",
        "Baja presion de combustible o encendido debil.",
        "Compresion baja o sincronizacion alterada.",
    ],
    "P0301": [
        "Misfire en cilindro 1 por bobina, bujia o inyector.",
        "Fuga de compresion en cilindro 1.",
        "Problema de cableado/driver de encendido o inyeccion.",
    ],
    "P0302": [
        "Misfire en cilindro 2 por bobina, bujia o inyector.",
        "Fuga de compresion en cilindro 2.",
        "Problema de cableado/driver de encendido o inyeccion.",
    ],
    "P0303": [
        "Misfire en cilindro 3 por bobina, bujia o inyector.",
        "Fuga de compresion en cilindro 3.",
        "Problema de cableado/driver de encendido o inyeccion.",
    ],
    "P0304": [
        "Misfire en cilindro 4 por bobina, bujia o inyector.",
        "Fuga de compresion en cilindro 4.",
        "Problema de cableado/driver de encendido o inyeccion.",
    ],
    "P0335": [
        "Sensor CKP sin seÃ±al o intermitente.",
        "Distancia CKP-rueda fonica incorrecta.",
        "Cableado CKP con interferencia o falso contacto.",
    ],
    "P0340": [
        "Sensor CMP sin seÃ±al o fuera de sincronismo.",
        "Problema en distribuciÃ³n/cadena/correa.",
        "Circuito CMP con falla de alimentacion/tierra.",
    ],
    "P0351": [
        "Circuito primario/secundario bobina cilindro 1 con falla.",
        "Bobina daÃ±ada o con aislamiento degradado.",
        "Driver ECU de bobina con anomalia.",
    ],
    "P0352": [
        "Circuito primario/secundario bobina cilindro 2 con falla.",
        "Bobina daÃ±ada o con aislamiento degradado.",
        "Driver ECU de bobina con anomalia.",
    ],
    "P0353": [
        "Circuito primario/secundario bobina cilindro 3 con falla.",
        "Bobina daÃ±ada o con aislamiento degradado.",
        "Driver ECU de bobina con anomalia.",
    ],
    "P0354": [
        "Circuito primario/secundario bobina cilindro 4 con falla.",
        "Bobina daÃ±ada o con aislamiento degradado.",
        "Driver ECU de bobina con anomalia.",
    ],
    "P0402": [
        "Flujo EGR excesivo por valvula atascada abierta.",
        "Control EGR fuera de rango.",
        "Acumulacion de carbon alterando el asiento de la valvula.",
    ],
    "P0087": [
        "Presion de combustible/riel demasiado baja.",
        "Bomba de alta o regulador con rendimiento insuficiente.",
        "Filtro obstruido o fuga en circuito de combustible.",
    ],
}


def infer_generic_dtc_causes(code: str, description: str) -> List[str]:
    letter = code[:1]
    if letter == "P":
        return [
            "Verificar datos en vivo y freeze frame para confirmar condicion de falla.",
            "Inspeccionar cableado, conectores y tierras del sistema de motor/transmision.",
            "Comprobar integridad mecanica (presion/compresion/sincronismo) antes de reemplazar piezas.",
        ]
    if letter == "B":
        return [
            "Revisar alimentaciones, tierras y red de carroceria relacionadas.",
            "Inspeccionar modulos/actuadores de confort y seguridad asociados.",
            "Confirmar continuidad de arnes y conectores del sistema body.",
        ]
    if letter == "C":
        return [
            "Verificar sensores y actuadores de chasis (ABS/ESP/direccion).",
            "Revisar cableado y conectores en zonas expuestas a vibracion/humedad.",
            "Corroborar seÃ±ales con escaner y pruebas electricas basicas.",
        ]
    if letter == "U":
        return [
            "Diagnosticar red CAN/LIN: resistencia, cortos y terminaciones.",
            "Verificar alimentacion de modulos que pierden comunicacion.",
            "Revisar calidad de masa/chasis y conectores de red.",
        ]
    return [
        "Validar el codigo en la base DTC del fabricante.",
        "Revisar datos de contexto y repetir prueba de confirmacion.",
    ]

EVALUATION_QUESTION_BANK: List[Dict[str, Any]] = [
{
        "id": "q01",
        "text": "Un DTC activo/confirmado indica:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Falla detectada y validada por la ECU."},
            {"id": "b", "text": "Solo una prueba visual sin evidencia."},
            {"id": "c", "text": "Codigo borrado automaticamente."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q02",
        "text": "Un DTC historico significa:",
        "points": 2,
        "options": [
            {"id": "a", "text": "La falla ocurrio antes y ahora puede no estar presente."},
            {"id": "b", "text": "La falla esta ocurriendo en este instante siempre."},
            {"id": "c", "text": "El modulo no tiene memoria."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q03",
        "text": "Un DTC pendiente indica:",
        "points": 2,
        "options": [
            {"id": "a", "text": "La condicion de falla aparecio, pero aun no se confirma en todos los ciclos."},
            {"id": "b", "text": "La ECU quedo bloqueada permanentemente."},
            {"id": "c", "text": "El codigo es falso y debe ignorarse."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q04",
        "text": "En un codigo DTC, la letra P significa:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Powertrain (motor/transmision)."},
            {"id": "b", "text": "Parking assist."},
            {"id": "c", "text": "Programming mode."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q05",
        "text": "En un codigo DTC, la letra C significa:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Chassis (frenos, direccion, estabilidad)."},
            {"id": "b", "text": "Combustion chamber."},
            {"id": "c", "text": "Cooling only."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q06",
        "text": "En un codigo DTC, la letra B significa:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Body (carroceria/confort/seguridad)."},
            {"id": "b", "text": "Battery pack only."},
            {"id": "c", "text": "Boost control."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q07",
        "text": "En un codigo DTC, la letra U significa:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Network/Communication (red entre modulos)."},
            {"id": "b", "text": "Underdrive clutch."},
            {"id": "c", "text": "Unidad de usuario."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q08",
        "text": "Para diagnosticar un DTC con contexto real, la combinacion mas correcta es:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Modo 03 + Modo 02 + datos en vivo (Modo 01)."},
            {"id": "b", "text": "Solo Modo 04 para borrar y volver a probar."},
            {"id": "c", "text": "Solo Modo 09 (VIN)."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q09",
        "text": "Al borrar DTC (Modo 04), una buena practica tecnica es:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Corregir causa raiz primero y luego validar que no reaparezca."},
            {"id": "b", "text": "Borrar sin mirar freeze frame ni PIDs."},
            {"id": "c", "text": "Desconectar sensores para ocultar la falla."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q10",
        "text": "Que modo del escaner se usa para PIDs en vivo?",
        "points": 2,
        "options": [
            {"id": "a", "text": "Modo 01"},
            {"id": "b", "text": "Modo 04"},
            {"id": "c", "text": "Modo 09"},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q11",
        "text": "Que modo se usa para freeze frame?",
        "points": 2,
        "options": [
            {"id": "a", "text": "Modo 02"},
            {"id": "b", "text": "Modo 05"},
            {"id": "c", "text": "Modo 08"},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q12",
        "text": "Que modo se usa para leer codigos almacenados?",
        "points": 2,
        "options": [
            {"id": "a", "text": "Modo 03"},
            {"id": "b", "text": "Modo 01"},
            {"id": "c", "text": "Modo 09"},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q13",
        "text": "Que modo se usa para borrar codigos?",
        "points": 2,
        "options": [
            {"id": "a", "text": "Modo 04"},
            {"id": "b", "text": "Modo 07"},
            {"id": "c", "text": "Modo 06"},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q14",
        "text": "En esta plataforma, el Modo 05 esta enfocado en:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Pruebas de sensores de oxigeno."},
            {"id": "b", "text": "Cambio de VIN."},
            {"id": "c", "text": "Borrado de adaptaciones."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q15",
        "text": "Que modo reporta resultados de monitores internos?",
        "points": 2,
        "options": [
            {"id": "a", "text": "Modo 06"},
            {"id": "b", "text": "Modo 03"},
            {"id": "c", "text": "Modo 02"},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q16",
        "text": "Que modo muestra DTC pendientes antes de confirmarse?",
        "points": 2,
        "options": [
            {"id": "a", "text": "Modo 07"},
            {"id": "b", "text": "Modo 04"},
            {"id": "c", "text": "Modo 01"},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q17",
        "text": "Que modo corresponde al control bidireccional de actuadores?",
        "points": 2,
        "options": [
            {"id": "a", "text": "Modo 08"},
            {"id": "b", "text": "Modo 05"},
            {"id": "c", "text": "Modo 09"},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q18",
        "text": "Que modo se usa para info ECU (VIN/perfil/protocolo)?",
        "points": 2,
        "options": [
            {"id": "a", "text": "Modo 09"},
            {"id": "b", "text": "Modo 01"},
            {"id": "c", "text": "Modo 06"},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q20",
        "text": "APP y throttle en aceleracion saludable deben:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Subir de forma correlacionada."},
            {"id": "b", "text": "Ir en sentido opuesto."},
            {"id": "c", "text": "Quedarse fijos siempre."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q21",
        "text": "En gasolina sin carga, MAP en ralenti normalmente es:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Menor que en alta carga."},
            {"id": "b", "text": "Mayor que en plena carga."},
            {"id": "c", "text": "Identico en todo regimen."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q22",
        "text": "Al acelerar, un MAF sano debe:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Incrementar flujo de aire de forma coherente."},
            {"id": "b", "text": "Bajar permanentemente."},
            {"id": "c", "text": "Quedarse clavado en cero."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q23",
        "text": "Con KOEO y motor frio, IAT suele estar:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Cerca de temperatura ambiente."},
            {"id": "b", "text": "Siempre por encima de 120 C."},
            {"id": "c", "text": "Siempre negativa en clima calido."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q24",
        "text": "ECT durante calentamiento normal debe:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Subir progresivamente hasta temperatura de operacion."},
            {"id": "b", "text": "Bajar al acelerar."},
            {"id": "c", "text": "Permanecer fija en 0 C."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q25",
        "text": "En lazo cerrado saludable, O2 B1S1 suele:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Oscilar cruzando zona rica y pobre."},
            {"id": "b", "text": "Quedarse fija en 0.45 V todo el tiempo."},
            {"id": "c", "text": "Subir linealmente sin oscilacion."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q26",
        "text": "O2 B1S2 (post-catalizador) saludable suele verse:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Mas estable que B1S1."},
            {"id": "b", "text": "Conmutando mas rapido que B1S1."},
            {"id": "c", "text": "Siempre en 0.0 V."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q27",
        "text": "STFT muy positivo de forma sostenida sugiere:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Correccion por mezcla pobre."},
            {"id": "b", "text": "Exceso de avance de encendido solamente."},
            {"id": "c", "text": "Falla exclusiva de ABS."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q28",
        "text": "LTFT representa principalmente:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Ajuste de combustible de largo plazo."},
            {"id": "b", "text": "Voltaje de bateria instantaneo."},
            {"id": "c", "text": "Estado de ventilador unicamente."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q29",
        "text": "Con motor encendido, bateria saludable suele estar cerca de:",
        "points": 2,
        "options": [
            {"id": "a", "text": "13.5 a 14.8 V aprox."},
            {"id": "b", "text": "8.0 V aprox."},
            {"id": "c", "text": "2.0 V aprox."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q30",
        "text": "Fuel pressure baja bajo carga puede causar:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Condicion pobre, tirones y posible misfire."},
            {"id": "b", "text": "Lectura perfecta de todos los sensores."},
            {"id": "c", "text": "Comunicacion CAN mas rapida."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q31",
        "text": "En diesel, rail pressure saludable al aumentar carga tiende a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Subir segun demanda."},
            {"id": "b", "text": "Caer a cero siempre."},
            {"id": "c", "text": "No cambiar nunca."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q32",
        "text": "Si APP sube y boost no responde, una sospecha razonable es:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Falla de control de turbo o fuga en sobrealimentacion."},
            {"id": "b", "text": "Falla exclusiva de luces interiores."},
            {"id": "c", "text": "VIN incorrecto."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q33",
        "text": "EGR excesivamente abierta en ralenti puede provocar:",
        "points": 2,
        "options": [
            {"id": "a", "text": "RalentI inestable y posible caida de RPM."},
            {"id": "b", "text": "Combustion siempre mas eficiente."},
            {"id": "c", "text": "Aumento fijo de bateria."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q34",
        "text": "inj_cmd_ms al incrementar carga normalmente:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Aumenta para aportar mas combustible."},
            {"id": "b", "text": "Debe quedar fijo en 0.1 ms."},
            {"id": "c", "text": "Debe bajar a cero en aceleracion."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q35",
        "text": "inj_dead_time_ms representa:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Tiempo no lineal de apertura/cierre del inyector."},
            {"id": "b", "text": "Tiempo de giro del ventilador."},
            {"id": "c", "text": "Tiempo de respuesta del pedal APP."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q36",
        "text": "En esta simulacion, inj_ms se interpreta como:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Inyeccion fisica resultante (cmd + dead time)."},
            {"id": "b", "text": "Voltaje del sensor O2."},
            {"id": "c", "text": "Estado del Modo 09."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q37",
        "text": "spark_advance debe:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Variar con regimen/carga, no permanecer fijo siempre."},
            {"id": "b", "text": "Ser siempre 0 grados."},
            {"id": "c", "text": "Ser igual al MAP en kPa."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q38",
        "text": "calculated_load normalmente aumenta cuando:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Suben APP y demanda de aire/combustible."},
            {"id": "b", "text": "El motor esta apagado (KOEO)."},
            {"id": "c", "text": "Se borran DTC."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q39",
        "text": "CKP intermitente suele reflejarse en:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Inestabilidad de RPM y sincronia CKP."},
            {"id": "b", "text": "Mejora inmediata de combustion."},
            {"id": "c", "text": "Aumento fijo de O2 B1S2."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q40",
        "text": "CMP sin senal valida puede provocar:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Errores de sincronismo y fallas de arranque/rendimiento."},
            {"id": "b", "text": "Solo cambio cosmetico en tablero."},
            {"id": "c", "text": "Ninguna consecuencia en la ECU."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q41",
        "text": "Con ECT alta, cooling_fan deberia:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Activarse para controlar temperatura."},
            {"id": "b", "text": "Apagarse siempre."},
            {"id": "c", "text": "Invertir polaridad de bateria."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q42",
        "text": "Para un diagnostico tecnico completo, lo mas correcto es:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Cruzar DTC + freeze frame + PIDs + prueba funcional."},
            {"id": "b", "text": "Basarse solo en un codigo y reemplazar piezas."},
            {"id": "c", "text": "Ignorar modos del escaner."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q43",
        "text": "Si STFT esta en +22% y LTFT en +18%, la mezcla tiende a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Pobre."},
            {"id": "b", "text": "Rica."},
            {"id": "c", "text": "Estequiometrica perfecta sin correccion."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q44",
        "text": "Si STFT esta en -20% y LTFT en -15%, la mezcla tiende a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Rica."},
            {"id": "b", "text": "Pobre."},
            {"id": "c", "text": "Sin cambios de combustible."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q45",
        "text": "En gasolina a ralenti estable, O2 B1S1 en lazo cerrado debe:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Oscilar entre zona baja y zona alta."},
            {"id": "b", "text": "Permanecer fijo en 0.45 V por minutos."},
            {"id": "c", "text": "Permanecer en 0 V de forma constante."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q46",
        "text": "Un O2 B1S1 que cambia muy lento suele indicar:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Sensor envejecido o mezcla fuera de control."},
            {"id": "b", "text": "Bateria completamente cargada."},
            {"id": "c", "text": "Operacion perfecta sin fallas."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q47",
        "text": "MAP alto en ralenti (motor gasolina sin turbo) sugiere primero:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Fuga de vacio o problema mecanico de sincronizacion."},
            {"id": "b", "text": "Catalizador nuevo."},
            {"id": "c", "text": "A/C apagado."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q48",
        "text": "Si aceleras y el MAF casi no sube, una sospecha inicial es:",
        "points": 2,
        "options": [
            {"id": "a", "text": "MAF sucio/restringido o admision anormal."},
            {"id": "b", "text": "Solo falla en limpiaparabrisas."},
            {"id": "c", "text": "Compresion perfecta en todos los cilindros."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q49",
        "text": "ECT sesgado en frio provoca normalmente:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Enriquecimiento excesivo y consumo alto."},
            {"id": "b", "text": "Corte total de inyeccion en ralenti."},
            {"id": "c", "text": "Sin cambios en estrategia de mezcla."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q50",
        "text": "Con KOEO, el MAP debe estar cercano a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Presion atmosferica local."},
            {"id": "b", "text": "Vacuo de ralenti."},
            {"id": "c", "text": "0 kPa absoluto."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q51",
        "text": "El DTC P0171 se asocia principalmente a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Mezcla pobre."},
            {"id": "b", "text": "Mezcla rica."},
            {"id": "c", "text": "Falla de red CAN como causa primaria."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q52",
        "text": "El DTC P0172 se asocia principalmente a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Mezcla rica."},
            {"id": "b", "text": "Mezcla pobre."},
            {"id": "c", "text": "Falla exclusiva de ABS."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q53",
        "text": "Si STFT cambia rapido y LTFT casi no cambia, eso describe mejor:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Correccion de corto plazo activa con adaptacion lenta de largo plazo."},
            {"id": "b", "text": "Sensor CKP desconectado."},
            {"id": "c", "text": "Prueba de compresion mecanica."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q54",
        "text": "Si LTFT se mantiene alto positivo por tiempo prolongado, suele indicar:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Tendencia pobre sostenida en la mezcla."},
            {"id": "b", "text": "Mezcla rica sostenida."},
            {"id": "c", "text": "Alternador sobrecargado."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q55",
        "text": "En diagnostico de mezcla, el orden tecnico recomendado es:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Contexto de etapa + PIDs + DTC + freeze frame."},
            {"id": "b", "text": "Borrar codigos y entregar el auto sin validar."},
            {"id": "c", "text": "Cambiar piezas al azar."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q56",
        "text": "El Modo 01 del escaner corresponde a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Lectura de datos en vivo (PIDs)."},
            {"id": "b", "text": "Borrado de codigos."},
            {"id": "c", "text": "Informacion de VIN."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q57",
        "text": "El Modo 02 del escaner corresponde a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Freeze frame."},
            {"id": "b", "text": "Control bidireccional."},
            {"id": "c", "text": "Pruebas de red UDS."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q58",
        "text": "El Modo 03 del escaner corresponde a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Lectura de codigos DTC almacenados."},
            {"id": "b", "text": "Ajuste de cuerpo de aceleracion."},
            {"id": "c", "text": "Prueba de compresion."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q59",
        "text": "El Modo 04 del escaner corresponde a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Borrado de codigos y reseteo basico de monitores."},
            {"id": "b", "text": "Lectura de VIN."},
            {"id": "c", "text": "Activacion de actuadores."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q60",
        "text": "El Modo 07 del escaner corresponde a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Lectura de codigos pendientes."},
            {"id": "b", "text": "Borrado total de memoria EEPROM."},
            {"id": "c", "text": "Programacion de llaves."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q61",
        "text": "El Modo 09 del escaner corresponde a:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Informacion de ECU/vehiculo como VIN."},
            {"id": "b", "text": "Balanceo de inyectores en banco."},
            {"id": "c", "text": "Reflash de software ECM."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q62",
        "text": "Si un DTC esta pendiente, historico y activo en evaluacion, su prioridad de atencion es:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Activo primero, luego pendiente y luego historico."},
            {"id": "b", "text": "Historico primero siempre."},
            {"id": "c", "text": "Todos con la misma urgencia tecnica."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q63",
        "text": "Al ver O2 alto y trims muy negativos de forma repetida, lo mas probable es:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Condicion rica que la ECU intenta corregir quitando combustible."},
            {"id": "b", "text": "Condicion pobre extrema."},
            {"id": "c", "text": "Sensor MAP desconectado con lectura perfecta."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q64",
        "text": "Al ver O2 bajo y trims muy positivos de forma repetida, lo mas probable es:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Condicion pobre que la ECU intenta corregir agregando combustible."},
            {"id": "b", "text": "Condicion rica sostenida."},
            {"id": "c", "text": "Bateria sobrecargada."},
        ],
        "correct_option_id": "a",
    },
{
        "id": "q65",
        "text": "Un tecnico evita conclusiones falsas cuando:",
        "points": 2,
        "options": [
            {"id": "a", "text": "Relaciona comportamiento de graficas con etapa de operacion y carga."},
            {"id": "b", "text": "Diagnostica con un solo valor aislado."},
            {"id": "c", "text": "Ignora los datos en vivo y solo borra codigos."},
        ],
        "correct_option_id": "a",
    }
]

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
        self._o2_next_flip_ts = 0.0
        self._o2_micro_bias = 0.0
        self._o2_phase = 0.0
        self._o2_phase_started_ts = 0.0
        self._o2_phase_duration = 0.35
        self._o2_prev_peak_target = 0.14
        self._o2_peak_target = 0.86
        self._monitor_frame_source: Dict[str, float] = {}
        self._last_stage = "key_on"
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
        self.engine_shutdown_cause = ""
        self._low_rpm_elapsed = 0.0
        self._engine_on_elapsed = 0.0
        self.auto_adjust_enabled = False
        self.auto_variation_pct = 0.0
        self.scenario_name = "Normal / referencia"
        self.current_preset = "normal"
        self.sensors = self._default_sensor_set()
        self.actuators = self._default_actuators()
        self.reset_runtime()

    def _default_sensor_set(self) -> Dict[str, float]:
        diesel_mode = self.mode == "diesel"
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
            "o2_b1s1": 0.0 if diesel_mode else 0.45,
            "o2_b1s2": 0.0 if diesel_mode else 0.45,
            "stft": 0.0,
            "ltft": 0.0,
            "battery_voltage": 12.3,
            "fuel_pressure": 180.0 if diesel_mode else 350.0,
            "rail_pressure": 40.0 if diesel_mode else 300.0,
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

    def _student_link(self, local_ip: str | None = None) -> str:
        ip = local_ip or get_local_ip()
        return f"http://{ip}:8000/student"

    def _whatsapp_link(self, local_ip: str | None = None) -> str:
        text = f"Accede al panel 2 de {PROJECT_NAME}: {self._student_link(local_ip)}"
        encoded = text.replace(" ", "%20").replace(":", "%3A").replace("/", "%2F")
        base = "https://wa.me/"
        number = "".join(ch for ch in self.whatsapp_number if ch.isdigit())
        return f"{base}{number}?text={encoded}" if number else f"https://wa.me/?text={encoded}"

    def snapshot(self) -> Dict[str, Any]:
        local_ip = get_local_ip()
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
            "auto_adjust": {
                "enabled": self.auto_adjust_enabled,
                "variation_pct": self.auto_variation_pct,
            },
            "connection": {
                "ip": local_ip,
                "student_link": self._student_link(local_ip),
                "whatsapp_number": self.whatsapp_number,
                "whatsapp_link": self._whatsapp_link(local_ip),
            },
        }

    async def apply_command(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with self._lock:
            if action == "set_mode":
                new_mode = payload.get("mode", "gasoline")
                if new_mode in {"gasoline", "diesel"}:
                    self.mode = new_mode
                    self.vehicle_profile = "Gasolina 1.6 MPI" if new_mode == "gasoline" else "DiÃ©sel 2.0 Common Rail"
                    self.reset_runtime()
            elif action == "toggle_engine":
                target = bool(payload.get("engine_on", not self.engine_on))
                self.engine_on = target
                if self.engine_on and self.stage == "key_on":
                    self.stage = "idle"
                    self.engine_shutdown_alert = False
                    self.engine_shutdown_message = ""
                    self.engine_shutdown_cause = ""
                    self._low_rpm_elapsed = 0.0
                elif not self.engine_on:
                    self.stage = "key_on"
                    self._low_rpm_elapsed = 0.0
            elif action == "set_stage":
                stage = payload.get("stage", "key_on")
                if stage in {"key_on", "crank", "idle", "part_load", "high_load", "decel"}:
                    self.stage = stage
                    self.engine_on = stage != "key_on"
                    if self.engine_on:
                        self.engine_shutdown_alert = False
                        self.engine_shutdown_message = ""
                        self.engine_shutdown_cause = ""
                    self._low_rpm_elapsed = 0.0
            elif action == "set_sensor":
                key = payload.get("key")
                value = payload.get("value")
                if key in self.sensors and isinstance(value, (int, float)):
                    if self.auto_adjust_enabled and key in {"rpm", "map", "maf", "rail_pressure"}:
                        pass
                    else:
                        self.manual_overrides[key] = float(value)
            elif action == "set_auto_adjust":
                self.auto_adjust_enabled = bool(payload.get("enabled", self.auto_adjust_enabled))
                variation = payload.get("variation_pct", self.auto_variation_pct)
                if isinstance(variation, (int, float)):
                    self.auto_variation_pct = float(self._clamp(float(variation), -35.0, 35.0))
                if not self.auto_adjust_enabled:
                    for key in ("rpm", "map", "maf", "rail_pressure"):
                        self.manual_overrides.pop(key, None)
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
        self.engine_shutdown_alert = False
        self.engine_shutdown_message = ""
        self.engine_shutdown_cause = ""
        self._low_rpm_elapsed = 0.0
        self._engine_on_elapsed = 0.0
        self._t = 0.0
        self._o2_flip = False
        self._o2_next_flip_ts = 0.0
        self._o2_micro_bias = 0.0
        self._o2_phase = 0.0
        self._o2_phase_started_ts = 0.0
        self._o2_phase_duration = 0.35
        self._o2_prev_peak_target = 0.14
        self._o2_peak_target = 0.86
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
            "En esta plataforma educativa, el mando se realiza solo desde el Panel 1 del instructor para simular fallas y obligar al anÃ¡lisis en el Panel 2. "
            "Por esa razÃ³n, los actuadores especÃ­ficos no se muestran aquÃ­ por el momento."
        )
        if active:
            self.last_mode08_note = base + " Actualmente hay una prueba o alteraciÃ³n comandada desde el Panel 1 que puede modificar el comportamiento del motor."
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
            "title": "Modo 08 Â· Control de actuadores",
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
            self.scenario_name = "Arranque en frÃ­o"
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
            self.scenario_name = "Escenario: ECT falsa en frÃ­o"
            self.faults.update({"ect_biased_cold"})
        elif preset == "misfire":
            self.scenario_name = "Escenario: misfire cilindro 1"
            self.faults.update({"misfire_cyl1"})
        elif preset == "diesel_rail":
            self.scenario_name = "Escenario: diÃ©sel con riel bajo"
            self.mode = "diesel"
            self.vehicle_profile = "DiÃ©sel 2.0 Common Rail"
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
            self.scenario_name = "Escenario: ralentÃ­ inestable"
            self.faults.update({"idle_hunt"})
        elif preset == "o2_intermittent":
            self.scenario_name = "Escenario: O2 intermitente"
            self.faults.update({"o2_intermittent"})
        elif preset == "turbo_low":
            self.scenario_name = "Escenario: turbo bajo"
            self.mode = "diesel"
            self.vehicle_profile = "DiÃ©sel 2.0 Common Rail"
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
        desc = DTC_DESCRIPTION_OVERRIDES.get(code) or DTC_DB.get(code, "DescripciÃ³n no disponible en la base de datos")
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
        # Valor de escÃ¡ner: tiempo de inyecciÃ³n comandado por la ECU.
        inj_cmd_ms = self._clamp(1.22 + load * 0.012 + (rpm - idle_rpm) / 6000.0 * 0.35, 1.2, 10.8)
        # Valor fÃ­sico total: tiempo comandado + retardo de apertura del inyector (dead time).
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
        low_side_fuel = self._clamp(420.0 + app_pct * 1.1, 360.0, 560.0)
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
            # En diesel educativo esta plataforma no usa sensor O2 para diagnostico.
            "o2_b1s1": 0.0,
            "o2_b1s2": 0.0,
            "stft": 0.0,
            "ltft": 0.0,
            "battery_voltage": battery,
            "fuel_pressure": low_side_fuel,
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
            actual["fuel_pressure"] = self._clamp(expected.get("fuel_pressure", 420.0) * (0.92 + 0.08 * rpm_ratio), 260.0, 650.0)

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
        prev_app = float(previous.get("app", app_pct))
        app_delta = app_pct - prev_app
        accel_factor = self._clamp(max(app_delta, 0.0) / 6.0 + app_pct / 100.0, 0.0, 1.6)
        if "maf_low" in self.faults:
            actual["maf"] *= 0.58
            severity += 0.35
        if "maf_dirty" in self.faults:
            # MAF sucio educativo: rezago, sub-respuesta en aceleracion y ruido irregular.
            dirty_ratio = self._clamp(0.82 - 0.15 * accel_factor, 0.52, 0.90)
            dirty_bias_target = expected["maf"] * dirty_ratio
            lag_alpha = 0.02 if app_delta > 0.25 else 0.04
            lagged_maf = self._smooth_value(previous.get("maf", dirty_bias_target), dirty_bias_target, lag_alpha)
            ripple_amp = 0.18 + 0.55 * accel_factor
            ripple = ripple_amp * math.sin(self._t * 4.8) + (0.10 + 0.25 * accel_factor) * math.sin(self._t * 11.2 + 0.8)
            drop_gate = math.sin(self._t * 1.15 + accel_factor * 1.9)
            micro_drop = -(0.35 + 0.85 * accel_factor) * abs(math.sin(self._t * 14.2)) if drop_gate > 0.62 else 0.0
            actual["maf"] = max(0.20, lagged_maf + ripple + micro_drop)
            severity += 0.24
        if "vacuum_leak" in self.faults:
            actual["map"] += 9.0 + 2.8 * math.sin(self._t * 1.8) + 5.5 * accel_factor
            actual["maf"] *= self._clamp(0.96 - 0.10 * accel_factor, 0.78, 0.96)
            severity += 0.26
        if "ect_biased_cold" in self.faults:
            actual["ect"] = self._smooth_value(previous.get("ect", 12.0), 12.0, 0.12)
            severity += 0.20
        if "o2_slow" in self.faults:
            base_o2 = 0.24 if app_delta > 0.2 else 0.78
            target_o2 = self._clamp(base_o2 + 0.08 * math.sin(self._t * (0.35 + 0.15 * accel_factor)), 0.06, 0.96)
            actual["o2_b1s1"] = self._smooth_value(previous.get("o2_b1s1", target_o2), target_o2, 0.025)
            severity += 0.16
        if "o2_intermittent" in self.faults:
            drop_prob = 0.10 + 0.16 * accel_factor
            if random.random() < drop_prob:
                actual["o2_b1s1"] = 0.05 if random.random() < 0.5 else 0.95
            else:
                noisy = expected.get("o2_b1s1", actual.get("o2_b1s1", 0.45)) + 0.07 * math.sin(self._t * 9.0)
                actual["o2_b1s1"] = self._clamp(noisy, 0.05, 0.95)
            severity += 0.14
        if "misfire_cyl1" in self.faults:
            drop = 80.0 + 35.0 * abs(math.sin(self._t * 6.2))
            actual["rpm"] -= drop
            actual["map"] += 7.0
            actual["maf"] *= 0.92
            severity += 0.38
        if "ckp_intermittent" in self.faults:
            actual["ckp_sync"] = 0.0 if random.random() < (0.18 + 0.24 * accel_factor) else 1.0
            if actual["ckp_sync"] < 0.5:
                actual["rpm"] *= 0.55 if app_pct > 20.0 else 0.68
                actual["map"] += 3.0 + 6.0 * accel_factor
            severity += 0.24
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
            lag_alpha = 0.04 if app_delta > 0.2 else 0.08
            lagged_map = self._smooth_value(previous.get("map", map_target), map_target, lag_alpha)
            if app_delta > 0.2:
                lagged_map -= 2.0 + 7.0 * accel_factor
            ringing = (1.6 + 6.0 * accel_factor) * math.sin(self._t * 2.2)
            map_hi = 260.0 if self.mode == "diesel" else 120.0
            actual["map"] = self._clamp(lagged_map + ringing, 15.0, map_hi)
            severity += 0.17
        if "throttle_lag" in self.faults:
            throttle_ratio = self._clamp(0.88 - 0.20 * accel_factor, 0.54, 0.88)
            throttle_target = expected["throttle"] * throttle_ratio
            lag_alpha = 0.04 if app_delta > 0.2 else 0.08
            lagged_throttle = self._smooth_value(previous.get("throttle", throttle_target), throttle_target, lag_alpha)
            if app_delta > 0.2:
                lagged_throttle -= min(8.0, app_delta * 10.0)
            actual["throttle"] = self._clamp(lagged_throttle, 0.0, 100.0)
            severity += 0.18
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
            cyl_results.append(row(f"$06_MIS_C{cyl}", f"Modo 06 Â· Misfire cilindro {cyl}", current, 0.78, 1.02, "ratio", fail=fail, note="Ãndice de combustiÃ³n por cilindro"))

        rows = [
            row("$06_MAF_RAT", "Modo 06 Â· Racionalidad MAF", abs(maf - expected_maf) / max(expected_maf, 0.1), 0.0, 0.22, "ratio", fail=("P0101" in dtc_codes) or ("maf_low" in faults) or ("maf_dirty" in faults), note="DesviaciÃ³n frente al aire esperado"),
            row("$06_MAP_RAT", "Modo 06 Â· Respuesta MAP", abs(map_kpa - expected_map) / max(expected_map, 1.0), 0.0, 0.18, "ratio", fail=("P0106" in dtc_codes) or ("vacuum_leak" in faults) or ("map_slow" in faults), note="DesviaciÃ³n MAP respecto al cÃ¡lculo"),
            row("$06_TPS_RAT", "Modo 06 Â· Seguimiento throttle", abs(throttle - expected_throttle), 0.0, 8.0, "%", fail=("P0121" in dtc_codes) or ("throttle_lag" in faults), note="Error entre throttle real y esperado"),
            row("$06_EGR", "Modo 06 Â· Flujo EGR", egr, 0.0, 18.0 if app < 15 else 32.0, "%", fail=("P0402" in dtc_codes) or ("egr_stuck_open" in faults), note="Apertura EGR comparada con la carga actual"),
            row("$06_CKP_CMP", "Modo 06 Â· SincronÃ­a CKP/CMP", min(ckp, cmpv), 0.95, 1.02, "ratio", fail=("P0335" in dtc_codes) or ("P0340" in dtc_codes) or ckp < 0.95 or cmpv < 0.95, note="Ãndice de sincronÃ­a entre CKP y CMP"),
            row("$06_EVAP_PURGE", "Modo 06 Â· Purga EVAP", 1.0 if not self.actuators.get("purge_cmd", False) else 0.82, 0.78, 1.02, "ratio", fail=False, note="Resultado del autotest de purga"),
            row("$06_COOL_FAN", "Modo 06 Â· Control electroventilador", fan, 0.0, 1.0, "state", fail=(self.engine_on and ect >= 96.0 and fan < 0.5), note="Debe activarse por encima de 96 Â°C"),
        ]

        if self.mode == "diesel":
            rows.extend([
                row("$06_RAIL", "Modo 06 Â· PresiÃ³n de riel", abs(rail - expected_rail) / max(expected_rail, 1.0), 0.0, 0.22, "ratio", fail=("P0087" in dtc_codes) or ("rail_pressure_low" in faults), note="Error relativo de presiÃ³n de riel"),
                row("$06_BOOST", "Modo 06 Â· Control de turbo", abs(boost - expected_boost) / max(expected_boost, 1.0), 0.0, 0.18, "ratio", fail=("P0299" in dtc_codes) or ("boost_low" in faults), note="Error relativo de sobrealimentaciÃ³n"),
            ])
        else:
            rows.extend([
                row("$06_CAT", "Modo 06 Â· Eficiencia catalizador", cat_ratio, 0.62, 1.02, "ratio", fail=("P0420" in dtc_codes) or (cat_ratio < 0.62), note="RelaciÃ³n de actividad O2 tras catalizador"),
                row("$06_O2_RESP", "Modo 06 Â· Respuesta O2 B1S1", o2_switch_activity, 0.28, 0.95, "ratio", fail=("P0133" in dtc_codes) or ("o2_slow" in faults) or ("o2_intermittent" in faults), note="Velocidad de cruce rico/pobre"),
                row("$06_O2_HEAT", "Modo 06 Â· Calefactor O2", batt if self.engine_on else 0.0, 11.5, 15.2, "V", fail=(self.engine_on and batt < 11.5), note="AlimentaciÃ³n disponible al calentador"),
                row("$06_FUEL_TRIM", "Modo 06 Â· Balance combustible", abs(stft + ltft), 0.0, 18.0, "%", fail=(closed_loop and abs(stft + ltft) > 18.0) or ("P0171" in dtc_codes) or ("P0172" in dtc_codes), note="Suma de correcciones STFT + LTFT"),
                row("$06_FUEL_P", "Modo 06 Â· PresiÃ³n de combustible", fuel_p, 280.0, 420.0, "kPa", fail=(self.engine_on and not self.actuators.get("fuel_pump", True)), note="Prueba de suministro de combustible"),
                row("$06_IDLE_CTL", "Modo 06 Â· Control de ralentÃ­", abs(rpm - expected_rpm), 0.0, 90.0, "rpm", fail=(self.stage == "idle" and abs(rpm - expected_rpm) > 90.0) or ("idle_hunt" in faults), note="Error entre ralentÃ­ objetivo y real"),
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

        # Calentamiento normal del motor hasta temperatura de operaciÃ³n.
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

        # RalentÃ­ alto en frÃ­o y retorno a ralentÃ­ mÃ­nimo cuando APP <= 3%.
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
        if self.auto_adjust_enabled and self.engine_on:
            v = self._clamp(self.auto_variation_pct / 100.0, -0.35, 0.35)
            if self.mode == "gasoline":
                rpm_target = (780.0 + (app_pct * 28.0)) * (1.0 + (v * 0.55))
                map_target = (24.0 + (app_pct * 1.18)) * (1.0 + (v * 0.75))
                maf_target = (1.8 + (app_pct * 0.23)) * (1.0 + (v * 0.85))
                rail_target = 300.0
            else:
                rpm_target = (760.0 + (app_pct * 24.0)) * (1.0 + (v * 0.50))
                map_target = (95.0 + (app_pct * 0.95)) * (1.0 + (v * 0.55))
                maf_target = (3.0 + (app_pct * 0.28)) * (1.0 + (v * 0.70))
                rail_target = (320.0 + (app_pct * 8.5)) * (1.0 + (v * 0.90))

            self.manual_overrides["rpm"] = self._clamp(rpm_target, 380.0, 4500.0)
            self.manual_overrides["map"] = self._clamp(map_target, 15.0, 260.0)
            self.manual_overrides["maf"] = self._clamp(maf_target, 0.0, 60.0)
            self.manual_overrides["rail_pressure"] = self._clamp(rail_target, 100.0, 1800.0)

        if not self.engine_on or self.stage == "key_on":
            self._engine_on_elapsed = 0.0
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

        self._engine_on_elapsed += dt
        expected = self._compute_gasoline_expected(app_pct) if self.mode == "gasoline" else self._compute_diesel_expected(app_pct)
        previous = dict(self.sensors)
        self._apply_temperature_model(expected, previous, app_pct)

        manual_misfire_flags = {f for f in self.faults if f.startswith("misfire_cyl")}
        effective_misfire_flags = set(manual_misfire_flags)

        if not self.actuators.get("fuel_pump", True):
            expected["fuel_pressure"] = 40.0
            if self.mode == "diesel":
                expected["fuel_pressure"] = 110.0
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
                actual["fuel_pressure"] = self._smooth_value(previous.get("fuel_pressure", 180.0), 90.0, 0.25)
                actual["rail_pressure"] = self._smooth_value(previous.get("rail_pressure", 300.0), 160.0, 0.25)
            actual["rpm"] -= 180.0
            actual["map"] += 5.0

        # Una sola bobina o inyector desconectado no debe llevar rpm a colapso inmediato.
        single_cyl_actuator_fault = (injector_fault_count + coil_fault_count) == 1 and self.actuators.get("fuel_pump", True)
        if single_cyl_actuator_fault:
            rpm_floor = 430.0 if self.mode == "gasoline" else 380.0
            actual["rpm"] = max(actual.get("rpm", 0.0), rpm_floor)

        # Al reactivar actuadores, la dinÃ¡mica debe regresar a la normalidad del escenario actual.
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
        # Estabilidad educativa: con APP > 0, las RPM no deben oscilar de forma excesiva.
        # Si no hay fallas activas ni manipulaciones fuertes, se acota la variacion.
        if self.engine_on and app_pct > 2.0:
            has_non_app_manual = any(k != "app" for k in self.manual_overrides.keys())
            stable_context = (len(self.faults) == 0) and (not has_non_app_manual) and (not actuator_disturbance_active)
            expected_rpm = float(expected.get("rpm", actual.get("rpm", 0.0)))
            band = 85.0 if stable_context else 160.0
            rpm_min = max(0.0, expected_rpm - band)
            rpm_max = expected_rpm + band
            bounded_rpm = self._clamp(float(actual.get("rpm", 0.0)), rpm_min, rpm_max)
            smooth_alpha = 0.28 if stable_context else 0.20
            actual["rpm"] = self._smooth_value(float(previous.get("rpm", bounded_rpm)), bounded_rpm, smooth_alpha)
        actual["rpm"] = max(0.0, actual.get("rpm", 0.0))

        active_cylinders = max(0, 4 - len(effective_misfire_flags))
        severe_shutdown = active_cylinders <= 1
        if severe_shutdown:
            self.engine_on = False
            self.stage = "key_on"
            self.engine_shutdown_alert = True
            self.engine_shutdown_message = "âš  Motor apagado por falla"
            self.engine_shutdown_cause = "misfire"
            for key in ("rpm", "vehicle_speed", "maf", "inj_cmd_ms", "inj_ms", "calculated_load"):
                actual[key] = 0.0
            actual["map"] = 100.0
            actual["stft"] = 0.0
            actual["ltft"] = 0.0
            actual["o2_b1s1"] = 0.45
            actual["o2_b1s2"] = 0.45
            effective_misfire_flags = set(self._root_cause_cylinder_flags())
        elif not self._root_cause_cylinder_flags() and self.engine_shutdown_cause not in {"low_rpm", "stall_250"}:
            self.engine_shutdown_alert = False
            self.engine_shutdown_message = ""
            self.engine_shutdown_cause = ""

        if not severe_shutdown and self.engine_on:
            # Con 2 o mas cilindros activos, la ECU corrige para mantener el motor vivo.
            cylinder_fault_context = len(effective_misfire_flags) > 0
            can_rpm_shutdown = (active_cylinders <= 1) or (not cylinder_fault_context)
            if not can_rpm_shutdown:
                rpm_floor = 380.0 if self.mode == "gasoline" else 340.0
                actual["rpm"] = max(actual.get("rpm", 0.0), rpm_floor)
                self._low_rpm_elapsed = 0.0
            else:
                # Regla dura: por debajo de 250 rpm el motor se detiene.
                if self._engine_on_elapsed >= 1.0 and actual.get("rpm", 0.0) < 250.0:
                    self.engine_on = False
                    self.stage = "key_on"
                    self.engine_shutdown_alert = True
                    self.engine_shutdown_message = "MOTOR APAGADO (RPM < 250)"
                    self.engine_shutdown_cause = "stall_250"
                    self._low_rpm_elapsed = 0.0
                    for key in ("rpm", "vehicle_speed", "maf", "inj_cmd_ms", "inj_ms", "calculated_load"):
                        actual[key] = 0.0
                    actual["map"] = 100.0
                    actual["stft"] = 0.0
                    actual["ltft"] = 0.0
                    actual["o2_b1s1"] = 0.45
                    actual["o2_b1s2"] = 0.45
                else:
                    stationary_idle = self.stage == "idle" and app_pct <= 3.5 and actual.get("vehicle_speed", 0.0) <= 1.5
                    if stationary_idle:
                        if single_cyl_actuator_fault:
                            rpm_threshold = 360.0 if self.mode == "gasoline" else 340.0
                            shutdown_after = 6.0
                        else:
                            rpm_threshold = 520.0 if self.mode == "gasoline" else 500.0
                            shutdown_after = 3.0
                    else:
                        rpm_threshold = 620.0 if self.mode == "gasoline" else 580.0
                        shutdown_after = 1.4

                    if actual.get("rpm", 0.0) < rpm_threshold:
                        self._low_rpm_elapsed += dt
                    else:
                        self._low_rpm_elapsed = max(0.0, self._low_rpm_elapsed - (dt * 1.5))

                    if self._low_rpm_elapsed >= shutdown_after:
                        self.engine_on = False
                        self.stage = "key_on"
                        self.engine_shutdown_alert = True
                        self.engine_shutdown_message = "MOTOR APAGADO (RPM CRITICA)"
                        self.engine_shutdown_cause = "low_rpm"
                        self._low_rpm_elapsed = 0.0
                        for key in ("rpm", "vehicle_speed", "maf", "inj_cmd_ms", "inj_ms", "calculated_load"):
                            actual[key] = 0.0
                        actual["map"] = 100.0
                        actual["stft"] = 0.0
                        actual["ltft"] = 0.0
                        actual["o2_b1s1"] = 0.45
                        actual["o2_b1s2"] = 0.45
        else:
            self._low_rpm_elapsed = 0.0

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
                        # Con falla la seÃ±al narrowband sigue oscilando, pero sesgada a rico o pobre.
                        dominant_rich = (coil_fault_count * 0.22) + (manual_misfire_count * 0.14)
                        dominant_lean = injector_fault_count * 0.22
                        o2_center = self._clamp(0.50 + dominant_rich - dominant_lean, 0.22, 0.78)
                        o2_swing = 0.18
                        o2_shape = math.sin(self._t * 4.4) + 0.18 * math.sin(self._t * 8.8)
                        o2_target = self._clamp(o2_center + o2_swing * o2_shape, 0.10, 0.90)
                        smooth_alpha = 0.72
                    else:
                        # Healthy narrowband: rounded waveform with asymmetric dwell.
                        # Lean bias -> more time in low zone. Rich bias -> opposite.
                        trim_quality = self._clamp((abs(actual["stft"]) + 0.6 * abs(actual["ltft"])) / 25.0, 0.0, 1.0)
                        rpm_factor = self._clamp(actual.get("rpm", 800.0) / 2200.0, 0.35, 1.55)
                        load_factor = self._clamp(app_pct / 100.0, 0.0, 1.0)

                        # Positive trim_balance means lean tendency, negative means rich.
                        trim_balance = self._clamp((actual["stft"] + 0.6 * actual["ltft"]) / 18.0, -1.0, 1.0)

                        # Better mixture control switches faster.
                        switch_hz = self._clamp(0.82 + (0.55 * rpm_factor) + (0.08 * load_factor) - (0.42 * trim_quality), 0.60, 1.60)
                        cycle = (self._o2_phase / (2.0 * math.pi)) % 1.0
                        cycle = (cycle + (switch_hz * dt)) % 1.0
                        self._o2_phase = cycle * (2.0 * math.pi)

                        # Keep rounded transitions and shift dwell by mixture bias.
                        transition_portion = 0.18
                        dwell_total = 1.0 - (2.0 * transition_portion)
                        low_share = self._clamp(0.50 + (0.30 * trim_balance), 0.26, 0.74)
                        low_dwell = dwell_total * low_share
                        high_dwell = max(0.02, dwell_total - low_dwell)
                        t_drop = transition_portion
                        t_low_end = t_drop + low_dwell
                        t_rise_end = t_low_end + transition_portion

                        # Always cross below 0.20 and above 0.80, without touching 0 or 1.
                        low_level = self._clamp(0.15 - (0.03 * max(trim_balance, 0.0)), 0.11, 0.18)
                        high_level = self._clamp(0.85 + (0.03 * max(-trim_balance, 0.0)), 0.82, 0.89)

                        if cycle < t_drop:
                            x = cycle / max(t_drop, 1e-6)
                            blend = 0.5 - 0.5 * math.cos(math.pi * x)
                            o2_target = high_level + (low_level - high_level) * blend
                        elif cycle < t_low_end:
                            x = (cycle - t_drop) / max(low_dwell, 1e-6)
                            o2_target = low_level + 0.008 * math.sin(math.pi * x)
                        elif cycle < t_rise_end:
                            x = (cycle - t_low_end) / max(transition_portion, 1e-6)
                            blend = 0.5 - 0.5 * math.cos(math.pi * x)
                            o2_target = low_level + (high_level - low_level) * blend
                        else:
                            x = (cycle - t_rise_end) / max(high_dwell, 1e-6)
                            o2_target = high_level - 0.008 * math.sin(math.pi * x)

                        # Tiny irregularity to avoid perfect synthetic trace.
                        o2_target += 0.004 * math.sin(self._t * 1.9 + 0.7)
                        o2_target = self._clamp(o2_target, 0.11, 0.89)
                        smooth_alpha = 0.96
                else:
                    warmup_factor = self._clamp((70.0 - actual["ect"]) / 70.0, 0.0, 1.0)
                    o2_target = self._clamp(0.76 + 0.10 * warmup_factor + 0.02 * math.sin(self._t * 1.6), 0.20, 0.90)
                    smooth_alpha = 0.14
                actual["o2_b1s1"] = self._smooth_value(previous.get("o2_b1s1", o2_target), o2_target, smooth_alpha)
                actual["o2_b1s1"] = self._clamp(actual["o2_b1s1"], 0.13, 0.87)
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
            # Diesel: mantenemos O2 neutral para no simular un sensor que no aplica en este modulo.
            actual["o2_b1s1"] = 0.0
            actual["o2_b1s2"] = 0.0

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
            if self.mode == "gasoline":
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


class SimulationHub:
    def __init__(self) -> None:
        ensure_session_registry_db()
        self.states: Dict[str, SimulationState] = {}
        self.connected_students: Dict[str, int] = {}
        self.student_names: Dict[str, str] = {}
        self.session_students_seen: Dict[str, str] = {}
        self.clear_dtc_requests: Dict[str, bool] = {}
        self.clear_dtc_approved: Dict[str, bool] = {}
        self.engine_off_requests: Dict[str, bool] = {}
        self.engine_off_approved: Dict[str, bool] = {}
        self.reset_session_requests: Dict[str, bool] = {}
        self.reset_session_approved: Dict[str, bool] = {}
        self.engine_control_requests: Dict[str, bool] = {}
        self.engine_control_enabled: Dict[str, bool] = {}
        self.active_evaluations: Dict[str, Dict[str, Any]] = {}
        self.evaluation_submissions: Dict[str, List[Dict[str, Any]]] = {}
        self._evaluation_seq = 0
        self.connected_instructors = 0
        self._default_student_id = "student-001"
        self.current_session_name = "Clase 1"
        self.current_session_code = "SCANMATIC"

    def _normalize_student_id(self, student_id: Optional[str]) -> str:
        sid = str(student_id or "").strip()
        if not sid:
            sid = self._default_student_id
        if len(sid) > 64:
            sid = sid[:64]
        safe = []
        for ch in sid:
            if ch.isalnum() or ch in {"-", "_"}:
                safe.append(ch)
        normalized = "".join(safe) or self._default_student_id
        return normalized

    def ensure_state(self, student_id: Optional[str]) -> tuple[str, SimulationState]:
        sid = self._normalize_student_id(student_id)
        st = self.states.get(sid)
        if st is None:
            st = SimulationState()
            st.session_name = self.current_session_name
            st.session_code = self.current_session_code
            self.states[sid] = st
        if sid not in self.student_names:
            self.student_names[sid] = sid
        if sid not in self.clear_dtc_requests:
            self.clear_dtc_requests[sid] = False
        if sid not in self.clear_dtc_approved:
            self.clear_dtc_approved[sid] = False
        if sid not in self.engine_off_requests:
            self.engine_off_requests[sid] = False
        if sid not in self.engine_off_approved:
            self.engine_off_approved[sid] = False
        if sid not in self.reset_session_requests:
            self.reset_session_requests[sid] = False
        if sid not in self.reset_session_approved:
            self.reset_session_approved[sid] = False
        if sid not in self.engine_control_requests:
            self.engine_control_requests[sid] = False
        if sid not in self.engine_control_enabled:
            self.engine_control_enabled[sid] = False
        return sid, st

    def _normalize_student_name(self, name: Optional[str], fallback: str) -> str:
        text = str(name or "").strip()
        if not text:
            return fallback
        text = " ".join(text.split())
        if len(text) > 48:
            text = text[:48]
        return text

    def set_student_name(self, student_id: Optional[str], name: Optional[str]) -> str:
        sid, _ = self.ensure_state(student_id)
        normalized = self._normalize_student_name(name, sid)
        self.student_names[sid] = normalized
        self.session_students_seen[sid] = normalized
        return normalized

    def get_student_name(self, student_id: Optional[str]) -> str:
        sid = self._normalize_student_id(student_id)
        return self.student_names.get(sid, sid)

    def connect_student(self, student_id: Optional[str]) -> str:
        sid, _ = self.ensure_state(student_id)
        self.connected_students[sid] = self.connected_students.get(sid, 0) + 1
        self.session_students_seen[sid] = self.get_student_name(sid)
        return sid

    def disconnect_student(self, student_id: Optional[str]) -> None:
        sid = self._normalize_student_id(student_id)
        count = self.connected_students.get(sid, 0)
        if count <= 1:
            self.connected_students.pop(sid, None)
        else:
            self.connected_students[sid] = count - 1

    def connect_instructor(self) -> None:
        self.connected_instructors += 1

    def disconnect_instructor(self) -> None:
        self.connected_instructors = max(0, self.connected_instructors - 1)

    def total_students_connected(self) -> int:
        return sum(self.connected_students.values())

    def list_students(self) -> List[Dict[str, Any]]:
        rows = []
        for sid in sorted(self.states.keys()):
            st = self.states[sid]
            latest = self.latest_submission(sid)
            rows.append({
                "id": sid,
                "name": self.get_student_name(sid),
                "connected": self.connected_students.get(sid, 0) > 0,
                "connections": self.connected_students.get(sid, 0),
                "engine_on": st.engine_on,
                "stage": st.stage,
                "mode": st.mode,
                "rpm": st.sensors.get("rpm", 0.0),
                "dtc_count": len(st.dtc_records),
                "mil_on": st.mil_on,
                "latest_score_20": latest.get("score_20") if latest else None,
                "clear_dtc_request_pending": bool(self.clear_dtc_requests.get(sid, False)),
                "clear_dtc_approved": bool(self.clear_dtc_approved.get(sid, False)),
                "engine_off_request_pending": bool(self.engine_off_requests.get(sid, False)),
                "engine_off_approved": bool(self.engine_off_approved.get(sid, False)),
                "reset_session_request_pending": bool(self.reset_session_requests.get(sid, False)),
                "reset_session_approved": bool(self.reset_session_approved.get(sid, False)),
                "engine_control_request_pending": bool(self.engine_control_requests.get(sid, False)),
                "engine_control_enabled": bool(self.engine_control_enabled.get(sid, False)),
            })
        return rows

    def request_clear_dtcs(self, student_id: Optional[str]) -> Dict[str, Any]:
        sid, _ = self.ensure_state(student_id)
        self.clear_dtc_requests[sid] = True
        return {"ok": True, "student_id": sid, "request_pending": True}

    def approve_clear_dtcs(self, student_id: Optional[str], approved: bool = True) -> Dict[str, Any]:
        sid, _ = self.ensure_state(student_id)
        self.clear_dtc_approved[sid] = bool(approved)
        if approved:
            self.clear_dtc_requests[sid] = False
        else:
            self.clear_dtc_requests[sid] = False
        return {
            "ok": True,
            "student_id": sid,
            "approved": bool(self.clear_dtc_approved[sid]),
            "request_pending": bool(self.clear_dtc_requests.get(sid, False)),
        }

    def clear_permission_snapshot(self, student_id: Optional[str]) -> Dict[str, Any]:
        sid = self._normalize_student_id(student_id)
        return {
            "request_pending": bool(self.clear_dtc_requests.get(sid, False)),
            "approved": bool(self.clear_dtc_approved.get(sid, False)),
        }

    def request_engine_off(self, student_id: Optional[str]) -> Dict[str, Any]:
        sid, _ = self.ensure_state(student_id)
        self.engine_off_requests[sid] = True
        return {"ok": True, "student_id": sid, "request_pending": True}

    async def approve_engine_off(self, student_id: Optional[str], approved: bool = True) -> Dict[str, Any]:
        sid, st = self.ensure_state(student_id)
        self.engine_off_approved[sid] = bool(approved)
        if approved:
            self.engine_off_requests[sid] = False
            await st.apply_command("toggle_engine", {"engine_on": False})
            self.engine_off_approved[sid] = False
        else:
            self.engine_off_requests[sid] = False
            self.engine_off_approved[sid] = False
        return {
            "ok": True,
            "student_id": sid,
            "approved": bool(approved),
            "request_pending": bool(self.engine_off_requests.get(sid, False)),
        }

    def engine_off_permission_snapshot(self, student_id: Optional[str]) -> Dict[str, Any]:
        sid = self._normalize_student_id(student_id)
        return {
            "request_pending": bool(self.engine_off_requests.get(sid, False)),
            "approved": bool(self.engine_off_approved.get(sid, False)),
        }

    def request_reset_session(self, student_id: Optional[str]) -> Dict[str, Any]:
        sid, _ = self.ensure_state(student_id)
        self.reset_session_requests[sid] = True
        return {"ok": True, "student_id": sid, "request_pending": True}

    async def approve_reset_session(self, student_id: Optional[str], approved: bool = True) -> Dict[str, Any]:
        sid, st = self.ensure_state(student_id)
        self.reset_session_approved[sid] = bool(approved)
        if approved:
            self.reset_session_requests[sid] = False
            await st.apply_command("reset_all", {})
            self.reset_session_approved[sid] = False
        else:
            self.reset_session_requests[sid] = False
            self.reset_session_approved[sid] = False
        return {
            "ok": True,
            "student_id": sid,
            "approved": bool(approved),
            "request_pending": bool(self.reset_session_requests.get(sid, False)),
        }

    def reset_session_permission_snapshot(self, student_id: Optional[str]) -> Dict[str, Any]:
        sid = self._normalize_student_id(student_id)
        return {
            "request_pending": bool(self.reset_session_requests.get(sid, False)),
            "approved": bool(self.reset_session_approved.get(sid, False)),
        }

    def request_engine_control(self, student_id: Optional[str]) -> Dict[str, Any]:
        sid, _ = self.ensure_state(student_id)
        self.engine_control_requests[sid] = True
        return {"ok": True, "student_id": sid, "request_pending": True}

    def approve_engine_control(self, student_id: Optional[str], approved: bool = True) -> Dict[str, Any]:
        sid, _ = self.ensure_state(student_id)
        self.engine_control_enabled[sid] = bool(approved)
        self.engine_control_requests[sid] = False
        return {
            "ok": True,
            "student_id": sid,
            "enabled": bool(self.engine_control_enabled.get(sid, False)),
            "request_pending": bool(self.engine_control_requests.get(sid, False)),
        }

    def engine_control_snapshot(self, student_id: Optional[str]) -> Dict[str, Any]:
        sid, st = self.ensure_state(student_id)
        enabled = bool(self.engine_control_enabled.get(sid, False))
        clear_done = bool(st.last_clear_ts > 0.0)
        return {
            "request_pending": bool(self.engine_control_requests.get(sid, False)),
            "enabled": enabled,
            "can_use_now": bool(clear_done or enabled),
            "last_clear_ts": float(st.last_clear_ts),
        }

    def _archive_current_session(self) -> Dict[str, Any]:
        roster_ids = sorted(set(self.session_students_seen.keys()) | set(self.states.keys()) | set(self.student_names.keys()))
        roster: List[Dict[str, Any]] = []
        for sid in roster_ids:
            st = self.states.get(sid)
            sensors = (st.sensors if st else {}) or {}
            latest = self.latest_submission(sid) or {}
            row = {
                "student_id": sid,
                "student_name": self.session_students_seen.get(sid) or self.get_student_name(sid),
                "mode": st.mode if st else "",
                "stage": st.stage if st else "",
                "engine_on": bool(st.engine_on) if st else False,
                "mil_on": bool(st.mil_on) if st else False,
                "dtc_count": len(st.dtc_records) if st else 0,
                "latest_score_20": float(latest.get("score_20", 0.0)) if latest else 0.0,
                "sensors": dict(sensors),
            }
            roster.append(row)
        ended_at = time.time()
        ended_at_iso = datetime.fromtimestamp(ended_at).strftime("%Y-%m-%d %H:%M:%S")
        SESSION_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts_tag = datetime.fromtimestamp(ended_at).strftime("%Y%m%d_%H%M%S")
        code_tag = "".join(ch for ch in self.current_session_code if ch.isalnum() or ch in {"-", "_"})[:24] or "session"
        csv_path = SESSION_EXPORT_DIR / f"session_{ts_tag}_{code_tag}.csv"
        sensor_cols = list(DEFAULT_SENSOR_ORDER)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "session_name",
                "session_code",
                "saved_at",
                "student_name",
                "student_code",
                "mode",
                "stage",
                "engine_on",
                "mil_on",
                "dtc_count",
                "latest_score_20",
                *sensor_cols,
            ])
            if roster:
                for item in roster:
                    sensors = item.get("sensors", {})
                    writer.writerow([
                        self.current_session_name,
                        self.current_session_code,
                        ended_at_iso,
                        item.get("student_name", ""),
                        item.get("student_id", ""),
                        item.get("mode", ""),
                        item.get("stage", ""),
                        1 if item.get("engine_on", False) else 0,
                        1 if item.get("mil_on", False) else 0,
                        item.get("dtc_count", 0),
                        item.get("latest_score_20", 0.0),
                        *[sensors.get(k, "") for k in sensor_cols],
                    ])
            else:
                writer.writerow([self.current_session_name, self.current_session_code, ended_at_iso, "", "", "", "", 0, 0, 0, 0.0, *["" for _ in sensor_cols]])
        ensure_session_registry_db()
        with sqlite3.connect(str(SESSION_DB_FILE)) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO session_logs (session_name, session_code, ended_at_ts, student_count, export_csv_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.current_session_name, self.current_session_code, ended_at, len(roster), str(csv_path)),
            )
            session_log_id = int(cur.lastrowid)
            for item in roster:
                cur.execute(
                    """
                    INSERT INTO session_students (session_log_id, student_id, student_name, snapshot_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        session_log_id,
                        item.get("student_id", ""),
                        item.get("student_name", ""),
                        json.dumps(item, ensure_ascii=False),
                    ),
                )
            conn.commit()
        return {
            "session_log_id": session_log_id,
            "session_name": self.current_session_name,
            "session_code": self.current_session_code,
            "student_count": len(roster),
            "export_csv_file": str(csv_path),
            "download_url": f"/api/session-export/{session_log_id}",
        }

    def export_current_session_snapshot(self) -> Dict[str, Any]:
        archived = self._archive_current_session()
        return {
            "ok": True,
            "archived": archived,
        }

    def rotate_session(self, session_name: str, session_code: str) -> Dict[str, Any]:
        archived = self._archive_current_session()
        next_name = str(session_name or "").strip() or "Clase 1"
        next_code = str(session_code or "").strip().upper() or "SCANMATIC"
        self.current_session_name = next_name
        self.current_session_code = next_code
        existing_ids = sorted(set(self.states.keys()) | set(self.connected_students.keys()) | set(self.student_names.keys()))
        self.states = {}
        self.student_names = {}
        self.session_students_seen = {}
        self.clear_dtc_requests = {}
        self.clear_dtc_approved = {}
        self.engine_off_requests = {}
        self.engine_off_approved = {}
        self.reset_session_requests = {}
        self.reset_session_approved = {}
        self.engine_control_requests = {}
        self.engine_control_enabled = {}
        self.active_evaluations = {}
        self.evaluation_submissions = {}
        for sid in existing_ids:
            st = SimulationState()
            st.session_name = self.current_session_name
            st.session_code = self.current_session_code
            self.states[sid] = st
            self.student_names[sid] = sid
            self.ensure_state(sid)
        if not self.states:
            self.ensure_state(self._default_student_id)
        return {
            "ok": True,
            "new_session_name": self.current_session_name,
            "new_session_code": self.current_session_code,
            "archived": archived,
        }

    def session_registry_recent(self, limit: int = 30) -> List[Dict[str, Any]]:
        ensure_session_registry_db()
        out: List[Dict[str, Any]] = []
        with sqlite3.connect(str(SESSION_DB_FILE)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, session_name, session_code, ended_at_ts, student_count, export_csv_path
                FROM session_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            for row in rows:
                students = conn.execute(
                    """
                    SELECT student_name, student_id
                    FROM session_students
                    WHERE session_log_id = ?
                    ORDER BY student_name COLLATE NOCASE
                    """,
                    (int(row["id"]),),
                ).fetchall()
                out.append({
                    "id": int(row["id"]),
                    "session_name": str(row["session_name"] or ""),
                    "session_code": str(row["session_code"] or ""),
                    "ended_at_ts": float(row["ended_at_ts"] or 0.0),
                    "student_count": int(row["student_count"] or 0),
                    "students": [
                        {
                            "name": str(s["student_name"] or ""),
                            "code": str(s["student_id"] or ""),
                        }
                        for s in students
                    ],
                    "export_csv_file": str(row["export_csv_path"] or ""),
                    "download_url": f"/api/session-export/{int(row['id'])}",
                })
        return out

    def question_bank(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in EVALUATION_QUESTION_BANK]

    def _question_map(self) -> Dict[str, Dict[str, Any]]:
        return {q["id"]: q for q in EVALUATION_QUESTION_BANK}

    def _normalize_question_ids(self, question_ids: Any) -> List[str]:
        qmap = self._question_map()
        raw = question_ids if isinstance(question_ids, list) else []
        out = []
        for item in raw:
            qid = str(item).strip()
            if qid in qmap and qid not in out:
                out.append(qid)
        if not out:
            out = [q["id"] for q in EVALUATION_QUESTION_BANK]
        return out

    def latest_submission(self, student_id: Optional[str]) -> Optional[Dict[str, Any]]:
        sid = self._normalize_student_id(student_id)
        rows = self.evaluation_submissions.get(sid) or []
        if not rows:
            return None
        return dict(rows[-1])

    def _student_evaluation_snapshot(self, student_id: Optional[str]) -> Dict[str, Any]:
        sid = self._normalize_student_id(student_id)
        active = self.active_evaluations.get(sid)
        latest = self.latest_submission(sid)
        return {
            "active": dict(active) if active else None,
            "latest_submission": dict(latest) if latest else None,
        }

    def publish_evaluation(self, target_scope: str, target_student_id: Optional[str], question_ids: Any, title: str = "Evaluacion diagnostica") -> Dict[str, Any]:
        qids = self._normalize_question_ids(question_ids)
        qmap = self._question_map()
        rng = random.SystemRandom()
        questions: List[Dict[str, Any]] = []
        for qid in qids:
            source = qmap[qid]
            q = dict(source)
            options = [dict(opt) for opt in (source.get("options") or []) if isinstance(opt, dict)]
            rng.shuffle(options)
            q["options"] = options
            questions.append(q)
        question_count = len(questions)
        points_per_question_20 = (20.0 / float(question_count)) if question_count > 0 else 0.0
        for idx, q in enumerate(questions):
            q["question_number"] = idx + 1
            q["points_20"] = points_per_question_20
        max_points = sum(float(q.get("points", 0.0)) for q in questions)
        max_points_20 = 20.0 if question_count > 0 else 0.0
        self._evaluation_seq += 1
        evaluation_id = f"EV-{self._evaluation_seq:04d}"
        targets: List[str] = []
        if str(target_scope).strip().lower() == "all":
            if not self.states:
                self.ensure_state(self._default_student_id)
            targets = sorted(self.states.keys())
        else:
            sid, _ = self.ensure_state(target_student_id)
            targets = [sid]
        published_at = time.time()
        for sid in targets:
            self.active_evaluations[sid] = {
                "evaluation_id": evaluation_id,
                "title": str(title or "Evaluacion diagnostica"),
                "questions": questions,
                "max_points": max_points,
                "max_points_20": max_points_20,
                "points_per_question_20": points_per_question_20,
                "status": "active",
                "published_at": published_at,
            }
        return {
            "ok": True,
            "evaluation_id": evaluation_id,
            "targets": targets,
            "question_count": question_count,
            "max_points": max_points,
            "max_points_20": max_points_20,
            "points_per_question_20": round(points_per_question_20, 4),
        }

    def submit_evaluation(self, student_id: Optional[str], answers: Any) -> Dict[str, Any]:
        sid, _ = self.ensure_state(student_id)
        active = self.active_evaluations.get(sid)
        if not active:
            return {"ok": False, "error": "No hay una evaluacion activa para este estudiante."}
        questions = active.get("questions", [])
        valid_ids = {str(q["id"]) for q in questions}
        answer_map = answers if isinstance(answers, dict) else {}
        selected_answers: Dict[str, str] = {}
        for qid, option_id in answer_map.items():
            qid_s = str(qid).strip()
            opt_s = str(option_id).strip()
            if qid_s in valid_ids and opt_s:
                selected_answers[qid_s] = opt_s
        raw_max = float(active.get("max_points", 0.0))
        score_max_20 = float(active.get("max_points_20", 20.0))
        raw_score = 0.0
        score_20 = 0.0
        details = []
        correct_count = 0
        for q in questions:
            qid = str(q.get("id", ""))
            question_number = int(q.get("question_number") or 0)
            selected = selected_answers.get(qid, "")
            correct = str(q.get("correct_option_id", ""))
            options = q.get("options", [])
            option_text = {
                str(opt.get("id", "")): str(opt.get("text", ""))
                for opt in options if isinstance(opt, dict)
            }
            is_correct = selected != "" and selected == correct
            if is_correct:
                raw_score += float(q.get("points", 0.0))
                score_20 += float(q.get("points_20", 0.0))
                correct_count += 1
            details.append({
                "question_number": question_number,
                "question_id": qid,
                "question_text": str(q.get("text", "")),
                "selected_option_id": selected,
                "selected_option_text": option_text.get(selected, ""),
                "correct_option_id": correct,
                "correct_option_text": option_text.get(correct, ""),
                "is_correct": is_correct,
            })
        score_20 = round(max(0.0, min(score_max_20, score_20)), 2)
        submitted_at = time.time()
        result = {
            "evaluation_id": active.get("evaluation_id"),
            "student_id": sid,
            "student_name": self.get_student_name(sid),
            "answers": selected_answers,
            "answer_details": details,
            "answered_count": len(selected_answers),
            "correct_count": correct_count,
            "incorrect_count": max(0, len(selected_answers) - correct_count),
            "unanswered_count": max(0, len(questions) - len(selected_answers)),
            "question_count": len(questions),
            "raw_score": round(raw_score, 2),
            "raw_max": round(raw_max, 2),
            "score_20": score_20,
            "max_points_20": score_max_20,
            "submitted_at": submitted_at,
        }
        self.evaluation_submissions.setdefault(sid, []).append(result)
        if len(self.evaluation_submissions[sid]) > 40:
            self.evaluation_submissions[sid] = self.evaluation_submissions[sid][-40:]
        return {"ok": True, "result": result}

    def evaluation_analytics(self) -> Dict[str, Any]:
        students = self.list_students()
        latest_rows = []
        for row in students:
            sid = row["id"]
            latest = self.latest_submission(sid)
            if latest:
                latest_rows.append({
                    "student_id": sid,
                    "student_name": row.get("name") or sid,
                    "score_20": float(latest.get("score_20", 0.0)),
                    "submitted_at": float(latest.get("submitted_at", 0.0)),
                })
        latest_rows.sort(key=lambda x: x["submitted_at"], reverse=True)
        avg_score = round(sum(r["score_20"] for r in latest_rows) / len(latest_rows), 2) if latest_rows else 0.0
        distribution = {"excelente": 0, "bien": 0, "mejorar": 0}
        for r in latest_rows:
            score = r["score_20"]
            if score >= 17.0:
                distribution["excelente"] += 1
            elif score >= 14.0:
                distribution["bien"] += 1
            else:
                distribution["mejorar"] += 1
        all_submissions = []
        for sid, rows in self.evaluation_submissions.items():
            for item in rows:
                all_submissions.append({
                    "student_id": sid,
                    "student_name": self.get_student_name(sid),
                    "score_20": float(item.get("score_20", 0.0)),
                    "submitted_at": float(item.get("submitted_at", 0.0)),
                })
        all_submissions.sort(key=lambda x: x["submitted_at"])
        trend = all_submissions[-20:]
        latest_details_by_student: List[Dict[str, Any]] = []
        for row in latest_rows:
            sid = row["student_id"]
            latest = self.latest_submission(sid)
            if not latest:
                continue
            latest_details_by_student.append({
                "student_id": sid,
                "student_name": row.get("student_name") or sid,
                "score_20": float(latest.get("score_20", 0.0)),
                "question_count": int(latest.get("question_count", 0)),
                "answered_count": int(latest.get("answered_count", 0)),
                "correct_count": int(latest.get("correct_count", 0)),
                "incorrect_count": int(latest.get("incorrect_count", 0)),
                "submitted_at": float(latest.get("submitted_at", 0.0)),
                "answer_details": [dict(item) for item in latest.get("answer_details", [])],
            })
        question_stats = []
        qmap = self._question_map()
        latest_by_sid = {r["student_id"]: self.latest_submission(r["student_id"]) for r in latest_rows}
        for q in EVALUATION_QUESTION_BANK:
            asked = 0
            answered = 0
            correct = 0
            for sid, latest in latest_by_sid.items():
                if not latest:
                    continue
                sub = latest
                active = self.active_evaluations.get(sid) or {}
                asked_ids = {str(item.get("id")) for item in active.get("questions", [])}
                if q["id"] in asked_ids:
                    asked += 1
                    details = sub.get("answer_details", [])
                    row = next((d for d in details if d.get("question_id") == q["id"]), None)
                    if row and row.get("selected_option_id"):
                        answered += 1
                        if bool(row.get("is_correct")):
                            correct += 1
            correct_rate = round((correct / asked) * 100.0, 2) if asked > 0 else 0.0
            question_stats.append({
                "id": q["id"],
                "text": qmap[q["id"]]["text"],
                "points": qmap[q["id"]]["points"],
                "asked_count": asked,
                "answered_count": answered,
                "correct_count": correct,
                "correct_rate_pct": correct_rate,
            })
        return {
            "latest_count": len(latest_rows),
            "average_score_20": avg_score,
            "distribution": distribution,
            "by_student": latest_rows,
            "trend": trend,
            "latest_details_by_student": latest_details_by_student,
            "question_stats": question_stats,
        }

    def _decorate_snapshot(self, sid: str, snap: Dict[str, Any]) -> Dict[str, Any]:
        snap["student_id"] = sid
        snap["student_name"] = self.get_student_name(sid)
        snap["students_connected"] = self.total_students_connected()
        snap["instructors_connected"] = self.connected_instructors
        snap["evaluation"] = self._student_evaluation_snapshot(sid)
        snap["clear_dtc"] = self.clear_permission_snapshot(sid)
        snap["engine_off"] = self.engine_off_permission_snapshot(sid)
        snap["reset_session"] = self.reset_session_permission_snapshot(sid)
        snap["engine_control"] = self.engine_control_snapshot(sid)
        return snap

    def snapshot_for_student(self, student_id: Optional[str]) -> Dict[str, Any]:
        sid, st = self.ensure_state(student_id)
        snap = st.snapshot()
        return self._decorate_snapshot(sid, snap)

    def snapshot_for_instructor(self, selected_student_id: Optional[str] = None) -> Dict[str, Any]:
        if not self.states:
            self.ensure_state(self._default_student_id)
        selected_sid = self._normalize_student_id(selected_student_id)
        if selected_sid not in self.states:
            connected = [sid for sid, count in self.connected_students.items() if count > 0]
            if connected:
                selected_sid = sorted(connected)[0]
            else:
                selected_sid = sorted(self.states.keys())[0]
        snap = self.snapshot_for_student(selected_sid)
        snap["control_center"] = {
            "selected_student_id": selected_sid,
            "students": self.list_students(),
            "apply_all_default": False,
        }
        snap["evaluation_bank"] = self.question_bank()
        snap["evaluation_analytics"] = self.evaluation_analytics()
        snap["session_registry"] = self.session_registry_recent()
        return snap

    async def update_all(self, dt: float = 0.25) -> None:
        if not self.states:
            self.ensure_state(self._default_student_id)
        for st in self.states.values():
            st.update(dt)

    async def apply_instructor_command(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if action == "export_session_current":
            return self.export_current_session_snapshot()
        if action == "set_session_global":
            return self.rotate_session(
                session_name=str(payload.get("session_name") or self.current_session_name),
                session_code=str(payload.get("session_code") or self.current_session_code),
            )
        if action == "publish_evaluation":
            return self.publish_evaluation(
                target_scope=str(payload.get("target_scope") or "selected"),
                target_student_id=payload.get("target_student_id"),
                question_ids=payload.get("question_ids"),
                title=str(payload.get("title") or "Evaluacion diagnostica"),
            )
        if action == "approve_clear_dtcs":
            target_scope = str(payload.get("target_scope") or "selected").strip().lower()
            approved = bool(payload.get("approved", True))
            targets: List[Dict[str, Any]] = []
            if target_scope == "all":
                if not self.states:
                    self.ensure_state(self._default_student_id)
                for sid in sorted(self.states.keys()):
                    targets.append(self.approve_clear_dtcs(sid, approved=approved))
                return {"ok": True, "targets": targets}
            sid = payload.get("target_student_id")
            return self.approve_clear_dtcs(sid, approved=approved)
        if action == "approve_engine_off":
            target_scope = str(payload.get("target_scope") or "selected").strip().lower()
            approved = bool(payload.get("approved", True))
            targets: List[Dict[str, Any]] = []
            if target_scope == "all":
                if not self.states:
                    self.ensure_state(self._default_student_id)
                for sid in sorted(self.states.keys()):
                    targets.append(await self.approve_engine_off(sid, approved=approved))
                return {"ok": True, "targets": targets}
            sid = payload.get("target_student_id")
            return await self.approve_engine_off(sid, approved=approved)
        if action == "approve_reset_session":
            target_scope = str(payload.get("target_scope") or "selected").strip().lower()
            approved = bool(payload.get("approved", True))
            targets: List[Dict[str, Any]] = []
            if target_scope == "all":
                if not self.states:
                    self.ensure_state(self._default_student_id)
                for sid in sorted(self.states.keys()):
                    targets.append(await self.approve_reset_session(sid, approved=approved))
                return {"ok": True, "targets": targets}
            sid = payload.get("target_student_id")
            return await self.approve_reset_session(sid, approved=approved)
        if action == "approve_engine_control":
            target_scope = str(payload.get("target_scope") or "selected").strip().lower()
            approved = bool(payload.get("approved", True))
            targets: List[Dict[str, Any]] = []
            if target_scope == "all":
                if not self.states:
                    self.ensure_state(self._default_student_id)
                for sid in sorted(self.states.keys()):
                    targets.append(self.approve_engine_control(sid, approved=approved))
                return {"ok": True, "targets": targets}
            sid = payload.get("target_student_id")
            return self.approve_engine_control(sid, approved=approved)
        target_scope = str(payload.get("target_scope") or "selected").strip().lower()
        target_student_id = payload.get("target_student_id")
        if target_scope == "all":
            if not self.states:
                self.ensure_state(self._default_student_id)
            result: Dict[str, Any] = {"ok": True, "targets": []}
            for sid, st in list(self.states.items()):
                res = await st.apply_command(action, payload)
                result["targets"].append({"student_id": sid, "ok": bool(res.get("ok", False))})
            return result
        sid, st = self.ensure_state(target_student_id)
        result = await st.apply_command(action, payload)
        result["student_id"] = sid
        return result

    def state_for_student(self, student_id: Optional[str]) -> SimulationState:
        _, st = self.ensure_state(student_id)
        return st


hub = SimulationHub()
app = FastAPI(title=PROJECT_NAME)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


class WSManager:
    def __init__(self) -> None:
        self.clients: Dict[WebSocket, Dict[str, str]] = {}

    async def connect(self, ws: WebSocket, role: str, student_id: Optional[str] = None, selected_student_id: Optional[str] = None) -> Dict[str, str]:
        await ws.accept()
        info = {
            "role": role,
            "student_id": str(student_id or ""),
            "selected_student_id": str(selected_student_id or ""),
        }
        self.clients[ws] = info
        return info

    def disconnect(self, ws: WebSocket) -> Optional[Dict[str, str]]:
        return self.clients.pop(ws, None)

    async def broadcast(self) -> None:
        if not self.clients:
            return
        dead = []
        for ws, info in list(self.clients.items()):
            try:
                if info.get("role") == "instructor":
                    payload = hub.snapshot_for_instructor(info.get("selected_student_id") or None)
                else:
                    payload = hub.snapshot_for_student(info.get("student_id") or None)
                await ws.send_text(json.dumps({"type": "state", "payload": payload}))
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
        await hub.update_all(0.25)
        await ws_manager.broadcast()
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
async def api_state(role: str = "student", student_id: str = "", selected_student_id: str = "") -> Dict[str, Any]:
    if role == "instructor":
        return hub.snapshot_for_instructor(selected_student_id or None)
    return hub.snapshot_for_student(student_id or None)


@app.get("/api/connection-info")
async def connection_info() -> Dict[str, Any]:
    snap = hub.snapshot_for_instructor()
    return snap["connection"]


@app.get("/api/dtc-search")
async def api_dtc_search(q: str = "", limit: int = 20) -> List[Dict[str, str]]:
    _, st = hub.ensure_state(None)
    return st.search_dtc(query=q, limit=limit)


@app.get("/api/dtc-detail")
async def api_dtc_detail(code: str = "") -> Dict[str, Any]:
    normalized = str(code or "").strip().upper()
    if len(normalized) != 5 or normalized[0:1] not in {"P", "B", "C", "U"}:
        return {"ok": False, "error": "Formato DTC invalido. Ejemplo: P0301"}
    found_in_db = normalized in DTC_DB or normalized in DTC_DESCRIPTION_OVERRIDES
    description = DTC_DB.get(normalized) or DTC_DESCRIPTION_OVERRIDES.get(normalized) or "Codigo no encontrado en la base."
    causes = DTC_CAUSES_OVERRIDES.get(normalized) or infer_generic_dtc_causes(normalized, description)
    return {
        "ok": True,
        "code": normalized,
        "found_in_db": found_in_db,
        "description": description,
        "causes": causes,
        "structure": {
            "system_letter": normalized[0],
            "generic_or_mfr": normalized[1],
            "subsystem_digit": normalized[2],
            "fault_digits": normalized[3:5],
        },
    }


@app.get("/api/session-export/{log_id}")
async def api_session_export(log_id: int) -> Any:
    ensure_session_registry_db()
    with sqlite3.connect(str(SESSION_DB_FILE)) as conn:
        row = conn.execute(
            "SELECT export_csv_path FROM session_logs WHERE id = ?",
            (int(log_id),),
        ).fetchone()
    if not row:
        return JSONResponse({"ok": False, "error": "Registro no encontrado."}, status_code=404)
    path = Path(str(row[0] or "")).resolve()
    if not path.exists():
        return JSONResponse({"ok": False, "error": "Archivo exportado no disponible."}, status_code=404)
    return FileResponse(str(path), filename=path.name, media_type="text/csv")


@app.post("/api/instructor/command")
async def instructor_command(request: Request) -> JSONResponse:
    payload = await request.json()
    result = await hub.apply_instructor_command(payload.get("action"), payload)
    return JSONResponse(result)


@app.post("/api/student/action")
async def student_action(request: Request) -> JSONResponse:
    payload = await request.json()
    student_id = payload.get("student_id")
    sid, st = hub.ensure_state(student_id)
    action = payload.get("action")
    if action == "set_student_name":
        saved_name = hub.set_student_name(sid, payload.get("name"))
        return JSONResponse({"ok": True, "student_id": sid, "student_name": saved_name})
    if action == "set_app":
        app_value = payload.get("value")
        if not isinstance(app_value, (int, float)):
            return JSONResponse({"ok": False, "error": "Valor APP invalido."}, status_code=400)
        st.manual_overrides["app"] = max(0.0, min(100.0, float(app_value)))
        st.update(0.0)
        return JSONResponse({"ok": True, "student_id": sid, "app": st.manual_overrides["app"]})
    if action == "current_evaluation":
        return JSONResponse({"ok": True, "evaluation": hub._student_evaluation_snapshot(sid)})
    if action == "submit_evaluation":
        result = hub.submit_evaluation(sid, payload.get("answers"))
        status = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=status)
    if action == "read_dtcs":
        return JSONResponse({"ok": True, "dtcs": [asdict(x) for x in st.dtc_records.values()]})
    if action == "clear_dtcs":
        clear_perm = hub.clear_permission_snapshot(sid)
        if not clear_perm.get("approved", False):
            hub.request_clear_dtcs(sid)
            return JSONResponse({
                "ok": False,
                "requires_approval": True,
                "message": "Borrado de DTC pendiente de aprobaciÃ³n del instructor.",
                "dtcs": [asdict(x) for x in st.dtc_records.values()],
            }, status_code=403)
        await st.apply_command("clear_dtcs", {})
        hub.clear_dtc_approved[sid] = False
        hub.clear_dtc_requests[sid] = False
        return JSONResponse({"ok": True, "dtcs": [asdict(x) for x in st.dtc_records.values()]})
    if action == "request_engine_control":
        clear_done = bool(st.last_clear_ts > 0.0)
        if not clear_done:
            return JSONResponse({
                "ok": False,
                "requires_clear_first": True,
                "message": "Primero debes borrar DTC para habilitar solicitud de control de motor.",
            }, status_code=400)
        hub.request_engine_control(sid)
        return JSONResponse({
            "ok": False,
            "requires_approval": True,
            "message": "Solicitud enviada: el instructor debe autorizar control de motor.",
        }, status_code=403)
    if action in {"toggle_engine_student", "set_stage_student"}:
        control = hub.engine_control_snapshot(sid)
        if not control.get("enabled", False):
            return JSONResponse({
                "ok": False,
                "error": "Control de motor no autorizado por el instructor. Solicita autorizacion desde tu panel.",
            }, status_code=403)
        if action == "toggle_engine_student":
            target = bool(payload.get("engine_on", not st.engine_on))
            result = await st.apply_command("toggle_engine", {"engine_on": target})
            return JSONResponse({"ok": True, "result": result, "engine_on": st.engine_on, "stage": st.stage})
        stage = str(payload.get("stage") or "")
        allowed_stages = {"key_on", "crank", "idle", "part_load", "high_load", "decel"}
        if stage not in allowed_stages:
            return JSONResponse({"ok": False, "error": "Etapa invalida."}, status_code=400)
        result = await st.apply_command("set_stage", {"stage": stage})
        return JSONResponse({"ok": True, "result": result, "engine_on": st.engine_on, "stage": st.stage})
    if action == "request_engine_off":
        hub.request_engine_off(sid)
        return JSONResponse({
            "ok": False,
            "requires_approval": True,
            "message": "Solicitud enviada: el instructor debe autorizar apagar el motor.",
        }, status_code=403)
    if action == "request_reset_session":
        hub.request_reset_session(sid)
        return JSONResponse({
            "ok": False,
            "requires_approval": True,
            "message": "Solicitud enviada: el instructor debe autorizar el restablecimiento.",
        }, status_code=403)
    if action == "live_data":
        return JSONResponse({"ok": True, "sensors": st.sensors, "mil_on": st.mil_on})
    if action == "freeze_frame":
        return JSONResponse({"ok": True, "freeze_frames": st.latest_freeze_frames(), "current": st.freeze_frame(st.sensors)})
    if action == "mode05":
        return JSONResponse({"ok": True, "o2_tests": {
            "b1s1_voltage": st.sensors.get("o2_b1s1", 0.0),
            "b1s2_voltage": st.sensors.get("o2_b1s2", 0.0),
            "closed_loop_ready": st.sensors.get("ect", 0.0) >= 70.0 and st.engine_on,
            "heater_ready": st.sensors.get("battery_voltage", 0.0) > 12.0,
        }})
    if action == "mode06":
        return JSONResponse({"ok": True, "monitor_results": st.monitor_results()})
    if action == "mode07":
        return JSONResponse({"ok": True, "dtcs": [asdict(x) for x in st.dtc_records.values() if x.status == "pending"]})
    if action == "mode08":
        return JSONResponse({"ok": True, "mode08": st.mode08_summary()})
    if action == "mode09":
        return JSONResponse({"ok": True, "ecu_info": {
            "vin": st.vin,
            "vehicle_profile": st.vehicle_profile,
            "session_name": st.session_name,
            "mode": st.mode,
            "stage": st.stage,
            "protocol": "OBD2 Simulado",
        }})
    return JSONResponse({"ok": False, "error": "AcciÃ³n no soportada"}, status_code=400)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    role = ws.query_params.get("role", "student")
    student_id = ws.query_params.get("student_id", "")
    selected_student_id = ws.query_params.get("selected_student_id", "")
    if role == "student" and not student_id:
        student_id = f"student-{uuid.uuid4().hex[:8]}"
    info = await ws_manager.connect(ws, role=role, student_id=student_id, selected_student_id=selected_student_id)
    if role == "instructor":
        hub.connect_instructor()
        await ws.send_text(json.dumps({"type": "state", "payload": hub.snapshot_for_instructor(selected_student_id or None)}))
    else:
        sid = hub.connect_student(student_id)
        info["student_id"] = sid
        await ws.send_text(json.dumps({"type": "state", "payload": hub.snapshot_for_student(sid)}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        meta = ws_manager.disconnect(ws) or {}
        if role == "instructor":
            hub.disconnect_instructor()
        else:
            hub.disconnect_student(meta.get("student_id") or student_id)

