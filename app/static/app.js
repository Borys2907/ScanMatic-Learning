const state = {
  latest: null,
  role: document.body.dataset.role || "student",
  studentId: null,
  studentName: null,
  selectedStudentId: "",
  targetAllStudents: false,
  controlCenterStudents: [],
  evaluationChartType: "bar",
  evaluationSelectedQuestionIds: new Set(),
  evaluationSelectionInitialized: false,
  evaluationReviewPanelOpen: false,
  evaluationReviewStudentId: "",
  studentEvaluationAnswers: {},
  studentEvaluationId: "",
  ws: null,
  selectedSignals: new Set(["rpm"]),
  history: {},
  maxPoints: 160,
  secondsPerSample: 0.25,
  graphMode: "mixed",
  studentDtcScanned: false,
  pendingDraw: false,
  graphPaused: false,
  activeScannerSection: "scanner-home",
  expandedChartKey: null,
  modalChartToken: null,
  splitChartSignature: "",
  engineShutdownDismissed: false,
  lastEngineShutdownMessage: "",
  studentAppSendTimer: null,
  lastAuthPendingSignature: "",
  authAlertDismissedSignature: "",
  authAlertAudioTimer: null,
  authPendingFlags: {},
  sessionFieldsEditing: false,
  sessionFieldsDirty: false,
};

const signalMap = {
  rpm: {label:"RPM", unit:"rpm"},
  vehicle_speed: {label:"Velocidad", unit:"km/h"},
  app: {label:"APP", unit:"%"},
  throttle: {label:"Throttle", unit:"%"},
  map: {label:"MAP", unit:"kPa"},
  maf: {label:"MAF", unit:"g/s"},
  iat: {label:"IAT", unit:"C"},
  ect: {label:"ECT", unit:"C"},
  o2_b1s1: {label:"O2 B1S1", unit:"V"},
  o2_b1s2: {label:"O2 B1S2", unit:"V"},
  stft: {label:"STFT", unit:"%"},
  ltft: {label:"LTFT", unit:"%"},
  battery_voltage: {label:"Bateria", unit:"V"},
  fuel_pressure: {label:"Fuel Press", unit:"kPa"},
  rail_pressure: {label:"Rail", unit:"bar"},
  boost: {label:"Boost", unit:"kPa"},
  egr_position: {label:"EGR", unit:"%"},
  inj_cmd_ms: {label:"Iny. comandada", unit:"ms"},
  inj_dead_time_ms: {label:"Dead time", unit:"ms"},
  inj_ms: {label:"Iny. fisica", unit:"ms"},
  spark_advance: {label:"Avance", unit:""},
  calculated_load: {label:"Carga calc.", unit:"%"},
  ckp_sync: {label:"CKP sync", unit:""},
  cmp_sync: {label:"CMP sync", unit:""},
  cooling_fan: {label:"Electroventilador", unit:"0/1"},
};

const MODE_VISIBLE_SIGNALS = {
  gasoline: ["rpm","vehicle_speed","throttle","app","map","maf","iat","ect","o2_b1s1","o2_b1s2","stft","ltft","battery_voltage","fuel_pressure","egr_position","inj_cmd_ms","inj_dead_time_ms","inj_ms","spark_advance","calculated_load","cooling_fan","ckp_sync","cmp_sync"],
  diesel: ["rpm","vehicle_speed","throttle","app","map","maf","iat","ect","battery_voltage","fuel_pressure","rail_pressure","boost","egr_position","inj_cmd_ms","inj_dead_time_ms","inj_ms","spark_advance","calculated_load","cooling_fan","ckp_sync","cmp_sync"],
};

const MODE_VISIBLE_FAULTS = {
  gasoline: ["maf_low","maf_dirty","vacuum_leak","ect_biased_cold","o2_slow","o2_intermittent","misfire_cyl1","ckp_intermittent","cmp_fault","map_slow","throttle_lag","idle_hunt","egr_stuck_open"],
  diesel: ["rail_pressure_low","boost_low","egr_stuck_open","misfire_cyl1","ckp_intermittent","cmp_fault"],
};

const MODE_VISIBLE_PRESETS = {
  gasoline: ["normal","cold_start","lean_maf","rich_ect","misfire","ckp_dropout","o2_lazy","dirty_maf","slow_map","throttle_lag","idle_hunt","o2_intermittent","egr_stuck"],
  diesel: ["normal","cold_start","diesel_rail","turbo_low","egr_stuck","ckp_dropout","misfire"],
};

const MODE_VISIBLE_SENSOR_SLIDERS = {
  gasoline: ["rpm","map","maf","ect","app"],
  diesel: ["rpm","map","maf","ect","rail_pressure","app"],
};

const MODE_VISIBLE_ACTUATORS = {
  gasoline: ["fuel_pump","injector_cyl1","injector_cyl2","injector_cyl3","injector_cyl4","coil_cyl1","coil_cyl2","coil_cyl3","coil_cyl4","egr_cmd","cooling_fan_mode"],
  diesel: ["fuel_pump","injector_cyl1","injector_cyl2","injector_cyl3","injector_cyl4","egr_cmd","cooling_fan_mode"],
};

function currentModeKey(){
  return state.latest?.mode === "diesel" ? "diesel" : "gasoline";
}

function modeAllowedSignals(mode){
  return MODE_VISIBLE_SIGNALS[mode] || MODE_VISIBLE_SIGNALS.gasoline;
}

const chartColors = ["#4aa8ff", "#57f2a8", "#ffbe55", "#ff6b6b", "#c78cff", "#7fe0ff", "#f28de1", "#9bff8e", "#ffd6a5", "#9ad5ff"];

async function postJSON(url, data){
  const res = await fetch(url,{method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(data)});
  return await res.json();
}

function ensureStudentId(){
  if(state.role !== "student") return "";
  if(state.studentId) return state.studentId;
  const key = "scanmatic_student_id";
  const existing = localStorage.getItem(key);
  if(existing){
    state.studentId = existing;
    return existing;
  }
  const generated = `student-${Math.random().toString(36).slice(2, 10)}`;
  localStorage.setItem(key, generated);
  state.studentId = generated;
  return generated;
}

function ensureStudentName(){
  if(state.role !== "student") return "";
  if(typeof state.studentName === "string" && state.studentName.length) return state.studentName;
  const key = "scanmatic_student_name";
  const existing = (localStorage.getItem(key) || "").trim();
  state.studentName = existing;
  return existing;
}

function instructorTargetPayload(){
  return {
    target_scope: state.targetAllStudents ? "all" : "selected",
    target_student_id: state.selectedStudentId || "",
  };
}

function sendInstructorCommand(payload){
  return postJSON("/api/instructor/command", {...payload, ...instructorTargetPayload()});
}

function sendInstructorCommandForStudent(studentId, payload){
  return postJSON("/api/instructor/command", {
    ...payload,
    target_scope: "selected",
    target_student_id: studentId || "",
  });
}

function sendStudentAction(payload){
  const sid = ensureStudentId();
  return postJSON("/api/student/action", {...payload, student_id: sid});
}

function fmt(value){
  if(typeof value === "number") return Number.isInteger(value) ? value.toString() : value.toFixed(2).replace(/\.00$/,'');
  return value ?? "-";
}

function badgeStatus(isOn){ return `<span class="badge ${isOn ? 'ok':'bad'}">${isOn ? 'Activo':'Apagado'}</span>`; }

function getPathValue(data, path){
  return path.split('.').reduce((acc, part)=>acc?.[part], data);
}

function renderKPIs(data){
  document.querySelectorAll("[data-kpi]").forEach(el=>{
    const val = getPathValue(data, el.dataset.kpi);
    el.textContent = fmt(val);
  });
  const engineEl = document.getElementById("engineStatus");
  if(engineEl) engineEl.innerHTML = `<span class="status-dot ${data.engine_on ? 'on':'off'}"></span>${data.engine_on ? 'Motor en marcha':'Motor KOEO / detenido'}`;
}

function renderStateSummary(data){
  const el = document.getElementById("stateSummary");
  if(!el) return;
  const stageLabel = data.stage === "key_on" ? "KOEO" : data.stage;
  const milBadge = data.mil_on ? '<span class="badge bad">Check Engine ON</span>' : '<span class="badge ok">Check Engine OFF</span>';
  const scenario = data.scenario_name ? `  Escenario: ${data.scenario_name}` : '';
  el.innerHTML = `<div class="row" style="justify-content:space-between"><div><div><strong>${data.vehicle_profile}</strong></div><small>Sesion: ${data.session_name}  Codigo: <span class="code-box">${data.session_code}</span>${scenario}</small></div><div class="row"><span class="badge info">${data.mode === 'gasoline' ? 'Gasolina':'Diesel'}</span>${badgeStatus(data.engine_on)}<span class="badge warn">Etapa: ${stageLabel}</span>${milBadge}</div></div>`;
}

function unitFor(key){
  const units = {rpm:"rpm",vehicle_speed:"km/h",throttle:"%",app:"%",map:"kPa",maf:"g/s",iat:"C",ect:"C",o2_b1s1:"V",o2_b1s2:"V",stft:"%",ltft:"%",battery_voltage:"V",fuel_pressure:"kPa",rail_pressure:"bar",boost:"kPa",egr_position:"%",inj_cmd_ms:"ms",inj_dead_time_ms:"ms",inj_ms:"ms",spark_advance:"",calculated_load:"%",cooling_fan:"0/1"};
  return units[key] || "";
}

function renderSensorsTable(data){
  const table = document.getElementById("liveDataTable");
  if(!table) return;
  const mode = data?.mode === "diesel" ? "diesel" : "gasoline";
  const order = MODE_VISIBLE_SIGNALS[mode] || MODE_VISIBLE_SIGNALS.gasoline;
  table.innerHTML = order.map(key=>`<tr><td>${key}</td><td>${fmt(data.sensors?.[key])}</td><td>${unitFor(key)}</td></tr>`).join("");
}

function renderDtcTable(data){
  const table = document.getElementById("dtcTable");
  if(!table) return;

  if(state.role === "student" && !state.studentDtcScanned){
    table.innerHTML = "";
    toggleStudentDtcHint(true);
    return;
  }

  toggleStudentDtcHint(false);
  if(!data.dtcs?.length){ table.innerHTML = `<tr><td colspan="5"><span class="badge ok">Sin DTC activos</span></td></tr>`; return; }
  table.innerHTML = data.dtcs.map((dtc, idx)=>`<tr><td><strong>${dtc.code}</strong></td><td>${dtc.description}</td><td>${dtc.status}</td><td>${dtc.category}</td><td><button class="ghost" type="button" data-freeze-idx="${idx}">Freeze</button></td></tr>`).join("");
  table.querySelectorAll('[data-freeze-idx]').forEach(btn=>{
    btn.onclick = ()=>{
      const idx = Number(btn.dataset.freezeIdx);
      const dtc = data.dtcs?.[idx];
      showFreezeFrameData(dtc?.freeze_frame || {}, {navigate:true});
    };
  });
}

function toggleStudentDtcHint(show){
  const el = document.getElementById("studentDtcHint");
  if(el) el.style.display = show ? "block" : "none";
}

function showFreezeFrameData(data, options = {}){
  const el = document.getElementById("freezeFrameBox");
  if(!el) return;
  const rows = Object.entries(data || {}).map(([k,v])=>`<tr><td>${k}</td><td>${fmt(v)}</td></tr>`).join("");
  el.innerHTML = rows ? `<div class="freeze-card"><table><tbody>${rows}</tbody></table></div>` : `<div class="scan-placeholder">Aun no hay freeze frame registrado.</div>`;
  if(options.navigate && state.role === "student") setScannerSection("scanner-freeze");
}

function showFreezeFrameEncoded(encodedText){
  const data = JSON.parse(decodeURIComponent(encodedText));
  showFreezeFrameData(data, {navigate:true});
}
window.showFreezeFrameEncoded = showFreezeFrameEncoded;

function renderFreezeFramesResponse(payload){
  const el = document.getElementById("freezeFrameBox");
  if(!el) return;
  if(state.role === "student") setScannerSection("scanner-freeze");
  const frames = payload?.freeze_frames || [];
  const current = payload?.current || null;
  if(!frames.length && !current){
    el.innerHTML = `<div class="scan-placeholder">Aun no hay freeze frame registrado.</div>`;
    return;
  }
  const blocks = [];
  frames.forEach(item=>{
    const rows = Object.entries(item.freeze_frame || {}).map(([k,v])=>`<tr><td>${k}</td><td>${fmt(v)}</td></tr>`).join("");
    blocks.push(`<div class="freeze-card"><div class="row" style="justify-content:space-between"><strong>${item.code}</strong><span class="badge ${item.status === 'confirmed' ? 'bad':'warn'}">${item.status}</span></div><div class="footer-note">${item.description}</div><table><tbody>${rows}</tbody></table></div>`);
  });
  if(current){
    const currentRows = Object.entries(current).map(([k,v])=>`<tr><td>${k}</td><td>${fmt(v)}</td></tr>`).join("");
    blocks.push(`<div class="freeze-card"><div class="row" style="justify-content:space-between"><strong>Estado actual</strong><span class="badge info">live</span></div><table><tbody>${currentRows}</tbody></table></div>`);
  }
  el.innerHTML = blocks.join("");
}



