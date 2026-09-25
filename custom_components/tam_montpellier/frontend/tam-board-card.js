/*
 * TaM Montpellier – departure board card.
 *
 * Shipped with the tam_montpellier integration, which loads it in the
 * frontend: no resource to declare, no dependency. Pick a stop in the
 * visual editor; the card finds that stop's sensors by itself.
 */

const CARD_VERSION = "0.4.0";
const DOMAIN = "tam_montpellier";

const STYLES = `
:host { display: block; }
ha-card { overflow: hidden; }
.tam { text-align: left; color: var(--primary-text-color); font-family: var(--ha-font-family-body, inherit); }
.band { position: relative; overflow: hidden; display: flex; align-items: center; gap: 12px; padding: 14px 16px; background: var(--line); color: var(--ink); }
.motif { position: absolute; right: -6px; top: 0; height: 100%; opacity: .22; pointer-events: none; }
.pill { flex: none; width: 40px; height: 40px; border-radius: 50%; background: var(--ink); color: var(--line); display: grid; place-items: center; font-size: 22px; font-weight: 800; }
.where { min-width: 0; position: relative; }
.stop { font-size: 19px; font-weight: 700; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dir { font-size: 13px; opacity: .9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.next { display: flex; align-items: center; gap: 16px; padding: 16px 16px 12px; }
.count { flex: none; min-width: 92px; min-height: 52px; display: flex; align-items: baseline; gap: 4px; color: var(--accent); }
.count b { font-size: 52px; line-height: 1; font-weight: 800; font-variant-numeric: tabular-nums; }
.count span { font-size: 17px; font-weight: 700; }
.count.soon { align-items: center; font-size: 30px; font-weight: 800; animation: tam-blink 1.2s ease-in-out infinite; }
.info { min-width: 0; }
.dest { font-size: 18px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 4px; font-size: 12.5px; color: var(--secondary-text-color); }
.src { display: inline-flex; align-items: center; gap: 6px; }
.src.live i { width: 8px; height: 8px; border-radius: 50%; background: #2EAD4B; box-shadow: 0 0 0 0 rgba(46,173,75,.6); animation: tam-pulse 2s infinite; }
.src.sched, .src.est { padding: 1px 7px; border-radius: 99px; border: 1px solid var(--divider-color); }
.late { color: #D9480F; font-weight: 700; }
.none { padding: 20px 16px; color: var(--secondary-text-color); font-size: 15px; }
.chips { display: grid; grid-template-columns: repeat(auto-fill, minmax(84px, 1fr)); gap: 8px; padding: 0 16px 16px; }
.chip { border-radius: 10px; padding: 7px 10px; background: color-mix(in srgb, var(--line) 12%, transparent); border-left: 3px solid var(--line); min-width: 0; }
.chip b { display: block; font-size: 18px; font-weight: 800; font-variant-numeric: tabular-nums; }
.chip b small { font-size: 11px; font-weight: 700; margin-left: 2px; }
.chip span { display: block; font-size: 11.5px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chip.sched { border-left-style: dashed; }
.alert { display: flex; gap: 10px; align-items: flex-start; margin: 0 12px 12px; padding: 10px 12px; border-radius: 10px; background: color-mix(in srgb, #E8590C 13%, var(--card-background-color, #fff)); color: var(--primary-text-color); font-size: 13px; line-height: 1.35; }
.alert ha-icon { --mdc-icon-size: 20px; color: #E8590C; flex: none; }
@keyframes tam-pulse { 0% { box-shadow: 0 0 0 0 rgba(46,173,75,.55); } 70% { box-shadow: 0 0 0 7px rgba(46,173,75,0); } 100% { box-shadow: 0 0 0 0 rgba(46,173,75,0); } }
@keyframes tam-blink { 50% { opacity: .45; } }
`;

/** Sensors of a monitored stop, found through its device. */
function stopEntities(hass, deviceId) {
  const result = { minutes: undefined, alert: undefined };
  for (const entry of Object.values(hass.entities || {})) {
    if (entry.device_id !== deviceId || entry.platform !== DOMAIN) continue;
    const state = hass.states[entry.entity_id];
    if (!state) continue;
    if ("departures" in state.attributes) result.minutes = entry.entity_id;
    else if ("alert_count" in state.attributes) result.alert = entry.entity_id;
  }
  return result;
}

/** Devices (monitored stops) of the integration. */
function stopDevices(hass) {
  const ids = new Set(
    Object.values(hass.entities || {})
      .filter((entry) => entry.platform === DOMAIN && entry.device_id)
      .map((entry) => entry.device_id),
  );
  return [...ids].map((id) => hass.devices?.[id]).filter(Boolean);
}

