/* Licensed to the Apache Software Foundation (ASF) under the Apache License, Version 2.0. */
/* Wingman.OS console: 25SHA sim, BPM metronome, voting, highway viz. No build step. */
"use strict";

const $ = (id) => document.getElementById(id);

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function seal25(input) {
  const enc = new TextEncoder();
  let data = enc.encode(input);
  const rounds = [];
  for (let i = 1; i <= 25; i++) {
    const hex = await sha256Hex(data);
    if (i <= 3 || i === 25) rounds.push({ i, hex });
    data = Uint8Array.from(hex.match(/../g).map((h) => parseInt(h, 16)));
  }
  return { rounds, seal: rounds[rounds.length - 1].hex };
}

$("sha-run").addEventListener("click", async () => {
  const input = $("sha-input").value;
  const list = $("sha-rounds");
  list.innerHTML = "<li>sealing…</li>";
  const { rounds, seal } = await seal25(input);
  list.innerHTML = "";
  for (const r of rounds) {
    const li = document.createElement("li");
    li.textContent = `round ${r.i}: ${r.hex.slice(0, 48)}…`;
    list.appendChild(li);
  }
  const mid = document.createElement("li");
  mid.textContent = "… rounds 4–24 chained …";
  list.insertBefore(mid, list.lastChild);
  $("sha-result").textContent = `cache_seal: ${seal}`;
});

/* --- BPM metronome (WebAudio, audio-reactive canvas) --- */
let audioCtx = null;
let timer = null;
let beatAt = 0;
let pulse = 0;

function drawMetro(bpm) {
  const cv = $("metro-canvas");
  const ctx = cv.getContext("2d");
  const t = performance.now() / 1000;
  pulse = Math.max(0, pulse - 0.04);
  ctx.fillStyle = "#0d0b0a";
  ctx.fillRect(0, 0, cv.width, cv.height);
  const cx = cv.width / 2, cy = cv.height / 2;
  const r = 30 + pulse * 70 + Math.sin(t * 2) * 3;
  const g = ctx.createRadialGradient(cx, cy, 4, cx, cy, r);
  g.addColorStop(0, "rgba(212,175,55,0.9)");
  g.addColorStop(1, "rgba(212,175,55,0)");
  ctx.fillStyle = g;
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#f2e9d8";
  ctx.font = "28px Georgia";
  ctx.textAlign = "center";
  ctx.fillText(`${bpm} BPM`, cx, cy + 8);
  requestAnimationFrame(() => drawMetro(bpm));
}

function click(freq) {
  const o = audioCtx.createOscillator();
  const g = audioCtx.createGain();
  o.frequency.value = freq;
  g.gain.setValueAtTime(0.25, audioCtx.currentTime);
  g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.12);
  o.connect(g).connect(audioCtx.destination);
  o.start(); o.stop(audioCtx.currentTime + 0.13);
}

$("bpm").addEventListener("input", (e) => { $("bpm-val").textContent = e.target.value; });

$("metro-toggle").addEventListener("click", () => {
  if (timer) {
    clearInterval(timer); timer = null;
    $("metro-toggle").textContent = "Start";
    return;
  }
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const bpm = Number($("bpm").value);
  $("metro-toggle").textContent = "Stop";
  let beat = 0;
  const tick = () => {
    beat += 1;
    pulse = 1;
    beatAt = Date.now();
    click(beat % 4 === 1 ? 1567 : 1046);
  };
  tick();
  timer = setInterval(tick, 60000 / bpm);
  requestAnimationFrame(() => drawMetro(bpm));
});

$("metro-publish").addEventListener("click", async () => {
  const bpm = Number($("bpm").value);
  const res = await fetch("/api/v1/swarm/state", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      loop_id: "golden-hour-ring", agent_id: "bpm-metronome",
      summary: `Tempo lock at ${bpm} BPM for the Golden Hour live set.`,
      key_points: [`bpm=${bpm}`, `beat_at=${new Date(beatAt || Date.now()).toISOString()}`],
      next_action: "sha-cache: seal this tempo state.",
    }),
  });
  $("metro-status").textContent = res.ok ? `published @ ${bpm} BPM` : `tunnel error ${res.status}`;
  refreshFeed();
});

