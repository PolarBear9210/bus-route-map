// 지역별 버스 노선 지도 — 전처리된 web/data/*만 읽는 정적 페이지
const TYPE_COLORS = {
  간선: "#2563eb", 지선: "#16a34a", 마을: "#0d9488", 순환: "#ca8a04", 광역: "#dc2626",
  공항: "#7c3aed", 심야: "#334155", 투어: "#a16207", "통근버스(서울)": "#1e3a8a",
  경기일반: "#ea580c", 경기좌석: "#be123c", 광역급행: "#db2777", "통근버스(P)": "#4338ca", 예약: "#a21caf", 경기마을: "#65a30d", 똑버스: "#0284c7", 시외: "#78716c",
  인천일반: "#0891b2", 인천좌석: "#0369a1", 인천광역: "#9a3412", 인천마을: "#4d7c0f",
  경기: "#ea580c", 인천: "#0891b2",
};
const TYPE_ORDER = Object.keys(TYPE_COLORS);

const state = {
  level: "gu",
  selection: [], // 선택한 지역 코드들 (모두 state.level 단위)
  setOp: "union", // 여러 지역 선택 시 "union"(합집합) | "inter"(교집합)
  hiddenTypes: new Set(),
  showStop: true,
  showPass: true,
  activeRid: null,
  activeStation: null, // 클릭한 정류장 sid
  showStations: true,
  choro: "", // "" | "stop" | "pass" | "all"
};
const CHORO_COLORS = ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#084594"];
let choroBreaks = [];

let data, routeGeo = {}, stations = {}, regionLayers = {}, regionIndex = {};
const bundles = new Map(); // 시군구 코드 → 묶음 파일 로드 Promise
let renderToken = 0;
const map = L.map("map", { preferCanvas: true, zoomControl: false }).setView([37.5, 127.05], 10);
L.control.zoom({ position: "bottomright" }).addTo(map);
// 배경 지도: OSM 기본 지도에 흑백·밝기 필터(style.css의 .basemap)를 씌워 연한 회색으로 만든다.
// 노선 색이 배경과 섞이지 않게 하려는 것. (CARTO 회색 지도는 2026년부터 API 키가 필요해 쓰지 않음)
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, className: "basemap",
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
map.createPane("regions").style.zIndex = 350;
// 선택한 지역 바깥을 어둡게 가리는 막. 경계 위·노선 아래
map.createPane("mask").style.zIndex = 380;
map.getPane("mask").style.pointerEvents = "none";
const maskLayer = L.layerGroup().addTo(map);
const routeLayer = L.layerGroup().addTo(map);
// 정류장은 노선보다 항상 위에 보이도록 별도 pane/canvas에 그린다
map.createPane("stations").style.zIndex = 450;
// 캔버스를 위에 덮으면 아래 노선의 마우스 이벤트를 가로채므로 SVG로 그리고, 빈 곳은 이벤트가 통과하게 한다
map.getPane("stations").style.pointerEvents = "none";
const stationRenderer = L.svg({ pane: "stations" });
const stationRoutes = new Map(); // sid → 그 정류장에 서는 노선 rid 집합
const stationLayer = L.layerGroup().addTo(map);
// 상세 보기에서 강조하는 정류장은 전용 층에 그린다. 같은 층이면 확대·축소 때 다시 그린 흰 정류장 점이 그 위를 덮는다
map.createPane("stops").style.zIndex = 452;
map.getPane("stops").style.pointerEvents = "none";
const stopRenderer = L.svg({ pane: "stops" });
const stopLayer = L.layerGroup().addTo(map);
// 상세 보기 중인 노선의 운행 방향 화살표. 마우스 이벤트는 받지 않는다
map.createPane("arrows").style.zIndex = 455;
map.getPane("arrows").style.pointerEvents = "none";
const arrowLayer = L.layerGroup().addTo(map);
const ARROW_GAP_PX = 70;
const ARROW_OFFSET_PX = 5; // 진행 방향 오른쪽으로 비켜 그려 같은 길을 오가는 노선의 화살표가 겹치지 않게 한다
const estimated = new Set(); // 실제 경로 없이 정류장을 이어 만든 추정 경로뿐인 노선
const estParts = {}; // rid → 추정 조각 번호 Set (API 경로가 부정확해 정류장을 직선으로 이은 구간)
let regionStations = new Map(); // 선택 지역 안 정류장 sid → 정차 노선 이름들
let drawn = {}; // rid → polyline

const $ = (s) => document.querySelector(s);
const collator = new Intl.Collator("ko", { numeric: true });

async function load() {
  // 전처리를 다시 돌리면 파일이 바뀌므로 매번 서버에 새 버전이 있는지 확인한다
  const get = (f) => fetch(`data/${f}`, { cache: "no-cache" }).then((r) => r.json());
  const [rr, si, gu, dong] = await Promise.all([get("region_routes.json"), get("regions_si.geojson"), get("regions_gu.geojson"), get("regions_dong.geojson")]);
  data = rr;
  for (const [rid, pct] of Object.entries(rr.meta.realPct || {})) if (pct === 0) estimated.add(rid);
  for (const e of Object.values(rr.dong)) for (const [rid, sids] of Object.entries(e.stop)) for (const sid of sids) {
    if (!stationRoutes.has(sid)) stationRoutes.set(sid, new Set());
    stationRoutes.get(sid).add(rid);
  }
  buildRegions("si", si);
  buildRegions("gu", gu);
  buildRegions("dong", dong);
  buildTypeFilters();
  fillSido(); fillCity(""); fillDong("", "");
  const m = data.meta;
  $("#meta").textContent = `노선 ${m.routeCount}개 · 수집 서울 ${fmtDate(m.collectedOn)}${m.gyeonggiCollectedOn ? `, 경기 ${fmtDate(m.gyeonggiCollectedOn)}` : ""}${m.incheonCollectedOn ? `, 인천 ${fmtDate(m.incheonCollectedOn)}` : ""} · 행정동 경계 ${fmtDate(m.boundaryVersion.replace("ver", ""))} · 통과 기준 ${m.minPassMeters}m 이상`;
  setLevel("gu");
}

// 노선 경로·정류장 좌표는 시군구별 묶음 파일(data/bundles/<코드>.json)로 나뉘어 있어 필요할 때 읽는다
function ensureBundles(guCodes) {
  return Promise.all([...new Set(guCodes)].map((code) => {
    if (!bundles.has(code)) {
      bundles.set(code, fetch(`data/bundles/${code}.json`, { cache: "no-cache" }).then((r) => r.json()).then((b) => {
        // 경로는 조각 목록: [[[x,y]...], ...]. 순서가 뒤섞인 원본 경로를 끊은 노선만 조각이 여러 개다
        for (const [rid, parts] of Object.entries(b.routes)) routeGeo[rid] ??= parts.map((part) => part.map(([x, y]) => [y, x]));
        for (const [rid, idx] of Object.entries(b.estParts)) estParts[rid] ??= new Set(idx);
        Object.assign(stations, b.stations);
      }).catch((err) => { bundles.delete(code); throw err; }));
    }
    return bundles.get(code);
  }));
}