// ---------------------------------------------------------------------------
// Line motifs, drawn as a watermark in the band, after each line's livery:
// T1 swallows and T2 flowers (Garouste & Bonetti), T3 sea creatures and T4
// Louis XIV sun with acanthus (Christian Lacroix), T5 leafy vine ("Feuille de
// vie", Barthélémy Toguo). `ink` draws the shapes; `bg`, the band colour,
// cuts details out of them.

const fmt = (v) => v.toFixed(1);
const lerp = (a, b, t) => a + (b - a) * t;
const DEG = Math.PI / 180;

/** Curve whose curvature (degrees per unit) follows k(t), width w(t). */
function sweep(x, y, angle, len, k, w, n = 60) {
  const pts = [];
  let a = angle * DEG;
  const ds = len / n;
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    pts.push([x, y, a, w(t)]);
    a += k(t) * DEG * ds;
    x += Math.cos(a) * ds;
    y += Math.sin(a) * ds;
  }
  return pts;
}

/** Outline of a swept curve, with distinct widths on each side. */
function ribbon(pts, left = (t, w) => w / 2, right = left) {
  const n = pts.length - 1;
  const side = (s, fn) => pts.map(([x, y, a, w], i) => {
    const d = s * fn(i / n, w);
    return `${fmt(x - Math.sin(a) * d)} ${fmt(y + Math.cos(a) * d)}`;
  });
  return `M${side(1, left).join('L')}L${side(-1, right).reverse().join('L')}Z`;
}

const path = (pts) => `<path d="${ribbon(pts)}"/>`;

/** Centre line of part of a swept curve. */
function spine(pts, from, to) {
  const n = pts.length - 1;
  return `M${pts.slice(Math.round(from * n), Math.round(to * n) + 1).map(([x, y]) => `${fmt(x)} ${fmt(y)}`).join('L')}`;
}

const SWALLOW = 'M44 0C38-5 28-7 17-6C8-19-8-36-32-44C-20-28-11-15-6-6L-46-20L-17 0L-46 20L-6 6C-11 15-20 28-32 44C-8 36 8 19 17 6C28 7 38 5 44 0Z';
const FISH = 'M-20 0C-10-12 12-12 20 0C12 12-10 12-20 0ZM-20 0L-32-11L-28 0L-32 11ZM12-2.5a2.2 2.2 0 1 0 .1 0Z';

function starPath(outer, inner) {
  let d = '';
  for (let i = 0; i < 10; i++) {
    const a = (-90 + 36 * i) * DEG, r = i % 2 ? inner : outer;
    d += `${i ? 'L' : 'M'}${fmt(r * Math.cos(a))} ${fmt(r * Math.sin(a))}`;
  }
  return `${d}Z`;
}

function swallows(ink) {
  const bird = (t) => `<path d="${SWALLOW}" transform="${t}"/>`;
  return `<g fill="${ink}">${bird('translate(40 34) rotate(-14) scale(.42)')}${bird('translate(96 20) scale(-.3 .3) rotate(-10)')}${bird('translate(120 58) rotate(-24) scale(.24)')}${bird('translate(76 62) scale(-.2 .2) rotate(-18)')}</g>`;
}

function flowers(ink, bg) {
  const petals = [0, 72, 144, 216, 288].map((r) => `<ellipse rx="12.5" ry="14" cy="-16" transform="rotate(${r})"/>`).join('');
  const flower = (t) => `<g transform="${t}">${petals}<circle r="9" fill="${bg}"/></g>`;
  return `<g fill="${ink}">${flower('translate(94 40) rotate(-8)')}${flower('translate(34 22) scale(.45) rotate(20)')}${flower('translate(52 62) scale(.3) rotate(40)')}</g>`;
}

function sea(ink) {
  return `<g fill="${ink}" fill-rule="evenodd"><path d="${FISH}" transform="translate(52 30) rotate(-10) scale(.85)"/><path d="${FISH}" transform="translate(96 60) scale(-.5 .5)"/><path d="${starPath(18, 7)}" stroke="${ink}" stroke-width="5" stroke-linejoin="round" transform="translate(124 26) rotate(12)"/><circle cx="76" cy="17" r="2.6"/><circle cx="83" cy="9" r="1.8"/></g>`;
}

