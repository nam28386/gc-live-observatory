"use strict";

const panels = [...document.querySelectorAll(".chart")];
const time = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit", minute: "2-digit", timeZone: "UTC",
});
let currentSnapshot;
let lastTimelineRefresh = 0;

function show(id, text) {
  const node = document.getElementById(id);
  if (node.textContent !== text) node.textContent = text;
}

function received(observation) {
  if (!observation) return "—";
  const utcMs = Number(BigInt(observation.utc_ns) / 1000000n);
  return new Date(utcMs).toISOString().slice(11, 23) + " UTC";
}

function price(ticks, tickSize) {
  return (ticks * tickSize).toFixed(1);
}

function draw(panel, bars, tickSize) {
  const frame = panel.dataset.frame;
  const canvas = panel.querySelector("canvas");
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const scale = window.devicePixelRatio || 1;
  const ctx = canvas.getContext("2d");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.font = "11px system-ui, sans-serif";

  const last = bars.at(-1);
  if (!last) {
    ctx.fillStyle = "#a9b4b8";
    ctx.fillText("Awaiting observed trades", 13, 25);
    return;
  }

  const status = [last.finalized ? "Finalized" : "Forming"];
  if (last.partial) status.push("partial");
  panel.querySelector("output").textContent =
    price(last.close_ticks, tickSize) + " · " + last.volume + " contracts · " + status.join(", ");
  // Accessible chart metadata also identifies the source candle represented by this render.
  canvas.setAttribute("aria-label",
    frame + " chart, latest candle " + new Date(last.start_ms).toISOString() +
    ", close " + price(last.close_ticks, tickSize) + ", volume " + last.volume);
  canvas.dataset.latestCandle = [last.start_ms, last.close_ticks, last.volume].join(":");

  const duration = last.end_ms - last.start_ms;
  const slots = Math.min(40, Math.max(10, Math.floor((width - 75) / 13)));
  const first = last.start_ms - (slots - 1) * duration;
  const visible = bars.filter(bar => bar.start_ms >= first);
  const highest = Math.max(...visible.map(bar => bar.high_ticks)) + 2;
  const lowest = Math.min(...visible.map(bar => bar.low_ticks)) - 2;
  const left = 12;
  const right = width - 57;
  const chartTop = 16;
  const chartBottom = height - 29;
  const step = (right - left) / slots;
  const y = ticks => chartTop + (highest - ticks) * (chartBottom - chartTop) / (highest - lowest);

  ctx.strokeStyle = "#343f44";
  ctx.fillStyle = "#a9b4b8";
  for (let i = 0; i < 4; i += 1) {
    const ticks = Math.round(lowest + i * (highest - lowest) / 3);
    ctx.beginPath();
    ctx.moveTo(left, y(ticks));
    ctx.lineTo(right, y(ticks));
    ctx.stroke();
    ctx.fillText(price(ticks, tickSize), right + 8, y(ticks) + 4);
  }

  for (const bar of visible) {
    const x = left + ((bar.start_ms - first) / duration + 0.5) * step;
    const bodyTop = Math.min(y(bar.open_ticks), y(bar.close_ticks));
    const bodyHeight = Math.max(1.5, Math.abs(y(bar.open_ticks) - y(bar.close_ticks)));
    const halfWidth = Math.max(2, step * 0.3);
    ctx.strokeStyle = ctx.fillStyle = bar.close_ticks >= bar.open_ticks ? "#79d4b1" : "#ef988b";
    ctx.beginPath();
    ctx.moveTo(x, y(bar.high_ticks));
    ctx.lineTo(x, y(bar.low_ticks));
    ctx.stroke();
    if (bar.finalized && !bar.partial) {
      ctx.fillRect(x - halfWidth, bodyTop, 2 * halfWidth, bodyHeight);
    } else {
      ctx.strokeRect(x - halfWidth, bodyTop, 2 * halfWidth, bodyHeight);
    }
  }

  ctx.fillStyle = "#a9b4b8";
  ctx.fillText(time.format(first), left, height - 8);
  ctx.textAlign = "right";
  ctx.fillText(time.format(last.start_ms) + " UTC", right, height - 8);
}