// 지역이 걸친 시군구 묶음 파일들: 시는 속한 구 전부, 구는 자신, 행정동은 속한 구
const bundlesOf = (level, code) => (level === "si" ? regionIndex[`si:${code}`].sggs : level === "gu" ? [code] : [regionIndex[`dong:${code}`].sgg]);
const LEVEL_NAMES = { si: "시", gu: "구", dong: "행정동" };

function fmtDate(s) { return `${s.slice(0, 4)}.${s.slice(4, 6)}.${s.slice(6, 8)}`; }

// 서울 외 노선은 번호가 겹칠 수 있어 면허 시군을 함께 보여준다 (예: 성남 "5100" vs 수원 "5100")
function cityTag(r) { return r.city && r.city !== "서울" ? `[${r.city}] ` : ""; }

function buildRegions(level, geojson) {
  regionLayers[level] = L.geoJSON(geojson, {
    pane: "regions",
    style: (f) => regionStyle(level, f.properties.code),
    onEachFeature: (f, layer) => {
      const p = f.properties;
      const label = level === "dong" ? `${p.name} (${p.sggnm})` : p.name;
      regionIndex[`${level}:${p.code}`] = { level, code: p.code, name: p.name, label, layer, sgg: p.sgg, sggnm: p.sggnm, sido: p.sido, sggs: p.sggs?.split(",") };
      layer.bindTooltip(p.name, { sticky: true });
      // 클릭은 아래 map.on("click")이 처리한다 (지역 클릭은 지도 click으로도 이어지므로 여기서도 처리하면 두 번 처리됨)
    },
  });
}

// 지역 선택은 지도 click 하나로 처리한다. 노선을 한 번 그리면 노선 캔버스가 지역 경계 위를 덮어
// 지역 레이어가 클릭을 받지 못하므로, 클릭 위치가 들어 있는 지역을 직접 찾는다.
// 노선·정류장 클릭은 지도 click으로 이어지지 않게 각 클릭 처리에서 stopPropagation 한다.
function inRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function regionAt(latlng, level = state.level) {
  let hit = null;
  regionLayers[level]?.eachLayer((l) => {
    if (hit || !l.getBounds().contains(latlng)) return;
    const g = l.feature.geometry, polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
    if (polys.some(([outer, ...holes]) => inRing(latlng.lng, latlng.lat, outer) && !holes.some((h) => inRing(latlng.lng, latlng.lat, h)))) hit = l.feature.properties.code;
  });
  return hit;
}
// 서울특별시 전체는 노선이 1,300개가 넘어 한 번에 그리면 화면이 멈춘다 → 시 단위로는 고르지 않고 구 단위로 넘긴다
const SEOUL_SI = "11000";
function switchLevel(level) {
  document.querySelector(`input[name="level"][value="${level}"]`).checked = true;
  setLevel(level);
}

// 지도 클릭(노선·정류장이 아닌 곳)
// - Ctrl/Shift+클릭: 그 지역을 선택에 더하거나 뺀다
// - 선택된 지역 안: 선택은 두고 열린 노선·정류장 상세만 닫는다
// - 선택된 지역 밖(다른 지역이나 경계 밖): 선택에서 벗어난다
// - 선택이 없을 때: 그 지역을 고른다
// 더블클릭 확대가 지역 클릭으로 처리되지 않도록(상세가 닫히거나 선택이 풀림) 잠깐 기다렸다가 처리한다
let clickTimer = null;
map.on("click", (e) => { clearTimeout(clickTimer); clickTimer = setTimeout(() => handleMapClick(e), 250); });
map.on("dblclick", () => clearTimeout(clickTimer));

function handleMapClick(e) {
  const code = regionAt(e.latlng), ev = e.originalEvent;
  const seoulSi = state.level === "si" && code === SEOUL_SI;
  if (ev.ctrlKey || ev.metaKey || ev.shiftKey) {
    if (code && !seoulSi) toggleRegion(code);
    return;
  }
  if (state.selection.length) {
    if (code && state.selection.includes(code)) closeDetail(); else clearSelection();
    return;
  }
  if (!code) return;
  if (seoulSi) {
    const gu = regionAt(e.latlng, "gu");
    switchLevel("gu");
    if (gu) selectRegion("gu", gu);
    return;
  }
  selectRegion(state.level, code);
}

// 선택한 지역의 경계선은 updateMask()가 바깥 가림막과 함께 따로 그린다
function regionStyle(level, code) {
  const sel = level === state.level && state.selection.includes(code);
  const base = { color: "#7b8794", weight: sel ? 0 : 0.8, fillColor: "#64748b", fillOpacity: 0.02 };
  if (state.choro) {
    base.fillColor = choroColor(countFor(level, code, state.choro));
    base.fillOpacity = sel ? 0.35 : 0.6;
  }
  return base;
}

// 선택한 지역 바깥을 어둡게 가리고, 경계를 흰 테두리를 두른 파란 선으로 그린다
const maskRenderer = L.svg({ pane: "mask" });
function updateMask() {
  maskLayer.clearLayers();
  if (!state.selection.length) return;
  const rings = []; // 선택 지역 바깥 경계들 [lat, lng]
  for (const c of state.selection) {
    const g = regionIndex[`${state.level}:${c}`].layer.feature.geometry;
    for (const poly of g.type === "Polygon" ? [g.coordinates] : g.coordinates) rings.push(poly[0].map(([x, y]) => [y, x]));
  }
  const world = [[-89, -179], [-89, 179], [89, 179], [89, -179]];
  const opts = { renderer: maskRenderer, interactive: false };
  L.polygon([world, ...rings], { ...opts, stroke: false, fillColor: "#0f172a", fillOpacity: 0.12 }).addTo(maskLayer);
  L.polyline(rings.map((r) => [...r, r[0]]), { ...opts, color: "#fff", weight: 6, opacity: 0.9 }).addTo(maskLayer);
  L.polyline(rings.map((r) => [...r, r[0]]), { ...opts, color: "#1d4ed8", weight: 2.5 }).addTo(maskLayer);
}

function restyleRegions() {
  regionLayers[state.level].eachLayer((l) => l.setStyle(regionStyle(state.level, l.feature.properties.code)));
  for (const c of state.selection) regionIndex[`${state.level}:${c}`].layer.bringToFront();
}

