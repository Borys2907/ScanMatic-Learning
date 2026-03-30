const state = {
  latest: null,
  role: document.body.dataset.role || "student",
  ws: null,
  selectedSignals: new Set(["rpm"]),
  history: {},
  maxPoints: 160,
  secondsPerSample: 0.25,
  graphMode: "mixed",
  studentDtcScanned: false,
  pendingDraw: false,
  activeScannerSection: "scanner-home",
  expandedChartKey: null,
  modalChartToken: null,
  splitChartSignature: "",
};

const signalMap = {
  rpm: {label:"RPM", unit:"rpm"},
  vehicle_speed: {label:"Velocidad", unit:"km/h"},
  app: {label:"APP", unit:"%"},
  throttle: {label:"Throttle", unit:"%"},
  map: {label:"MAP", unit:"kPa"},
  maf: {label:"MAF", unit:"g/s"},
  iat: {label:"IAT", unit:"°C"},
  ect: {label:"ECT", unit:"°C"},
  o2_b1s1: {label:"O2 B1S1", unit:"V"},
  o2_b1s2: {label:"O2 B1S2", unit:"V"},
  stft: {label:"STFT", unit:"%"},
  ltft: {label:"LTFT", unit:"%"},
  battery_voltage: {label:"Batería", unit:"V"},
  fuel_pressure: {label:"Fuel Press", unit:"kPa"},
  rail_pressure: {label:"Rail", unit:"bar"},
  boost: {label:"Boost", unit:"kPa"},
  egr_position: {label:"EGR", unit:"%"},
  inj_cmd_ms: {label:"Iny. comandada", unit:"ms"},
  inj_dead_time_ms: {label:"Dead time", unit:"ms"},
  inj_ms: {label:"Iny. física", unit:"ms"},
  spark_advance: {label:"Avance", unit:"°"},
  calculated_load: {label:"Carga calc.", unit:"%"},
  ckp_sync: {label:"CKP sync", unit:""},
  cmp_sync: {label:"CMP sync", unit:""},
  cooling_fan: {label:"Electroventilador", unit:"0/1"},
};

const chartColors = ["#4aa8ff", "#57f2a8", "#ffbe55", "#ff6b6b", "#c78cff", "#7fe0ff", "#f28de1", "#9bff8e", "#ffd6a5", "#9ad5ff"];

async function postJSON(url, data){
  const res = await fetch(url,{method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(data)});
  return await res.json();
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
  const scenario = data.scenario_name ? ` · Escenario: ${data.scenario_name}` : '';
  el.innerHTML = `<div class="row" style="justify-content:space-between"><div><div><strong>${data.vehicle_profile}</strong></div><small>Sesión: ${data.session_name} · Código: <span class="code-box">${data.session_code}</span>${scenario}</small></div><div class="row"><span class="badge info">${data.mode === 'gasoline' ? 'Gasolina':'Diésel'}</span>${badgeStatus(data.engine_on)}<span class="badge warn">Etapa: ${stageLabel}</span>${milBadge}</div></div>`;
}

function unitFor(key){
  const units = {rpm:"rpm",vehicle_speed:"km/h",throttle:"%",app:"%",map:"kPa",maf:"g/s",iat:"°C",ect:"°C",o2_b1s1:"V",o2_b1s2:"V",stft:"%",ltft:"%",battery_voltage:"V",fuel_pressure:"kPa",rail_pressure:"bar",boost:"kPa",egr_position:"%",inj_cmd_ms:"ms",inj_dead_time_ms:"ms",inj_ms:"ms",spark_advance:"°",calculated_load:"%",cooling_fan:"0/1"};
  return units[key] || "";
}