/* --- voting --- */
$("vote-btn").addEventListener("click", async () => {
  const res = await fetch("/api/v1/swarm/audio/vote", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: $("vote-user").value, mix_id: $("vote-mix").value }),
  });
  const body = await res.json();
  $("vote-result").textContent = res.ok
    ? `counted: ${body.mix_id} → ${body.vote_count} votes`
    : `error ${res.status}: ${body.detail}`;
  const ol = $("rankings");
  ol.innerHTML = "";
  for (const r of body.rankings || []) {
    const li = document.createElement("li");
    li.textContent = `#${r.rank} ${r.mix_id} — ${r.votes} votes`;
    ol.appendChild(li);
  }
});

/* --- highway viz + feed --- */
const RING = ["bpm-metronome", "sha-cache", "mix-voter", "visual-mascot"];

async function drawRing() {
  const res = await fetch("/api/v1/swarm/topology").then((r) => r.json());
  const svg = $("ring");
  const cx = 260, cy = 120, rx = 190, ry = 80;
  svg.innerHTML = "";
  const pos = RING.map((_, i) => {
    const a = (i / RING.length) * Math.PI * 2 - Math.PI / 2;
    return [cx + rx * Math.cos(a), cy + ry * Math.sin(a)];
  });
  RING.forEach((id, i) => {
    const [x1, y1] = pos[i], [x2, y2] = pos[(i + 1) % pos.length];
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", x1); line.setAttribute("y1", y1);
    line.setAttribute("x2", x2); line.setAttribute("y2", y2);
    line.setAttribute("stroke", "#8a7223"); line.setAttribute("stroke-width", "2");
    line.setAttribute("marker-end", "url(#arr)");
    svg.appendChild(line);
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("cx", x1); c.setAttribute("cy", y1); c.setAttribute("r", "26");
    c.setAttribute("fill", "#141110"); c.setAttribute("stroke", "#d4af37"); c.setAttribute("stroke-width", "2");
    svg.appendChild(c);
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", x1); t.setAttribute("y", y1 + 44);
    t.setAttribute("fill", "#d4af37"); t.setAttribute("font-size", "11"); t.setAttribute("text-anchor", "middle");
    t.textContent = (res.nodes && res.nodes[i] ? res.nodes[i].agent_id : id);
    svg.appendChild(t);
  });
}

async function refreshFeed() {
  const feed = $("tunnel-feed");
  const caught = await fetch("/api/v1/swarm/state/next?agent_id=mix-voter&loop_id=golden-hour-ring");
  if (caught.status === 200) {
    const v = await caught.json();
    const li = document.createElement("li");
    li.textContent = `turn ${v.turn} · ${v.agent_id} → ${v.summary} (seal ${String(v.cache_seal).slice(0, 12)}…)`;
    feed.prepend(li);
    while (feed.children.length > 12) feed.lastChild.remove();
  }
}

$("vec-publish").addEventListener("click", async () => {
  const res = await fetch("/api/v1/swarm/state", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      loop_id: "golden-hour-ring", agent_id: $("vec-agent").value,
      summary: $("vec-summary").value, key_points: [], next_action: "next node: continue the cycle.",
    }),
  });
  if (res.ok) refreshFeed();
});

$("vec-catch").addEventListener("click", refreshFeed);

(async function init() {
  const [ozone, health] = await Promise.all([
    fetch("/api/v1/security/ozone").then((r) => r.json()),
    fetch("/api/v1/health").then((r) => r.json()),
  ]);
  $("ozone-chip").textContent = `ozone ${ozone.alg} · ${ozone.key_configured ? "key set" : "dev key"}`;
  $("health-chip").textContent = `backend ${health.status} · v${health.version}`;
  drawRing();
  setInterval(refreshFeed, 5000);
})();