// 유형 필터를 적용한 지역의 노선 수
function countFor(level, code, mode) {
  const e = data[level][code], R = data.meta.routes;
  const n = (obj) => Object.keys(obj).filter((rid) => !state.hiddenTypes.has(R[rid].typeName)).length;
  return mode === "stop" ? n(e.stop) : mode === "pass" ? n(e.pass) : n(e.stop) + n(e.pass);
}

// 분위수로 구간을 나눈다 (값이 몰려 있어도 색이 골고루 쓰이도록)
function updateChoro() {
  const legend = $("#legend");
  if (!state.choro) { legend.hidden = true; restyleRegions(); return; }
  const vals = Object.keys(data[state.level]).map((c) => countFor(state.level, c, state.choro)).sort((a, b) => a - b);
  const k = CHORO_COLORS.length;
  choroBreaks = [...new Set(Array.from({ length: k - 1 }, (_, i) => vals[Math.floor(((i + 1) * vals.length) / k)]))]
    .filter((b) => b > vals[0]);
  legend.hidden = false;
  legend.innerHTML = `
    <div class="bar">${CHORO_COLORS.slice(0, choroBreaks.length + 1).map((c) => `<span style="background:${c}"></span>`).join("")}</div>
    <div class="ticks"><span>${vals[0]}</span>${choroBreaks.map((b) => `<span>${b}</span>`).join("")}<span>${vals[vals.length - 1]}</span></div>`;
  restyleRegions();
}

function choroColor(v) {
  let i = 0;
  while (i < choroBreaks.length && v >= choroBreaks[i]) i++;
  return CHORO_COLORS[i];
}

function setLevel(level) {
  clearSelection();
  state.level = level;
  for (const [lv, layer] of Object.entries(regionLayers)) {
    if (lv === level) layer.addTo(map); else layer.remove();
  }
  updateChoro();
}

// 지역 고르기: 시도 → 시·군·구 → (시 전체 | 구 전체 | 행정동) 을 고르고 [확인]을 누르면 지도에서 클릭한 것과 같이 선택된다.
// "수원시 장안구"처럼 구가 있는 시는 시로 묶고, 세 번째 상자에서 "수원시 전체", 구별 "○○구 전체"와 동들을 보여준다.
// 고른 항목에 맞춰 시/구/행정동 단위가 바뀐다. 세 번째 상자를 비우고 확인하면 두 번째 상자의 시(서울은 구), 둘 다 비우면 서울특별시 전체.
const cityOf = (sggnm) => sggnm.split(" ")[0];
const dongList = () => Object.values(regionIndex).filter((r) => r.level === "dong");
const opt = (value, text, selected = false) => `<option value="${value}"${selected ? " selected" : ""}>${text}</option>`;

function fillSido(sido = "") {
  const list = [...new Set(dongList().map((d) => d.sido))];
  $("#pick-sido").innerHTML = opt("", "시도") + list.map((s) => opt(s, s, s === sido)).join("");
}

function fillCity(sido, city = "") {
  const sel = $("#pick-city");
  const cities = [...new Set(dongList().filter((d) => d.sido === sido).map((d) => cityOf(d.sggnm)))].sort(collator.compare);
  sel.innerHTML = opt("", sido === "서울" ? "구" : "시·군") + cities.map((c) => opt(c, c, c === city)).join("");
  sel.disabled = !sido;
}

const siByName = (name) => Object.values(regionIndex).find((r) => r.level === "si" && r.name === name);

// 세 번째 상자 값: "si:<시 코드>", "gu:<시군구코드>" 또는 "dong:<행정동코드>"
function fillDong(sido, city, value = "") {
  const sel = $("#pick-dong");
  const dongs = dongList().filter((d) => d.sido === sido && cityOf(d.sggnm) === city);
  const groups = new Map(); // 시군구 코드 → { name, dongs }
  for (const d of dongs) {
    if (!groups.has(d.sgg)) groups.set(d.sgg, { name: d.sggnm.split(" ")[1] || d.sggnm, dongs: [] });
    groups.get(d.sgg).dongs.push(d);
  }
  const multi = groups.size > 1; // 구가 여러 개인 시는 구별로 묶는다
  const options = ([sgg, g]) => opt(`gu:${sgg}`, `${g.name} 전체`, value === `gu:${sgg}`)
    + g.dongs.sort((a, b) => collator.compare(a.name, b.name)).map((d) => opt(`dong:${d.code}`, d.name, value === `dong:${d.code}`)).join("");
  const si = multi ? siByName(city) : null;
  sel.innerHTML = opt("", multi ? "시 전체 / 구 / 행정동" : "전체 / 행정동")
    + (si ? opt(`si:${si.code}`, `${city} 전체`, value === `si:${si.code}`) : "")
    + [...groups].sort(([, a], [, b]) => collator.compare(a.name, b.name))
      .map((e) => (multi ? `<optgroup label="${e[1].name}">${options(e)}</optgroup>` : options(e))).join("");
  sel.disabled = !city;
}

// 지도에서 지역을 클릭했을 때 선택 상자를 그 지역으로 맞춘다
function syncPicker(level, code) {
  const r = regionIndex[`${level}:${code}`];
  if (!r) return;
  fillSido(r.sido);
  if (level === "si" && r.name === "서울특별시") { fillCity(r.sido); fillDong(r.sido, ""); return; }
  const city = cityOf(level === "dong" ? r.sggnm : r.name);
  // 구가 하나뿐인 시(의정부시 등)는 시 코드와 구 코드가 같아 세 번째 상자의 "○○시 전체"(gu:)로 맞춘다
  const value = level === "si" && r.sggs.length === 1 ? `gu:${code}` : `${level}:${code}`;
  fillCity(r.sido, city);
  fillDong(r.sido, city, value);
}

// 선택 상자에서 고른 지역 → [단위, 코드]
function pickedRegion() {
  const sido = $("#pick-sido").value, city = $("#pick-city").value, third = $("#pick-dong").value;
  if (third) return third.split(":");
  if (city) {
    const si = siByName(city);
    if (si) return ["si", si.code]; // 경기 시·군
    const gu = Object.values(regionIndex).find((r) => r.level === "gu" && r.name === city);
    if (gu) return ["gu", gu.code]; // 서울의 구
  }
  return null;
}

function applyPick() {
  const picked = pickedRegion();
  if (!picked) {
    // 서울은 구까지, 경기는 시·군까지는 골라야 한다 (서울특별시 전체는 너무 무거워 고르지 않는다)
    const sel = $("#pick-sido").value ? $("#pick-city") : $("#pick-sido");
    sel.focus();
    sel.classList.add("need");
    setTimeout(() => sel.classList.remove("need"), 1200);
    return;
  }
  const [level, code] = picked;
  if (level !== state.level) switchLevel(level);
  selectRegion(level, code, true);
}