function renderSensorsTable(data){
  const table = document.getElementById("liveDataTable");
  if(!table) return;
  const order = ["rpm","vehicle_speed","throttle","app","map","maf","iat","ect","o2_b1s1","o2_b1s2","stft","ltft","battery_voltage","fuel_pressure","rail_pressure","boost","egr_position","inj_cmd_ms","inj_dead_time_ms","inj_ms","spark_advance","calculated_load","cooling_fan","ckp_sync","cmp_sync"];
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
  el.innerHTML = rows ? `<div class="freeze-card"><table><tbody>${rows}</tbody></table></div>` : `<div class="scan-placeholder">Aún no hay freeze frame registrado.</div>`;
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
    el.innerHTML = `<div class="scan-placeholder">Aún no hay freeze frame registrado.</div>`;
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
  if(!data){ box.innerHTML = '<div class="scan-placeholder">Presiona el botón para consultar las pruebas O2.</div>'; return; }
  box.innerHTML = `<div class="mode-box-grid"><div class="mode-stat"><small>B1S1</small><strong>${fmt(data.b1s1_voltage)} V</strong></div><div class="mode-stat"><small>B1S2</small><strong>${fmt(data.b1s2_voltage)} V</strong></div><div class="mode-stat"><small>Lazo cerrado</small><strong>${data.closed_loop_ready ? 'Sí':'No'}</strong></div><div class="mode-stat"><small>Calefactor listo</small><strong>${data.heater_ready ? 'Sí':'No'}</strong></div></div>`;
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
  if(!info){ box.innerHTML = '<div class="scan-placeholder">Presiona el botón para consultar el Modo 08.</div>'; return; }
  const activeCount = Number(info.active_hidden_tests || 0);
  const behaviorBadge = activeCount > 0 ? '<span class="badge warn">Comportamiento del motor alterado por prueba</span>' : '<span class="badge ok">Sin pruebas activas</span>';
  box.innerHTML = `
    <div class="freeze-card">
      <div class="row" style="justify-content:space-between;align-items:flex-start;gap:12px">
        <div>
          <strong>${info.title || 'Modo 08 · Control de actuadores'}</strong>
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
  if(!info){ box.innerHTML = '<div class="scan-placeholder">Presiona el botón para cargar la información ECU.</div>'; return; }
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
  for(const [key,val] of Object.entries(data.sensors)){
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

  const axis = buildAxisForKeys(series);
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
    const value = max - ratio * yRange;
    ctx.fillText(fmt(Number(value.toFixed(3))), 6, y + 4);
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

  series.forEach(({key, points, color}, idx)=>{
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    if(key === "o2_b1s1" || key === "o2_b1s2"){
      drawSmoothCurveSeries(ctx, points, margin, plotW, plotH, min, yRange);
    } else {
      ctx.beginPath();
      points.forEach((val,i)=>{
        const x = margin.left + (i/(points.length-1))*plotW;
        const y = margin.top + (1 - ((val-min)/yRange))*plotH;
        if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
      });
      ctx.stroke();
    }
    ctx.fillStyle = color;
    const lastVal = points[points.length-1];
    ctx.fillText(`${signalMap[key]?.label || key}: ${fmt(lastVal)} ${signalMap[key]?.unit || unitFor(key)}`, margin.left + 8, 16 + idx*16);

    if(key === "o2_b1s1" || key === "o2_b1s2"){
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
  const yUnit = signalMap[keyList[0]]?.unit || unitFor(keyList[0]) || "valor";
  ctx.fillText(`Y: ${yUnit}`, 0, 0);
  ctx.restore();

  ctx.fillStyle = "#c7dced";
  ctx.fillText("X: tiempo", width/2 - 28, height - 10);
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
  modal.innerHTML = `<div class="chart-modal-card"><div class="chart-modal-toolbar"><div id="chartModalTitle" class="split-title">Gráfica ampliada</div><button id="chartModalClose" class="ghost mini-btn" type="button">Cerrar</button></div><canvas id="chartModalCanvas"></canvas></div>`;
  document.body.appendChild(modal);
  modal.addEventListener("click", (ev)=>{ if(ev.target === modal) closeChartModal(); });
  modal.querySelector("#chartModalClose").onclick = ()=>closeChartModal();
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


function scheduleDraw(){
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
  });
}

function renderFaults(data){
  const root = document.getElementById("faultList"); if(!root) return;
  const faults = [["maf_low","MAF bajo"],["maf_dirty","MAF sucio"],["vacuum_leak","Falso aire"],["ect_biased_cold","ECT falsa en frío"],["o2_slow","O2 lenta"],["o2_intermittent","O2 intermitente"],["misfire_cyl1","Misfire cil.1"],["ckp_intermittent","CKP intermitente"],["cmp_fault","CMP sin señal"],["map_slow","MAP con retardo"],["throttle_lag","Throttle con retardo"],["idle_hunt","Ralentí inestable"],["rail_pressure_low","Riel bajo"],["boost_low","Turbo bajo"],["egr_stuck_open","EGR atascada"]];
  const active = new Set(data.faults || []);
  root.innerHTML = faults.map(([key,label])=>`<div class="fault-item ${active.has(key) ? 'active':''}" data-fault="${key}">${label}</div>`).join("");
  root.querySelectorAll(".fault-item").forEach(el=>{ el.onclick = async ()=>{ await postJSON("/api/instructor/command", {action:"toggle_fault", fault: el.dataset.fault}); }; });
}


function renderEngineShutdownAlert(data){
  let overlay = document.getElementById("engineShutdownOverlay");
  if(!overlay){
    overlay = document.createElement("div");
    overlay.id = "engineShutdownOverlay";
    overlay.className = "engine-shutdown-overlay";
    document.body.appendChild(overlay);
  }
  if(data.engine_shutdown_alert){
    overlay.innerHTML = `<div class="engine-shutdown-card"><div class="engine-shutdown-icon">⚠</div><div class="engine-shutdown-title">${data.engine_shutdown_message || 'Motor apagado por falla'}</div><div class="engine-shutdown-subtitle">Quedaron menos de 2 cilindros operativos. Corrige la causa y borra fallas para reiniciar.</div></div>`;
    overlay.classList.add("visible");
  } else {
    overlay.classList.remove("visible");
    overlay.innerHTML = "";
  }
}

function renderStudentConnection(data){
  const el = document.getElementById("studentSummary"); if(!el) return;
  const mil = data.mil_on ? '<span class="badge bad">⚠ Check Engine</span>' : '<span class="badge ok">✓ Sin MIL</span>';
  el.innerHTML = `<div class="row" style="flex-wrap:wrap"><span class="badge info">VIN ${data.vin}</span><span class="badge info">Modo ${data.mode}</span>${mil}<span class="badge warn">${data.students_connected} estudiantes</span></div>`;
}

function renderSignalButtons(){
  const root = document.getElementById("signalButtons"); if(!root) return;
  root.innerHTML = Object.entries(signalMap).map(([key, meta])=>`<div class="signal-toggle ${state.selectedSignals.has(key) ? 'active':''}" data-sig="${key}"><strong>${meta.label}</strong><br><small>${meta.unit}</small></div>`).join("");
  root.querySelectorAll(".signal-toggle").forEach(el=>{ el.onclick = ()=>{ const key = el.dataset.sig; if(state.selectedSignals.has(key)) state.selectedSignals.delete(key); else state.selectedSignals.add(key); if(state.selectedSignals.size===0) state.selectedSignals.add("rpm"); state.splitChartSignature = ""; renderSignalButtons(); scheduleDraw(); }; });
}

function syncInstructorControls(data){
  if(state.role !== "instructor") return;
  const map = {sessionName:data.session_name, sessionCode:data.session_code, modeSelect:data.mode, stageSelect:data.stage, presetSelect:data.current_preset || "normal", whatsNumber:data.connection?.whatsapp_number || "", graphModeSelect: state.graphMode};
  Object.entries(map).forEach(([id,val])=>{ const el = document.getElementById(id); if(el && document.activeElement !== el) el.value = val; });
  document.querySelectorAll("[data-sensor]").forEach(slider=>{ const key = slider.dataset.sensor; const value = data.manual_overrides?.[key] ?? data.sensors?.[key] ?? slider.value; if(document.activeElement !== slider) slider.value = value; const out = document.getElementById(`${key}_val`); if(out) out.textContent = fmt(Number(value)); });
  const ipEl = document.getElementById("serverIp"); if(ipEl) ipEl.value = data.connection?.ip || "";
  const linkEl = document.getElementById("studentLink"); if(linkEl) linkEl.value = data.connection?.student_link || "";
  const waBtn = document.getElementById("waShareBtn"); if(waBtn) waBtn.href = data.connection?.whatsapp_link || "#";
}

function renderAll(data){
  state.latest = data;
  renderKPIs(data);
  renderStateSummary(data);
  renderSensorsTable(data);
  renderDtcTable(data);
  renderStudentConnection(data);
  renderFaults(data);
  renderActuatorsPanel(data);
  renderEngineShutdownAlert(data);
  renderMode06(data.monitor_results || []);
  syncInstructorControls(data);
  updateHistory(data);
  scheduleDraw();
}

async function connectWS(){
  const proto = location.protocol === "https:" ? "wss" : "ws";
  state.ws = new WebSocket(`${proto}://${location.host}/ws?role=${state.role}`);
  state.ws.onmessage = (event)=>{ const msg = JSON.parse(event.data); if(msg.type === "state") renderAll(msg.payload); };
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
  const engineBtn = document.getElementById("toggleEngineBtn");
  const resetBtn = document.getElementById("resetBtn");
  const clearBtn = document.getElementById("clearBtn");
  const saveSessionBtn = document.getElementById("saveSessionBtn");
  const saveWhatsBtn = document.getElementById("saveWhatsBtn");
  const copyLinkBtn = document.getElementById("copyLinkBtn");
  const graphModeSel = document.getElementById("graphModeSelect");

  if(modeSel) modeSel.onchange = ()=>postJSON("/api/instructor/command", {action:"set_mode", mode: modeSel.value});
  if(stageSel) stageSel.onchange = ()=>postJSON("/api/instructor/command", {action:"set_stage", stage: stageSel.value});
  if(presetSel) presetSel.onchange = ()=>postJSON("/api/instructor/command", {action:"apply_preset", preset: presetSel.value});
  if(engineBtn) engineBtn.onclick = ()=>postJSON("/api/instructor/command", {action:"toggle_engine", engine_on: !(state.latest?.engine_on)});
  if(resetBtn) resetBtn.onclick = ()=>postJSON("/api/instructor/command", {action:"reset_all"});
  if(clearBtn) clearBtn.onclick = ()=>postJSON("/api/instructor/command", {action:"clear_dtcs"});
  if(saveSessionBtn) saveSessionBtn.onclick = ()=>postJSON("/api/instructor/command", {action:"set_session", session_name: document.getElementById("sessionName").value, session_code: document.getElementById("sessionCode").value});
  if(saveWhatsBtn) saveWhatsBtn.onclick = async ()=>{ const number = document.getElementById("whatsNumber").value.trim(); localStorage.setItem("scanmatic_whatsapp_number", number); await postJSON("/api/instructor/command", {action:"set_whatsapp_number", number}); };
  if(copyLinkBtn) copyLinkBtn.onclick = async ()=>{ const link = document.getElementById("studentLink").value; try{ await navigator.clipboard.writeText(link); copyLinkBtn.textContent = "Copiado"; setTimeout(()=>copyLinkBtn.textContent = "Copiar link", 1500);}catch(_e){} };
  if(graphModeSel) graphModeSel.onchange = ()=>{ state.graphMode = graphModeSel.value; renderGraphMode(); scheduleDraw(); };

  document.querySelectorAll("[data-actuator]").forEach(control=>{
    if(control.tagName === 'SELECT') control.onchange = ()=>postJSON('/api/instructor/command', {action:'set_actuator', key: control.dataset.actuator, value: control.value});
    else control.onchange = ()=>postJSON('/api/instructor/command', {action:'set_actuator', key: control.dataset.actuator, value: control.checked});
  });

  document.querySelectorAll("[data-sensor]").forEach(slider=>{
    slider.oninput = ()=>{ const output = document.getElementById(`${slider.dataset.sensor}_val`); if(output) output.textContent = slider.value; };
    slider.onchange = ()=>postJSON("/api/instructor/command", {action:"set_sensor", key: slider.dataset.sensor, value: Number(slider.value)});
  });

  if(!localStorage.getItem("scanmatic_whatsapp_prompt_done")){
    const saved = localStorage.getItem("scanmatic_whatsapp_number") || "";
    const number = window.prompt("Número de WhatsApp del instructor para preparar el enlace de compartición:", saved);
    if(number !== null){
      localStorage.setItem("scanmatic_whatsapp_number", number.trim());
      await postJSON("/api/instructor/command", {action:"set_whatsapp_number", number: number.trim()});
    }
    localStorage.setItem("scanmatic_whatsapp_prompt_done", "1");
  }else{
    const saved = localStorage.getItem("scanmatic_whatsapp_number");
    if(saved) await postJSON("/api/instructor/command", {action:"set_whatsapp_number", number: saved});
  }
}