function renderMode05(data){
  const box = document.getElementById("mode05Box"); if(!box) return;
  if(!data){ box.innerHTML = '<div class="scan-placeholder">Presiona el boton para consultar las pruebas O2.</div>'; return; }
  box.innerHTML = `<div class="mode-box-grid"><div class="mode-stat"><small>B1S1</small><strong>${fmt(data.b1s1_voltage)} V</strong></div><div class="mode-stat"><small>B1S2</small><strong>${fmt(data.b1s2_voltage)} V</strong></div><div class="mode-stat"><small>Lazo cerrado</small><strong>${data.closed_loop_ready ? 'Si':'No'}</strong></div><div class="mode-stat"><small>Calefactor listo</small><strong>${data.heater_ready ? 'Si':'No'}</strong></div></div>`;
}

function renderMode06(rows){
  const table = document.getElementById("mode06Table"); if(!table) return;
  if(!rows?.length){ table.innerHTML = '<tr><td colspan="7"><span class="badge ok">Sin resultados</span></td></tr>'; return; }
  table.innerHTML = rows.map(r=>{
    const wearClass = (r.status === 'fail' || (r.wear_pct||0) >= 85) ? 'bad' : ((r.wear_pct||0) >= 60 ? 'warn' : 'ok');
    const note = r.note ? `<div class="footer-note">${r.note}</div>` : '';
    return `<tr><td>${r.tid}</td><td><strong>${r.label}</strong>${note}</td><td>${fmt(r.min)} ${r.unit||''}</td><td>${fmt(r.current)} ${r.unit||''}</td><td>${fmt(r.max)} ${r.unit||''}</td><td><span class="badge ${wearClass}">${fmt(r.wear_pct)}%</span></td><td><span class="badge ${r.status==='fail'?'bad':'ok'}">${r.status}</span></td></tr>`;
  }).join('');
}

function renderMode07(rows){
  const table = document.getElementById("mode07Table"); if(!table) return;
  if(!rows?.length){ table.innerHTML = '<tr><td colspan="3"><span class="badge ok">Sin DTC pendientes</span></td></tr>'; return; }
  table.innerHTML = rows.map(r=>`<tr><td><strong>${r.code}</strong></td><td>${r.description}</td><td>${r.status}</td></tr>`).join('');
}

function renderMode08(info){
  const box = document.getElementById("mode08Box"); if(!box) return;
  if(!info){ box.innerHTML = '<div class="scan-placeholder">Presiona el boton para consultar el Modo 08.</div>'; return; }
  const activeCount = Number(info.active_hidden_tests || 0);
  const behaviorBadge = activeCount > 0 ? '<span class="badge warn">Comportamiento del motor alterado por prueba</span>' : '<span class="badge ok">Sin pruebas activas</span>';
  box.innerHTML = `
    <div class="freeze-card">
      <div class="row" style="justify-content:space-between;align-items:flex-start;gap:12px">
        <div>
          <strong>${info.title || 'Modo 08  Control de actuadores'}</strong>
          <div class="footer-note" style="margin-top:8px">${info.message || ''}</div>
        </div>
        ${behaviorBadge}
      </div>
      <div class="mode-box-grid" style="margin-top:14px">
        <div class="mode-stat"><small>Pruebas ocultas activas</small><strong>${activeCount}</strong></div>
        <div class="mode-stat"><small>Estado educativo</small><strong>${info.engine_behavior || 'normal'}</strong></div>
      </div>
    </div>`;
}

function renderMode09(info){
  const box = document.getElementById("mode09Box"); if(!box) return;
  if(!info){ box.innerHTML = '<div class="scan-placeholder">Presiona el boton para cargar la informacion ECU.</div>'; return; }
  box.innerHTML = `<div class="mode-box-grid"><div class="mode-stat"><small>VIN</small><strong>${info.vin}</strong></div><div class="mode-stat"><small>Perfil</small><strong>${info.vehicle_profile}</strong></div><div class="mode-stat"><small>Protocolo</small><strong>${info.protocol}</strong></div><div class="mode-stat"><small>Etapa</small><strong>${info.stage}</strong></div></div>`;
}

function renderActuatorsPanel(data){
  document.querySelectorAll('[data-actuator]').forEach(el=>{
    const key = el.dataset.actuator;
    const value = data.actuators?.[key];
    if(el.tagName === 'SELECT'){
      if(document.activeElement !== el) el.value = value ?? 'auto';
    } else if(el.type === 'checkbox'){
      if(document.activeElement !== el) el.checked = !!value;
    }
  });
  if(state.role === 'student') renderMode08(data.mode08_summary);
}

function updateHistory(data){
  if(!data?.sensors) return;
  const mode = data?.mode === "diesel" ? "diesel" : "gasoline";
  const allowed = new Set(modeAllowedSignals(mode));
  for(const [key,val] of Object.entries(data.sensors)){
    if(!allowed.has(key)) continue;
    if(!state.history[key]) state.history[key] = [];
    state.history[key].push(Number(val) || 0);
    if(state.history[key].length > state.maxPoints) state.history[key].shift();
  }
}

function samplePoints(points, target){
  if(points.length <= target) return points.slice();
  const step = points.length / target;
  const out = [];
  for(let i = 0; i < target; i++) out.push(points[Math.floor(i * step)]);
  return out;
}

function smoothingWindowForSignal(key){
  if(["o2_b1s1", "o2_b1s2"].includes(key)) return 1;
  if(["stft", "ltft"].includes(key)) return 4;
  if(["rpm", "map", "maf", "inj_ms", "inj_cmd_ms", "spark_advance", "calculated_load", "vehicle_speed"].includes(key)) return 3;
  if(["ect", "iat", "battery_voltage", "fuel_pressure", "rail_pressure", "boost"].includes(key)) return 3;
  return 2;
}

function smoothSeries(points, windowSize){
  if(windowSize <= 1 || points.length < 3) return points.slice();
  const out = [];
  const half = Math.floor(windowSize / 2);
  for(let i = 0; i < points.length; i++){
    const start = Math.max(0, i - half);
    const end = Math.min(points.length - 1, i + half);
    let total = 0;
    let weightTotal = 0;
    for(let j = start; j <= end; j++){
      const distance = Math.abs(i - j);
      const weight = half + 1 - distance;
      total += points[j] * weight;
      weightTotal += weight;
    }
    out.push(total / Math.max(weightTotal, 1));
  }
  return out;
}

function getDisplaySeries(key, raw){
  if(key === "o2_b1s1" || key === "o2_b1s2"){
    return raw.slice();
  }
  const sampled = samplePoints(raw, Math.min(160, state.maxPoints));
  return smoothSeries(sampled, smoothingWindowForSignal(key));
}

function niceStep(range){
  if(range <= 0) return 1;
  const rough = range / 4;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
  const residual = rough / magnitude;
  if(residual <= 1) return 1 * magnitude;
  if(residual <= 2) return 2 * magnitude;
  if(residual <= 5) return 5 * magnitude;
  return 10 * magnitude;
}

function roundDown(value, step){
  return Math.floor(value / step) * step;
}

function roundUp(value, step){
  return Math.ceil(value / step) * step;
}

function fixedAxisForSignal(key){
  const fixed = {
    o2_b1s1: {min: 0.0, max: 1.0, ticks: 5},
    o2_b1s2: {min: 0.0, max: 1.0, ticks: 5},
    stft: {min: -30, max: 30, ticks: 6},
    ltft: {min: -30, max: 30, ticks: 6},
    throttle: {min: 0, max: 100, ticks: 5},
    app: {min: 0, max: 100, ticks: 5},
    calculated_load: {min: 0, max: 100, ticks: 5},
    cooling_fan: {min: 0, max: 1, ticks: 2},
    ckp_sync: {min: 0, max: 1.2, ticks: 4},
    cmp_sync: {min: 0, max: 1.2, ticks: 4},
    battery_voltage: {min: 10, max: 16, ticks: 6},
  };
  return fixed[key] || null;
}

function computedAxisForSignal(key, points){
  const fixed = fixedAxisForSignal(key);
  if(fixed) return fixed;

  const minVal = Math.min(...points);
  const maxVal = Math.max(...points);
  const span = maxVal - minVal;

  const compactSignals = new Set([
    "rpm","map","maf","fuel_pressure","rail_pressure","boost",
    "inj_cmd_ms","inj_dead_time_ms","inj_ms","spark_advance",
    "ect","iat","vehicle_speed","egr_position"
  ]);

  let min = minVal;
  let max = maxVal;

  if(span === 0){
    const pad = Math.max(Math.abs(minVal) * 0.08, 1);
    min -= pad;
    max += pad;
  } else {
    const padFactor = compactSignals.has(key) ? 0.12 : 0.18;
    const pad = span * padFactor;
    min -= pad;
    max += pad;
  }

  if(key === "rpm"){
    const step = 100;
    min = roundDown(min, step);
    max = roundUp(max, step);
    if((max - min) < 400){
      const center = (max + min) / 2;
      min = roundDown(center - 200, step);
      max = roundUp(center + 200, step);
    }
    return {min, max, ticks: 4};
  }

  if(key === "map"){
    const step = 5;
    min = roundDown(min, step);
    max = roundUp(max, step);
    if((max - min) < 20){
      const center = (max + min) / 2;
      min = roundDown(center - 10, step);
      max = roundUp(center + 10, step);
    }
    return {min, max, ticks: 4};
  }

  if(key === "maf"){
    const step = 1;
    min = roundDown(min, step);
    max = roundUp(max, step);
    if((max - min) < 6){
      const center = (max + min) / 2;
      min = roundDown(center - 3, step);
      max = roundUp(center + 3, step);
    }
    return {min, max, ticks: 4};
  }

  if(key === "fuel_pressure"){
    const step = 10;
    min = roundDown(min, step);
    max = roundUp(max, step);
    return {min, max, ticks: 4};
  }

  if(key === "rail_pressure"){
    const step = 20;
    min = roundDown(min, step);
    max = roundUp(max, step);
    return {min, max, ticks: 4};
  }

  if(key === "boost"){
    const step = 5;
    min = roundDown(min, step);
    max = roundUp(max, step);
    return {min, max, ticks: 4};
  }

  if(key === "ect" || key === "iat"){
    const step = 5;
    min = roundDown(min, step);
    max = roundUp(max, step);
    if((max - min) < 20){
      const center = (max + min) / 2;
      min = roundDown(center - 10, step);
      max = roundUp(center + 10, step);
    }
    return {min, max, ticks: 4};
  }

  if(key === "inj_cmd_ms" || key === "inj_dead_time_ms" || key === "inj_ms"){
    const step = 0.5;
    min = roundDown(min, step);
    max = roundUp(max, step);
    return {min, max, ticks: 4};
  }

  if(key === "spark_advance"){
    const step = 5;
    min = roundDown(min, step);
    max = roundUp(max, step);
    return {min, max, ticks: 4};
  }

  if(key === "vehicle_speed"){
    const step = 10;
    min = roundDown(min, step);
    max = roundUp(max, step);
    return {min, max, ticks: 4};
  }

  const step = niceStep(max - min);
  min = roundDown(min, step);
  max = roundUp(max, step);
  return {min, max, ticks: 4};
}

function buildAxisForKeys(seriesList){
  if(seriesList.length === 1){
    return computedAxisForSignal(seriesList[0].key, seriesList[0].points);
  }

  const all = seriesList.flatMap(item => item.points);
  let min = Math.min(...all);
  let max = Math.max(...all);
  if(min === max){
    const pad = Math.max(Math.abs(min) * 0.1, 1);
    min -= pad;
    max += pad;
  } else {
    const pad = (max - min) * 0.14;
    min -= pad;
    max += pad;
  }
  const step = niceStep(max - min);
  min = roundDown(min, step);
  max = roundUp(max, step);
  return {min, max, ticks: 4};
}

function drawSmoothCurveSeries(ctx, points, margin, plotW, plotH, min, yRange){
  if(points.length < 2) return;
  const coords = points.map((val, i) => ({
    x: margin.left + (i / (points.length - 1)) * plotW,
    y: margin.top + (1 - ((val - min) / yRange)) * plotH,
  }));
  ctx.beginPath();
  ctx.moveTo(coords[0].x, coords[0].y);
  for(let i = 0; i < coords.length - 1; i++){
    const current = coords[i];
    const next = coords[i + 1];
    const xc = (current.x + next.x) / 2;
    const yc = (current.y + next.y) / 2;
    ctx.quadraticCurveTo(current.x, current.y, xc, yc);
  }
  const last = coords[coords.length - 1];
  ctx.lineTo(last.x, last.y);
  ctx.stroke();
}