$("#pick-sido").addEventListener("change", (e) => { fillCity(e.target.value); fillDong(e.target.value, ""); });
$("#pick-city").addEventListener("change", (e) => fillDong($("#pick-sido").value, e.target.value));
$("#pick-go").addEventListener("click", applyPick);
// 선택 상자에서 Enter를 눌러도 확인과 같다
$("#region-picker").addEventListener("keydown", (e) => { if (e.key === "Enter") applyPick(); });

// 유형 필터 묶음. 목록에 없는 유형은 "공통"으로 들어간다
const TYPE_GROUPS = [
  ["서울", ["간선", "지선", "마을", "순환", "광역", "통근버스(서울)", "심야", "투어"]],
  ["경기", ["경기일반", "경기좌석", "경기마을", "통근버스(P)", "예약", "똑버스", "시외", "경기"]],
  ["인천", ["인천일반", "인천좌석", "인천광역", "인천마을", "인천"]],
  ["공통", ["광역급행", "공항"]],
];

// 유형 필터: 묶음별 칩 버튼. 칩을 누르면 그 유형을, 묶음 이름을 누르면 묶음 전체를 끄고 켠다
function buildTypeFilters() {
  const present = new Set(Object.values(data.meta.routes).map((r) => r.typeName));
  const grouped = new Set(TYPE_GROUPS.flatMap(([, ts]) => ts));
  const groups = TYPE_GROUPS.map(([g, ts]) => [g, ts.filter((t) => present.has(t))]);
  groups[groups.length - 1][1].push(...[...present].filter((t) => !grouped.has(t)));
  $("#type-filters").innerHTML = `
    <div class="tf-top"><span class="tf-label">노선 유형</span><button class="tf-all" type="button">전체 켜기</button></div>
    ${groups.filter(([, ts]) => ts.length).map(([g, ts]) => `
      <div class="tf-group" data-group="${g}">
        <button class="tf-head" type="button" data-group="${g}" title="${g} 유형 모두 끄기/켜기"><span>${g}</span><span class="tf-count"></span></button>
        <div class="tf-chips">${ts.map((t) => `<button class="tf-chip" type="button" data-type="${t}" aria-pressed="true"><i style="background:${TYPE_COLORS[t] || "#666"}"></i>${t}</button>`).join("")}</div>
      </div>`).join("")}`;
  syncTypeFilters();
}

function syncTypeFilters() {
  for (const c of document.querySelectorAll(".tf-chip")) c.setAttribute("aria-pressed", String(!state.hiddenTypes.has(c.dataset.type)));
  for (const g of document.querySelectorAll(".tf-group")) {
    const chips = [...g.querySelectorAll(".tf-chip")], on = chips.filter((c) => c.getAttribute("aria-pressed") === "true").length;
    g.querySelector(".tf-count").textContent = `${on}/${chips.length}`;
    g.classList.toggle("all-off", on === 0);
  }
  // 모두 켜져 있으면 "전체 끄기", 하나라도 꺼져 있으면 "전체 켜기"
  $(".tf-all").textContent = state.hiddenTypes.size === 0 ? "전체 끄기" : "전체 켜기";
}

const allTypes = () => [...document.querySelectorAll(".tf-chip")].map((c) => c.dataset.type);

function setTypesHidden(types, hidden) {
  for (const t of types) hidden ? state.hiddenTypes.add(t) : state.hiddenTypes.delete(t);
  syncTypeFilters();
  closeDetail();
  render();
  updateChoro();
}

function clearSelection() {
  const prev = state.selection;
  state.selection = [];
  renderToken++;
  for (const c of prev) regionIndex[`${state.level}:${c}`].layer.setStyle(regionStyle(state.level, c));
  $("#pick-dong").value = ""; // 시도·시군구 선택은 남겨 두어 같은 시의 다른 구·동을 바로 고를 수 있게 한다
  routeLayer.clearLayers();
  stopLayer.clearLayers();
  regionStations = new Map();
  drawStations();
  drawn = {};
  $("#result").innerHTML = "";
  closeDetail();
  updateMask();
  updateSide();
}

// 떠 있는 카드에 가리지 않는 영역 안에 bounds가 들어오도록 지도를 맞춘다
function fitTo(bounds, pad = 30) {
  const box = (sel) => { const el = $(sel); return el.hidden ? null : el.getBoundingClientRect(); };
  const c = box("#controls"), s = ui.sideCollapsed ? null : box("#side");
  const W = window.innerWidth, H = window.innerHeight;
  let tl = [pad, pad], br = [pad, pad];
  if (narrow()) {
    if (c) tl[1] += c.bottom;
    if (s) br[1] += H - s.top;
  } else {
    if (c) tl[0] += c.right;
    if (s) br[0] += W - s.left;
  }
  map.fitBounds(bounds, { paddingTopLeft: tl, paddingBottomRight: br });
}

// 새로 지역을 고르면 접어 둔 결과 패널을 다시 편다
function openSide() {
  if (ui.sideCollapsed) { ui.sideCollapsed = false; saveUi(); }
  // 좁은 화면에서는 지도가 보이도록 선택 카드를 접는다 (기억하지는 않음)
  if (narrow() && !ui.controlsCollapsed) { ui.controlsCollapsed = true; updateControls(); }
  updateSide();
}

function selectRegion(level, code, fit = false) {
  clearSelection();
  state.selection = [code];
  const reg = regionIndex[`${level}:${code}`];
  reg.layer.setStyle(regionStyle(level, code)).bringToFront();
  updateMask();
  syncPicker(level, code);
  openSide(); // 패널을 먼저 열어야 지도를 맞출 때 패널 자리를 비켜 계산한다
  if (fit) fitTo(reg.layer.getBounds(), 40);
  render();
}

function toggleRegion(code) {
  const sel = state.selection.filter((c) => c !== code);
  if (sel.length === state.selection.length) sel.push(code);
  clearSelection();
  state.selection = sel;
  for (const c of sel) regionIndex[`${state.level}:${c}`].layer.setStyle(regionStyle(state.level, c)).bringToFront();
  updateMask();
  if (sel.length) { openSide(); render(); }
}

const routeSort = (a, b) => TYPE_ORDER.indexOf(a.typeName) - TYPE_ORDER.indexOf(b.typeName) || collator.compare(a.name, b.name);

// 지역 하나의 정차/통과 노선 목록
function currentRoutes(level, code) {
  const entry = data[level][code];
  const R = data.meta.routes;
  const make = (rid, kind, extra) => ({ rid, kind, ...R[rid], ...extra });
  const stop = Object.entries(entry.stop).map(([rid, sids]) => make(rid, "stop", { sids }));
  const pass = Object.entries(entry.pass).map(([rid, len]) => make(rid, "pass", { len }));
  return { stop: stop.sort(routeSort), pass: pass.sort(routeSort) };
}

