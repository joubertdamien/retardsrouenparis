"use strict";

const state = { period: "daily", index: null, series: null, current: null, crosscheck: null };

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
  return response.json();
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function fmtDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString("fr-FR", {
    weekday: "short", day: "2-digit", month: "2-digit", year: "numeric",
  });
}

function fmtDelay(minutes) {
  if (minutes === null || minutes === undefined) return "—";
  if (minutes < 0) return `${minutes.toFixed(1)} min (avance)`;
  return `+${minutes.toFixed(1)} min`;
}

function fmtDelaySec(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  return fmtDelay(seconds / 60);
}

function periodLabel(report) {
  if (report.period.type === "daily") return fmtDate(report.period.start);
  if (report.period.type === "weekly") {
    return `Semaine du ${fmtDate(report.period.start)} au ${fmtDate(report.period.end)}`;
  }
  const d = new Date(report.period.start + "T00:00:00");
  return d.toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
}

function renderSummary(summary) {
  const cards = document.getElementById("summary-cards");
  cards.innerHTML = "";
  const items = [
    { label: "Trains prévus", value: summary.scheduled, cls: "" },
    { label: "Ayant circulé", value: summary.circulated, cls: "" },
    { label: "Supprimés", value: summary.cancelled, cls: summary.cancelled ? "bad" : "good" },
    { label: "Mesurés", value: summary.measured, cls: "" },
    { label: "En retard (≥ 5 min)", value: summary.delayed, cls: summary.delayed ? "bad" : "good" },
    {
      label: "Ponctualité",
      value: summary.punctuality_rate === null ? "—" : `${summary.punctuality_rate} %`,
      cls: summary.punctuality_rate === null ? "" : summary.punctuality_rate >= 90 ? "good" : "bad",
    },
    { label: "Retard moyen", value: fmtDelay(summary.avg_delay_minutes), cls: "" },
    { label: "Retard max", value: fmtDelay(summary.max_delay_minutes), cls: summary.max_delay_minutes >= 15 ? "bad" : "" },
  ];
  if (summary.cross_checked !== undefined && summary.cross_checked > 0) {
    items.push({
      label: "Vérifiés (GTFS-RT)",
      value: `${summary.cross_check_agreeing}/${summary.cross_checked}`,
      cls: summary.cross_check_agreeing === summary.cross_checked ? "good" : "bad",
    });
  }
  for (const item of items) {
    const card = document.createElement("div");
    card.className = `card ${item.cls}`;
    card.innerHTML = `<div class="value"></div><div class="label"></div>`;
    card.querySelector(".value").textContent = item.value;
    card.querySelector(".label").textContent = item.label;
    cards.appendChild(card);
  }
}

function crossCheckBadge(train) {
  if (train.data_source === "gtfs_rt_trip_updates") {
    return '<span class="badge muted">GTFS-RT</span>';
  }
  if (train.delay_cross_check_agrees === null || train.delay_cross_check_agrees === undefined) {
    return '<span class="badge muted">non vérifié</span>';
  }
  if (train.delay_cross_check_agrees) {
    return '<span class="badge ok">concordant</span>';
  }
  return '<span class="badge delayed">écart détecté</span>';
}

function renderSourceBanner(index) {
  const banner = document.getElementById("source-banner");
  const badge = document.getElementById("source-badge");
  const message = document.getElementById("source-message");
  const source = index.data_source || "sncf_api";
  const tuOnly = index.trip_updates_only || {};
  if (source === "gtfs_rt_trip_updates" || tuOnly.active) {
    banner.style.display = "flex";
    banner.className = "banner warn";
    badge.textContent = "GTFS-RT TripUpdates";
    badge.className = "badge warn";
    const n = tuOnly.trains_from_trip_updates || 0;
    const d = tuOnly.delayed_from_trip_updates || 0;
    message.textContent = `Token API SNCF non disponible — ${n} trains suivis, ${d} en retard (≥ 5 min), données issues du flux GTFS-RT TripUpdates (sans token).`;
  } else {
    banner.style.display = "flex";
    banner.className = "banner ok";
    badge.textContent = "API SNCF + GTFS-RT";
    badge.className = "badge ok";
    const cc = index.cross_check || {};
    message.textContent = `Données API SNCF temps réel${cc.trains_cross_checked ? ` · ${cc.delays_agreeing}/${cc.trains_cross_checked} retards vérifiés par GTFS-RT` : ""}.`;
  }
}

