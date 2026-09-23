/*
 * TaM Montpellier – departure board card.
 *
 * Shipped with the tam_montpellier integration, which loads it in the
 * frontend: no resource to declare, no dependency. Pick a stop in the
 * visual editor; the card finds that stop's sensors by itself.
 */

const CARD_VERSION = "0.3.0";
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

  // Line motifs: Garouste & Bonetti swallows (T1) and flowers (T2).
  const swallow = 'M44 0C38-5 28-7 17-6C8-19-8-36-32-44C-20-28-11-15-6-6L-46-20L-17 0L-46 20L-6 6C-11 15-20 28-32 44C-8 36 8 19 17 6C28 7 38 5 44 0Z';
  const petals = [0, 45, 90, 135, 180, 225, 270, 315]
    .map((r) => `<ellipse rx="11" ry="21" cy="-24" transform="rotate(${r})"/>`).join('');
  const motifs = {
    '1': `<g fill="${ink}"><path d="${swallow}" transform="translate(40 34) rotate(-14) scale(.42)"/><path d="${swallow}" transform="translate(96 20) scale(-.3 .3) rotate(-10)"/><path d="${swallow}" transform="translate(120 58) rotate(-24) scale(.24)"/></g>`,
    '2': `<g fill="${ink}"><g transform="translate(92 40)">${petals}<circle r="11"/></g><g transform="translate(34 22) scale(.45)">${petals}<circle r="11"/></g></g>`,
  };
  const motif = motifs[line] ? `<svg class="motif" viewBox="0 0 150 80" aria-hidden="true">${motifs[line]}</svg>` : '';

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