// 선택한 여러 지역을 합친 노선 목록.
// 합집합: 한 곳이라도 지나는 노선. 한 곳이라도 서면 정차.
// 교집합: 모든 지역을 지나는 노선. 모든 지역에 서야 정차, 아니면 통과로 분류.
function combinedRoutes(level, codes, op) {
  const R = data.meta.routes;
  const per = {}; // rid → { code: {kind, sids|len} }
  for (const code of codes) {
    const e = data[level][code];
    for (const [rid, sids] of Object.entries(e.stop)) (per[rid] ??= {})[code] = { kind: "stop", sids };
    for (const [rid, len] of Object.entries(e.pass)) (per[rid] ??= {})[code] = { kind: "pass", len };
  }
  const stop = [], pass = [];
  for (const [rid, by] of Object.entries(per)) {
    const hits = Object.values(by);
    if (op === "inter" && hits.length < codes.length) continue;
    const stopIn = codes.filter((c) => by[c]?.kind === "stop");
    const isStop = op === "inter" ? stopIn.length === codes.length : stopIn.length > 0;
    const r = {
      rid, ...R[rid], kind: isStop ? "stop" : "pass", by,
      sids: [...new Set(stopIn.flatMap((c) => by[c].sids))],
      len: hits.reduce((s, h) => s + (h.len || 0), 0),
      stopCount: stopIn.length, hitCount: hits.length,
    };
    (isStop ? stop : pass).push(r);
  }
  return { stop: stop.sort(routeSort), pass: pass.sort(routeSort) };
}

function visible(r) {
  return !state.hiddenTypes.has(r.typeName) && (r.kind === "stop" ? state.showStop : state.showPass);
}

async function render() {
  if (!state.selection.length) return;
  const level = state.level, codes = state.selection, multi = codes.length > 1;
  // 묶음 파일을 기다리는 사이 다른 지역을 고르면 이전 요청의 결과는 버린다
  const token = ++renderToken;
  const need = codes.flatMap((c) => bundlesOf(level, c)).filter((g) => !bundles.has(g));
  if (need.length) $("#result").innerHTML = '<p class="hint">노선 경로를 불러오는 중…</p>';
  try {
    await ensureBundles(codes.flatMap((c) => bundlesOf(level, c)));
  } catch (err) {
    if (token === renderToken) $("#result").innerHTML = `<p class="hint">노선 경로를 불러오지 못했습니다: ${err.message}</p>`;
    return;
  }
  if (token !== renderToken || state.selection !== codes) return;
  const regs = codes.map((c) => regionIndex[`${level}:${c}`]);
  const { stop, pass } = multi ? combinedRoutes(level, codes, state.setOp) : currentRoutes(level, codes[0]);
  const vs = stop.filter(visible), vp = pass.filter(visible);

  routeLayer.clearLayers();
  drawn = {};
  // 통과 노선을 먼저 그려서 정차 노선이 위에 오도록 한다
  for (const r of [...vp, ...vs]) {
    const coords = routeGeo[r.rid];
    if (!coords) continue;
    const line = routeShape(r).addTo(routeLayer);
    line.bindTooltip(`${cityTag(r)}${r.name} · ${r.kind === "stop" ? "정차" : "통과"}${estNote(r.rid)}`, { sticky: true });
    line.on("click", (e) => { L.DomEvent.stopPropagation(e); showDetail(r); });
    line.on("mouseover", () => {
      // 정류장을 보는 중에는 그 정류장과 관계없는 (흐려진) 노선은 반응하지 않는다
      if (state.activeStation && !stationRoutes.get(state.activeStation).has(r.rid)) return line.closeTooltip();
      highlight(r.rid);
    });
    line.on("mouseout", () => highlight(null));
    drawn[r.rid] = { line, r };
  }

  regionStations = new Map();
  for (const r of vs) for (const sid of r.sids) {
    if (!regionStations.has(sid)) regionStations.set(sid, []);
    regionStations.get(sid).push(r.name);
  }
  drawStations();

  const section = (title, list) => list.length
    ? `<div class="group-title"><span>${title}</span><span class="count">${list.length}</span></div><ul class="routes">${list.map((r) => routeItem(r, noteFor(r, multi, codes.length) + (!routeGeo[r.rid] ? " · 경로 없음" : estNote(r.rid)))).join("")}</ul>`
    : "";
  const shownList = [...vs, ...vp];
  const r0 = regs[0];
  const kicker = multi ? `${regs.length}개 지역 · ${LEVEL_NAMES[level]}` : [r0.sido, LEVEL_NAMES[level], level === "dong" ? r0.sggnm : ""].filter(Boolean).join(" · ");
  const filtered = shownList.length < stop.length + pass.length;

  $("#result").innerHTML = `
    <div class="res-head">
      <div class="res-title">
        <div class="kicker">${kicker}</div>
        <h2>${multi ? `${state.setOp === "union" ? "합집합" : "교집합"}` : r0.name}</h2>
      </div>
      <div class="res-actions"><button class="btn" id="csv-region" title="필터가 적용된 목록을 CSV로 내보냅니다">CSV</button><button class="btn" id="clear-sel" title="선택 해제 (Esc)">선택 해제</button></div>
    </div>
    ${multi ? `<div class="chips">${regs.map((g) => `<button class="chip" data-code="${g.code}" title="선택에서 빼기">${g.name} <span aria-hidden="true">✕</span></button>`).join("")}</div>
      <div class="setop seg">
        <label><input type="radio" name="setop" value="union" ${state.setOp === "union" ? "checked" : ""}><span title="한 곳이라도 지나는 노선">합집합</span></label>
        <label><input type="radio" name="setop" value="inter" ${state.setOp === "inter" ? "checked" : ""}><span title="모든 지역을 지나는 노선">교집합</span></label>
      </div>` : ""}
    <div class="stats">
      <div class="stat"><b>${stop.length}</b><span><i class="line solid"></i>정차</span></div>
      <div class="stat"><b>${pass.length}</b><span><i class="line dashed"></i>통과</span></div>
      <div class="stat"><b>${stop.length + pass.length}</b><span>전체 노선</span></div>
    </div>
    ${typeBreakdown(shownList)}
    ${filtered ? `<p class="filter-note">필터 적용 중 · ${shownList.length}개 표시</p>` : ""}
    ${section(multi && state.setOp === "inter" ? "모든 지역에 정차" : "정차 노선", vs)}${section(multi && state.setOp === "inter" ? "일부 또는 전부 통과" : "통과 노선", vp)}
    ${shownList.length === 0 ? '<p class="hint">조건에 맞는 노선이 없습니다.</p>' : ""}`;

  const shown = new Set(shownList.map((r) => r.rid));
  $("#csv-region").onclick = () => downloadCsv(regs, `버스노선_${regs.map((g) => g.name).join("+")}${multi ? `_${state.setOp === "union" ? "합집합" : "교집합"}` : ""}`, multi ? shown : null);
  $("#clear-sel").onclick = clearSelection;
  for (const c of $("#result").querySelectorAll(".chip")) c.onclick = () => toggleRegion(c.dataset.code);
  for (const el of $("#result").querySelectorAll('input[name="setop"]')) el.onchange = (e) => { state.setOp = e.target.value; closeDetail(); render(); };
  bindRouteItems($("#result"), (rid) => shownList.find((x) => x.rid === rid), showDetail);
}