function update(snapshot) {
  currentSnapshot = snapshot;
  const { status, trade, bbo } = snapshot;
  const tickSize = Number(status.tick_size);
  show("source", status.mode === "LIVE" ? "LIVE / FXAURUM" : "MOCK / SYNTHETIC");
  show("instrument", status.exchange + " · " + status.symbol +
    (status.expiry ? " · expiry " + status.expiry : " · simulated clock") + " (UTC)");
  show("last-volume", received(status.last_volume_update));
  const recorder = status.recorder;
  show("recorder", status.recorder_error ? "Capture failed · collector stopped" :
    recorder.enabled ? recorder.durable_events + " durable records" : "Capture off");
  show("issues", status.last_issue ? status.last_issue + " · " + status.counters[status.last_issue] : "—");
  document.getElementById("recorder").classList.toggle("warning", status.recorder_error);
  if (recorder.enabled && Date.now() - lastTimelineRefresh > 2000) {
    lastTimelineRefresh = Date.now();
    refreshTimeline();
  }
  show("trade", trade ? price(trade.price_ticks, tickSize) : "—");
  show("quote", bbo ? price(bbo.bid_ticks, tickSize) + " × " + price(bbo.ask_ticks, tickSize) : "—");
  show("spread", bbo ? bbo.spread_ticks + " ticks" : "—");
  show("cvd", String(snapshot.cvd_observed));
  show("transport", status.transport_connected ? "Connected" : "Disconnected / retrying");
  show("ack", status.subscribe_ack ? "Acknowledged #" + status.request_id :
    status.transport_connected ? "Waiting #" + status.request_id : "Disconnected");
  show("freshness", status.trade_freshness + " / " + status.bbo_freshness);
  show("coverage", "Partial session · " + status.coverage.length + " retained segment(s)");
  show("last-message", received(status.last_message));
  show("last-trade", received(status.last_eligible_trade));
  show("last-bbo", received(status.last_bbo_message));
  show("last-bbo-change", received(status.last_bbo_change));
  show("last-oi", received(status.last_oi_update));
  const count = status.counters.coverage_gaps || 0;
  const openGap = status.gaps.some(gap => !gap.resumed);
  show("gaps", count + (openGap ? " · open gap" : count ? " · historical gaps retained" : ""));
  document.getElementById("gaps").classList.toggle("warning", count > 0);
  for (const panel of panels) draw(panel, snapshot.candles[panel.dataset.frame], tickSize);
}

async function refreshTimeline() {
  const timeline = document.getElementById("timeline");
  timeline.hidden = false;
  const list = document.getElementById("timeline-entries");
  try {
    const response = await fetch("/api/history?gaps=true&limit=12", { cache: "no-store" });
    if (!response.ok) throw new Error("Capture history unavailable");
    const { records } = await response.json();
    const labels = { gap: "Capture interrupted", restart_gap: "Recorder restarted",
      coverage_start: "Market data observed" };
    list.replaceChildren(...records.map(record => {
      const row = document.createElement("li");
      const start = record.payload.from_utc_ns;
      row.textContent = received(record) + " · " + labels[record.kind] +
        (start ? " · previous capture ended " + received({ utc_ns: start }) : "") +
        (record.payload.reason ? " · " + record.payload.reason : "") +
        " · " + record.provenance.symbol + " (" + record.provenance.source + ")";
      return row;
    }));
  } catch {
    const row = document.createElement("li");
    row.textContent = "Capture history unavailable";
    list.replaceChildren(row);
  }
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(protocol + "//" + location.host + "/ws");
  socket.addEventListener("message", event => {
    update(JSON.parse(event.data));
    show("delivery", "Dashboard stream connected");
  });
  socket.addEventListener("error", () => socket.close());
  socket.addEventListener("close", () => {
    show("delivery", "Dashboard disconnected · showing last received data");
    setTimeout(connect, 500);
  });
}

window.addEventListener("resize", () => {
  if (currentSnapshot) update(currentSnapshot);
});
connect();