function sun(ink, bg) {
  const out = [];
  const X = 75, Y = 39;
  // Acanthus leaf: smooth inside its curl, the outside cut into lobes.
  const leaf = (x, y, a, len, W, k, lobes = 3) => {
    const pts = sweep(x, y, a, len, k, () => 0, 80);
    const env = (t) => Math.pow(Math.sin(Math.PI * Math.min(1, 0.06 + t)), 0.7);
    const lobed = (t) => W * env(t) * (0.45 + 0.55 * Math.pow((t * lobes + 0.15) % 1, 1.6));
    const smooth = (t) => W * 0.32 * env(t) + 0.3;
    return `<path d="${k(0.5) > 0 ? ribbon(pts, lobed, smooth) : ribbon(pts, smooth, lobed)}"/>`;
  };
  // Rays, straight and flaming in turn, around the disc.
  for (let i = 0; i < 16; i++) {
    const a = (i / 16) * 2 * Math.PI, b = a + Math.PI / 16, w = 0.16;
    const at = (r, t) => `${fmt(X + r * Math.cos(t))} ${fmt(Y + r * Math.sin(t))}`;
    out.push(`<path d="M${at(10, a - w)}L${at(19, a)}L${at(10, a + w)}Z"/>`);
    out.push(path(sweep(X + 9.5 * Math.cos(b), Y + 9.5 * Math.sin(b), b / DEG, 9, (t) => 28 * Math.sin(2 * Math.PI * t), (t) => lerp(2.2, 0.3, t), 24)));
  }
  out.push(`<circle cx="${X}" cy="${Y}" r="9"/><circle cx="${X}" cy="${Y}" r="6.4" fill="none" stroke="${bg}" stroke-width="0.9"/>`);
  // Beaded medallion, crown and pendant.
  for (let i = 0; i < 36; i++) {
    const a = (i / 36) * 2 * Math.PI;
    out.push(`<circle cx="${fmt(X + 23 * Math.cos(a))}" cy="${fmt(Y + 23 * Math.sin(a))}" r="${i % 2 ? 0.9 : 1.4}"/>`);
  }
  out.push(`<path d="M${X - 8} 13.5H${X + 8}L${X + 9.5} 6.5L${X + 4.5} 10L${X} 4.5L${X - 4.5} 10L${X - 9.5} 6.5Z"/>`,
    `<circle cx="${X}" cy="4" r="1.3"/><circle cx="${X - 9.5}" cy="6" r="1.1"/><circle cx="${X + 9.5}" cy="6" r="1.1"/>`,
    `<circle cx="${X}" cy="65.5" r="1.3"/><path d="M${X} 68C${X + 4} 72 ${X + 3} 76 ${X} 77.5C${X - 3} 76 ${X - 4} 72 ${X} 68Z"/>`);
  // Acanthus rinceau on each side, rising into a volute.
  const side = [];
  const main = sweep(92, 56, 12, 96, (t) => -(1.2 + 15 * Math.pow(t, 2.6)), (t) => 1 + 2.4 * Math.pow(Math.sin(Math.PI * Math.min(1, t * 1.1)), 0.6), 100);
  side.push(path(main));
  const [ax, ay, aa] = main[30];
  side.push(path(sweep(ax, ay, aa / DEG + 30, 36, (t) => 1 + 24 * Math.pow(t, 2.2), (t) => lerp(2, 0.9, t), 60)));
  side.push(leaf(ax, ay, aa / DEG - 70, 20, 7, (t) => 4 + 16 * t * t, 3.5));
  const [bx, by, ba] = main[64];
  side.push(leaf(bx, by, ba / DEG + 80, 15, 5.5, (t) => 4 + 16 * t * t));
  side.push(leaf(96, 26, -35, 22, 6.5, (t) => 3 + 14 * t * t, 3.5));
  out.push(side.join(''), `<g transform="translate(150 0) scale(-1 1)">${side.join('')}</g>`);
  return `<g fill="${ink}">${out.join('')}</g>`;
}

