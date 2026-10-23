/* Illustrations for Turnover: a drawing of each home, and a live scene of all of them.
 * Everything is inline SVG drawn for this app. The art reflects real state:
 * lit windows = guests staying, open door + sparkle = turnover, dark = vacant,
 * red badge = needs you, green tick = ready. The sky follows the (simulated) time of day. */
(() => {
  "use strict";

  const WIN = { occupied: "#ffd27a", turnover: "#ffe6b0", vacant: "#46536a" };
  const GLOW = { occupied: 0.9, turnover: 0.5, vacant: 0 };

  function windowRect(x, y, w, h, state, rx = 1.5) {
    const c = WIN[state] || WIN.vacant;
    const glow = GLOW[state] ? `<rect x="${x - 2}" y="${y - 2}" width="${w + 4}" height="${h + 4}" rx="${rx + 2}" fill="${c}" opacity="${GLOW[state] * 0.35}"/>` : "";
    return `${glow}<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${c}"/>`;
  }

  // Each home drawn on a 120 x 96 canvas, ground at y = 88.
  const HOMES = {
    cabin(s) { // Lakeview Cabin: A-frame among pines
      const door = s === "turnover" ? `<rect x="55" y="68" width="10" height="20" fill="#2b1d14"/><rect x="55" y="68" width="4" height="20" fill="#a86a43"/>` : `<rect x="55" y="68" width="10" height="20" rx="1" fill="#4a2e1d"/>`;
      return `
        <path d="M6 88 L16 54 L26 88Z M10 70 L16 46 L22 70Z" fill="#2f6b4f"/>
        <path d="M96 88 L106 50 L116 88Z M100 68 L106 42 L112 68Z" fill="#3c7d5c"/>
        <rect x="70" y="30" width="7" height="16" fill="#6b4630"/>
        <path d="M60 16 L102 88 L18 88Z" fill="#7a4a2f"/>
        <path d="M60 30 L88 88 L32 88Z" fill="#c98d5e"/>
        <path d="M60 16 L102 88" stroke="#4e2e1c" stroke-width="5" stroke-linecap="round"/>
        <path d="M60 16 L18 88" stroke="#4e2e1c" stroke-width="5" stroke-linecap="round"/>
        <path d="M60 40 L69 56 L51 56Z" fill="${WIN[s] || WIN.vacant}"/>
        ${windowRect(40, 66, 9, 9, s)}${windowRect(71, 66, 9, 9, s)}
        ${door}
        <rect x="14" y="86" width="96" height="3" rx="1.5" fill="#000" opacity=".08"/>`;
    },
    loft(s) { // Harbor Loft: brick warehouse by the water
      let wins = "";
      for (let r = 0; r < 4; r++) for (let c = 0; c < 3; c++) wins += windowRect(40 + c * 15, 20 + r * 14, 9, 9, (r + c) % 3 === 1 && s === "occupied" ? "turnover" : s, 1);
      const door = s === "turnover" ? `<rect x="54" y="76" width="12" height="12" fill="#2b2b33"/>` : `<rect x="54" y="76" width="12" height="12" rx="1" fill="#3b3f4c"/>`;
      return `
        <rect x="32" y="12" width="56" height="76" rx="2" fill="#b4573f"/>
        <rect x="32" y="12" width="56" height="76" rx="2" fill="url(#brick)" opacity=".25"/>
        <rect x="28" y="8" width="64" height="7" rx="2" fill="#7e3a2a"/>
        <rect x="74" y="0" width="6" height="10" fill="#7e3a2a"/>
        ${wins}${door}
        <path d="M0 90 q6 -4 12 0 t12 0 t12 0 M84 90 q6 -4 12 0 t12 0 t12 0" stroke="#5aa3c9" stroke-width="2.5" fill="none" stroke-linecap="round"/>
        <path d="M96 80 h18 l-4 6 h-12z" fill="#ffffff"/><path d="M104 80 v-14 l8 12z" fill="#f1f1f1"/>`;
    },
    bungalow(s) { // Juniper Bungalow: desert adobe with a palm
      const door = s === "turnover" ? `<path d="M52 88 v-14 a6 6 0 0 1 12 0 v14z" fill="#3a2418"/>` : `<path d="M52 88 v-14 a6 6 0 0 1 12 0 v14z" fill="#2f7f86"/>`;
      return `
        <path d="M100 88 C98 70 99 58 102 44" stroke="#8a6a45" stroke-width="4" fill="none" stroke-linecap="round"/>
        <path d="M102 44 q-14 -6 -22 4 M102 44 q-6 -14 -18 -14 M102 44 q10 -12 18 -6 M102 44 q14 0 16 12 M102 44 q4 -14 -2 -20" stroke="#3f8a5a" stroke-width="5" fill="none" stroke-linecap="round"/>
        <rect x="14" y="50" width="82" height="38" rx="3" fill="#f0cfa2"/>
        <rect x="10" y="44" width="90" height="8" rx="2" fill="#c47f53"/>
        <circle cx="18" cy="54" r="2" fill="#8a5232"/><circle cx="36" cy="54" r="2" fill="#8a5232"/><circle cx="74" cy="54" r="2" fill="#8a5232"/><circle cx="92" cy="54" r="2" fill="#8a5232"/>
        ${windowRect(24, 62, 14, 12, s, 6)}${windowRect(72, 62, 14, 12, s, 6)}
        ${door}
        <path d="M6 88 v-12 a4 4 0 0 1 8 0 v12 M6 80 h-3 v-6 M14 78 h3 v-5" stroke="#4f8d5b" stroke-width="3.5" fill="none" stroke-linecap="round"/>`;
    },
    studio(s) { // Cedar Studio: tiny modern box with a Joshua tree
      const door = s === "turnover" ? `<rect x="66" y="64" width="9" height="24" fill="#20242c"/>` : `<rect x="66" y="64" width="9" height="24" rx="1" fill="#e0a43a"/>`;
      return `
        <path d="M16 88 V62 M16 70 L8 58 V50 M16 66 L26 54 V46 M8 58 L4 52" stroke="#6b5640" stroke-width="3.5" fill="none" stroke-linecap="round"/>
        <circle cx="8" cy="48" r="5" fill="#6f8f57"/><circle cx="26" cy="44" r="5" fill="#6f8f57"/><circle cx="4" cy="50" r="3.5" fill="#7fa165"/><circle cx="16" cy="58" r="4" fill="#7fa165"/>
        <rect x="36" y="38" width="52" height="50" rx="3" fill="#ece6dc"/>
        <rect x="33" y="34" width="58" height="6" rx="2" fill="#2f3542"/>
        ${windowRect(42, 46, 20, 18, s, 1)}
        <path d="M52 46 v18" stroke="#2f3542" stroke-width="1.5" opacity=".5"/>
        ${door}
        <ellipse cx="100" cy="86" rx="9" ry="4" fill="#c9a98a"/><ellipse cx="108" cy="87" rx="5" ry="3" fill="#b8967a"/>`;
    },
    house(s) { // generic
      return `
        <path d="M60 18 L100 50 H20Z" fill="#5b6b86"/>
        <rect x="28" y="48" width="64" height="40" rx="2" fill="#e9edf3"/>
        ${windowRect(36, 58, 14, 12, s)}${windowRect(70, 58, 14, 12, s)}
        <rect x="54" y="66" width="12" height="22" rx="1" fill="#3b4a62"/>`;
    },
  };
  const KIND = { lakeview: "cabin", harbor: "loft", juniper: "bungalow", cedar: "studio" };
  const ORDER = ["cabin", "loft", "bungalow", "studio", "house"];

  function kindFor(id, i = 0) {
    if (KIND[id]) return KIND[id];
    const s = String(id || "");
    if (/cabin|lake|lodge|chalet/.test(s)) return "cabin";
    if (/loft|harbor|harbour|city|apt/.test(s)) return "loft";
    if (/bungalow|casa|desert|palm/.test(s)) return "bungalow";
    if (/studio|tiny|cedar/.test(s)) return "studio";
    return ORDER[i % ORDER.length];
  }

  function badge(mark) {
    if (mark === "alert") return `<g class="art-badge"><circle cx="104" cy="14" r="11" fill="#c93a2e" stroke="#fff" stroke-width="2.5"/><path d="M104 8 v7" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/><circle cx="104" cy="19.5" r="1.6" fill="#fff"/></g>`;
    if (mark === "ready") return `<g><circle cx="104" cy="14" r="11" fill="#1e7a4e" stroke="#fff" stroke-width="2.5"/><path d="m99 14 3.5 3.5 6.5-7" stroke="#fff" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g>`;
    if (mark === "turnover") return `<g fill="#f2b544"><path d="M100 6 l2 5 5 2 -5 2 -2 5 -2 -5 -5 -2 5 -2z"/><path d="M112 20 l1 3 3 1 -3 1 -1 3 -1 -3 -3 -1 3 -1z"/></g>`;
    return "";
  }

  const DEFS = `<defs><pattern id="brick" width="8" height="6" patternUnits="userSpaceOnUse"><path d="M0 0 h8 M0 3 h8 M4 0 v3 M0 3 v3" stroke="#5a2216" stroke-width=".6" fill="none"/></pattern></defs>`;

  /** A home on its own, for list thumbnails and banners. */
  function home(id, state, mark, i = 0) {
    const k = kindFor(id, i);
    return `<svg viewBox="0 0 120 96" aria-hidden="true" focusable="false">${DEFS}${HOMES[k](state)}${badge(mark)}</svg>`;
  }

  function sky(hour) {
    if (hour < 6 || hour >= 21) return { top: "#16203d", bottom: "#3a4777", hill1: "#2b3760", hill2: "#1f2a4c", dark: true, night: true };
    if (hour < 10) return { top: "#bfe0f4", bottom: "#fde9d2", hill1: "#bcd9bf", hill2: "#94c19d", dark: false };
    if (hour < 17) return { top: "#9fd2f1", bottom: "#e9f5fb", hill1: "#b6d8bd", hill2: "#86b993", dark: false };
    return { top: "#5f5a9e", bottom: "#f6b689", hill1: "#9b8aa8", hill2: "#6f6688", dark: true };
  }

  /** The hero scene in two layers: a sky and hills that fill the card, and the sun or moon
   *  plus the homes, kept to the right so they never sit behind the text. */
  function scene(homes, hour, { single = false } = {}) {
    const k = sky(hour);
    let stars = "";
    if (k.night) {
      for (let i = 0; i < 46; i++) {
        const x = (i * 211) % 1000, y = 8 + ((i * 97) % 130), r = (i % 3) * 0.45 + 0.6;
        stars += `<circle cx="${x}" cy="${y}" r="${r}" fill="#fff" opacity="${0.3 + (i % 4) * 0.15}"/>`;
      }
    }
    const back = `<svg class="scene-bg" viewBox="0 0 1000 240" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <defs><linearGradient id="skyg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${k.top}"/><stop offset="1" stop-color="${k.bottom}"/></linearGradient></defs>
      <rect width="1000" height="240" fill="url(#skyg)"/>
      <path d="M0 196 C160 168 300 182 450 176 S760 150 1000 168 V240 H0Z" fill="${k.hill1}"/>
      <path d="M0 220 C200 206 380 216 520 212 S820 200 1000 206 V240 H0Z" fill="${k.hill2}"/>
    </svg>`;

    const starLayer = stars ? `<svg class="scene-bg" viewBox="0 0 1000 240" preserveAspectRatio="xMidYMin slice" aria-hidden="true" focusable="false">${stars}</svg>` : "";
    const W = 500;
    const t = Math.max(0, Math.min(1, (hour - 6) / 15)); // 6 AM .. 9 PM
    let sunMoon;
    if (k.night) sunMoon = `<circle cx="${W - 70}" cy="50" r="20" fill="#f4efe0"/><circle cx="${W - 61}" cy="44" r="18" fill="${k.top}"/>`;
    else {
      const sx = 40 + t * (W - 80), sy = 120 - Math.sin(t * Math.PI) * 88;
      sunMoon = `<circle cx="${sx}" cy="${sy}" r="46" fill="#ffd36b" opacity=".2"/><circle cx="${sx}" cy="${sy}" r="21" fill="#ffcf5c"/>
        <g fill="#fff" opacity="${k.dark ? 0.35 : 0.85}"><ellipse cx="120" cy="40" rx="36" ry="10"/><ellipse cx="146" cy="33" rx="22" ry="10"/><ellipse cx="380" cy="72" rx="30" ry="8"/><ellipse cx="400" cy="66" rx="18" ry="8"/></g>`;
    }
    const n = homes.length;
    const size = single ? 170 : Math.min(118, (W - 20) / Math.max(1, n));
    const art = homes.map((h, i) => {
      const x = single ? W - size - 30 : 10 + i * ((W - 20 - size) / Math.max(1, n - 1)) + (n === 1 ? (W - 20 - size) / 2 : 0);
      const hgt = size * 0.8;
      const y = 222 - hgt + (single ? 0 : (i % 2) * 6) - 2;
      return `<svg x="${x}" y="${y}" width="${size}" height="${hgt}" viewBox="0 0 120 96">${HOMES[kindFor(h.id, i)](h.state)}${badge(h.mark)}</svg>`;
    }).join("");
    const front = `<svg class="scene-fg" viewBox="0 0 ${W} 240" preserveAspectRatio="xMaxYMax meet" aria-hidden="true" focusable="false">${DEFS}${sunMoon}${art}</svg>`;
    return back + starLayer + front;
  }

  window.TurnoverArt = { home, scene, sky, kindFor };
})();