async function bindStudentControls(){
  document.querySelectorAll(".scanner-menu-btn").forEach(btn=>{ btn.onclick = ()=>setScannerSection(btn.dataset.scannerSection); });
  document.querySelectorAll("[data-go-section]").forEach(btn=>{ btn.onclick = ()=>setScannerSection(btn.dataset.goSection); });
  const readBtn = document.getElementById("readDtcsBtn");
  const clearBtn = document.getElementById("studentClearBtn");
  const ffBtn = document.getElementById("freezeBtn");
  const dtcSearchBtn = document.getElementById("searchDtcBtn");
  const graphModeSel = document.getElementById("graphModeSelect");
  const mode05Btn = document.getElementById("mode05Btn");
  const mode06Btn = document.getElementById("mode06Btn");
  const mode07Btn = document.getElementById("mode07Btn");
  const mode08Btn = document.getElementById("mode08Btn");
  const mode09Btn = document.getElementById("mode09Btn");
  if(readBtn) readBtn.onclick = async ()=>{ const data = await postJSON("/api/student/action", {action:"read_dtcs"}); state.studentDtcScanned = true; renderDtcTable({dtcs:data.dtcs || []}); setScannerSection("scanner-read-dtc"); };
  if(clearBtn) clearBtn.onclick = async ()=>{ const data = await postJSON("/api/student/action", {action:"clear_dtcs"}); state.studentDtcScanned = true; renderDtcTable({dtcs:data.dtcs || []}); const result = document.getElementById("clearResultBox"); if(result){ result.style.display = "block"; result.textContent = data.dtcs?.length ? "Se borraron algunos códigos, pero aún quedan DTC activos por fallas presentes." : "No quedan DTC activos después del borrado."; } };
  if(ffBtn) ffBtn.onclick = async ()=>{ const data = await postJSON("/api/student/action", {action:"freeze_frame"}); renderFreezeFramesResponse(data); setScannerSection("scanner-freeze"); };
  if(dtcSearchBtn) dtcSearchBtn.onclick = async ()=>{ const q = document.getElementById("dtcSearchInput").value; const rows = await fetch(`/api/dtc-search?q=${encodeURIComponent(q)}&limit=25`).then(r=>r.json()); const table = document.getElementById("dtcLookupTable"); table.innerHTML = rows.map(r=>`<tr><td><strong>${r.code}</strong></td><td>${r.description}</td></tr>`).join(""); };
  if(mode05Btn) mode05Btn.onclick = async ()=>{ const data = await postJSON('/api/student/action',{action:'mode05'}); renderMode05(data.o2_tests); setScannerSection('scanner-mode05'); };
  if(mode06Btn) mode06Btn.onclick = async ()=>{ const data = await postJSON('/api/student/action',{action:'mode06'}); renderMode06(data.monitor_results||[]); setScannerSection('scanner-mode06'); };
  if(mode07Btn) mode07Btn.onclick = async ()=>{ const data = await postJSON('/api/student/action',{action:'mode07'}); renderMode07(data.dtcs||[]); setScannerSection('scanner-mode07'); };
  if(mode08Btn) mode08Btn.onclick = async ()=>{ const data = await postJSON('/api/student/action',{action:'mode08'}); renderMode08(data.mode08); setScannerSection('scanner-mode08'); };
  if(mode09Btn) mode09Btn.onclick = async ()=>{ const data = await postJSON('/api/student/action',{action:'mode09'}); renderMode09(data.ecu_info); setScannerSection('scanner-mode09'); };
  if(graphModeSel) graphModeSel.onchange = ()=>{ state.graphMode = graphModeSel.value; renderGraphMode(); scheduleDraw(); };
}

window.addEventListener("resize", ()=>scheduleDraw());

window.addEventListener("DOMContentLoaded", async ()=>{
  initTabs();
  renderSignalButtons();
  renderGraphMode();
  ensureChartModal();
  document.querySelectorAll('[data-expand-chart][data-chart-target="mixed"]').forEach(btn=>{ btn.onclick = ()=>toggleChartExpand("mixed", "mixed"); });
  setScannerSection(state.activeScannerSection);
  const current = await fetch("/api/state").then(r=>r.json());
  renderAll(current); renderMode05(null); renderMode07([]); renderMode09(null);
  await connectWS();
  await bindInstructorControls();
  await bindStudentControls();
});