function vine(ink, bg) {
  const out = [];
  const N = 130;
  const vein = (pts, from, to) => `<path d="${spine(pts, from, to)}" stroke="${bg}" stroke-width="0.9" fill="none" stroke-linecap="round"/>`;
  const coil = (x, y, a, len, dir, w0) => path(sweep(x, y, a, len, (u) => dir * (0.5 + 34 * Math.pow(u, 2.2)), (u) => lerp(w0, 0.8, u), 80));
  // Stem waving across the band, ending in a tendril.
  const stem = sweep(-2, 60, -32, N, (t) => 1.1 * Math.sin(2 * Math.PI * (1.1 * t + 0.02)), (t) => lerp(2.6, 1.4, t), N);
  out.push(path(stem));
  const [ex, ey, ea] = stem[N];
  out.push(coil(ex, ey, ea / DEG, 44, -1, 1.4));
  // Leaves on alternate sides, some melting into a coiling tendril:
  // [position, side, petiole, length, width, tendril, curl].
  for (const [t, s, pet, len, W, tail, curl] of [
    [0.14, -1, 4, 20, 8, 0, 0], [0.3, 1, 3, 20, 8, 22, 34], [0.47, -1, 3, 27, 11, 36, 36],
    [0.64, 1, 3, 19, 8, 0, 0], [0.76, -1, 3, 20, 8, 24, 34], [0.88, 1, 3, 17, 7, 0, 0],
  ]) {
    const [x, y, a] = stem[Math.round(t * N)];
    const total = pet + len + tail, L0 = pet / total, L1 = (pet + len) / total, bend = s * 0.5;
    const leaf = sweep(x, y, a / DEG + s * 50, total,
      (u) => (u < L1 ? bend : bend - s * curl * Math.pow((u - L1) / (1 - L1), 2)),
      (u) => (u < L0 ? 1.6 : u < L1 ? 1.4 + W * Math.pow(Math.sin(Math.PI * (u - L0) / (L1 - L0)), 0.9) : lerp(1.4, 0.8, (u - L1) / (1 - L1))),
      Math.max(60, Math.round(total)));
    out.push(path(leaf), vein(leaf, L0 + 0.03, L1 - 0.03));
  }
  for (const [t, s, len] of [[0.22, -1, 26], [0.56, -1, 24], [0.71, 1, 24]]) {
    const [x, y, a] = stem[Math.round(t * N)];
    out.push(coil(x, y, a / DEG + s * 38, len, s, 1.2));
  }
  return `<g fill="${ink}">${out.join('')}</g>`;
}

const MOTIFS = { '1': swallows, '2': flowers, '3': sea, '4': sun, '5': vine };
const motifCache = new Map();

/** Watermark of a line, drawn in `ink` over its `bg` colour. */
function lineMotif(line, ink, bg) {
  if (!MOTIFS[line]) return '';
  const key = `${line}|${ink}|${bg}`;
  if (!motifCache.has(key)) {
    motifCache.set(key, `<svg class="motif" viewBox="0 0 150 80" aria-hidden="true">${MOTIFS[line](ink, bg)}</svg>`);
  }
  return motifCache.get(key);
}

function render(entity, states, hass, variables, alertEntity) {
  const e = entity;
  if (!e) return '<div class="tam empty">Capteur introuvable</div>';
  const a = e.attributes;
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const color = a.line_color || '#005CA9';
  const ink = a.line_text_color || '#FFFFFF';
  const line = a.line || '?';
  // Line colors used as text are adjusted to stay readable on the card:
  // light ones (T3 lime) darkened on light themes, dark ones (T1 blue, T4
  // brown, T5 green) lightened on dark themes.
  const rgb = (color.match(/[0-9a-f]{2}/gi) || ['00', '00', '00']).map((h) => parseInt(h, 16) / 255);
  const luminance = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
  const darkTheme = Boolean(hass.themes && hass.themes.darkMode);
  let accent = color;
  if (!darkTheme && luminance > 0.62) accent = `color-mix(in srgb, ${color} 62%, #000)`;
  if (darkTheme && luminance < 0.4) accent = `color-mix(in srgb, ${color} 55%, #fff)`;
  // "Comédie → Mosson (Tram 1) Minutes avant le prochain passage"
  const title = (a.friendly_name || '').split(' (Tram')[0];
  const [stopName, direction] = title.includes(' → ') ? title.split(' → ') : [title, ''];
  const stop = variables.stop_name || stopName;
  const towards = variables.direction || direction;
  const deps = Array.isArray(a.departures) ? a.departures : [];

  const motif = lineMotif(line, ink, color);

  const sources = {
    realtime: '<span class="src live"><i></i>Temps réel</span>',
    estimated: '<span class="src est">Estimé</span>',
    scheduled: '<span class="src sched">Théorique</span>',
  };
  const delay = (d) => (d.delay >= 2 ? `<span class="late">+${d.delay} min</span>` : '');

  let next;
  const first = deps[0];
  if (!first || e.state === 'unknown' || e.state === 'unavailable') {
    next = '<div class="none">Pas de passage prévu</div>';
  } else {
    const minutes = Number(e.state);
    const count = minutes < 1
      ? '<div class="count soon">Proche</div>'
      : `<div class="count"><b>${minutes}</b><span>min</span></div>`;
    next = `<div class="next">${count}<div class="info">
        <div class="dest">${esc(first.destination)}</div>
        <div class="meta">${sources[first.source] || ''}${delay(first)}</div>
      </div></div>`;
  }

  const chips = deps.slice(1, 5).map((d) => `<div class="chip${d.source === 'scheduled' ? ' sched' : ''}">
      <b>${d.minutes < 1 ? 'Proche' : d.minutes + '<small>min</small>'}</b>
      <span>${esc(d.destination)}</span></div>`).join('');

  const alertState = alertEntity ? states[alertEntity] : undefined;
  const alertCount = alertState ? Number(alertState.attributes.alert_count || 0) : 0;
  const alert = alertCount > 0
    ? `<div class="alert"><ha-icon icon="mdi:alert"></ha-icon><span>${esc(alertState.attributes.full_message || alertState.state)}</span></div>`
    : '';

  return `<div class="tam" style="--line:${color};--ink:${ink};--accent:${accent}">
    <div class="band">${motif}
      <div class="pill">${esc(line)}</div>
      <div class="where"><div class="stop">${esc(stop)}</div><div class="dir">Direction ${esc(towards)}</div></div>
    </div>
    ${next}
    ${chips ? `<div class="chips">${chips}</div>` : ''}
    ${alert}
  </div>`;

}