function renderTrains(trains) {
  const tbody = document.querySelector("#trains-table tbody");
  tbody.innerHTML = "";
  for (const train of trains) {
    const tr = document.createElement("tr");
    if (train.status === "cancelled") tr.className = "cancelled";
    else if (train.delay_minutes !== null && train.delay_minutes >= 5) tr.className = "delayed";

    const badge = train.status === "cancelled"
      ? '<span class="badge cancelled">supprimé</span>'
      : train.delay_minutes !== null && train.delay_minutes >= 5
        ? '<span class="badge delayed">en retard</span>'
        : '<span class="badge ok">à l\'heure</span>';

    const diff = train.delay_cross_check_difference_seconds;
    const diffText = diff === null || diff === undefined ? "—" : `±${(diff / 60).toFixed(1)} min`;

    const cells = [
      train.train_number || "—",
      train.line_code || train.line_name || "—",
      train.direction || "—",
      fmtTime(train.scheduled_departure),
      fmtTime(train.actual_departure),
      fmtTime(train.scheduled_arrival),
      fmtTime(train.actual_arrival),
      fmtDelaySec(train.trip_update_delay_seconds),
      fmtDelay(train.delay_minutes),
      diffText,
      badge,
      crossCheckBadge(train),
    ];
    for (const cell of cells) {
      const td = document.createElement("td");
      td.innerHTML = cell;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function drawChart(days) {
  const canvas = document.getElementById("chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width = canvas.clientWidth * (window.devicePixelRatio || 1);
  const height = canvas.height = 120 * (window.devicePixelRatio || 1);
  ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
  const cssWidth = canvas.clientWidth;
  const cssHeight = 120;
  ctx.clearRect(0, 0, cssWidth, cssHeight);

  const measured = days.filter((d) => d.punctuality_rate !== null);
  if (measured.length < 2) {
    ctx.fillStyle = "#666";
    ctx.font = "14px sans-serif";
    ctx.fillText("Ponctualité journalière (données insuffisantes)", 10, 30);
    return;
  }

  const padL = 40, padR = 10, padT = 15, padB = 25;
  const plotW = cssWidth - padL - padR;
  const plotH = cssHeight - padT - padB;
  const minRate = Math.min(...measured.map((d) => d.punctuality_rate));
  const maxRate = 100;
  const x = (i) => padL + (i / (measured.length - 1)) * plotW;
  const y = (rate) => padT + plotH - ((rate - minRate) / (maxRate - minRate)) * plotH;

  ctx.strokeStyle = "#dde3ea";
  ctx.beginPath();
  ctx.moveTo(padL, y(100));
  ctx.lineTo(padL + plotW, y(100));
  ctx.stroke();

  ctx.strokeStyle = "#005a9c";
  ctx.lineWidth = 2;
  ctx.beginPath();
  measured.forEach((d, i) => (i === 0 ? ctx.moveTo(x(i), y(d.punctuality_rate)) : ctx.lineTo(x(i), y(d.punctuality_rate))));
  ctx.stroke();

  ctx.fillStyle = "#005a9c";
  measured.forEach((d, i) => {
    ctx.beginPath();
    ctx.arc(x(i), y(d.punctuality_rate), 3, 0, Math.PI * 2);
    ctx.fill();
  });

  ctx.fillStyle = "#666";
  ctx.font = "11px sans-serif";
  ctx.fillText(`${minRate.toFixed(0)}%`, 4, y(minRate) + 4);
  ctx.fillText("100%", 4, y(100) + 4);
  ctx.fillText(measured[0].date, padL, cssHeight - 6);
  const last = measured[measured.length - 1].date;
  ctx.fillText(last, cssWidth - padR - ctx.measureText(last).width, cssHeight - 6);
}

async function loadReport(reportRef) {
  const report = await fetchJson(reportRef.url);
  state.current = report;
  document.getElementById("generated-at").textContent =
    `Rapport ${periodLabel(report)} · généré le ${new Date(report.generated_at).toLocaleString("fr-FR")}`;
  renderSummary(report.summary);
  renderTrains(report.trains);
  drawChart(state.series.days.filter((d) => d.date >= report.period.start && d.date <= report.period.end));
}

function fillPeriodSelect() {
  const select = document.getElementById("period-select");
  select.innerHTML = "";
  const group = state.index.periods.find((p) => p.type === state.period);
  for (const report of group.reports.slice().reverse()) {
    const option = document.createElement("option");
    option.value = report.url;
    option.textContent = `${report.start} → ${report.summary.punctuality_rate ?? "—"} % de ponctualité`;
    select.appendChild(option);
  }
  select.onchange = () => loadReport({ url: select.value });
  if (select.options.length) loadReport({ url: select.value });
}

function renderCrosscheck(crosscheck) {
  const section = document.getElementById("crosscheck-section");
  if (!crosscheck || !crosscheck.trains_cross_checked) return;
  section.style.display = "block";

  const total = crosscheck.trains_cross_checked;
  const agreeing = crosscheck.delays_agreeing;
  document.getElementById("crosscheck-summary").textContent =
    `${crosscheck.trips_in_feed} courses dans le flux GTFS-RT · ${total} trains vérifiés · ${agreeing} retards concordants (tolérance ${crosscheck.tolerance_seconds}s)`;

  const cards = document.getElementById("crosscheck-cards");
  cards.innerHTML = "";
  for (const item of [
    { label: "Courses GTFS-RT", value: crosscheck.trips_in_feed, cls: "" },
    { label: "Trains vérifiés", value: total, cls: "" },
    { label: "Retards concordants", value: agreeing, cls: agreeing === total ? "good" : "bad" },
    { label: "Taux d'accord", value: total ? `${Math.round(agreeing / total * 100)} %` : "—", cls: agreeing === total ? "good" : "bad" },
  ]) {
    const card = document.createElement("div");
    card.className = `card ${item.cls}`;
    card.innerHTML = `<div class="value"></div><div class="label"></div>`;
    card.querySelector(".value").textContent = item.value;
    card.querySelector(".label").textContent = item.label;
    cards.appendChild(card);
  }

  const tbody = document.querySelector("#crosscheck-table tbody");
  tbody.innerHTML = "";
  const trains = (crosscheck.trains || []).slice().reverse();
  for (const train of trains.slice(0, 100)) {
    const tr = document.createElement("tr");
    const agrees = train.agrees;
    if (agrees === true) tr.className = "";
    else if (agrees === false) tr.className = "delayed";

    const agreeBadge = agrees === true
      ? '<span class="badge ok">concordant</span>'
      : agrees === false
        ? '<span class="badge delayed">écart</span>'
        : '<span class="badge muted">—</span>';

    const diff = train.difference_seconds;
    const diffText = diff === null || diff === undefined ? "—" : `${(diff / 60).toFixed(1)} min`;

    const cells = [
      train.train_number || "—",
      train.service_date || "—",
      fmtDelaySec(train.announced_delay_seconds),
      fmtDelaySec(train.trip_updates_delay_seconds),
      diffText,
      agreeBadge,
    ];
    for (const cell of cells) {
      const td = document.createElement("td");
      td.innerHTML = cell;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

async function init() {
  try {
    state.index = await fetchJson("index.json");
    state.series = await fetchJson("series.json");
    try {
      state.crosscheck = await fetchJson("crosscheck.json");
      renderCrosscheck(state.crosscheck);
    } catch {
      state.crosscheck = null;
    }
  } catch (error) {
    document.querySelector("main").innerHTML =
      `<p class="card">Aucune donnée disponible pour le moment. ${error.message}</p>`;
    return;
  }
  renderSourceBanner(state.index);
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.period = tab.dataset.period;
      fillPeriodSelect();
    });
  });
  fillPeriodSelect();
}

document.addEventListener("DOMContentLoaded", init);