// 결과·정류장 상세에서 쓰는 노선 한 줄: 번호 배지 + (기점 → 종점 / 면허 시군·유형·메모)
function routeItem(r, note = "") {
  const meta = [r.city && r.city !== "서울" ? r.city : "", r.typeName, note].filter(Boolean).join(" · ");
  return `
    <li data-rid="${r.rid}">
      <span class="badge" style="background:${TYPE_COLORS[r.typeName] || "#666"}">${r.name}</span>
      <span class="ri-main">
        <span class="ri-route">${r.start} <span class="ri-arrow">→</span> ${r.end}</span>
        <span class="ri-meta">${meta}</span>
      </span>
    </li>`;
}

function bindRouteItems(container, find, onClick) {
  for (const li of container.querySelectorAll("li[data-rid]")) {
    const rid = li.dataset.rid;
    li.addEventListener("mouseenter", () => highlight(rid));
    li.addEventListener("mouseleave", () => highlight(null));
    li.addEventListener("click", () => onClick(find(rid), rid));
  }
}

// 보이는 노선의 유형 구성: 막대 하나 + 상위 유형 범례
function typeBreakdown(list) {
  if (!list.length) return "";
  const counts = {};
  for (const r of list) counts[r.typeName] = (counts[r.typeName] || 0) + 1;
  const items = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const bar = items.map(([t, n]) => `<span style="flex:${n};background:${TYPE_COLORS[t] || "#666"}" title="${t} ${n}"></span>`).join("");
  const legend = items.map(([t, n]) => `<span class="tl-item"><i style="background:${TYPE_COLORS[t] || "#666"}"></i>${t} <b>${n}</b></span>`).join("");
  return `<div class="typebar" aria-label="유형 구성">${bar}</div><div class="typelegend">${legend}</div>`;
}

function noteFor(r, multi, n) {
  if (!multi) return r.kind === "stop" ? `정류장 ${r.sids.length}` : `${fmtLen(r.len)} 통과`;
  return `${r.hitCount}/${n}곳 · 정차 ${r.stopCount}곳`;
}

// 현재 필터(유형·정차/통과)를 적용해 지역별 노선 목록을 CSV로 내려받는다. 엑셀에서 한글이 깨지지 않게 BOM을 붙인다.
// onlyRids가 있으면(여러 지역 합집합/교집합) 그 노선만 지역별로 한 줄씩 쓴다.
function downloadCsv(regions, filename, onlyRids = null) {
  const header = ["지역단위", "구", "지역", "지역코드", "노선", "면허지역", "유형", "구분", "지역내정류장수", "지역내통과거리m", "기점", "종점", "운수사", "노선ID"];
  const rows = [header];
  for (const reg of regions) {
    const { stop, pass } = currentRoutes(reg.level, reg.code);
    const gu = reg.level === "dong" ? reg.label.match(/\((.+)\)$/)[1] : reg.name;
    for (const r of [...stop, ...pass].filter((x) => (onlyRids ? onlyRids.has(x.rid) && !state.hiddenTypes.has(x.typeName) : visible(x)))) {
      rows.push([LEVEL_NAMES[reg.level], gu, reg.name, reg.code, r.name, r.city || "", r.typeName,
        r.kind === "stop" ? "정차" : "통과", r.sids ? r.sids.length : 0, r.len ?? "", r.start, r.end, (r.corp || "").replace(/\s+/g, " ").trim(), r.rid]);
    }
  }
  const esc = (v) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : v);
  const csv = "﻿" + rows.map((row) => row.map(esc).join(",")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  a.download = `${filename}_${data.meta.collectedOn}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function fmtLen(m) { return m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${m}m`; }

// 추정 구간은 실제 도로와 다르므로 노선 색 대신 가는 회색 점선으로 구분한다
const EST_STYLE = { color: "#8a96a0", weight: 2, opacity: 0.8, dashArray: "1 5", lineCap: "round" };

function estNote(rid) {
  const pct = data.meta.realPct?.[rid];
  return pct === undefined ? "" : pct === 0 ? " · 추정 경로" : " · 일부 추정";
}

// 노선 하나를 실제 경로 조각(노선 색)과 추정 조각(회색 점선)으로 나눠 그린다
function routeShape(r) {
  const parts = routeGeo[r.rid], est = estParts[r.rid];
  const real = parts.filter((_, i) => !est?.has(i)), guess = parts.filter((_, i) => est?.has(i));
  const group = L.featureGroup();
  if (real.length) L.polyline(real, baseStyle(r)).addTo(group);
  if (guess.length) L.polyline(guess, { ...EST_STYLE, isEst: true }).addTo(group);
  return group;
}

// mod: 강조/흐리게 할 때 덮어쓸 스타일 (weight, opacity)
function styleRoute(group, r, mod = {}) {
  group.eachLayer((l) => l.setStyle({ ...(l.options.isEst ? EST_STYLE : baseStyle(r)), ...mod }));
}

function baseStyle(r) {
  return {
    color: TYPE_COLORS[r.typeName] || "#666",
    weight: r.kind === "stop" ? 3 : 2.5,
    opacity: r.kind === "stop" ? 0.75 : 0.6,
    dashArray: r.kind === "pass" ? "6 6" : null,
  };
}

// 일부 노선만 강조하고 나머지는 흐리게. rid가 null이면 상세 보기 중인 노선이나 정류장 노선을 유지하고, 없으면 원래대로
function highlight(rid) {
  const targets = rid ? new Set([rid])
    : state.activeRid ? new Set([state.activeRid])
    : state.activeStation ? stationRoutes.get(state.activeStation)
    : null;
  for (const [id, { line, r }] of Object.entries(drawn)) {
    if (!targets) styleRoute(line, r);
    else if (targets.has(id)) { styleRoute(line, r, { weight: targets.size === 1 ? 6 : 4.5, opacity: 1 }); line.bringToFront(); }
    else styleRoute(line, r, { opacity: 0.08 });
  }
  for (const li of document.querySelectorAll("#result li")) li.classList.toggle("active", !!targets && targets.has(li.dataset.rid));
}