class TamBoardCard extends HTMLElement {
  setConfig(config) {
    if (!config.device && !config.entity) {
      throw new Error("Choisissez un arrêt (device) ou un capteur (entity)");
    }
    this._config = config;
    this._signature = undefined;
  }

  set hass(hass) {
    this._hass = hass;
    let minutes = this._config.entity;
    let alert = this._config.alert_entity;
    const deviceId = this._config.device || hass.entities?.[minutes]?.device_id;
    if (deviceId) {
      const found = stopEntities(hass, deviceId);
      minutes = minutes || found.minutes;
      alert = alert || found.alert;
    }
    const entity = minutes ? hass.states[minutes] : undefined;
    const alertState = alert ? hass.states[alert] : undefined;
    const signature = [entity?.last_updated, alertState?.last_updated, hass.themes?.darkMode].join("|");
    if (signature === this._signature) return;
    this._signature = signature;
    this._minutes = minutes;
    this._render(entity, alert);
  }

  _render(entity, alert) {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `<style>${STYLES}</style><ha-card></ha-card>`;
      this.shadowRoot.querySelector("ha-card").addEventListener("click", () => {
        if (!this._minutes) return;
        this.dispatchEvent(new CustomEvent("hass-more-info", {
          bubbles: true, composed: true, detail: { entityId: this._minutes },
        }));
      });
    }
    const variables = { stop_name: this._config.name, direction: this._config.direction };
    this.shadowRoot.querySelector("ha-card").innerHTML =
      render(entity, this._hass.states, this._hass, variables, alert);
  }

  getCardSize() { return 4; }

  getGridOptions() { return { columns: 12, rows: "auto", min_columns: 6 }; }

  static getConfigElement() { return document.createElement("tam-board-card-editor"); }

  static getStubConfig(hass) {
    const device = stopDevices(hass)[0];
    return device ? { device: device.id } : { device: "" };
  }
}

class TamBoardCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) => ({
        device: "Arrêt",
        name: "Nom affiché (facultatif)",
        direction: "Direction affichée (facultatif)",
      })[schema.name];
      this._form.addEventListener("value-changed", (ev) => {
        const config = { ...this._config, ...ev.detail.value };
        for (const key of ["name", "direction"]) if (!config[key]) delete config[key];
        this.dispatchEvent(new CustomEvent("config-changed", {
          bubbles: true, composed: true, detail: { config },
        }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = [
      { name: "device", required: true, selector: { device: { integration: DOMAIN } } },
      { name: "name", selector: { text: {} } },
      { name: "direction", selector: { text: {} } },
    ];
  }
}

if (!customElements.get("tam-board-card")) {
  customElements.define("tam-board-card", TamBoardCard);
  customElements.define("tam-board-card-editor", TamBoardCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "tam-board-card",
    name: "TaM – Afficheur de quai",
    description: "Prochains trams d'un arrêt, aux couleurs de la ligne TaM.",
    preview: true,
    documentationURL: "https://github.com/3615nulsi/ha-tam-montpellier",
  });
  console.info(`%c TAM-BOARD-CARD %c ${CARD_VERSION} `, "color:#fff;background:#005CA9;font-weight:700", "color:#005CA9");
}
