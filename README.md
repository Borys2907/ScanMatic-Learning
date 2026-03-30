# ScanMatic AutoTech Learning

Prototipo funcional MVP del simulador automotriz educativo web.

## Incluye
- Panel **1** del instructor: `/instructor`
- Panel **2** del estudiante: `/student`
- ECU virtual gasolina y diésel
- Datos en vivo
- DTC activos con base CSV integrada
- Freeze frame
- Presets de fallas
- Gráfica de PIDs
- Osciloscopio simulado educativo

## Requisitos
- Python 3.10, 3.11 o 3.12 recomendado
- Windows, Linux o macOS

## Instalación
```bash
pip install -r requirements.txt
```

Esta versión usa solo dependencias necesarias para evitar errores de compilación con
`watchfiles`, `httptools` y otros extras opcionales de Uvicorn.

## Ejecución
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

En Windows también puedes abrir directamente:

```text
start_server.bat
```

Después abre:
- Instructor: `http://127.0.0.1:8000/instructor`
- Estudiante local: `http://127.0.0.1:8000/student`

## Acceso desde otros equipos en la misma red Wi-Fi
1. Ejecuta el servidor en la PC del instructor.
2. Averigua la IP local de esa PC.
3. En los celulares o laptops de los estudiantes abre:

```text
http://IP_DEL_INSTRUCTOR:8000/student
```

Ejemplo:
```text
http://192.168.1.35:8000/student
```

## Estructura
- `app/main.py` → backend FastAPI + lógica del simulador
- `app/templates/` → vistas del instructor y estudiante
- `app/static/` → CSS y JavaScript
- `data/obd-trouble-codes.csv` → base de datos DTC

## Notas
- Esta versión es educativa y simulada, no se conecta a una ECU real.
- El osciloscopio genera formas de onda pedagógicas en función del estado del simulador.
- El CSV de DTC se usa como base real de descripciones.


Cambios v3:
- acceso del estudiante por link generado con la IP actual
- botón para compartir por WhatsApp con mensaje prellenado
- panel 2 sin enlace al panel 1
- osciloscopio eliminado
- estado por defecto: auto sano en KOEO
- los valores manuales del panel 1 persisten en el servidor y sobreviven al refresco del navegador


## Nota sobre WebSocket

Esta version instala `wsproto`, una libreria WebSocket pura en Python, para que el panel instructor y estudiante puedan intercambiar actualizaciones en tiempo real sin requerir `uvicorn[standard]`, `watchfiles` ni `httptools`. Uvicorn permite usar `wsproto` como backend WebSocket alternativo.