// 정류장을 클릭하면 그 정류장에 서는 노선 목록을 보여주고 해당 노선만 강조한다
function showStation(sid) {
  state.activeRid = null;
  state.activeStation = sid;
  highlight(null);
  drawArrows();
  stopLayer.clearLayers();
  const s = stations[sid];
  map.getPane("stations").classList.remove("dimmed");
  L.circleMarker([s.y, s.x], { renderer: stopRenderer, interactive: false, radius: 8, color: "#1d4ed8", weight: 4, fillColor: "#fff", fillOpacity: 1 })
    .addTo(stopLayer);
  const R = data.meta.routes;
  const list = [...stationRoutes.get(sid)].map((rid) => ({ rid, ...R[rid] })).sort(routeSort);
  const d = openDetailView(`
    <div class="detail-head">
      <span class="st-icon" aria-hidden="true"></span>
      <div><div class="kicker">정류장${s.ars && s.ars !== "0" ? ` · ${s.ars}` : ""}</div><h3>${s.name}</h3></div>
    </div>
    <div class="group-title"><span>이 정류장에 서는 노선</span><span class="count">${list.length}</span></div>
    <ul class="routes">${list.map((r) => routeItem(r, drawn[r.rid] ? "" : "필터로 숨김")).join("")}</ul>`);
  bindRouteItems(d, (rid) => drawn[rid]?.r, (r, rid) => {
    if (r) showDetail(r);
    else fitTo(L.latLngBounds(routeGeo[rid]?.flat() || [[s.y, s.x]]));
  });
}

// 상세(노선·정류장)는 결과 목록 자리에 꽉 차게 연다. "← 목록"으로 돌아가면 목록의 스크롤 위치가 그대로 남아 있다
function openDetailView(html) {
  const d = $("#detail");
  d.hidden = false;
  d.innerHTML = `<div class="detail-nav"><button class="back" title="목록으로 (Esc)">← 목록</button></div>${html}`;
  d.scrollTop = 0;
  d.querySelector(".back").onclick = closeDetail;
  $("#side").classList.add("detail-open");
  if (ui.sideCollapsed) openSide();
  return d;
}

function showDetail(r) {
  state.activeStation = null;
  state.activeRid = r.rid;
  highlight(null);
  drawArrows();
  stopLayer.clearLayers();
  const names = (r.sids || []).map((sid) => stations[sid]).filter(Boolean);
  const color = TYPE_COLORS[r.typeName] || "#666";
  // 이 노선의 정류장이 잘 보이도록 나머지 흰 정류장 점은 흐리게 둔다
  map.getPane("stations").classList.add("dimmed");
  for (const sid of r.sids || []) {
    const s = stations[sid];
    if (!s) continue;
    L.circleMarker([s.y, s.x], { renderer: stopRenderer, radius: 7, color: "#fff", weight: 2.5, fillColor: color, fillOpacity: 1 })
      .bindTooltip(`${s.name} (${s.ars})`)
      .on("click", (e) => { L.DomEvent.stopPropagation(e); showStation(sid); })
      .addTo(stopLayer);
  }
  const where = r.by ? "선택 지역" : "이 지역";
  const estMsg = estimated.has(r.rid)
    ? "API에 쓸 만한 경로 좌표가 없어 정류장을 순서대로 이은 추정 경로입니다. 실제 도로와 다를 수 있고 통과 판정에는 쓰지 않습니다."
    : data.meta.realPct?.[r.rid] !== undefined ? `API 경로 좌표가 일부 구간만 맞아, 경로의 약 ${100 - data.meta.realPct[r.rid]}%는 정류장을 이은 추정 구간(회색 점선)입니다. 추정 구간은 통과 판정에 쓰지 않습니다.` : "";
  const d = openDetailView(`
    <div class="detail-head">
      <span class="badge big" style="background:${color}">${r.name}</span>
      <div><div class="kicker">${[r.typeName, r.city && r.city !== "서울" ? r.city : ""].filter(Boolean).join(" · ")}</div>${r.corp ? `<div class="d-corp">${r.corp}</div>` : ""}</div>
    </div>
    <div class="d-ends"><div><small>기점</small>${r.start}</div><span class="ri-arrow">→</span><div><small>종점</small>${r.end}</div></div>
    <div class="d-actions">
      ${routeGeo[r.rid] ? '<button class="btn fit">노선 전체 보기</button><span class="route-sub">▶ 화살표: 운행 방향</span>' : '<span class="route-sub">경로 좌표가 없는 노선입니다.</span>'}
    </div>
    ${estMsg ? `<p class="d-note">${estMsg}</p>` : ""}
    ${r.by ? `<div class="group-title"><span>지역별</span></div><ul class="per-region">${state.selection.map((c) => {
        const h = r.by[c], nm = regionIndex[`${state.level}:${c}`].name;
        return `<li><span>${nm}</span><span>${!h ? "지나지 않음" : h.kind === "stop" ? `정차 · 정류장 ${h.sids.length}` : `통과 ${fmtLen(h.len)}`}</span></li>`;
      }).join("")}</ul>` : ""}
    ${names.length
      ? `<div class="group-title"><span>${where} 안 정류장</span><span class="count">${names.length}</span></div>
         <ol class="timeline" style="--c:${color}">${names.map((s) => `<li><span class="t-name">${s.name}</span><span class="t-ars">${s.ars && s.ars !== "0" ? s.ars : ""}</span></li>`).join("")}</ol>`
      : `<p class="d-note">${where}에서는 정차하지 않고 약 <b>${fmtLen(r.len)}</b> 지나갑니다.</p>`}`);
  const fit = d.querySelector(".fit");
  if (fit) fit.onclick = () => fitTo(L.latLngBounds(routeGeo[r.rid].flat()));
}

// 축소했을 때 점이 뭉치지 않도록 확대 수준에 따라 크기를 바꾼다
function stationRadius() {
  const z = map.getZoom();
  return z <= 11 ? 1.5 : z === 12 ? 2 : z === 13 ? 3 : z === 14 ? 4 : 5;
}
map.on("zoomend", drawStations);

// 선택 지역 안에서 (필터에 보이는) 정차 노선이 서는 정류장
function drawStations() {
  stationLayer.clearLayers();
  if (!state.showStations) return;
  for (const [sid, names] of regionStations) {
    const s = stations[sid];
    if (!s) continue;
    L.circleMarker([s.y, s.x], { renderer: stationRenderer, radius: stationRadius(), color: "#475569", weight: map.getZoom() >= 14 ? 1.5 : 1, fillColor: "#fff", fillOpacity: 1 })
      .bindTooltip(`<b>${s.name}</b> ${s.ars}<br>${names.length}개 노선: ${names.slice(0, 12).join(", ")}${names.length > 12 ? " …" : ""}<br><span class="route-sub">클릭하면 노선 강조</span>`)
      .on("click", (e) => { L.DomEvent.stopPropagation(e); showStation(sid); })
      .addTo(stationLayer);
  }
}