function drawChartToCanvas(canvas, keyList){
  if(!canvas || !keyList.length) return;
  const allowed = new Set(modeAllowedSignals(currentModeKey()));
  keyList = keyList.filter(k=>allowed.has(k));
  if(!keyList.length) return;
  const card = canvas.closest("[data-chart-card]");
  const expanded = card?.classList.contains("chart-expanded");
  const width = Math.max(360, (card?.clientWidth || canvas.parentElement.clientWidth) - 4);
  const height = expanded ? Math.max(420, Math.round(window.innerHeight * 0.72)) : 280;
  if(canvas.width !== width) canvas.width = width;
  if(canvas.height !== height) canvas.height = height;
  const ctx = canvas.getContext("2d");
  const margin = {top: 28, right: 18, bottom: 34, left: 58};
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  if(plotW <= 10 || plotH <= 10) return;

  ctx.clearRect(0,0,width,height);
  ctx.fillStyle = "#081a29";
  ctx.fillRect(0,0,width,height);

  const series = keyList.map((key, idx)=>{
    const raw = state.history[key] || [];
    const points = getDisplaySeries(key, raw);
    return { key, idx, points, color: chartColors[idx % chartColors.length] };
  }).filter(item => item.points.length >= 2);
  if(!series.length) return;

  const useMultiScale = keyList.length > 1;
  const seriesWithAxis = series.map((item)=>{
    const axisForSeries = computedAxisForSignal(item.key, item.points);
    const rawMin = Math.min(...item.points);
    const rawMax = Math.max(...item.points);
    const rawLast = item.points[item.points.length - 1];
    return {...item, axis: axisForSeries, rawMin, rawMax, rawLast};
  });
  const axis = useMultiScale ? {min: 0, max: 100, ticks: 5} : buildAxisForKeys(seriesWithAxis);
  const min = axis.min;
  const max = axis.max;
  const ticks = axis.ticks || 4;
  const yRange = Math.max(max - min, 0.0001);

  ctx.strokeStyle = "#20415f";
  ctx.lineWidth = 1;
  ctx.font = "11px Arial";
  ctx.fillStyle = "#9fc3dd";

  for(let i=0;i<=ticks;i++){
    const ratio = i / ticks;
    const y = margin.top + ratio * plotH;
    ctx.beginPath();
    ctx.moveTo(margin.left, y);
    ctx.lineTo(width - margin.right, y);
    ctx.stroke();
    if(!useMultiScale){
      const value = max - ratio * yRange;
      ctx.fillText(fmt(Number(value.toFixed(3))), 6, y + 4);
    }
  }

  const secondsWindow = Math.max(1, Math.round((state.maxPoints - 1) * state.secondsPerSample));
  for(let i=0;i<=4;i++){
    const ratio = i / 4;
    const x = margin.left + ratio * plotW;
    ctx.beginPath();
    ctx.moveTo(x, margin.top);
    ctx.lineTo(x, height - margin.bottom);
    ctx.stroke();
    const sec = Math.round((ratio * secondsWindow) * 10) / 10;
    ctx.fillText(`${sec}s`, x - 10, height - 10);
  }

  ctx.strokeStyle = "#6ea4c5";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(margin.left, margin.top);
  ctx.lineTo(margin.left, height - margin.bottom);
  ctx.lineTo(width - margin.right, height - margin.bottom);
  ctx.stroke();

  seriesWithAxis.forEach(({key, points, color, axis: axisForSeries, rawLast, rawMin, rawMax}, idx)=>{
    const axisMin = useMultiScale ? axisForSeries.min : min;
    const axisRange = useMultiScale ? Math.max(axisForSeries.max - axisForSeries.min, 0.0001) : yRange;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    if(key === "o2_b1s1" || key === "o2_b1s2"){
      drawSmoothCurveSeries(ctx, points, margin, plotW, plotH, axisMin, axisRange);
    } else {
      ctx.beginPath();
      points.forEach((val,i)=>{
        const x = margin.left + (i/(points.length-1))*plotW;
        const y = margin.top + (1 - ((val-axisMin)/axisRange))*plotH;
        if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
      });
      ctx.stroke();
    }
    ctx.fillStyle = color;
    const lastVal = useMultiScale ? rawLast : points[points.length - 1];
    const unit = signalMap[key]?.unit || unitFor(key);
    const suffix = useMultiScale ? `  rango ${fmt(rawMin)}-${fmt(rawMax)} ${unit}` : "";
    ctx.fillText(`${signalMap[key]?.label || key}: ${fmt(lastVal)} ${unit}${suffix}`, margin.left + 8, 16 + idx*16);

    if(!useMultiScale && (key === "o2_b1s1" || key === "o2_b1s2")){
      ctx.save();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = "rgba(255,255,255,0.18)";
      [0.2, 0.8].forEach(refVal => {
        const yRef = margin.top + (1 - ((refVal-min)/yRange))*plotH;
        ctx.beginPath();
        ctx.moveTo(margin.left, yRef);
        ctx.lineTo(width - margin.right, yRef);
        ctx.stroke();
      });
      ctx.restore();
    }
  });

  ctx.save();
  ctx.translate(16, margin.top + plotH/2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillStyle = "#c7dced";
  const yUnit = useMultiScale ? "Multi-escala por PID" : (signalMap[keyList[0]]?.unit || unitFor(keyList[0]) || "valor");
  ctx.fillText(`Y: ${yUnit}`, 0, 0);
  ctx.restore();

  ctx.fillStyle = "#c7dced";
  ctx.fillText("X: tiempo", width/2 - 28, height - 10);
}

function resolveActiveGraphCanvas(){
  if(state.modalChartToken){
    const modalCanvas = document.getElementById("chartModalCanvas");
    if(modalCanvas) return modalCanvas;
  }
  if(state.graphMode === "split"){
    const splitCanvas = document.querySelector("#splitCharts canvas");
    if(splitCanvas) return splitCanvas;
  }
  return document.getElementById("liveChart");
}

function refreshGraphActionButtons(){
  const paused = !!state.graphPaused;
  document.querySelectorAll("[data-graph-pause-btn]").forEach(btn=>{
    btn.textContent = paused ? "Reanudar grafica" : "Pausar grafica";
    btn.classList.toggle("warn", paused);
    btn.classList.toggle("ghost", !paused);
  });
  const hasCanvas = !!resolveActiveGraphCanvas();
  document.querySelectorAll("[data-graph-export-btn]").forEach(btn=>{
    btn.disabled = !hasCanvas;
  });
}

function toggleGraphPause(){
  state.graphPaused = !state.graphPaused;
  refreshGraphActionButtons();
  if(!state.graphPaused){
    scheduleDraw(true);
  }
}

function exportGraphImage(){
  const canvas = resolveActiveGraphCanvas();
  if(!canvas) return;
  const mode = currentModeKey();
  const view = state.modalChartToken ? "modal" : state.graphMode;
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const name = `scanmatic_${mode}_${view}_${stamp}.png`;
  const link = document.createElement("a");
  link.href = canvas.toDataURL("image/png");
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function bindGraphActionButtons(){
  document.querySelectorAll("[data-graph-pause-btn]").forEach(btn=>{
    if(btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.onclick = ()=>toggleGraphPause();
  });
  document.querySelectorAll("[data-graph-export-btn]").forEach(btn=>{
    if(btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.onclick = ()=>exportGraphImage();
  });
  refreshGraphActionButtons();
}

function ensureSplitCanvases(){
  const root = document.getElementById("splitCharts");
  if(!root) return;
  const keys = Array.from(state.selectedSignals);
  const signature = keys.join("|");
  if(root.children.length && state.splitChartSignature === signature){
    Array.from(root.children).forEach(card=>{
      card.classList.toggle("chart-expanded", state.expandedChartKey === card.dataset.chartCard);
    });
    return;
  }
  state.splitChartSignature = signature;
  root.innerHTML = keys.map((key, idx)=>`<div class="split-card ${state.expandedChartKey === key ? 'chart-expanded' : ''}" data-chart-card="${key}"><div class="split-toolbar"><div class="split-title">${signalMap[key]?.label || key}</div><button class="ghost mini-btn" type="button" data-expand-chart="${key}">Ampliar</button></div><canvas id="split_chart_${idx}" data-key="${key}"></canvas></div>`).join("");
}

function renderGraphMode(){
  const mixed = document.getElementById("graphPanelMixed");
  const split = document.getElementById("graphPanelSplit");
  if(!mixed || !split) return;
  mixed.classList.toggle("active", state.graphMode === "mixed");
  split.classList.toggle("active", state.graphMode === "split");
  if(state.graphMode === "split") ensureSplitCanvases();
  updateExpandButtons();
}
function updateExpandButtons(){
  document.querySelectorAll("[data-expand-chart]").forEach(btn=>{
    const key = btn.dataset.expandChart;
    const target = btn.dataset.chartTarget || "split";
    const expanded = state.modalChartToken === `${target}:${key}`;
    btn.textContent = expanded ? "Reducir" : "Ampliar";
    btn.onclick = ()=>toggleChartExpand(key, target);
  });
}

function ensureChartModal(){
  let modal = document.getElementById("chartModal");
  if(modal) return modal;
  modal = document.createElement("div");
  modal.id = "chartModal";
  modal.className = "chart-modal";
  const studentControls = state.role === "student"
    ? `<div class="row" style="margin:4px 4px 0 4px;align-items:end;gap:12px"><div style="flex:1;min-width:220px"><label>APP <span data-student-app-value>0</span>%</label><input data-student-app-slider type="range" min="0" max="100" step="0.1" value="0"><small data-student-app-status class="footer-note"></small></div></div>`
    : "";
  modal.innerHTML = `<div class="chart-modal-card"><div class="chart-modal-toolbar"><div id="chartModalTitle" class="split-title">Grafica ampliada</div><div class="row"><button class="ghost mini-btn" type="button" data-graph-pause-btn>Pausar grafica</button><button class="ghost mini-btn" type="button" data-graph-export-btn>Exportar imagen</button><button id="chartModalClose" class="ghost mini-btn" type="button">Cerrar</button></div></div>${studentControls}<canvas id="chartModalCanvas"></canvas></div>`;
  document.body.appendChild(modal);
  modal.addEventListener("click", (ev)=>{ if(ev.target === modal) closeChartModal(); });
  modal.querySelector("#chartModalClose").onclick = ()=>closeChartModal();
  bindGraphActionButtons();
  return modal;
}

function openChartModal(key, target = "split"){
  const modal = ensureChartModal();
  state.modalChartToken = `${target}:${key}`;
  const title = modal.querySelector("#chartModalTitle");
  title.textContent = target === "mixed" ? "Vista mezclada" : (signalMap[key]?.label || key);
  modal.classList.add("active");
  document.body.classList.add("modal-open");
  scheduleDraw();
}

function closeChartModal(){
  state.modalChartToken = null;
  const modal = document.getElementById("chartModal");
  if(modal) modal.classList.remove("active");
  document.body.classList.remove("modal-open");
}

function toggleChartExpand(key, target = "split"){
  const token = `${target}:${key}`;
  if(state.modalChartToken === token){
    closeChartModal();
  } else {
    openChartModal(key, target);
  }
  updateExpandButtons();
}


function scheduleDraw(forceDraw = false){
  if(!forceDraw && state.graphPaused){
    refreshGraphActionButtons();
    return;
  }
  if(state.pendingDraw) return;
  state.pendingDraw = true;
  requestAnimationFrame(()=>{
    state.pendingDraw = false;
    const selected = Array.from(state.selectedSignals);
    drawChartToCanvas(document.getElementById("liveChart"), selected);
    if(state.graphMode === "split"){
      ensureSplitCanvases();
      updateExpandButtons();
      document.querySelectorAll("#splitCharts canvas").forEach((canvas, idx)=>{
        drawChartToCanvas(canvas, [selected[idx]].filter(Boolean));
      });
    }
    if(state.modalChartToken){
      const [target, key] = state.modalChartToken.split(":");
      const modalCanvas = document.getElementById("chartModalCanvas");
      if(modalCanvas){
        drawChartToCanvas(modalCanvas, target === "mixed" ? selected : [key].filter(Boolean));
      }
    }
    refreshGraphActionButtons();
  });
}

function renderFaults(data){
  const root = document.getElementById("faultList"); if(!root) return;
  const mode = data?.mode === "diesel" ? "diesel" : "gasoline";
  const allowed = new Set(MODE_VISIBLE_FAULTS[mode] || []);
  const faults = [["maf_low","MAF bajo"],["maf_dirty","MAF sucio"],["vacuum_leak","Falso aire"],["ect_biased_cold","ECT falsa en frio"],["o2_slow","O2 lenta"],["o2_intermittent","O2 intermitente"],["misfire_cyl1","Misfire cil.1"],["ckp_intermittent","CKP intermitente"],["cmp_fault","CMP sin senal"],["map_slow","MAP con retardo"],["throttle_lag","Throttle con retardo"],["idle_hunt","Ralenti inestable"],["rail_pressure_low","Riel bajo"],["boost_low","Turbo bajo"],["egr_stuck_open","EGR atascada"]]
    .filter(([key])=>allowed.has(key));
  const active = new Set(data.faults || []);
  root.innerHTML = faults.map(([key,label])=>`<button class="fault-item ${active.has(key) ? 'active':''}" type="button" data-fault="${key}" aria-pressed="${active.has(key) ? 'true':'false'}">${label}</button>`).join("");
  root.onclick = async (ev)=>{
    const btn = ev.target.closest("[data-fault]");
    if(!btn || !root.contains(btn)) return;
    await sendInstructorCommand({action:"toggle_fault", fault: btn.dataset.fault});
  };
}


async function recoverEngineFromAlert(){
  if(state.role !== "instructor") return;
  const recoverBtn = document.getElementById("recoverEngineBtn");
  if(recoverBtn){
    recoverBtn.disabled = true;
    recoverBtn.textContent = "Corrigiendo...";
  }
  try{
    await sendInstructorCommand({action:"reset_all"});
    await sendInstructorCommand({action:"set_stage", stage:"idle"});
    await sendInstructorCommand({action:"toggle_engine", engine_on:true});
  } finally {
    if(recoverBtn){
      recoverBtn.disabled = false;
      recoverBtn.textContent = "Corregir y reiniciar";
    }
  }
}

function closeEngineShutdownAlert(overlay){
  state.engineShutdownDismissed = true;
  if(overlay){
    overlay.classList.remove("visible");
    overlay.innerHTML = "";
  }
}

function renderEngineShutdownAlert(data){
  let overlay = document.getElementById("engineShutdownOverlay");
  if(!overlay){
    overlay = document.createElement("div");
    overlay.id = "engineShutdownOverlay";
    overlay.className = "engine-shutdown-overlay";
    overlay.onclick = (ev)=>{
      if(ev.target === overlay){
        closeEngineShutdownAlert(overlay);
      }
    };
    document.body.appendChild(overlay);
  }
  if(data.engine_shutdown_alert){
    const shutdownMessage = data.engine_shutdown_message || "Motor apagado por falla";
    state.lastEngineShutdownMessage = shutdownMessage;
    if(state.engineShutdownDismissed){
      overlay.classList.remove("visible");
      overlay.innerHTML = "";
      return;
    }
    const recoverButton = state.role === "instructor"
      ? `<button id="recoverEngineBtn" class="ok">Corregir y reiniciar</button>`
      : ``;
    overlay.innerHTML = `<div class="engine-shutdown-card"><div class="row" style="justify-content:flex-end"><button id="closeEngineShutdownTopBtn" class="warn">Cerrar</button></div><div class="engine-shutdown-icon">&#9888;</div><div class="engine-shutdown-title">${shutdownMessage}</div><div class="engine-shutdown-subtitle">Usa los controles del Panel 1 para corregir la causa y reiniciar.</div><div class="row" style="justify-content:center;margin-top:12px;gap:8px">${recoverButton}<button id="closeEngineShutdownBtn" class="warn">Cerrar aviso</button></div></div>`;
    overlay.classList.add("visible");
    const closeBtn = document.getElementById("closeEngineShutdownBtn");
    if(closeBtn){
      closeBtn.onclick = ()=>closeEngineShutdownAlert(overlay);
    }
    const closeTopBtn = document.getElementById("closeEngineShutdownTopBtn");
    if(closeTopBtn){
      closeTopBtn.onclick = ()=>closeEngineShutdownAlert(overlay);
    }
    const recoverBtn = document.getElementById("recoverEngineBtn");
    if(recoverBtn){
      recoverBtn.onclick = ()=>recoverEngineFromAlert();
    }
  } else {
    state.engineShutdownDismissed = false;
    state.lastEngineShutdownMessage = "";
    overlay.classList.remove("visible");
    overlay.innerHTML = "";
  }
}

function renderStudentConnection(data){
  const el = document.getElementById("studentSummary"); if(!el) return;
  const mil = data.mil_on ? '<span class="badge bad"> Check Engine</span>' : '<span class="badge ok"> Sin MIL</span>';
  const displayName = data.student_name || ensureStudentName() || data.student_id || "Sin nombre";
  el.innerHTML = `<div class="row" style="flex-wrap:wrap"><span class="badge info">Estudiante: ${displayName}</span><span class="badge info">VIN ${data.vin}</span><span class="badge info">Modo ${data.mode}</span>${mil}<span class="badge warn">${data.students_connected} estudiantes</span></div>`;
  const input = document.getElementById("studentNameInput");
  if(input && document.activeElement !== input){
    input.value = ensureStudentName() || data.student_name || "";
  }
}

function renderStudentEngineControl(data){
  if(state.role !== "student") return;
  const status = document.getElementById("studentEngineControlStatus");
  const reqBtn = document.getElementById("studentRequestEngineControlBtn");
  const toggleBtn = document.getElementById("studentToggleEngineBtn");
  const stageSel = document.getElementById("studentStageSelect");
  const applyBtn = document.getElementById("studentApplyStageBtn");
  const control = data?.engine_control || {};
  const canUseNow = !!control.can_use_now;
  const enabled = !!control.enabled && canUseNow;
  const pending = !!control.request_pending;

  if(reqBtn){
    reqBtn.disabled = !canUseNow || pending || enabled;
  }
  if(toggleBtn) toggleBtn.disabled = !enabled;
  if(stageSel){
    stageSel.disabled = !enabled;
    if(document.activeElement !== stageSel && data?.stage){
      stageSel.value = data.stage;
    }
  }
  if(applyBtn) applyBtn.disabled = !enabled;
  if(status){
    if(!canUseNow){
      status.textContent = "Estado: primero borra DTC para habilitar esta solicitud.";
    } else if(enabled){
      status.textContent = `Estado: autorizado. Motor ${data?.engine_on ? "encendido" : "apagado"}  Etapa: ${data?.stage || "-"}.`;
    } else if(pending){
      status.textContent = "Estado: solicitud enviada, esperando autorizacion del instructor.";
    } else {
      status.textContent = "Estado: listo para solicitar autorizacion.";
    }
  }
}

function renderCommandCenter(data){
  if(state.role !== "instructor") return;
  const center = data.control_center || {};
  const students = Array.isArray(center.students) ? center.students : [];
  state.controlCenterStudents = students;
  if(!state.selectedStudentId){
    state.selectedStudentId = center.selected_student_id || students[0]?.id || "";
  }
  const select = document.getElementById("targetStudentSelect");
  if(select){
    const current = state.selectedStudentId || "";
    select.innerHTML = students.length
      ? students.map(s => `<option value="${s.id}">${s.name || "Sin nombre"}${s.connected ? "  conectado" : "  offline"}</option>`).join("")
      : `<option value="">Sin estudiantes</option>`;
    if(Array.from(select.options).some(opt => opt.value === current)) select.value = current;
    else if(students[0]){ select.value = students[0].id; state.selectedStudentId = students[0].id; }
    select.disabled = state.targetAllStudents || students.length === 0;
  }
  const allToggle = document.getElementById("targetAllToggle");
  if(allToggle && document.activeElement !== allToggle){
    allToggle.checked = !!state.targetAllStudents;
  }
  const status = document.getElementById("commandCenterStatus");
  if(status){
    const connected = students.filter(s=>s.connected).length;
    const selectedRow = students.find(s => s.id === state.selectedStudentId);
    const targetName = selectedRow?.name || "ninguno";
    const targetText = state.targetAllStudents ? "Destino: TODOS" : `Destino: ${targetName}`;
    status.textContent = `Centro de comando: ${connected}/${students.length} estudiantes conectados. ${targetText}.`;
  }
}

function renderDtcClearRequests(data){
  if(state.role !== "instructor") return;
  const info = document.getElementById("dtcClearApprovalInfo");
  const approveBtn = document.getElementById("approveSelectedClearBtn");
  const approveEngineControlBtn = document.getElementById("approveSelectedEngineControlBtn");
  const approveEngineOffBtn = document.getElementById("approveSelectedEngineOffBtn");
  const approveResetBtn = document.getElementById("approveSelectedResetBtn");
  const denyBtn = document.getElementById("denySelectedClearBtn");
  if(!info) return;
  const selected = state.selectedStudentId || data?.control_center?.selected_student_id || "";
  const students = Array.isArray(data?.control_center?.students) ? data.control_center.students : [];
  const selectedRow = students.find(s => s.id === selected);
  const pendingClear = students.filter(s => s.clear_dtc_request_pending).length;
  const pendingEngineControl = students.filter(s => s.engine_control_request_pending).length;
  const pendingOff = students.filter(s => s.engine_off_request_pending).length;
  const pendingReset = students.filter(s => s.reset_session_request_pending).length;
  const totalPending = pendingClear + pendingEngineControl + pendingOff + pendingReset;
  if(!selectedRow){
    info.textContent = `Sin estudiante objetivo. Pendientes totales: ${totalPending}.`;
    if(approveBtn) approveBtn.disabled = true;
    if(approveEngineControlBtn) approveEngineControlBtn.disabled = true;
    if(approveEngineOffBtn) approveEngineOffBtn.disabled = true;
    if(approveResetBtn) approveResetBtn.disabled = true;
    if(denyBtn) denyBtn.disabled = true;
    if(approveBtn) approveBtn.classList.remove("alert-pulse");
    if(approveEngineControlBtn) approveEngineControlBtn.classList.remove("alert-pulse");
    if(approveEngineOffBtn) approveEngineOffBtn.classList.remove("alert-pulse");
    if(approveResetBtn) approveResetBtn.classList.remove("alert-pulse");
    updateInstructorAuthorizationAlert(students, totalPending);
    return;
  }
  const clearReq = !!selectedRow.clear_dtc_request_pending;
  const clearApproved = !!selectedRow.clear_dtc_approved;
  const engineControlReq = !!selectedRow.engine_control_request_pending;
  const engineControlEnabled = !!selectedRow.engine_control_enabled;
  const offReq = !!selectedRow.engine_off_request_pending;
  const resetReq = !!selectedRow.reset_session_request_pending;
  const status = [
    `Borrar DTC: ${clearReq ? "pendiente" : (clearApproved ? "aprobado" : "sin solicitud")}`,
    `Control motor: ${engineControlReq ? "pendiente" : (engineControlEnabled ? "habilitado" : "sin solicitud")}`,
    `Apagar: ${offReq ? "pendiente" : "sin solicitud"}`,
    `Restablecer: ${resetReq ? "pendiente" : "sin solicitud"}`,
  ].join("  ");
  info.textContent = `${selectedRow.name || selectedRow.id}: ${status}. Pendientes globales: ${totalPending}.`;
  if(approveBtn) approveBtn.disabled = false;
  if(approveEngineControlBtn) approveEngineControlBtn.disabled = false;
  if(approveEngineOffBtn) approveEngineOffBtn.disabled = false;
  if(approveResetBtn) approveResetBtn.disabled = false;
  if(denyBtn) denyBtn.disabled = false;
  if(approveBtn) approveBtn.classList.toggle("alert-pulse", clearReq);
  if(approveEngineControlBtn) approveEngineControlBtn.classList.toggle("alert-pulse", engineControlReq);
  if(approveEngineOffBtn) approveEngineOffBtn.classList.toggle("alert-pulse", offReq);
  if(approveResetBtn) approveResetBtn.classList.toggle("alert-pulse", resetReq);
  updateInstructorAuthorizationAlert(students, totalPending);
}

function playInstructorAlertTone(){
  try{
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if(!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = 920;
    gain.gain.value = 0.001;
    osc.connect(gain);
    gain.connect(ctx.destination);
    const t = ctx.currentTime;
    gain.gain.exponentialRampToValueAtTime(0.12, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.32);
    osc.start(t);
    osc.stop(t + 0.34);
    osc.onended = ()=>ctx.close();
  }catch(_e){}
}

function stopInstructorAuthAudio(){
  if(state.authAlertAudioTimer){
    clearInterval(state.authAlertAudioTimer);
    state.authAlertAudioTimer = null;
  }
}

function ensureInstructorAuthOverlay(){
  let overlay = document.getElementById("instructorAuthOverlay");
  if(overlay) return overlay;
  overlay = document.createElement("div");
  overlay.id = "instructorAuthOverlay";
  overlay.className = "instructor-auth-overlay";
  overlay.innerHTML = `<div class="instructor-auth-card"><div class="row" style="justify-content:space-between;align-items:center"><strong class="instructor-auth-title">ALERTA DE AUTORIZACION</strong><button id="instructorAuthCloseBtn" class="ghost">Cerrar</button></div><div id="instructorAuthBody" class="footer-note" style="margin-top:10px"></div></div>`;
  document.body.appendChild(overlay);
  const closeBtn = overlay.querySelector("#instructorAuthCloseBtn");
  if(closeBtn){
    closeBtn.onclick = ()=>{
      state.authAlertDismissedSignature = state.lastAuthPendingSignature || "";
      overlay.classList.remove("visible");
      stopInstructorAuthAudio();
    };
  }
  overlay.onclick = (ev)=>{
    if(ev.target === overlay){
      state.authAlertDismissedSignature = state.lastAuthPendingSignature || "";
      overlay.classList.remove("visible");
      stopInstructorAuthAudio();
    }
  };
  return overlay;
}

function updateInstructorAuthorizationAlert(students, totalPending){
  if(state.role !== "instructor") return;
  const overlay = ensureInstructorAuthOverlay();
  const body = document.getElementById("instructorAuthBody");
  if(!overlay || !body) return;

  const pendingRows = (students || []).map(s=>{
    const types = [];
    if(s.clear_dtc_request_pending) types.push("BORRAR DTC");
    if(s.engine_control_request_pending) types.push("CONTROL MOTOR");
    if(s.engine_off_request_pending) types.push("APAGAR MOTOR");
    if(s.reset_session_request_pending) types.push("RESTABLECER");
    return {id:s.id, name:s.name || s.id, types};
  }).filter(r=>r.types.length);

  const signature = pendingRows.map(r=>`${r.id}:${r.types.join(",")}`).join("|");
  state.lastAuthPendingSignature = signature;

  const nextFlags = {};
  pendingRows.forEach(r=>{
    r.types.forEach(tp=>{
      nextFlags[`${r.id}:${tp}`] = true;
    });
  });
  let newRequests = 0;
  Object.keys(nextFlags).forEach(key=>{
    if(!state.authPendingFlags[key]) newRequests += 1;
  });
  state.authPendingFlags = nextFlags;

  if(totalPending > 0){
    const actionMap = {
      "BORRAR DTC": "approve_clear_dtcs",
      "CONTROL MOTOR": "approve_engine_control",
      "APAGAR MOTOR": "approve_engine_off",
      "RESTABLECER": "approve_reset_session",
    };
    const rowsHtml = pendingRows.map(r=>{
      const actions = r.types.map(tp=>{
        const cmd = actionMap[tp];
        return `<div class="row" style="margin-top:6px;gap:6px"><span class="badge warn">${tp}</span><button class="ok mini-btn" data-auth-cmd="${cmd}" data-auth-approved="true" data-auth-student="${r.id}">Autorizar</button><button class="warn mini-btn" data-auth-cmd="${cmd}" data-auth-approved="false" data-auth-student="${r.id}">Denegar</button></div>`;
      }).join("");
      return `<div class="freeze-card" style="margin-top:8px"><div><strong>${r.name}</strong> (${r.id})</div>${actions}</div>`;
    }).join("");
    body.innerHTML = `<div style="font-size:15px;color:#ffd7d7">Hay ${totalPending} solicitud(es) pendiente(s) de autorizacion.</div>${rowsHtml}<div class="footer-note" style="margin-top:8px">Tambien puedes autorizar desde el Centro de comando.</div>`;

    const dismissedSame = signature && signature === state.authAlertDismissedSignature;
    if(!dismissedSame){
      overlay.classList.add("visible");
      if(newRequests > 0){
        playInstructorAlertTone();
        setTimeout(()=>playInstructorAlertTone(), 280);
        setTimeout(()=>playInstructorAlertTone(), 560);
      }
      if(!state.authAlertAudioTimer){
        state.authAlertAudioTimer = setInterval(()=>playInstructorAlertTone(), 2200);
      }
    }
    body.querySelectorAll("[data-auth-cmd]").forEach(btn=>{
      btn.onclick = async ()=>{
        const cmd = btn.dataset.authCmd;
        const approved = String(btn.dataset.authApproved || "").toLowerCase() === "true";
        const sid = btn.dataset.authStudent || "";
        btn.disabled = true;
        await sendInstructorCommandForStudent(sid, {action: cmd, approved});
      };
    });
    return;
  }

  body.innerHTML = "";
  overlay.classList.remove("visible");
  state.authAlertDismissedSignature = "";
  state.lastAuthPendingSignature = "";
  state.authPendingFlags = {};
  stopInstructorAuthAudio();
}

function syncStudentAccelerator(data){
  if(state.role !== "student") return;
  const sliders = Array.from(document.querySelectorAll("[data-student-app-slider]"));
  const outs = Array.from(document.querySelectorAll("[data-student-app-value]"));
  if(!sliders.length) return;
  const currentApp = Number(data?.sensors?.app ?? sliders[0]?.value ?? 0);
  const normalized = Number.isFinite(currentApp) ? currentApp : 0;
  sliders.forEach(slider=>{
    if(document.activeElement !== slider){
      slider.value = normalized;
    }
  });
  outs.forEach(out=>{ out.textContent = fmt(normalized); });
}

function renderEvaluationSelectionMeta(totalQuestions){
  const meta = document.getElementById("evaluationSelectionMeta");
  if(!meta) return;
  const selectedCount = state.evaluationSelectedQuestionIds.size;
  const perQuestion = selectedCount > 0 ? (20 / selectedCount) : 0;
  meta.textContent = `Seleccionadas: ${selectedCount}/${totalQuestions}. Cada pregunta vale ${perQuestion.toFixed(2)}/20.`;
}

function renderEvaluationReviewByStudent(analytics){
  const root = document.getElementById("evaluationReviewByStudent");
  const toggleBtn = document.getElementById("evaluationResultsByStudentBtn");
  const select = document.getElementById("evaluationResultsStudentSelect");
  if(!root) return;
  const rows = Array.isArray(analytics?.latest_details_by_student) ? analytics.latest_details_by_student : [];
  if(toggleBtn){
    toggleBtn.textContent = state.evaluationReviewPanelOpen ? "Ocultar resumen por estudiante" : "Ver resumen por estudiante";
    toggleBtn.onclick = ()=>{
      state.evaluationReviewPanelOpen = !state.evaluationReviewPanelOpen;
      renderEvaluationReviewByStudent(analytics);
    };
  }
  if(!state.evaluationReviewPanelOpen){
    if(select) select.style.display = "none";
    root.innerHTML = `<div class="scan-placeholder">Pulsa "Ver resumen por estudiante" para revisar resultados individuales.</div>`;
    return;
  }
  if(!rows.length){
    if(select) select.style.display = "none";
    root.innerHTML = `<div class="scan-placeholder">Aun no hay respuestas de estudiantes.</div>`;
    return;
  }
  if(select){
    select.style.display = "inline-flex";
    select.innerHTML = rows.map(r=>`<option value="${r.student_id}">${r.student_name || r.student_id}</option>`).join("");
    if(!rows.some(r=>r.student_id === state.evaluationReviewStudentId)){
      state.evaluationReviewStudentId = rows[0].student_id;
    }
    select.value = state.evaluationReviewStudentId || rows[0].student_id;
    select.onchange = ()=>{
      state.evaluationReviewStudentId = select.value || "";
      renderEvaluationReviewByStudent(analytics);
    };
  }
  const selectedRow = rows.find(r=>r.student_id === state.evaluationReviewStudentId) || rows[0];
  root.innerHTML = [selectedRow].map(row=>{
    const details = Array.isArray(row.answer_details) ? row.answer_details.slice().sort((a,b)=>Number(a.question_number||0)-Number(b.question_number||0)) : [];
    const detailHtml = details.map((d, idx)=>{
      const qn = Number(d.question_number || (idx + 1));
      const selected = d.selected_option_text || "(sin respuesta)";
      const correct = d.correct_option_text || "-";
      return `<div style="padding:6px 8px;border:1px solid #21405c;border-radius:8px;margin-top:6px">
        <div><strong>Pregunta ${qn}</strong> <span class="badge ${d.is_correct ? "ok" : "bad"}">${d.is_correct ? "Correcta" : "Incorrecta"}</span></div>
        <div class="footer-note">Respondio: ${selected}</div>
        <div class="footer-note">Correcta: ${correct}</div>
      </div>`;
    }).join("");
    return `<div class="freeze-card">
      <div class="row" style="justify-content:space-between;align-items:center">
        <strong>${row.student_name || row.student_id}</strong>
        <span class="badge info">${fmt(Number(row.score_20 || 0))}/20</span>
      </div>
      <div class="footer-note">Respondidas: ${row.answered_count || 0}/${row.question_count || 0}  Correctas: ${row.correct_count || 0}  Incorrectas: ${row.incorrect_count || 0}</div>
      ${detailHtml}
    </div>`;
  }).join("");
}

function renderEvaluationInstructor(data){
  if(state.role !== "instructor") return;
  const bank = Array.isArray(data.evaluation_bank) ? data.evaluation_bank : [];
  const bankRoot = document.getElementById("evaluationQuestionBank");
  const selectAllBtn = document.getElementById("evaluationSelectAllBtn");
  const deselectAllBtn = document.getElementById("evaluationDeselectAllBtn");

  if(!state.evaluationSelectionInitialized){
    state.evaluationSelectedQuestionIds = new Set(bank.map(q=>q.id));
    state.evaluationSelectionInitialized = true;
  } else {
    const validIds = new Set(bank.map(q=>q.id));
    state.evaluationSelectedQuestionIds = new Set(
      Array.from(state.evaluationSelectedQuestionIds).filter(id=>validIds.has(id))
    );
  }
  if(selectAllBtn){
    selectAllBtn.onclick = ()=>{
      state.evaluationSelectedQuestionIds = new Set(bank.map(q=>q.id));
      state.evaluationSelectionInitialized = true;
      renderEvaluationInstructor(state.latest || data);
    };
  }
  if(deselectAllBtn){
    deselectAllBtn.onclick = ()=>{
      state.evaluationSelectedQuestionIds = new Set();
      state.evaluationSelectionInitialized = true;
      renderEvaluationInstructor(state.latest || data);
    };
  }

  if(bankRoot){
    bankRoot.innerHTML = bank.map(q=>{
      const checked = state.evaluationSelectedQuestionIds.has(q.id) ? "checked" : "";
      const optionCount = Array.isArray(q.options) ? q.options.length : 0;
      return `<label class="eval-option-label"><input class="eval-option-input" type="checkbox" data-eval-question="${q.id}" ${checked}><span>${q.text} <strong>(${q.points} pt base)</strong> <small> ${optionCount} opciones</small></span></label>`;
    }).join("");
    bankRoot.querySelectorAll("[data-eval-question]").forEach(el=>{
      el.onchange = ()=>{
        if(el.checked) state.evaluationSelectedQuestionIds.add(el.dataset.evalQuestion);
        else state.evaluationSelectedQuestionIds.delete(el.dataset.evalQuestion);
        renderEvaluationSelectionMeta(bank.length);
      };
    });
  }
  renderEvaluationSelectionMeta(bank.length);
  renderEvaluationReviewByStudent(data.evaluation_analytics || null);
  const typeSel = document.getElementById("evaluationChartType");
  if(typeSel && document.activeElement !== typeSel){
    typeSel.value = state.evaluationChartType;
  }
  drawEvaluationAnalytics(data.evaluation_analytics || null);
}

function renderSessionRegistry(data){
  if(state.role !== "instructor") return;
  const body = document.getElementById("sessionRegistryTable");
  if(!body) return;
  const rows = Array.isArray(data?.session_registry) ? data.session_registry : [];
  if(!rows.length){
    body.innerHTML = `<tr><td colspan="5"><span class="badge warn">Sin registros guardados aun.</span></td></tr>`;
    return;
  }
  body.innerHTML = rows.map(row=>{
    const date = row.ended_at_ts ? new Date(Number(row.ended_at_ts) * 1000).toLocaleString() : "-";
    const students = Array.isArray(row.students) ? row.students.map(s=>`${s.name} (${s.code})`).join(", ") : "-";
    const download = row.download_url ? `<a class="button-like" href="${row.download_url}" target="_blank" rel="noopener">Descargar CSV (Excel)</a>` : "-";
    return `<tr>
      <td>${row.session_name || "-"}</td>
      <td>${row.session_code || "-"}</td>
      <td>${date}</td>
      <td><small>${students || "-"}</small></td>
      <td>${download}</td>
    </tr>`;
  }).join("");
}

function drawEvaluationAnalytics(analytics){
  const canvas = document.getElementById("evaluationAnalyticsChart");
  const summary = document.getElementById("evaluationAnalyticsSummary");
  if(!canvas || !analytics) return;
  const ctx = canvas.getContext("2d");
  const width = Math.max(320, canvas.parentElement?.clientWidth || 600);
  const height = 300;
  if(canvas.width !== width) canvas.width = width;
  if(canvas.height !== height) canvas.height = height;
  ctx.clearRect(0,0,width,height);
  ctx.fillStyle = "#081a29";
  ctx.fillRect(0,0,width,height);
  ctx.font = "12px Arial";

  const type = state.evaluationChartType || "bar";
  if(type === "pie"){
    const dist = analytics.distribution || {excelente:0,bien:0,mejorar:0};
    const vals = [Number(dist.excelente||0), Number(dist.bien||0), Number(dist.mejorar||0)];
    const labels = ["Excelente", "Bien", "Mejorar"];
    const colors = ["#2ecc71", "#f1c40f", "#e74c3c"];
    const total = vals.reduce((a,b)=>a+b,0) || 1;
    let start = -Math.PI/2;
    const cx = width * 0.35;
    const cy = height * 0.52;
    const r = Math.min(95, height * 0.34);
    vals.forEach((v, i)=>{
      const angle = (v / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, start, start + angle);
      ctx.closePath();
      ctx.fillStyle = colors[i];
      ctx.fill();
      start += angle;
    });
    ctx.fillStyle = "#c7dced";
    labels.forEach((label, i)=>{
      ctx.fillStyle = colors[i];
      ctx.fillText(`${label}: ${vals[i]}`, width * 0.63, 70 + i * 24);
    });
  } else if(type === "line"){
    const trend = Array.isArray(analytics.trend) ? analytics.trend : [];
    const vals = trend.map(t=>Number(t.score_20 || 0));
    const margin = {left: 42, right: 16, top: 22, bottom: 30};
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    ctx.strokeStyle = "#20415f";
    for(let i=0;i<=4;i++){
      const y = margin.top + (i/4)*plotH;
      ctx.beginPath(); ctx.moveTo(margin.left,y); ctx.lineTo(width-margin.right,y); ctx.stroke();
      const score = (20 - (i/4)*20).toFixed(0);
      ctx.fillStyle = "#9fc3dd";
      ctx.fillText(score, 8, y+4);
    }
    if(vals.length >= 2){
      ctx.strokeStyle = "#4aa8ff";
      ctx.lineWidth = 2;
      ctx.beginPath();
      vals.forEach((v,i)=>{
        const x = margin.left + (i/(vals.length-1))*plotW;
        const y = margin.top + (1 - (Math.max(0,Math.min(20,v))/20))*plotH;
        if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
      });
      ctx.stroke();
    }
  } else {
    const rows = Array.isArray(analytics.by_student) ? analytics.by_student : [];
    const margin = {left: 42, right: 16, top: 22, bottom: 50};
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    ctx.strokeStyle = "#20415f";
    for(let i=0;i<=4;i++){
      const y = margin.top + (i/4)*plotH;
      ctx.beginPath(); ctx.moveTo(margin.left,y); ctx.lineTo(width-margin.right,y); ctx.stroke();
      const score = (20 - (i/4)*20).toFixed(0);
      ctx.fillStyle = "#9fc3dd";
      ctx.fillText(score, 8, y+4);
    }
    const n = Math.max(rows.length, 1);
    const barW = Math.max(18, Math.min(56, (plotW / n) * 0.58));
    rows.forEach((row, i)=>{
      const score = Math.max(0, Math.min(20, Number(row.score_20 || 0)));
      const x = margin.left + ((i + 0.5) / n) * plotW - barW / 2;
      const h = (score / 20) * plotH;
      const y = margin.top + plotH - h;
      ctx.fillStyle = "#4aa8ff";
      ctx.fillRect(x, y, barW, h);
      ctx.fillStyle = "#c7dced";
      ctx.fillText(score.toFixed(1), x, y - 4);
      const label = String(row.student_name || row.student_id || `S${i+1}`).slice(0, 10);
      ctx.fillText(label, x, height - 14);
    });
  }
  if(summary){
    summary.textContent = `Promedio general: ${fmt(Number(analytics.average_score_20 || 0))}/20  Evaluaciones recientes: ${analytics.latest_count || 0}`;
  }
}

function buildStudentEvaluationResultHtml(latest){
  const details = Array.isArray(latest?.answer_details) ? latest.answer_details.slice().sort((a,b)=>Number(a.question_number||0)-Number(b.question_number||0)) : [];
  const detailRows = details.map((d, idx)=>{
    const qn = Number(d.question_number || (idx + 1));
    const selected = d.selected_option_text || "(sin respuesta)";
    const correct = d.correct_option_text || "-";
    return `<div style="padding:6px 8px;border:1px solid #21405c;border-radius:8px;margin-top:6px">
      <div><strong>Pregunta ${qn}</strong> <span class="badge ${d.is_correct ? "ok" : "bad"}">${d.is_correct ? "Correcta" : "Incorrecta"}</span></div>
      <div class="footer-note">Tu respuesta: ${selected}</div>
      <div class="footer-note">Respuesta correcta: ${correct}</div>
    </div>`;
  }).join("");
  const finalCorrectList = details.map((d, idx)=>{
    const qn = Number(d.question_number || (idx + 1));
    return `<li>Pregunta ${qn}: ${d.correct_option_text || "-"}</li>`;
  }).join("");
  return `<div><strong>Resultado: ${fmt(Number(latest?.score_20 || 0))}/20</strong></div>
    <div class="footer-note">Correctas: ${latest?.correct_count || 0}  Incorrectas: ${latest?.incorrect_count || 0}  Sin responder: ${latest?.unanswered_count || 0}</div>
    <div style="margin-top:6px">${detailRows}</div>
    <div style="margin-top:8px"><strong>Opciones correctas (resumen final):</strong></div>
    <ul style="margin-top:6px">${finalCorrectList}</ul>`;
}

function renderStudentEvaluation(data){
  if(state.role !== "student") return;
  const box = document.getElementById("studentEvaluationBox");
  const result = document.getElementById("studentEvaluationResult");
  const backBtn = document.getElementById("evaluationBackHomeBtn");
  if(!box) return;
  const active = data?.evaluation?.active || null;
  const latest = data?.evaluation?.latest_submission || null;
  if(!active){
    box.innerHTML = `<div class="scan-placeholder">Aun no hay evaluacion activa.</div>`;
    state.studentEvaluationAnswers = {};
    state.studentEvaluationId = "";
  } else {
    const currentEvalId = String(active.evaluation_id || "");
    if(currentEvalId && currentEvalId !== state.studentEvaluationId){
      state.studentEvaluationAnswers = {};
      state.studentEvaluationId = currentEvalId;
    }
    const questions = Array.isArray(active.questions) ? active.questions : [];
    const liveAnswers = {};
    box.querySelectorAll("[data-student-eval-option]:checked").forEach(el=>{
      const qid = el.dataset.studentEvalQ;
      const oid = el.dataset.studentEvalOption;
      if(qid && oid) liveAnswers[qid] = oid;
    });
    if(Object.keys(liveAnswers).length){
      state.studentEvaluationAnswers = {...state.studentEvaluationAnswers, ...liveAnswers};
    }
    box.innerHTML = `<div><strong>${active.title || "Evaluacion"}</strong><div class="footer-note">Responde seleccionando una opcion por pregunta. Puntaje total sobre 20.</div></div><div style="margin-top:10px">${questions.map(q=>{
      const options = Array.isArray(q.options) ? q.options : [];
      const selected = state.studentEvaluationAnswers[q.id] || "";
      const points20 = Number(q.points_20 || 0);
      const questionNumber = Number(q.question_number || 0);
      return `<div class="eval-question-card"><div style="margin-bottom:8px"><strong>${questionNumber > 0 ? `Pregunta ${questionNumber}: ` : ""}${q.text}</strong> <span class="footer-note">(${points20.toFixed(2)}/20)</span></div>${options.map(opt=>`<label class="eval-option-label"><input class="eval-option-input" type="radio" name="eval_q_${q.id}" data-student-eval-q="${q.id}" data-student-eval-option="${opt.id}" ${selected === opt.id ? "checked" : ""}><span>${opt.text}</span></label>`).join("")}</div>`;
    }).join("")}</div>`;
    box.querySelectorAll("[data-student-eval-option]").forEach(el=>{
      el.onchange = ()=>{
        const qid = el.dataset.studentEvalQ;
        const oid = el.dataset.studentEvalOption;
        if(qid && oid && el.checked){
          state.studentEvaluationAnswers[qid] = oid;
        }
      };
    });
  }
  if(result){
    if(latest){
      result.innerHTML = buildStudentEvaluationResultHtml(latest);
      if(backBtn) backBtn.style.display = "inline-flex";
    } else {
      result.textContent = "";
      if(backBtn) backBtn.style.display = "none";
    }
  }
}

function updateStudentExamLayout(data){
  if(state.role !== "student") return;
  const layout = document.querySelector(".scanner-layout");
  if(!layout) return;
  const activeEval = !!(data?.evaluation?.active);
  const inEvaluationSection = state.activeScannerSection === "scanner-evaluation";
  layout.classList.toggle("exam-focus", activeEval && inEvaluationSection);
}

function dtcComposedCodeByIds(charIds){
  const vals = charIds.map(id => document.getElementById(id)?.value || "");
  return vals.join("").toUpperCase();
}

function updateDtcComposedPreview(previewId, charIds){
  const out = document.getElementById(previewId);
  if(out) out.textContent = dtcComposedCodeByIds(charIds);
}

async function lookupComposedDtc(detailBoxId, charIds){
  const code = dtcComposedCodeByIds(charIds);
  const box = document.getElementById(detailBoxId);
  if(!box) return;
  const detail = await fetch(`/api/dtc-detail?code=${encodeURIComponent(code)}`).then(r=>r.json());
  if(!detail?.ok){
    box.style.display = "block";
    box.innerHTML = `<span class="badge bad">Codigo invalido</span><div class="footer-note" style="margin-top:8px">${detail?.error || "No se pudo consultar."}</div>`;
    return;
  }
  const causes = Array.isArray(detail.causes) ? detail.causes : [];
  const structure = detail.structure || {};
  const foundInDb = !!detail.found_in_db;
  const googleQuery = encodeURIComponent(`codigo DTC ${detail.code} causas solucion`);
  const googleLink = `https://www.google.com/search?q=${googleQuery}`;
  const externalLookup = foundInDb
    ? ""
    : `<div style="margin-top:10px"><a class="button-like" target="_blank" rel="noopener" href="${googleLink}">Buscar este DTC en Google</a></div>`;
  box.style.display = "block";
  box.innerHTML = `
    <div class="row" style="justify-content:space-between;align-items:center">
      <strong style="font-size:20px">${detail.code}</strong>
      <span class="badge info">Sistema ${structure.system_letter || "-"}</span>
    </div>
    <div style="margin-top:6px">${detail.description || "-"}</div>
    ${foundInDb ? "" : `<div class="footer-note" style="margin-top:6px">Este codigo no esta en la base local. Puedes consultar fuentes externas.</div>`}
    <div class="footer-note" style="margin-top:10px">Estructura: ${structure.system_letter || "-"} | ${structure.generic_or_mfr || "-"} | ${structure.subsystem_digit || "-"} | ${structure.fault_digits || "-"}</div>
    <div style="margin-top:10px"><strong>Causas probables:</strong></div>
    <ul style="margin-top:6px">${causes.map(c=>`<li>${c}</li>`).join("")}</ul>
    ${externalLookup}
  `;
}

function bindDtcToolkit(config){
  const {charIds, previewId, detailBoxId, buttonId} = config;
  const btn = document.getElementById(buttonId);
  charIds.forEach(id=>{
    const sel = document.getElementById(id);
    if(!sel) return;
    sel.onchange = ()=>updateDtcComposedPreview(previewId, charIds);
    sel.addEventListener("wheel", (ev)=>{
      ev.preventDefault();
      const step = ev.deltaY > 0 ? 1 : -1;
      const current = sel.selectedIndex;
      const total = sel.options.length;
      let next = current + step;
      if(next < 0) next = total - 1;
      if(next >= total) next = 0;
      sel.selectedIndex = next;
      updateDtcComposedPreview(previewId, charIds);
    }, {passive:false});
  });
  if(btn) btn.onclick = ()=>lookupComposedDtc(detailBoxId, charIds);
  updateDtcComposedPreview(previewId, charIds);
}

function renderSignalButtons(){
  const root = document.getElementById("signalButtons"); if(!root) return;
  const mode = state.latest?.mode === "diesel" ? "diesel" : "gasoline";
  const allowed = new Set(MODE_VISIBLE_SIGNALS[mode] || []);
  state.selectedSignals = new Set(Array.from(state.selectedSignals).filter(k=>allowed.has(k)));
  if(state.selectedSignals.size === 0) state.selectedSignals.add("rpm");
  root.innerHTML = Object.entries(signalMap)
    .filter(([key])=>allowed.has(key))
    .map(([key, meta])=>`<div class="signal-toggle ${state.selectedSignals.has(key) ? 'active':''}" data-sig="${key}"><strong>${meta.label}</strong><br><small>${meta.unit}</small></div>`)
    .join("");
  root.querySelectorAll(".signal-toggle").forEach(el=>{ el.onclick = ()=>{ const key = el.dataset.sig; if(state.selectedSignals.has(key)) state.selectedSignals.delete(key); else state.selectedSignals.add(key); if(state.selectedSignals.size===0) state.selectedSignals.add("rpm"); state.splitChartSignature = ""; renderSignalButtons(); scheduleDraw(); }; });
}

function updateSensorControlMode(data){
  if(state.role !== "instructor") return;
  const autoEnabled = !!data?.auto_adjust?.enabled;
  document.querySelectorAll("[data-sensor]").forEach(slider=>{
    if(slider.dataset.sensor !== "app"){
      slider.disabled = autoEnabled;
    }
  });
  const autoRange = document.getElementById("autoVariationRange");
  if(autoRange) autoRange.disabled = !autoEnabled;
}

function syncInstructorControls(data){
  if(state.role !== "instructor") return;
  const map = {
    modeSelect:data.mode,
    stageSelect:data.stage,
    presetSelect:data.current_preset || "normal",
    whatsNumber:data.connection?.whatsapp_number || "",
    graphModeSelect: state.graphMode,
    autoAdjustToggle: !!data.auto_adjust?.enabled,
  };
  Object.entries(map).forEach(([id,val])=>{
    const el = document.getElementById(id);
    if(!el || document.activeElement === el) return;
    if(el.type === "checkbox") el.checked = !!val;
    else el.value = val;
  });
  const sessionNameEl = document.getElementById("sessionName");
  const sessionCodeEl = document.getElementById("sessionCode");
  if(!state.sessionFieldsEditing && !state.sessionFieldsDirty){
    if(sessionNameEl && document.activeElement !== sessionNameEl) sessionNameEl.value = data.session_name || "";
    if(sessionCodeEl && document.activeElement !== sessionCodeEl) sessionCodeEl.value = data.session_code || "";
  }
  const autoVariation = document.getElementById("autoVariationRange");
  if(autoVariation && document.activeElement !== autoVariation){
    autoVariation.value = Number(data.auto_adjust?.variation_pct ?? 0);
  }
  const autoVariationOut = document.getElementById("autoVariation_val");
  if(autoVariationOut) autoVariationOut.textContent = fmt(Number(data.auto_adjust?.variation_pct ?? 0));
  document.querySelectorAll("[data-sensor]").forEach(slider=>{ const key = slider.dataset.sensor; const value = data.manual_overrides?.[key] ?? data.sensors?.[key] ?? slider.value; if(document.activeElement !== slider) slider.value = value; const out = document.getElementById(`${key}_val`); if(out) out.textContent = fmt(Number(value)); });
  const ipEl = document.getElementById("serverIp"); if(ipEl) ipEl.value = data.connection?.ip || "";
  const linkEl = document.getElementById("studentLink"); if(linkEl) linkEl.value = data.connection?.student_link || "";
  const waBtn = document.getElementById("waShareBtn"); if(waBtn) waBtn.href = data.connection?.whatsapp_link || "#";
  updateSensorControlMode(data);
}

function applyModeSpecificVisibility(data){
  const mode = data?.mode === "diesel" ? "diesel" : "gasoline";

  const presetSelect = document.getElementById("presetSelect");
  if(presetSelect){
    const allowedPresets = new Set(MODE_VISIBLE_PRESETS[mode] || []);
    Array.from(presetSelect.options).forEach(opt=>{
      const visible = allowedPresets.has(opt.value);
      opt.hidden = !visible;
      opt.disabled = !visible;
    });
    if(!allowedPresets.has(presetSelect.value)){
      const first = Array.from(presetSelect.options).find(opt=>!opt.disabled);
      if(first) presetSelect.value = first.value;
    }
  }

  const allowedSensors = new Set(MODE_VISIBLE_SENSOR_SLIDERS[mode] || []);
  document.querySelectorAll("[data-sensor]").forEach(el=>{
    const key = el.dataset.sensor;
    const holder = el.closest(".field");
    if(holder){
      holder.style.display = allowedSensors.has(key) ? "" : "none";
    }
  });

  const allowedActuators = new Set(MODE_VISIBLE_ACTUATORS[mode] || []);
  document.querySelectorAll("[data-actuator]").forEach(el=>{
    const key = el.dataset.actuator;
    const holder = el.closest(".actuator-item");
    if(holder){
      holder.style.display = allowedActuators.has(key) ? "" : "none";
    }
  });

  const mode05MenuBtn = document.querySelector('.scanner-menu-btn[data-scanner-section="scanner-mode05"]');
  const mode05Section = document.getElementById("scanner-mode05");
  const showMode05 = mode === "gasoline";
  if(mode05MenuBtn) mode05MenuBtn.style.display = showMode05 ? "" : "none";
  if(mode05Section) mode05Section.style.display = showMode05 ? "" : "none";
  if(mode === "diesel"){
    ["o2_b1s1","o2_b1s2","stft","ltft"].forEach(k=>{
      state.selectedSignals.delete(k);
      delete state.history[k];
    });
  }
  if(state.role === "student" && !showMode05 && state.activeScannerSection === "scanner-mode05"){
    setScannerSection("scanner-home");
  }
}

function renderAll(data){
  state.latest = data;
  renderKPIs(data);
  renderStateSummary(data);
  renderSensorsTable(data);
  renderDtcTable(data);
  renderStudentConnection(data);
  renderStudentEngineControl(data);
  renderCommandCenter(data);
  renderDtcClearRequests(data);
  renderEvaluationInstructor(data);
  renderSessionRegistry(data);
  renderStudentEvaluation(data);
  applyModeSpecificVisibility(data);
  renderFaults(data);
  renderActuatorsPanel(data);
  renderEngineShutdownAlert(data);
  syncStudentAccelerator(data);
  renderMode06(data.monitor_results || []);
  syncInstructorControls(data);
  updateStudentExamLayout(data);
  renderSignalButtons();
  updateHistory(data);
  scheduleDraw();
}

async function connectWS(){
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const params = new URLSearchParams({role: state.role});
  if(state.role === "student"){
    params.set("student_id", ensureStudentId());
  } else if(state.selectedStudentId){
    params.set("selected_student_id", state.selectedStudentId);
  }
  state.ws = new WebSocket(`${proto}://${location.host}/ws?${params.toString()}`);
  state.ws.onmessage = (event)=>{ const msg = JSON.parse(event.data); if(msg.type === "state") renderAll(msg.payload); };
}

async function reconnectWS(){
  try{
    if(state.ws){
      state.ws.close();
    }
  }catch(_e){}
  await connectWS();
}


function setScannerSection(sectionId){
  const sections = document.querySelectorAll(".scanner-section");
  const buttons = document.querySelectorAll(".scanner-menu-btn");
  if(!sections.length) return;
  sections.forEach(section=>section.classList.toggle("active", section.id === sectionId));
  buttons.forEach(btn=>btn.classList.toggle("active", btn.dataset.scannerSection === sectionId));
  state.activeScannerSection = sectionId;
  if(sectionId === "scanner-read-dtc" && !state.studentDtcScanned){
    toggleStudentDtcHint(true);
  }
  if(sectionId === "scanner-graphs") scheduleDraw();
  updateStudentExamLayout(state.latest);
}

function initTabs(){
  document.querySelectorAll(".tabbar").forEach(tabbar=>{
    tabbar.querySelectorAll(".tabbtn").forEach(btn=>{
      btn.onclick = ()=>{
        const target = btn.dataset.tabTarget;
        tabbar.querySelectorAll(".tabbtn").forEach(b=>b.classList.remove("active"));
        btn.classList.add("active");
        document.querySelectorAll(".tabpanel").forEach(panel=>panel.classList.toggle("active", panel.id === target));
        scheduleDraw();
      };
    });
  });
}

async function bindInstructorControls(){
  const modeSel = document.getElementById("modeSelect");
  const stageSel = document.getElementById("stageSelect");
  const presetSel = document.getElementById("presetSelect");
  const sessionNameInput = document.getElementById("sessionName");
  const sessionCodeInput = document.getElementById("sessionCode");
  const engineBtn = document.getElementById("toggleEngineBtn");
  const resetBtn = document.getElementById("resetBtn");
  const clearBtn = document.getElementById("clearBtn");
  const saveSessionBtn = document.getElementById("saveSessionBtn");
  const exportSessionBtn = document.getElementById("exportSessionBtn");
  const lastSessionExportLink = document.getElementById("lastSessionExportLink");
  const sessionSaveStatus = document.getElementById("sessionSaveStatus");
  const saveWhatsBtn = document.getElementById("saveWhatsBtn");
  const copyLinkBtn = document.getElementById("copyLinkBtn");
  const graphModeSel = document.getElementById("graphModeSelect");
  const autoAdjustToggle = document.getElementById("autoAdjustToggle");
  const autoVariationRange = document.getElementById("autoVariationRange");
  const targetStudentSelect = document.getElementById("targetStudentSelect");
  const targetAllToggle = document.getElementById("targetAllToggle");
  const sendEvaluationBtn = document.getElementById("sendEvaluationBtn");
  const evaluationTitleInput = document.getElementById("evaluationTitleInput");
  const evaluationChartType = document.getElementById("evaluationChartType");
  const evaluationPublishStatus = document.getElementById("evaluationPublishStatus");
  const sessionRegistryRefreshBtn = document.getElementById("sessionRegistryRefreshBtn");
  const approveSelectedClearBtn = document.getElementById("approveSelectedClearBtn");
  const approveSelectedEngineControlBtn = document.getElementById("approveSelectedEngineControlBtn");
  const approveSelectedEngineOffBtn = document.getElementById("approveSelectedEngineOffBtn");
  const approveSelectedResetBtn = document.getElementById("approveSelectedResetBtn");
  const denySelectedClearBtn = document.getElementById("denySelectedClearBtn");

  const markSessionEditing = ()=>{ state.sessionFieldsEditing = true; };
  const clearSessionEditing = ()=>{ state.sessionFieldsEditing = false; };
  const markSessionDirty = ()=>{ state.sessionFieldsDirty = true; };
  if(sessionNameInput){
    sessionNameInput.onfocus = markSessionEditing;
    sessionNameInput.oninput = ()=>{ markSessionEditing(); markSessionDirty(); };
    sessionNameInput.onblur = clearSessionEditing;
  }
  if(sessionCodeInput){
    sessionCodeInput.onfocus = markSessionEditing;
    sessionCodeInput.oninput = ()=>{ markSessionEditing(); markSessionDirty(); };
    sessionCodeInput.onblur = clearSessionEditing;
  }

  if(modeSel) modeSel.onchange = ()=>sendInstructorCommand({action:"set_mode", mode: modeSel.value});
  if(stageSel) stageSel.onchange = ()=>sendInstructorCommand({action:"set_stage", stage: stageSel.value});
  if(presetSel) presetSel.onchange = ()=>sendInstructorCommand({action:"apply_preset", preset: presetSel.value});
  if(engineBtn) engineBtn.onclick = ()=>sendInstructorCommand({action:"toggle_engine", engine_on: !(state.latest?.engine_on)});
  if(resetBtn) resetBtn.onclick = ()=>sendInstructorCommand({action:"reset_all"});
  if(clearBtn) clearBtn.onclick = ()=>sendInstructorCommand({action:"clear_dtcs"});
  if(saveSessionBtn) saveSessionBtn.onclick = async ()=>{
    const session_name = (document.getElementById("sessionName")?.value || "").trim();
    const session_code = (document.getElementById("sessionCode")?.value || "").trim();
    const res = await postJSON("/api/instructor/command", {
      action: "set_session_global",
      session_name,
      session_code,
    });
    if(sessionSaveStatus){
      if(res?.ok){
        const file = res?.archived?.export_csv_file || "";
        const count = Number(res?.archived?.student_count || 0);
        sessionSaveStatus.textContent = `Sesion guardada. Registro exportado (CSV/Excel) con datos por estudiante: ${count} estudiante(s). Archivo: ${file}`;
        if(lastSessionExportLink && res?.archived?.download_url){
          lastSessionExportLink.href = res.archived.download_url;
          lastSessionExportLink.style.display = "inline-flex";
          lastSessionExportLink.textContent = "Descargar ultimo export";
        }
      } else {
        sessionSaveStatus.textContent = res?.error || "No se pudo guardar/cambiar la sesion.";
      }
    }
    state.sessionFieldsEditing = false;
    if(res?.ok){
      state.sessionFieldsDirty = false;
    }
  };
  if(exportSessionBtn) exportSessionBtn.onclick = async ()=>{
    const res = await postJSON("/api/instructor/command", {action: "export_session_current"});
    if(sessionSaveStatus){
      if(res?.ok){
        const file = res?.archived?.export_csv_file || "";
        const count = Number(res?.archived?.student_count || 0);
        sessionSaveStatus.textContent = `Exportacion completada: ${count} estudiante(s). Archivo: ${file}`;
      } else {
        sessionSaveStatus.textContent = res?.error || "No se pudo exportar la sesion actual.";
      }
    }
    if(lastSessionExportLink && res?.archived?.download_url){
      lastSessionExportLink.href = res.archived.download_url;
      lastSessionExportLink.style.display = "inline-flex";
      lastSessionExportLink.textContent = "Descargar ultimo export";
      try{ lastSessionExportLink.click(); }catch(_e){}
    }
  };
  if(saveWhatsBtn) saveWhatsBtn.onclick = async ()=>{ const number = document.getElementById("whatsNumber").value.trim(); localStorage.setItem("scanmatic_whatsapp_number", number); await sendInstructorCommand({action:"set_whatsapp_number", number}); };
  if(copyLinkBtn) copyLinkBtn.onclick = async ()=>{ const link = document.getElementById("studentLink").value; try{ await navigator.clipboard.writeText(link); copyLinkBtn.textContent = "Copiado"; setTimeout(()=>copyLinkBtn.textContent = "Copiar link", 1500);}catch(_e){} };
  if(graphModeSel) graphModeSel.onchange = ()=>{ state.graphMode = graphModeSel.value; renderGraphMode(); scheduleDraw(); };
  if(autoAdjustToggle) autoAdjustToggle.onchange = ()=>sendInstructorCommand({
    action:"set_auto_adjust",
    enabled: autoAdjustToggle.checked,
    variation_pct: Number(autoVariationRange?.value || 0),
  });
  if(autoVariationRange){
    autoVariationRange.oninput = ()=>{ const out = document.getElementById("autoVariation_val"); if(out) out.textContent = autoVariationRange.value; };
    autoVariationRange.onchange = ()=>sendInstructorCommand({
      action:"set_auto_adjust",
      enabled: !!autoAdjustToggle?.checked,
      variation_pct: Number(autoVariationRange.value),
    });
  }
  if(targetAllToggle){
    targetAllToggle.onchange = ()=>{
      state.targetAllStudents = !!targetAllToggle.checked;
      renderCommandCenter(state.latest || {});
    };
  }
  if(targetStudentSelect){
    targetStudentSelect.onchange = async ()=>{
      state.selectedStudentId = targetStudentSelect.value || "";
      if(state.role === "instructor"){
        await reconnectWS();
      }
    };
  }
  if(evaluationChartType){
    evaluationChartType.onchange = ()=>{
      state.evaluationChartType = evaluationChartType.value;
      drawEvaluationAnalytics(state.latest?.evaluation_analytics || null);
    };
  }
  if(sendEvaluationBtn){
    sendEvaluationBtn.onclick = async ()=>{
      const ids = Array.from(state.evaluationSelectedQuestionIds);
      if(ids.length === 0){
        if(evaluationPublishStatus){
          evaluationPublishStatus.textContent = "Selecciona al menos una pregunta para enviar la evaluacion.";
        }
        return;
      }
      const payload = {
        action: "publish_evaluation",
        title: (evaluationTitleInput?.value || "Evaluacion diagnostica").trim(),
        question_ids: ids,
      };
      const res = await sendInstructorCommand(payload);
      if(evaluationPublishStatus){
        if(res?.ok){
          evaluationPublishStatus.textContent = `Evaluacion ${res.evaluation_id} enviada a ${res.targets?.length || 0} estudiante(s). Preguntas: ${res.question_count || ids.length}. Valor por pregunta: ${Number(res.points_per_question_20 || 0).toFixed(2)}/20.`;
        } else {
          evaluationPublishStatus.textContent = "No se pudo publicar la evaluacion.";
        }
      }
    };
  }
  if(approveSelectedClearBtn){
    approveSelectedClearBtn.onclick = async ()=>{
      await sendInstructorCommand({action:"approve_clear_dtcs", approved:true});
    };
  }
  if(approveSelectedEngineOffBtn){
    approveSelectedEngineOffBtn.onclick = async ()=>{
      await sendInstructorCommand({action:"approve_engine_off", approved:true});
    };
  }
  if(sessionRegistryRefreshBtn){
    sessionRegistryRefreshBtn.onclick = async ()=>{
      const url = `/api/state?role=instructor&selected_student_id=${encodeURIComponent(state.selectedStudentId || "")}`;
      const current = await fetch(url).then(r=>r.json());
      renderAll(current);
    };
  }
  if(approveSelectedEngineControlBtn){
    approveSelectedEngineControlBtn.onclick = async ()=>{
      await sendInstructorCommand({action:"approve_engine_control", approved:true});
    };
  }
  if(approveSelectedResetBtn){
    approveSelectedResetBtn.onclick = async ()=>{
      await sendInstructorCommand({action:"approve_reset_session", approved:true});
    };
  }
  if(denySelectedClearBtn){
    denySelectedClearBtn.onclick = async ()=>{
      await sendInstructorCommand({action:"approve_clear_dtcs", approved:false});
      await sendInstructorCommand({action:"approve_engine_control", approved:false});
      await sendInstructorCommand({action:"approve_engine_off", approved:false});
      await sendInstructorCommand({action:"approve_reset_session", approved:false});
    };
  }
  bindDtcToolkit({
    charIds: ["evalDtcChar0","evalDtcChar1","evalDtcChar2","evalDtcChar3","evalDtcChar4"],
    previewId: "evalDtcComposedValue",
    detailBoxId: "evalDtcDetailBox",
    buttonId: "evalDtcBuildLookupBtn",
  });

  document.querySelectorAll("[data-actuator]").forEach(control=>{
    if(control.tagName === 'SELECT') control.onchange = ()=>sendInstructorCommand({action:'set_actuator', key: control.dataset.actuator, value: control.value});
    else control.onchange = ()=>sendInstructorCommand({action:'set_actuator', key: control.dataset.actuator, value: control.checked});
  });

  document.querySelectorAll("[data-sensor]").forEach(slider=>{
    slider.oninput = ()=>{ const output = document.getElementById(`${slider.dataset.sensor}_val`); if(output) output.textContent = slider.value; };
    slider.onchange = ()=>{
      if(autoAdjustToggle?.checked && slider.dataset.sensor !== "app") return;
      sendInstructorCommand({action:"set_sensor", key: slider.dataset.sensor, value: Number(slider.value)});
    };
  });

  if(!localStorage.getItem("scanmatic_whatsapp_prompt_done")){
    const saved = localStorage.getItem("scanmatic_whatsapp_number") || "";
    const number = window.prompt("Numero de WhatsApp del instructor para preparar el enlace de comparticion:", saved);
    if(number !== null){
      localStorage.setItem("scanmatic_whatsapp_number", number.trim());
      await sendInstructorCommand({action:"set_whatsapp_number", number: number.trim()});
    }
    localStorage.setItem("scanmatic_whatsapp_prompt_done", "1");
  }else{
    const saved = localStorage.getItem("scanmatic_whatsapp_number");
    if(saved) await sendInstructorCommand({action:"set_whatsapp_number", number: saved});
  }
}

async function bindStudentControls(){
  document.querySelectorAll(".scanner-menu-btn").forEach(btn=>{ btn.onclick = ()=>setScannerSection(btn.dataset.scannerSection); });
  document.querySelectorAll("[data-go-section]").forEach(btn=>{ btn.onclick = ()=>setScannerSection(btn.dataset.goSection); });
  const readBtn = document.getElementById("readDtcsBtn");
  const clearBtn = document.getElementById("studentClearBtn");
  const requestEngineOffBtn = document.getElementById("studentRequestEngineOffBtn");
  const requestEngineControlBtn = document.getElementById("studentRequestEngineControlBtn");
  const studentToggleEngineBtn = document.getElementById("studentToggleEngineBtn");
  const studentStageSelect = document.getElementById("studentStageSelect");
  const studentApplyStageBtn = document.getElementById("studentApplyStageBtn");
  const requestResetBtn = document.getElementById("studentRequestResetBtn");
  const ffBtn = document.getElementById("freezeBtn");
  const dtcSearchBtn = document.getElementById("searchDtcBtn");
  const graphModeSel = document.getElementById("graphModeSelect");
  const mode05Btn = document.getElementById("mode05Btn");
  const mode06Btn = document.getElementById("mode06Btn");
  const mode07Btn = document.getElementById("mode07Btn");
  const mode08Btn = document.getElementById("mode08Btn");
  const mode09Btn = document.getElementById("mode09Btn");
  const submitEvaluationBtn = document.getElementById("submitEvaluationBtn");
  const evaluationBackHomeBtn = document.getElementById("evaluationBackHomeBtn");
  const studentNameInput = document.getElementById("studentNameInput");
  const saveStudentNameBtn = document.getElementById("saveStudentNameBtn");
  const studentNameStatus = document.getElementById("studentNameStatus");
  const studentAppSliders = Array.from(document.querySelectorAll("[data-student-app-slider]"));
  const studentAppStatusEls = Array.from(document.querySelectorAll("[data-student-app-status]"));
  const studentAppValueEls = Array.from(document.querySelectorAll("[data-student-app-value]"));

  const pushStudentName = async ()=>{
    const name = (studentNameInput?.value || "").trim();
    state.studentName = name;
    localStorage.setItem("scanmatic_student_name", name);
    const res = await sendStudentAction({action:"set_student_name", name});
    if(studentNameStatus){
      studentNameStatus.textContent = `Nombre guardado: ${res.student_name || name || "Sin nombre"}`;
    }
  };

  if(studentNameInput){
    studentNameInput.value = ensureStudentName();
    studentNameInput.addEventListener("keydown", async (ev)=>{
      if(ev.key === "Enter"){
        ev.preventDefault();
        await pushStudentName();
      }
    });
  }
  if(saveStudentNameBtn){
    saveStudentNameBtn.onclick = ()=>pushStudentName();
  }
  if(ensureStudentName()){
    await sendStudentAction({action:"set_student_name", name: ensureStudentName()});
  }

  if(readBtn) readBtn.onclick = async ()=>{ const data = await sendStudentAction({action:"read_dtcs"}); state.studentDtcScanned = true; renderDtcTable({dtcs:data.dtcs || []}); setScannerSection("scanner-read-dtc"); };
  if(clearBtn) clearBtn.onclick = async ()=>{
    const data = await sendStudentAction({action:"clear_dtcs"});
    state.studentDtcScanned = true;
    renderDtcTable({dtcs:data.dtcs || []});
    const result = document.getElementById("clearResultBox");
    if(result){
      result.style.display = "block";
      if(data?.ok){
        result.textContent = data.dtcs?.length ? "Se borraron algunos codigos, pero aun quedan DTC activos por fallas presentes." : "No quedan DTC activos despues del borrado.";
      } else if(data?.requires_approval){
        result.textContent = data?.message || "El instructor debe aprobar el borrado de DTC para tu sesion.";
      } else {
        result.textContent = data?.error || "No se pudo borrar DTC.";
      }
    }
  };
  if(requestEngineOffBtn) requestEngineOffBtn.onclick = async ()=>{
    const data = await sendStudentAction({action:"request_engine_off"});
    const result = document.getElementById("clearResultBox");
    if(result){
      result.style.display = "block";
      result.textContent = data?.message || "Solicitud enviada: esperando autorizacion del instructor para apagar motor.";
    }
  };
  if(requestEngineControlBtn) requestEngineControlBtn.onclick = async ()=>{
    const data = await sendStudentAction({action:"request_engine_control"});
    const result = document.getElementById("clearResultBox");
    if(result){
      result.style.display = "block";
      result.textContent = data?.message || data?.error || "Solicitud enviada: esperando autorizacion para controlar motor.";
    }
  };
  if(studentToggleEngineBtn) studentToggleEngineBtn.onclick = async ()=>{
    const current = !!state.latest?.engine_on;
    const data = await sendStudentAction({action:"toggle_engine_student", engine_on: !current});
    const result = document.getElementById("clearResultBox");
    if(result){
      result.style.display = "block";
      result.textContent = data?.ok ? `Motor ${data.engine_on ? "encendido" : "apagado"} en tu sesion.` : (data?.error || "No autorizado.");
    }
  };
  if(studentApplyStageBtn) studentApplyStageBtn.onclick = async ()=>{
    const stage = studentStageSelect?.value || "idle";
    const data = await sendStudentAction({action:"set_stage_student", stage});
    const result = document.getElementById("clearResultBox");
    if(result){
      result.style.display = "block";
      result.textContent = data?.ok ? `Etapa aplicada: ${data.stage}.` : (data?.error || "No autorizado.");
    }
  };
  if(requestResetBtn) requestResetBtn.onclick = async ()=>{
    const data = await sendStudentAction({action:"request_reset_session"});
    const result = document.getElementById("clearResultBox");
    if(result){
      result.style.display = "block";
      result.textContent = data?.message || "Solicitud enviada: esperando autorizacion del instructor para restablecer sesion.";
    }
  };
  if(ffBtn) ffBtn.onclick = async ()=>{ const data = await sendStudentAction({action:"freeze_frame"}); renderFreezeFramesResponse(data); setScannerSection("scanner-freeze"); };
  if(dtcSearchBtn) dtcSearchBtn.onclick = async ()=>{ const q = document.getElementById("dtcSearchInput").value; const rows = await fetch(`/api/dtc-search?q=${encodeURIComponent(q)}&limit=25`).then(r=>r.json()); const table = document.getElementById("dtcLookupTable"); table.innerHTML = rows.map(r=>`<tr><td><strong>${r.code}</strong></td><td>${r.description}</td></tr>`).join(""); };
  bindDtcToolkit({
    charIds: ["dtcChar0","dtcChar1","dtcChar2","dtcChar3","dtcChar4"],
    previewId: "dtcComposedValue",
    detailBoxId: "dtcDetailBox",
    buttonId: "dtcBuildLookupBtn",
  });
  bindDtcToolkit({
    charIds: ["evalStuDtcChar0","evalStuDtcChar1","evalStuDtcChar2","evalStuDtcChar3","evalStuDtcChar4"],
    previewId: "evalStuDtcComposedValue",
    detailBoxId: "evalStuDtcDetailBox",
    buttonId: "evalStuDtcBuildLookupBtn",
  });
  if(mode05Btn) mode05Btn.onclick = async ()=>{ const data = await sendStudentAction({action:'mode05'}); renderMode05(data.o2_tests); setScannerSection('scanner-mode05'); };
  if(mode06Btn) mode06Btn.onclick = async ()=>{ const data = await sendStudentAction({action:'mode06'}); renderMode06(data.monitor_results||[]); setScannerSection('scanner-mode06'); };
  if(mode07Btn) mode07Btn.onclick = async ()=>{ const data = await sendStudentAction({action:'mode07'}); renderMode07(data.dtcs||[]); setScannerSection('scanner-mode07'); };
  if(mode08Btn) mode08Btn.onclick = async ()=>{ const data = await sendStudentAction({action:'mode08'}); renderMode08(data.mode08); setScannerSection('scanner-mode08'); };
  if(mode09Btn) mode09Btn.onclick = async ()=>{ const data = await sendStudentAction({action:'mode09'}); renderMode09(data.ecu_info); setScannerSection('scanner-mode09'); };
  if(submitEvaluationBtn){
    submitEvaluationBtn.onclick = async ()=>{
      const answers = {...state.studentEvaluationAnswers};
      const res = await sendStudentAction({action:"submit_evaluation", answers});
      const out = document.getElementById("studentEvaluationResult");
      if(out){
        if(res?.ok && res?.result){
          out.innerHTML = buildStudentEvaluationResultHtml(res.result);
        } else {
          out.textContent = res?.error || "No se pudo enviar la evaluacion.";
        }
      }
    };
  }
  if(evaluationBackHomeBtn){
    evaluationBackHomeBtn.onclick = ()=>setScannerSection("scanner-home");
  }
  if(graphModeSel) graphModeSel.onchange = ()=>{ state.graphMode = graphModeSel.value; renderGraphMode(); scheduleDraw(); };
  if(studentAppSliders.length){
    const updateStatus = (text)=>studentAppStatusEls.forEach(el=>{ el.textContent = text; });
    const syncSliderValues = (value, activeSlider = null)=>{
      const n = Number(value || 0);
      studentAppSliders.forEach(sl=>{ if(sl !== activeSlider) sl.value = n; });
      studentAppValueEls.forEach(el=>{ el.textContent = fmt(n); });
    };
    const sendApp = async (value)=>{
      const val = Number(value || 0);
      const res = await sendStudentAction({action:"set_app", value: val});
      const text = res?.ok
        ? `APP aplicado: ${fmt(Number(res.app || val))}% (solo en tu sesion).`
        : (res?.error || "No se pudo actualizar APP.");
      updateStatus(text);
      if(res?.ok) syncSliderValues(Number(res.app || val));
    };
    studentAppSliders.forEach(slider=>{
      slider.oninput = ()=>{
        const val = Number(slider.value || 0);
        syncSliderValues(val, slider);
        if(state.studentAppSendTimer) clearTimeout(state.studentAppSendTimer);
        state.studentAppSendTimer = setTimeout(()=>sendApp(val), 130);
      };
      slider.onchange = ()=>sendApp(Number(slider.value || 0));
    });
  }
}

window.addEventListener("resize", ()=>scheduleDraw());

window.addEventListener("DOMContentLoaded", async ()=>{
  initTabs();
  renderSignalButtons();
  renderGraphMode();
  ensureChartModal();
  bindGraphActionButtons();
  document.querySelectorAll('[data-expand-chart][data-chart-target="mixed"]').forEach(btn=>{ btn.onclick = ()=>toggleChartExpand("mixed", "mixed"); });
  setScannerSection(state.activeScannerSection);
  const initialStateUrl = state.role === "instructor"
    ? `/api/state?role=instructor&selected_student_id=${encodeURIComponent(state.selectedStudentId || "")}`
    : `/api/state?role=student&student_id=${encodeURIComponent(ensureStudentId())}`;
  const current = await fetch(initialStateUrl).then(r=>r.json());
  renderAll(current); renderMode05(null); renderMode07([]); renderMode09(null);
  await connectWS();
  await bindInstructorControls();
  await bindStudentControls();
});



