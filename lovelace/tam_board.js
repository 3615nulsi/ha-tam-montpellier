// Body of the button-card custom field (receives entity, states, variables).
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

const alertId = e.entity_id.replace('_minutes_avant_le_prochain_passage', '_message_de_perturbation');
const alertState = states[alertId];
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
