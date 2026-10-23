(() => {
  "use strict";

  // ---------------------------------------------------------------- basics
  const $ = (id) => document.getElementById(id);
  const AXIS_START = 6;
  const AXIS_END = 24;
  // Words for zero to nine, digits from 10 (AP style), so a sentence never mixes "11" and "ten".
  const NUM = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];
  const word = (n) => (n >= 0 && n <= 9 ? NUM[n] : String(n));
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const pluralW = (n, one, many) => `${word(n)} ${n === 1 ? one : many}`;

  const ICONS = {
    today: '<circle cx="12" cy="12" r="4"/><path d="M12 2.8v2.1M12 19.1v2.1M4.9 4.9l1.5 1.5M17.6 17.6l1.5 1.5M2.8 12h2.1M19.1 12h2.1M4.9 19.1l1.5-1.5M17.6 6.4l1.5-1.5"/>',
    homes: '<path d="M3.8 10.6 12 4l8.2 6.6"/><path d="M6 9v11h12V9"/><path d="M10 20v-5.2h4V20"/>',
    activity: '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><path d="M4.2 6.5h.6M4.2 12h.6M4.2 17.5h.6" stroke-width="2.6"/>',
    link: '<path d="M10.2 13.8a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-.9.9"/><path d="M13.8 10.2a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l.9-.9"/>',
    play: '<path d="M8 5.6v12.8a.6.6 0 0 0 .9.5l10-6.4a.6.6 0 0 0 0-1L8.9 5.1a.6.6 0 0 0-.9.5z" fill="currentColor" stroke="none"/>',
    pause: '<rect x="6.5" y="5" width="3.8" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="13.7" y="5" width="3.8" height="14" rx="1" fill="currentColor" stroke="none"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="m8 12.3 2.8 2.8L16.2 9.6"/>',
    chevR: '<path d="m9.5 6 6 6-6 6"/>',
    chevL: '<path d="m14.5 6-6 6 6 6"/>',
    bed: '<path d="M3.5 18.5v-11M3.5 14h17v4.5M20.5 14v-2.4a3 3 0 0 0-3-3H11V14"/><circle cx="7.2" cy="11" r="1.8"/>',
    sparkle: '<path d="m12 3.5 1.9 5 5 1.9-5 1.9-1.9 5-1.9-5-5-1.9 5-1.9z"/><path d="M18.5 15.5v4M16.5 17.5h4"/>',
    doorbell: '<rect x="7" y="2.8" width="10" height="18.4" rx="5"/><circle cx="12" cy="9" r="2.3"/><circle cx="12" cy="16" r="1.1"/>',
    note: '<path d="M6 3.5h8.5L18.5 7.5v13H6z"/><path d="M9 11.5h6.5M9 15.5h6.5"/>',
  };
  const icon = (name) => `<span class="ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg></span>`;
  const hydrateIcons = (root = document) => root.querySelectorAll("[data-icon]").forEach((el) => { el.outerHTML = icon(el.dataset.icon); });

  let state = null;
  let route = { name: "today" };
  let filter = "all";
  const openGroups = new Set();
  const briefs = {};
  const seen = new Set();
  let firstRender = true;
  let pending = null;

  // ---------------------------------------------------------------- time
  const tzOf = (d) => d?.property?.timezone || state?.properties?.[0]?.property?.timezone || "America/Los_Angeles";
  // Non-breaking space so "11:00 AM" never wraps between the time and AM.
  const T = (iso, tz) => new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz || tzOf() }).replace(/\s/g, "\u00a0");
  const DAY = (iso, tz) => new Date(iso).toLocaleDateString("en-US", { weekday: "short", timeZone: tz || tzOf() });
  const DATE = (iso, tz) => new Date(iso).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: tz || tzOf() });
  const hourOf = (iso, tz) => Number(new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", hour12: false, timeZone: tz || tzOf() }));
  function ago(iso, tz) {
    const m = Math.round((new Date(state.now) - new Date(iso)) / 60000);
    if (m < 1) return "just now";
    if (m < 60) return `${m} min ago`;
    return T(iso, tz);
  }
  const sameDay = (iso, d) => new Date(iso) >= new Date(d.day_start) && new Date(iso) < new Date(d.day_end);
  function pos(iso, dayStart) {
    const h = (new Date(iso) - new Date(dayStart)) / 3.6e6;
    return Math.max(0, Math.min(100, ((h - AXIS_START) / (AXIS_END - AXIS_START)) * 100));
  }

  // ---------------------------------------------------------------- data helpers
  const homeOf = (pid) => state.properties.find((d) => d.property.id === pid);
  const nameOf = (pid) => homeOf(pid)?.property.name || pid;
  const titleOf = (a) => a.title.split(" · ")[0];
  const needsYou = () => state.alerts.filter((a) => a.status === "open" && (a.severity === "urgent" || a.severity === "warn"));
  const alertsFor = (d) => state.alerts.filter((a) => a.property_id === d.property.id && sameDay(a.created_at, d));
  const exp = (d, kind) => d.expectations.find((e) => e.kind === kind);
  const guests = (s) => (s?.guests ? `${s.guests} guests` : "Guests");

  function chip(d) {
    if (d.attention) return `<span class="chip alert">Needs you</span>`;
    const ready = exp(d, "turnover_ready");
    if (d.state === "turnover" && ready?.status === "met") return `<span class="chip good">Ready</span>`;
    return {
      vacant: `<span class="chip vacant">Vacant</span>`,
      occupied: `<span class="chip stay">Guests staying</span>`,
      turnover: `<span class="chip turn">Turnover</span>`,
    }[d.state];
  }

  function nowLine(d) {
    const p = d.property, tz = p.timezone;
    if (d.camera_offline) return "The door camera is offline";
    if (d.state === "occupied" && d.current_stay) {
      return `${guests(d.current_stay)} until ${DAY(d.current_stay.checkout, tz)} ${T(d.current_stay.checkout, tz)}`;
    }
    if (d.state === "turnover") {
      const ready = exp(d, "turnover_ready");
      const next = d.next_stay;
      if (ready?.status === "met") return next ? `Ready for the ${T(next.checkin, tz)} check-in` : "Turnover verified";
      const visit = d.visits[d.visits.length - 1];
      if (visit) return `Cleaner on site since ${T(visit.first_at, tz)}`;
      const due = exp(d, "cleaner_arrival");
      return due ? `Waiting for ${p.cleaner_name}, due by ${T(due.deadline, tz)}` : `Waiting for ${p.cleaner_name}`;
    }
    if (d.next_stay) return `Empty until ${DATE(d.next_stay.checkin, tz)}`;
    return "No upcoming stays";
  }

  function ringSaid(e) {
    if (e.type === "device_offline") return "Camera went offline";
    if (e.type === "device_online") return "Camera came back online";
    if (e.type === "device_added") return "New camera shared";
    if (e.type === "button_press") return "Doorbell pressed";
    const st = e.sub_type || "motion";
    return {
      human: "Person at the door",
      vehicle: "Vehicle",
      package_delivery: "Package delivered",
      other_motion: "Other motion",
      motion: "Motion (camera can't detect people)",
    }[st] || `Motion (${st})`;
  }

  function why(e) {
    const n = e.note || "";
    if (e.type === "device_online") return n.includes("closed") ? "Closed the offline alert" : "Logged";
    if (n.startsWith("CV check")) {
      const m = n.match(/(\d+) people, within the (\d+)-guest limit/);
      return m ? `Kept quiet: the vision check counted ${m[1]}, within the ${m[2]}-guest booking` : "Kept quiet after a vision check";
    }
    if (e.disposition === "suppressed") {
      if (n.startsWith("guest activity")) return "Kept quiet: guests are staying";
      if (n.includes("during turnover")) return "Kept quiet: it's the turnover window";
      if (n.includes("vacant home")) {
        if (e.classified_as === "other") return "Kept quiet: the vision check saw nobody";
        if (e.classified_as === "vehicle") return "Kept quiet: a vehicle in daytime at an empty home";
        return "Kept quiet: nothing that needs you";
      }
      return "Kept quiet";
    }
    if (e.disposition === "tracked") return "Counted toward the turnover";
    if (e.disposition === "alerted") return "Told you about it";
    if (e.type === "device_online") return "Turnover closed the offline alert";
    return cap(n) || "Logged";
  }

  const SEV = { urgent: "Act now", warn: "Take a look", info: "Good to know", good: "Done" };

  function visionLine(v) {
    if (!v) return "";
    const by = v.source === "fixture" ? "Vision check (offline demo)" : v.source.startsWith("bedrock:") ? "Vision check by Amazon Bedrock" : "Vision check";
    if (v.ok) return `<p class="vision">${by}: ${plural(v.people_count, "person", "people")}, ${Math.round(v.confidence * 100)}% confident.</p>`;
    return `<p class="vision">${by} couldn't tell (${esc(v.fallback_reason)}), so please look at the snapshot.</p>`;
  }

  function trail(a, tz) {
    if (!a.updates.length) return "";
    return `<ul class="trail">${a.updates.map((u) => {
      const [t, ...rest] = u.split(" · ");
      return rest.length ? `<li><time>${esc(t)}</time><span>${esc(rest.join(" · "))}</span></li>` : `<li><span>${esc(u)}</span></li>`;
    }).join("")}</ul>`;
  }

  const ART = window.TurnoverArt;
  function artOf(d) {
    const ready = exp(d, "turnover_ready");
    const mark = d.attention ? "alert" : ready?.status === "met" && d.state === "turnover" ? "ready" : d.state === "turnover" ? "turnover" : "";
    return { id: d.property.id, state: d.state, mark };
  }
  const thumb = (d, i) => { const a = artOf(d); return `<span class="thumb ${d.attention ? "alert" : d.state}">${ART.home(a.id, a.state, a.mark, i)}</span>`; };

  const AV = [["#e8e4f8", "#4b3f9a"], ["#ddf0e8", "#1f6b50"], ["#fde6d4", "#8a4a12"], ["#e1ebf8", "#2f5a8f"], ["#f6e1ea", "#8c2f55"]];
  function avatar(name) {
    const n = String(name || "?");
    const [bg, fg] = AV[[...n].reduce((s, c) => s + c.charCodeAt(0), 0) % AV.length];
    return `<span class="avatar" style="background:${bg};color:${fg}" aria-hidden="true">${esc(n[0])}</span>`;
  }

  // ---------------------------------------------------------------- ribbon
  function ribbon(d, { big = false, nowLineOn = true } = {}) {
    const ds = d.day_start;
    const segs = d.bands.map((b) => {
      const l = pos(b.start, ds), r = pos(b.end, ds);
      return r > l ? `<div class="rb-seg ${b.kind}" style="left:${l}%;width:${r - l}%"></div>` : "";
    }).join("");
    const visits = d.visits.map((v) => {
      const l = pos(v.first_at, ds), r = pos(v.last_at, ds);
      return `<div class="rb-visit" style="left:${l}%;width:${Math.max(0.8, r - l)}%"></div>`;
    }).join("");
    const marks = alertsFor(d).map((a) => `<div class="rb-mark ${a.severity}" style="left:${pos(a.created_at, ds)}%" title="${esc(titleOf(a))}, ${T(a.created_at, tzOf(d))}"></div>`).join("");
    const ticks = d.events.filter((e) => e.type === "motion_detected" || e.type === "button_press").map((e) =>
      `<div class="rb-tick ${e.disposition}" style="left:${pos(e.occurred_at, ds)}%" title="${T(e.occurred_at, tzOf(d))}: ${esc(ringSaid(e))}. ${esc(why(e))}"></div>`).join("");
    const x = pos(state.now, ds);
    const now = nowLineOn && x > 0 && x < 100 ? `<div class="rb-now" style="left:${x}%"></div>` : "";
    let extra = "", deadlines = "";
    if (big) {
      const hours = [6, 9, 12, 15, 18, 21, 24].map((h) => {
        const label = h === 24 ? "12 AM" : h === 12 ? "12 PM" : h > 12 ? `${h - 12} PM` : `${h} AM`;
        return `<span style="left:${((h - AXIS_START) / (AXIS_END - AXIS_START)) * 100}%">${label}</span>`;
      }).join("");
      const words = { cleaner_arrival: "Cleaner due", turnover_ready: "Ready by" };
      const dl = d.expectations.filter((e) => words[e.kind]);
      const flags = dl.map((e) => `<span class="rb-flag ${e.status}" style="left:${pos(e.deadline, ds)}%">${words[e.kind]} ${T(e.deadline, tzOf(d))}</span>`).join("");
      deadlines = dl.map((e) => `<div class="rb-dl ${e.status}" style="left:${pos(e.deadline, ds)}%"></div>`).join("");
      extra = `<div class="rb-hours">${hours}</div>${flags ? `<div class="rb-flags">${flags}</div>` : ""}`;
    }
    return `<div class="ribbon ${big ? "big" : ""}" aria-hidden="true"><div class="rb-track">${segs}${visits}${deadlines}${marks}${now}</div><div class="rb-ticks">${ticks}</div>${extra}</div>`;
  }

  const legend = `<div class="legend" aria-hidden="true">
      <span><i style="background:var(--stay)"></i>Guests staying</span>
      <span><i style="background:var(--turn)"></i>Turnover window</span>
      <span><i style="background:var(--visit);height:4px"></i>Cleaner at the door</span>
      <span><i style="background:#c8cdd5;width:3px;height:10px"></i>Kept quiet</span>
      <span><i style="background:var(--ink);width:3px;height:10px"></i>Told you</span>
    </div>`;

  // ---------------------------------------------------------------- views
  function viewToday() {
    const homes = state.properties;
    const needs = needsYou();
    const tz = tzOf();
    const h = hourOf(state.now, tz);
    const greet = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";

    const needHomes = [...new Set(needs.map((a) => a.property_id))].map(nameOf);
    let h1;
    if (needHomes.length) {
      const others = homes.length - needHomes.length;
      const list = needHomes.length === 1 ? needHomes[0] : `${needHomes.slice(0, -1).join(", ")} and ${needHomes.at(-1)}`;
      h1 = `<span>${esc(list)} ${needHomes.length === 1 ? "needs" : "need"} you.</span>` + (others ? `<span>The other ${word(others)} ${others === 1 ? "home is" : "homes are"} on track.</span>` : "");
    } else {
      h1 = `<span>All ${word(homes.length)} homes are on track.</span>`;
    }
    const turnovers = homes.filter((d) => d.bands.some((b) => b.kind === "turnover")).length;
    const checkins = homes.filter((d) => d.bands.some((b) => b.kind === "stay" && new Date(b.start) > new Date(d.day_start))).length;
    const plan = turnovers || checkins
      ? `Today has ${[turnovers && pluralW(turnovers, "turnover", "turnovers"), checkins && pluralW(checkins, "check-in", "check-ins")].filter(Boolean).join(" and ")}.`
      : "No turnovers or check-ins today.";
    const t = state.totals;
    const doors = t.ring_events
      ? `${cap(word(t.ring_events))} ${t.ring_events === 1 ? "thing has" : "things have"} happened at your doors so far, and ${t.suppressed === t.ring_events ? "all of them were" : `${word(t.suppressed)} ${t.suppressed === 1 ? "was" : "were"}`} routine, so Turnover kept ${t.suppressed === 1 ? "it" : "them"} quiet.`
      : "Nothing has happened at your doors yet.";

    const needsHtml = needs.length
      ? `<section class="needs" aria-label="Needs you">${needs.map(needCard).join("")}</section>`
      : `<div class="calm">${icon("check")}<span>Nothing needs you right now. Turnover will tell you if that changes.</span></div>`;

    const rows = homes.map((d, i) => `<a class="home-row" href="#/homes/${encodeURIComponent(d.property.id)}">
        ${thumb(d, i)}
        <div><div class="home-name">${esc(d.property.name)}</div><div class="home-city">${esc(d.property.city)}</div></div>
        <div class="home-now">${chip(d)}<span class="line">${esc(nowLine(d))}</span></div>
        ${ribbon(d)}
        <span class="chev">${icon("chevR")}</span>
      </a>`).join("");

    const rest = state.alerts.filter((a) => !needs.includes(a));
    const sent = outbound.slice(-1).map((o) => `<p class="sent">Sent to ${esc(o.to)}: “${esc(o.text)}”</p>`).join("");
    const updates = rest.length
      ? `<ul class="updates">${rest.map(updateItem).join("")}</ul>`
      : `<p class="empty">Quiet so far. Updates appear here as the day goes on.</p>`;

    return `
      <header class="hello scene ${ART.sky(h).dark ? "dark" : ""}">
        ${ART.scene(homes.map(artOf), h)}
        <div class="hello-text">
          <p class="greet">${greet}</p>
          <h1>${h1}</h1>
          <p class="sum">${esc(plan)} ${esc(doors)}</p>
          <ul class="stats" aria-label="Today in numbers">
            <li><b>${t.ring_events}</b> door events</li>
            <li><b>${t.suppressed}</b> kept quiet</li>
            <li><b>${state.alerts.length}</b> ${state.alerts.length === 1 ? "update" : "updates"} sent</li>
          </ul>
        </div>
      </header>
      <div class="today">
        <div class="stack">
          ${needsHtml}
          <section class="panel" aria-labelledby="homesTitle">
            <div class="panel-head"><h2 id="homesTitle">Your homes today</h2><span class="hint">6 AM to midnight</span></div>
            <div class="homes-list">${rows}</div>
            ${legend}
          </section>
        </div>
        <aside class="panel rail" aria-labelledby="updTitle">
          <div class="panel-head"><h2 id="updTitle">Updates</h2><span class="hint">${rest.length ? plural(rest.length, "update", "updates") : ""}</span></div>
          ${sent}${updates}
        </aside>
      </div>`;
  }

  function needCard(a) {
    const d = homeOf(a.property_id);
    const tz = tzOf(d);
    const img = a.snapshot_id ? `<img class="snap" src="/api/snapshots/${esc(a.snapshot_id)}" alt="Front-door snapshot from ${esc(nameOf(a.property_id))} at ${T(a.created_at, tz)}">` : "";
    const acts = a.actions.map((x, i) => `<button type="button" class="btn ${i === 0 ? "primary" : x.id === "dismiss" ? "quiet" : ""}" data-alert="${esc(a.id)}" data-act="${esc(x.id)}" data-label="${esc(x.label)}">${esc(x.label)}</button>`).join("");
    return `<article class="need ${a.severity} ${img ? "has-snap" : ""} ${seen.has(a.id) ? "" : "fresh"}">
        <div>
          <div class="need-top"><span class="chip ${a.severity === "urgent" ? "alert" : "warn"}">${SEV[a.severity]}</span><a class="plain-link strong" href="#/homes/${encodeURIComponent(a.property_id)}">${esc(nameOf(a.property_id))}</a><span>${ago(a.created_at, tz)}</span></div>
          <h3>${esc(titleOf(a))}</h3>
          <p class="body">${esc(a.body)}</p>
          ${visionLine(a.vision)}${trail(a, tz)}
        </div>
        ${img}
        ${acts ? `<div class="acts">${acts}</div>` : ""}
      </article>`;
  }

  function updateItem(a) {
    const tz = tzOf(homeOf(a.property_id));
    const closed = a.status !== "open";
    const acts = !closed && a.actions.length
      ? `<div class="acts">${a.actions.map((x, i) => `<button type="button" class="btn ${i === 0 ? "" : "quiet"}" data-alert="${esc(a.id)}" data-act="${esc(x.id)}" data-label="${esc(x.label)}">${esc(x.label)}</button>`).join("")}</div>` : "";
    const img = a.snapshot_id ? `<img class="snap" src="/api/snapshots/${esc(a.snapshot_id)}" alt="Front-door snapshot from ${esc(nameOf(a.property_id))}">` : "";
    return `<li class="up ${closed ? "closed" : ""} ${seen.has(a.id) || firstRender ? "" : "fresh"}">
        <span class="sev-dot ${a.severity}" aria-hidden="true"></span>
        <div>
          <p class="up-title">${esc(titleOf(a))}</p>
          <p class="up-meta">${esc(nameOf(a.property_id))}, ${T(a.created_at, tz)}${a.status === "resolved" ? ", resolved" : ""}</p>
          <p class="up-body">${esc(a.body)}</p>
          ${img}${visionLine(a.vision)}${trail(a, tz)}${acts}
        </div>
      </li>`;
  }

  function viewHomes() {
    const rows = state.properties.map((d, i) => {
      const p = d.property, tz = p.timezone;
      const next = d.state === "occupied" && d.current_stay
        ? `Checkout ${DATE(d.current_stay.checkout, tz)}<span class="sub">${T(d.current_stay.checkout, tz)}</span>`
        : d.next_stay ? `Check-in ${DATE(d.next_stay.checkin, tz)}<span class="sub">${esc(guests(d.next_stay))}, ${T(d.next_stay.checkin, tz)}</span>` : `<span class="sub">Nothing booked</span>`;
      const camLabel = d.camera_offline ? "Offline" : "Online";
      return `<tr class="clickable" data-href="#/homes/${encodeURIComponent(p.id)}">
          <td><div class="home-cell">${thumb(d, i)}<div><a href="#/homes/${encodeURIComponent(p.id)}" class="home-name plain-link">${esc(p.name)}</a><span class="sub">${esc(p.city)}</span></div></div></td>
          <td>${chip(d)}<span class="sub" style="margin-top:4px">${esc(nowLine(d))}</span></td>
          <td>${next}</td>
          <td><div class="person">${avatar(p.cleaner_name)}<div>${esc(p.cleaner_name)}<small>Backup: ${esc(p.backup_cleaner_name || "none")}</small></div></div></td>
          <td><span class="cam ${d.camera_offline ? "off" : ""}"><i aria-hidden="true"></i>${camLabel}</span></td>
        </tr>`;
    }).join("");
    return `
      <div class="page-head"><div><h1>Homes</h1><p>${plural(state.properties.length, "home", "homes")}, each read against its own booking calendar.</p></div></div>
      <section class="panel table-wrap">
        <table>
          <thead><tr><th scope="col">Home</th><th scope="col">Right now</th><th scope="col">Next</th><th scope="col">Cleaner</th><th scope="col">Door camera</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </section>`;
  }

  function viewHome(pid) {
    const d = homeOf(pid);
    if (!d) return `<a class="back" href="#/homes">${icon("chevL")}Homes</a><p>That home isn't in your account.</p>`;
    const p = d.property, tz = p.timezone;

    // the day as a story: alerts, with Ring's own events attached; quiet events folded together
    const alerts = alertsFor(d).slice().sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
    const events = d.events.slice().sort((a, b) => new Date(a.occurred_at) - new Date(b.occurred_at));
    const attached = new Set();
    const items = alerts.map((a) => {
      const ev = events.find((e) => !attached.has(e.id) && Math.abs(new Date(e.occurred_at) - new Date(a.created_at)) < 61000);
      if (ev) attached.add(ev.id);
      return { t: a.created_at, a, ev };
    });
    events.filter((e) => !attached.has(e.id)).forEach((e) => items.push({ t: e.occurred_at, e }));
    items.sort((x, y) => new Date(x.t) - new Date(y.t));

    const out = [];
    let group = [];
    const flush = () => {
      if (!group.length) return;
      const key = `${pid}:${group[0].e.id}`;
      if (group.length === 1) {
        const e = group[0].e;
        out.push(`<li class="st event"><time>${T(e.occurred_at, tz)}</time><span class="sev-dot quiet" aria-hidden="true"></span><div><p class="st-title">${esc(ringSaid(e))}</p><p class="st-meta">${esc(why(e))}</p></div></li>`);
      } else {
        out.push(`<li class="st event"><time>${T(group[0].e.occurred_at, tz)}</time><span class="sev-dot quiet" aria-hidden="true"></span><div>
          <details data-key="${esc(key)}" ${openGroups.has(key) ? "open" : ""}><summary>${cap(word(group.length))} routine events, kept quiet.</summary>
          <ul>${group.map(({ e }) => `<li><time>${T(e.occurred_at, tz)}</time><span>${esc(ringSaid(e))}. ${esc(why(e))}.</span></li>`).join("")}</ul></details></div></li>`);
      }
      group = [];
    };
    for (const it of items) {
      if (it.e && it.e.disposition === "suppressed") { group.push(it); continue; }
      flush();
      if (it.a) {
        const a = it.a;
        const img = a.snapshot_id ? `<img class="snap" src="/api/snapshots/${esc(a.snapshot_id)}" alt="Front-door snapshot at ${T(a.created_at, tz)}">` : "";
        const ring = it.ev ? `Ring reported: ${ringSaid(it.ev).toLowerCase()}.`
          : /no_show|missing|not_seen/.test(a.kind) ? "Nothing happened at the door. That's why Turnover told you."
          : /verified|short/.test(a.kind) ? "Checked against when Ring saw someone at the door during the turnover." : "";
        const acts = a.status === "open" && a.actions.length
          ? `<div class="acts">${a.actions.map((x, i) => `<button type="button" class="btn ${i === 0 ? "primary" : x.id === "dismiss" ? "quiet" : ""}" data-alert="${esc(a.id)}" data-act="${esc(x.id)}" data-label="${esc(x.label)}">${esc(x.label)}</button>`).join("")}</div>` : "";
        out.push(`<li class="st"><time>${T(a.created_at, tz)}</time><span class="sev-dot ${a.severity}" aria-hidden="true"></span><div>
          <p class="st-title">${esc(titleOf(a))}</p><p class="st-body">${esc(a.body)}</p>${ring ? `<p class="st-meta">${esc(ring)}</p>` : ""}
          ${img}${visionLine(a.vision)}${trail(a, tz)}${acts}</div></li>`);
      } else {
        const e = it.e;
        out.push(`<li class="st event"><time>${T(e.occurred_at, tz)}</time><span class="sev-dot ${e.disposition === "tracked" ? "tracked" : "quiet"}" aria-hidden="true"></span><div><p class="st-title">${esc(ringSaid(e))}</p><p class="st-meta">${esc(why(e))}</p></div></li>`);
      }
    }
    flush();
    const story = out.length ? `<ul class="story">${out.join("")}</ul>` : `<p class="empty">Nothing at the door yet today.</p>`;

    // side cards
    const stayRows = [];
    if (d.current_stay) stayRows.push(`<div><dt>Staying now</dt><dd>${esc(guests(d.current_stay))} until ${DATE(d.current_stay.checkout, tz)}, ${T(d.current_stay.checkout, tz)}</dd></div>`);
    if (d.last_stay) stayRows.push(`<div><dt>Checked out</dt><dd>${DATE(d.last_stay.checkout, tz)}, ${T(d.last_stay.checkout, tz)}</dd></div>`);
    if (d.next_stay) stayRows.push(`<div><dt>Next check-in</dt><dd>${esc(guests(d.next_stay))} on ${DATE(d.next_stay.checkin, tz)}, ${T(d.next_stay.checkin, tz)}</dd></div>`);
    if (!stayRows.length) stayRows.push(`<div><dd>Nothing booked.</dd></div>`);

    const visit = d.visits[d.visits.length - 1];
    const ready = exp(d, "turnover_ready");
    const visitLine = visit
      ? `<div><dt>Seen at the door today</dt><dd>${T(visit.first_at, tz)} to ${T(visit.last_at, tz)} (${Math.floor(visit.minutes / 60) ? `${Math.floor(visit.minutes / 60)} h ` : ""}${visit.minutes % 60} min)</dd></div>`
      : exp(d, "cleaner_arrival") ? `<div><dt>Today</dt><dd>Due by ${T(exp(d, "cleaner_arrival").deadline, tz)}</dd></div>` : "";
    const readyLine = ready ? `<div><dt>Ready check</dt><dd>${{ met: "Verified", missed: "Not confirmed", pending: `At ${T(ready.deadline, tz)}`, late: "Late" }[ready.status] || ready.status}</dd></div>` : "";

    const realDev = (id) => (state.ring?.devices || []).find((x) => x.id === id);
    const cams = p.device_ids.map((id) => {
      const rd = realDev(id);
      const name = rd?.name || "Front door camera";
      const enc = rd?.encrypted ? "Video is end-to-end encrypted, so no vision checks" : "Snapshots only when an alert needs one";
      return `<div class="row-between"><div>${esc(name)}<small class="sub" style="display:block;color:var(--ink-2);font-size:13px">${esc(enc)}</small></div><span class="cam ${d.camera_offline ? "off" : ""}"><i aria-hidden="true"></i>${d.camera_offline ? "Offline" : "Online"}</span></div>`;
    }).join("") || `<p class="hint">No camera assigned. Connect your Ring account to add one.</p>`;

    const brief = briefs[pid] ? `<div class="note-text">${esc(briefs[pid].text)}<small>Written by ${esc(briefs[pid].source.replace("bedrock:", "Amazon Bedrock, "))}</small></div>` : "";

    return `
      <a class="back" href="#/today">${icon("chevL")}Today</a>
      <header class="home-head scene ${ART.sky(hourOf(state.now, tz)).dark ? "dark" : ""}">
        ${ART.scene([artOf(d)], hourOf(state.now, tz), { single: true })}
        <div class="hello-text"><div class="head-row">${chip(d)}</div><h1>${esc(p.name)}</h1><p>${esc(p.city)}</p></div>
      </header>
      <section class="panel day-panel" aria-label="The day at ${esc(p.name)}">
        <div class="panel-head"><h2>${esc(nowLine(d))}</h2><span class="hint">${plural(d.metrics.ring_events, "door event", "door events")} today, ${d.metrics.suppressed} kept quiet</span></div>
        ${ribbon(d, { big: true })}
      </section>
      <div class="detail">
        <section class="panel" aria-labelledby="storyTitle">
          <div class="panel-head"><h2 id="storyTitle">Today, step by step</h2><span class="hint">What Ring saw, and what Turnover did</span></div>
          ${story}
        </section>
        <div class="cards">
          <section class="panel card"><h2>${icon("bed")}Stays</h2><dl>${stayRows.join("")}</dl></section>
          <section class="panel card"><h2>${icon("sparkle")}Cleaning</h2>
            <div class="person" style="margin-bottom:12px">${avatar(p.cleaner_name)}<div>${esc(p.cleaner_name)}<small>Backup: ${esc(p.backup_cleaner_name || "none")}</small></div></div>
            <dl>${visitLine}${readyLine}</dl></section>
          <section class="panel card"><h2>${icon("doorbell")}Door camera</h2>${cams}</section>
          <section class="panel card"><h2>${icon("note")}End of day note</h2>
            <p class="hint">A short summary of the day at ${esc(p.name)}, for you or a co-host.</p>
            <div class="acts"><button type="button" class="btn" id="briefBtn" data-pid="${esc(pid)}">Write today's note</button></div>${brief}</section>
        </div>
      </div>`;
  }

  function viewActivity() {
    const all = state.properties.flatMap((d) => d.events.map((e) => ({ e, d }))).sort((x, y) => new Date(y.e.occurred_at) - new Date(x.e.occurred_at));
    const counts = { all: all.length, alerted: 0, suppressed: 0, tracked: 0 };
    all.forEach(({ e }) => { if (counts[e.disposition] !== undefined) counts[e.disposition] += 1; });
    const shown = filter === "all" ? all : all.filter(({ e }) => e.disposition === filter);
    const chips = [["all", "Everything"], ["alerted", "Told you"], ["suppressed", "Kept quiet"], ["tracked", "Turnover visits"]]
      .map(([k, label]) => `<button type="button" class="filter" data-filter="${k}" aria-pressed="${filter === k}">${label}<b>${counts[k]}</b></button>`).join("");
    const told = (e, d) => state.alerts.find((a) => a.property_id === d.property.id && Math.abs(new Date(a.created_at) - new Date(e.occurred_at)) < 61000);
    const rows = shown.map(({ e, d }) => {
      const a = e.type === "device_online" ? null : told(e, d);
      const did = a ? `<span class="did told ${esc(a.severity)}">Told you: ${esc(titleOf(a))}</span>` : `<span class="did ${esc(e.disposition)}">${esc(why(e))}</span>`;
      return `<tr>
        <td class="time">${T(e.occurred_at, tzOf(d))}</td>
        <td><a class="plain-link" href="#/homes/${encodeURIComponent(d.property.id)}">${esc(d.property.name)}</a></td>
        <td>${esc(ringSaid(e))}</td>
        <td>${did}</td>
      </tr>`;
    }).join("");
    return `
      <div class="page-head"><div><h1>Activity</h1><p>Every event Ring reported today, and what Turnover did with it.</p></div><div class="filters" role="group" aria-label="Filter events">${chips}</div></div>
      <section class="panel table-wrap">
        ${shown.length ? `<table><thead><tr><th scope="col">Time</th><th scope="col">Home</th><th scope="col">Ring reported</th><th scope="col">Turnover</th></tr></thead><tbody>${rows}</tbody></table>`
          : `<p class="empty" style="padding:20px">No events here yet. Press Play the day to start the demo.</p>`}
      </section>`;
  }

  // ---------------------------------------------------------------- render
  function renderChrome() {
    const tz = tzOf();
    $("clockTime").textContent = T(state.now, tz);
    $("clockDay").textContent = `${DATE(state.now, tz)}${state.virtual_clock ? ", simulated" : ""}`;
    const p = state.player;
    $("demo").hidden = !p;
    if (p) {
      const label = p.running ? (p.paused ? "Resume" : "Pause") : (p.delivered ? "Play again" : "Play the day");
      $("playLabel").textContent = label;
      $("play").querySelector(".ico").outerHTML = icon(p.running && !p.paused ? "pause" : "play");
      document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(Math.abs(Number(b.dataset.speed) - p.minute_delay) < 1e-6)));
      $("pauseOnAlert").checked = p.pause_on_urgent;
    }
    $("navDot").hidden = !needsYou().length;
    $("ringNote").textContent = state.ring?.connected ? "Connected" : "";
    document.querySelectorAll(".nav a").forEach((a) => {
      const active = a.dataset.route === route.name || (route.name === "home" && a.dataset.route === "homes");
      if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }

  function render() {
    if (!state) return;
    renderChrome();
    const main = $("main");
    const html = route.name === "home" ? viewHome(route.id) : route.name === "homes" ? viewHomes() : route.name === "activity" ? viewActivity() : viewToday();
    main.innerHTML = html;
    state.alerts.forEach((a) => seen.add(a.id));
    firstRender = false;
    bindMain();
  }

  function bindMain() {
    const main = $("main");
    main.querySelectorAll("button[data-act]").forEach((b) => b.addEventListener("click", () => act(b)));
    main.querySelectorAll("tr[data-href]").forEach((tr) => tr.addEventListener("click", (ev) => { if (!ev.target.closest("a")) location.hash = tr.dataset.href; }));
    main.querySelectorAll("button[data-filter]").forEach((b) => b.addEventListener("click", () => { filter = b.dataset.filter; render(); }));
    main.querySelectorAll("details[data-key]").forEach((el) => el.addEventListener("toggle", () => { (el.open ? openGroups.add(el.dataset.key) : openGroups.delete(el.dataset.key)); }));
    const bb = $("briefBtn");
    if (bb) bb.addEventListener("click", async () => {
      bb.disabled = true; bb.textContent = "Writing…";
      try { briefs[bb.dataset.pid] = await api(`/api/properties/${encodeURIComponent(bb.dataset.pid)}/brief`, { method: "POST" }); }
      catch (e) { toast(`Couldn't write the note: ${e.message}`); }
      render();
    });
  }

  async function act(b) {
    b.disabled = true;
    try {
      await api(`/api/alerts/${encodeURIComponent(b.dataset.alert)}/actions/${encodeURIComponent(b.dataset.act)}`, { method: "POST" });
      if (b.dataset.act === "dismiss") toast("Got it. Marked as fine.");
      else if (!["text_backup", "message_guest"].includes(b.dataset.act)) toast(`Done: ${b.dataset.label}`);
      await refresh();
      if (state.player?.paused && !needsYou().length) { await api("/api/demo/resume", { method: "POST" }); refresh(); }
    } catch (e) { b.disabled = false; toast(e.message); }
  }

  // ---------------------------------------------------------------- io
  async function api(path, opts = {}) {
    const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  }
  async function refresh() { state = await api("/api/state"); render(); }
  function scheduleRefresh() { if (!pending) pending = setTimeout(() => { pending = null; refresh(); }, 120); }

  let toastTimer = null;
  function toast(text) {
    const t = $("toast");
    t.textContent = text; t.hidden = false;
    t.classList.remove("show"); void t.offsetWidth; t.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 4200);
  }

  const outbound = [];
  function connectStream() {
    const es = new EventSource("/api/stream");
    es.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.kind === "outbound") { outbound.push(msg.data); toast(`Sent to ${msg.data.to}: “${msg.data.text}”`); }
      if (msg.kind === "reset") { outbound.length = 0; seen.clear(); firstRender = true; Object.keys(briefs).forEach((k) => delete briefs[k]); }
      if (msg.kind === "clock" && state) { state.now = msg.data.now; state.player = { ...state.player, ...msg.data }; renderChrome(); }
      scheduleRefresh();
    };
    es.onerror = () => { es.close(); setTimeout(connectStream, 2000); };
  }

  function parseRoute() {
    const h = location.hash.replace(/^#\/?/, "");
    const [a, b] = h.split("/");
    if (a === "homes" && b) return { name: "home", id: decodeURIComponent(b) };
    if (a === "homes") return { name: "homes" };
    if (a === "activity") return { name: "activity" };
    return { name: "today" };
  }
  window.addEventListener("hashchange", () => { route = parseRoute(); render(); window.scrollTo(0, 0); $("main").focus({ preventScroll: true }); });

  // ---------------------------------------------------------------- demo controls
  $("play").addEventListener("click", async () => {
    const p = state?.player;
    if (!p) return;
    if (p.running && !p.paused) await api("/api/demo/pause", { method: "POST" });
    else if (p.running && p.paused) await api("/api/demo/resume", { method: "POST" });
    else {
      outbound.length = 0; seen.clear(); firstRender = true;
      const speed = Number(document.querySelector('.seg button[aria-pressed="true"]')?.dataset.speed || 0.12);
      await api("/api/demo/start", { method: "POST", body: JSON.stringify({ minute_delay: speed, pause_on_urgent: $("pauseOnAlert").checked }) });
      if (route.name !== "today") location.hash = "#/today";
    }
    refresh();
  });
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", async () => {
    document.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    if (state?.player) { state.player.minute_delay = Number(b.dataset.speed); await api("/api/demo/speed", { method: "POST", body: JSON.stringify({ minute_delay: Number(b.dataset.speed) }) }); }
  }));
  $("pauseOnAlert").addEventListener("change", () => api("/api/demo/speed", { method: "POST", body: JSON.stringify({ pause_on_urgent: $("pauseOnAlert").checked }) }));
  $("reset").addEventListener("click", async () => {
    outbound.length = 0; seen.clear(); firstRender = true;
    await api("/api/demo/reset", { method: "POST" });
    refresh();
  });

  // ---------------------------------------------------------------- Ring account
  const dlg = $("ringDialog");
  $("connect").addEventListener("click", () => { renderDevices(state?.ring?.devices || [], state?.ring?.account); dlg.showModal(); });
  $("closeDialog").addEventListener("click", () => dlg.close());
  $("doConnect").addEventListener("click", async () => {
    $("ringErr").textContent = "";
    const btn = $("doConnect");
    btn.disabled = true; btn.textContent = "Connecting…";
    try {
      const r = await api("/api/ring/playground", { method: "POST", body: JSON.stringify({ token: $("token").value }) });
      $("token").value = "";
      renderDevices(r.devices, r.account);
      refresh();
    } catch (e) { $("ringErr").textContent = e.message; }
    btn.disabled = false; btn.textContent = "Connect";
  });
  function renderDevices(devices, account) {
    if (!devices.length) { $("devices").innerHTML = ""; return; }
    const opts = state.properties.map((d) => `<option value="${esc(d.property.id)}">${esc(d.property.name)}</option>`).join("");
    $("devices").innerHTML = `<p class="hint">Connected${account ? ` to Ring account ${esc(account)}` : ""}. Choose which home each device is at.</p>` +
      devices.map((d) => {
        const at = state.properties.find((x) => x.property.device_ids.includes(d.id));
        return `<div class="device"><div>${esc(d.name)}<small>${d.online === false ? "Offline" : "Online"}${d.encrypted ? ", video end-to-end encrypted" : ""}${d.chime ? ", chime" : ""}</small></div>
          <select data-dev="${esc(d.id)}" aria-label="Home for ${esc(d.name)}"><option value="">Not used</option>${opts.replace(`value="${esc(at?.property.id)}"`, `value="${esc(at?.property.id)}" selected`)}</select></div>`;
      }).join("");
    $("devices").querySelectorAll("select[data-dev]").forEach((s) => s.addEventListener("change", async () => {
      if (!s.value) return;
      const prop = state.properties.find((d) => d.property.id === s.value).property;
      await api(`/api/properties/${encodeURIComponent(s.value)}/devices`, { method: "POST", body: JSON.stringify({ device_ids: [s.dataset.dev, ...prop.device_ids.filter((x) => x !== s.dataset.dev)] }) });
      toast(`Camera added to ${prop.name}`);
      refresh();
    }));
  }

  // ---------------------------------------------------------------- start
  hydrateIcons();
  route = parseRoute();
  refresh().then(connectStream);
})();