// 상세 보기 중인 노선을 따라 일정 간격(화면 픽셀 기준)으로 방향 화살표를 놓는다. 화면 밖은 그리지 않는다
function drawArrows() {
  arrowLayer.clearLayers();
  const parts = state.activeRid && routeGeo[state.activeRid];
  if (!parts) return;
  const color = estimated.has(state.activeRid) ? "#8a96a0" : TYPE_COLORS[data.meta.routes[state.activeRid].typeName] || "#666";
  const view = map.getBounds().pad(0.1);
  let count = 0;
  for (const part of parts) {
    const pts = part.map((c) => map.latLngToLayerPoint(c));
    let walked = 0, next = ARROW_GAP_PX / 2;
    for (let i = 1; i < pts.length && count < 400; i++) {
      const a = pts[i - 1], b = pts[i], seg = a.distanceTo(b);
      while (seg > 0 && walked + seg >= next) {
        const t = (next - walked) / seg;
        const dx = (b.x - a.x) / seg, dy = (b.y - a.y) / seg;
        // 화면 좌표는 y가 아래로 커지므로 진행 방향의 오른쪽은 (-dy, dx)
        const p = L.point(a.x + (b.x - a.x) * t - dy * ARROW_OFFSET_PX, a.y + (b.y - a.y) * t + dx * ARROW_OFFSET_PX);
        const ll = map.layerPointToLatLng(p);
        if (view.contains(ll)) {
          const deg = (Math.atan2(dy, dx) * 180) / Math.PI;
          L.marker(ll, {
            pane: "arrows", interactive: false, keyboard: false,
            icon: L.divIcon({
              className: "arrow", iconSize: [14, 14],
              html: `<svg viewBox="0 0 14 14" width="14" height="14" style="transform:rotate(${deg}deg)"><path d="M3 2.5 L12 7 L3 11.5 Z" fill="${color}" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
            }),
          }).addTo(arrowLayer);
          count++;
        }
        next += ARROW_GAP_PX;
      }
      walked += seg;
    }
  }
}
map.on("zoomend moveend", drawArrows);

function closeDetail() {
  state.activeRid = null;
  state.activeStation = null;
  arrowLayer.clearLayers();
  stopLayer.clearLayers();
  map.getPane("stations").classList.remove("dimmed");
  $("#detail").hidden = true;
  $("#side").classList.remove("detail-open");
  highlight(null);
}

// 왼쪽 위 선택 카드와 오른쪽 결과 패널의 접기 상태. 이 브라우저에만 기억한다
const ui = { controlsCollapsed: false, sideCollapsed: false };
try { Object.assign(ui, JSON.parse(localStorage.getItem("busmap.ui") || "{}")); } catch {}
const saveUi = () => { try { localStorage.setItem("busmap.ui", JSON.stringify(ui)); } catch {} };
const narrow = () => window.matchMedia("(max-width: 720px)").matches;

function setToggle(btn, expanded, [openIcon, closedIcon], label) {
  btn.textContent = expanded ? openIcon : closedIcon;
  btn.title = `${label} ${expanded ? "접기" : "펴기"}`;
  btn.setAttribute("aria-expanded", String(expanded));
}

function updateControls() {
  $("#controls").classList.toggle("collapsed", ui.controlsCollapsed);
  setToggle($("#toggle-controls"), !ui.controlsCollapsed, ["▴", "▾"], "선택·필터");
}

// 결과 패널은 지역을 골랐을 때만 보인다. 펼쳐져 있으면 확대·축소 버튼이 패널 옆으로 비켜난다
function updateSide() {
  const show = state.selection.length > 0;
  $("#side").hidden = !show;
  $("#side").classList.toggle("collapsed", ui.sideCollapsed);
  setToggle($("#toggle-side"), !ui.sideCollapsed, narrow() ? ["▾", "▴"] : ["▸", "◂"], "결과");
  document.body.classList.toggle("side-open", show && !ui.sideCollapsed);
}

$("#toggle-controls").addEventListener("click", () => { ui.controlsCollapsed = !ui.controlsCollapsed; saveUi(); updateControls(); });

// 사용법: 처음 방문하면 한 번 펼쳐 보여주고, 닫은 뒤에는 ? 버튼으로만 연다
function showHelp(open) {
  $("#help").hidden = !open;
  $("#toggle-help").setAttribute("aria-expanded", String(open));
  if (open && ui.controlsCollapsed) { ui.controlsCollapsed = false; updateControls(); }
  if (!open && !ui.helpSeen) { ui.helpSeen = true; saveUi(); }
}
$("#toggle-help").addEventListener("click", () => showHelp($("#help").hidden));
$("#close-help").addEventListener("click", () => showHelp(false));
if (!ui.helpSeen && !narrow()) showHelp(true);
$("#toggle-side").addEventListener("click", () => { ui.sideCollapsed = !ui.sideCollapsed; saveUi(); updateSide(); });
updateControls();

// 이벤트 연결
for (const el of document.querySelectorAll('input[name="level"]')) el.addEventListener("change", (e) => setLevel(e.target.value));
$("#type-filters").addEventListener("click", (e) => {
  const chip = e.target.closest(".tf-chip"), head = e.target.closest(".tf-head"), all = e.target.closest(".tf-all");
  if (chip) setTypesHidden([chip.dataset.type], !state.hiddenTypes.has(chip.dataset.type));
  else if (head) {
    // 묶음 안에 하나라도 켜져 있으면 모두 끄고, 모두 꺼져 있으면 모두 켠다
    const types = [...head.parentElement.querySelectorAll(".tf-chip")].map((c) => c.dataset.type);
    setTypesHidden(types, types.some((t) => !state.hiddenTypes.has(t)));
  } else if (all) {
    if (state.hiddenTypes.size === 0) setTypesHidden(allTypes(), true);
    else setTypesHidden([...state.hiddenTypes], false);
  }
});
$("#choro").addEventListener("change", (e) => { state.choro = e.target.value; updateChoro(); });
$("#csv-all").addEventListener("click", () => {
  const regs = Object.values(regionIndex).filter((r) => r.level === state.level).sort((a, b) => collator.compare(a.label, b.label));
  downloadCsv(regs, `버스노선_전체${LEVEL_NAMES[state.level]}`);
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.target.tagName === "INPUT") return;
  if (state.activeRid || state.activeStation) closeDetail(); else clearSelection();
});
$("#show-stations").addEventListener("change", (e) => { state.showStations = e.target.checked; drawStations(); });
$("#show-stop").addEventListener("change", (e) => { state.showStop = e.target.checked; closeDetail(); render(); });
$("#show-pass").addEventListener("change", (e) => { state.showPass = e.target.checked; closeDetail(); render(); });

load().catch((err) => {
  $("#result").innerHTML = `<p class="hint">데이터를 불러오지 못했습니다: ${err.message}<br>web 폴더에서 로컬 서버로 열어야 합니다.</p>`;
});
