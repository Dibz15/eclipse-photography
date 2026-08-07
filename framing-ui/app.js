(function () {
  'use strict';

  const SUN_RADIUS_KM = 696000.0;
  const $ = (id) => document.getElementById(id);

  // ---- state ------------------------------------------------------------
  let observer = null;
  let times = null; // { c2, peak, c3, hasTotality }

  // ---- astronomy helpers -------------------------------------------------
  function sunHorizontal(time) {
    const equ = Astronomy.Equator(Astronomy.Body.Sun, time, observer, true, true);
    const hor = Astronomy.Horizon(time, observer, equ.ra, equ.dec, 'normal');
    return { alt: hor.altitude, az: hor.azimuth, distAU: equ.dist };
  }

  function solarDiameterDeg(time) {
    const equ = Astronomy.Equator(Astronomy.Body.Sun, time, observer, true, true);
    const distKm = equ.dist * Astronomy.KM_PER_AU;
    return 2 * Math.asin(SUN_RADIUS_KM / distKm) * Astronomy.RAD2DEG;
  }

  function fmtUTC(date) {
    return date.toISOString().substring(11, 19) + ' UTC';
  }

  // ---- step 1: find the eclipse ------------------------------------------
  $('btn-find').addEventListener('click', findEclipse);

  function findEclipse() {
    const status = $('find-status');
    status.textContent = 'Searching\u2026';
    try {
      const lat = parseFloat($('lat').value);
      const lon = parseFloat($('lon').value);
      const elev = parseFloat($('elev').value) || 0;
      const dateStr = $('date').value;
      if (!dateStr) throw new Error('Pick a date first.');

      observer = new Astronomy.Observer(lat, lon, elev);
      const searchStart = Astronomy.MakeTime(new Date(dateStr + 'T00:00:00Z')).AddDays(-1);
      const eclipse = Astronomy.SearchLocalSolarEclipse(searchStart, observer);

      const peakDate = eclipse.peak.time.date;
      const requested = new Date(dateStr + 'T00:00:00Z');
      const dayDiff = Math.abs((peakDate - requested) / 86400000);
      if (dayDiff > 1.5) {
        status.textContent = `No eclipse visible from here on ${dateStr}. Nearest one at this location peaks ` +
          `${peakDate.toISOString().substring(0, 10)}. Showing that instead.`;
      } else {
        status.textContent = `Found a ${eclipse.kind} eclipse.`;
      }

      const hasTotality = !!(eclipse.total_begin && eclipse.total_end);
      times = {
        c2: hasTotality ? eclipse.total_begin.time : eclipse.peak.time,
        peak: eclipse.peak.time,
        c3: hasTotality ? eclipse.total_end.time : eclipse.peak.time,
        c1: eclipse.partial_begin.time,
        c4: eclipse.partial_end.time,
        hasTotality,
        kind: eclipse.kind,
      };

      if (!hasTotality) {
        status.textContent += ' No totality at this location (partial/annular only) \u2014 the plot will center on max eclipse instead.';
      }

      populateTimesTable();
      $('card-times').hidden = false;
      $('card-camera').hidden = false;
      $('card-framing').hidden = false;
      $('card-readout').hidden = false;
      render();
    } catch (err) {
      status.textContent = 'Could not find an eclipse there: ' + (err.message || err);
    }
  }

  function populateTimesTable() {
    const rows = [
      ['C1 (partial begins)', times.c1],
      ['C2 (totality begins)', times.hasTotality ? times.c2 : null],
      ['Max eclipse', times.peak],
      ['C3 (totality ends)', times.hasTotality ? times.c3 : null],
      ['C4 (partial ends)', times.c4],
    ];
    let html = '<tr><th>Event</th><th>Time</th><th>Alt</th><th>Az</th></tr>';
    for (const [label, t] of rows) {
      if (!t) { html += `<tr><td class="event-name">${label}</td><td colspan="3">&mdash;</td></tr>`; continue; }
      const h = sunHorizontal(t);
      html += `<tr><td class="event-name">${label}</td><td>${fmtUTC(t.date)}</td>` +
        `<td>${h.alt.toFixed(2)}\u00b0</td><td>${h.az.toFixed(2)}\u00b0</td></tr>`;
    }
    $('times-table').innerHTML = html;
  }

  // ---- override handling ---------------------------------------------------
  $('override-toggle').addEventListener('change', (e) => {
    $('override-fields').hidden = !e.target.checked;
    render();
  });
  ['ov-c2', 'ov-max', 'ov-c3'].forEach((id) => $(id).addEventListener('change', render));

  function effectiveTimes() {
    if (!times) return null;
    if (!$('override-toggle').checked) return times;
    const dateStr = $('date').value;
    const mk = (val, fallback) => {
      if (!val) return fallback;
      return Astronomy.MakeTime(new Date(dateStr + 'T' + val + 'Z'));
    };
    return {
      c2: mk($('ov-c2').value, times.c2),
      peak: mk($('ov-max').value, times.peak),
      c3: mk($('ov-c3').value, times.c3),
      c1: times.c1, c4: times.c4,
      hasTotality: true,
      kind: times.kind,
    };
  }

  // ---- scale mode toggle ----------------------------------------------------
  document.querySelectorAll('input[name="scale-mode"]').forEach((r) =>
    r.addEventListener('change', () => {
      const mode = document.querySelector('input[name="scale-mode"]:checked').value;
      $('mode-focal').hidden = mode !== 'focal';
      $('mode-measured').hidden = mode !== 'measured';
      render();
    })
  );

  function pxPerDegree(centerTime) {
    const mode = document.querySelector('input[name="scale-mode"]:checked').value;
    if (mode === 'measured') {
      const px = parseFloat($('measured-px').value) || 1;
      const diam = solarDiameterDeg(centerTime);
      const scale = px / diam;
      $('scale-readout').textContent = `Sun's angular diameter right now: ${diam.toFixed(4)}\u00b0 \u2192 ${scale.toFixed(0)} px/degree.`;
      return scale;
    }
    const f = parseFloat($('focal-eq').value) || 1;
    const fovWideDeg = 2 * Math.atan(36 / (2 * f)) * Astronomy.RAD2DEG;
    const wideRes = parseFloat($('res-wide').value) || 1;
    const scale = wideRes / fovWideDeg;
    $('scale-readout').textContent = `Assumes a 36\u00d724mm full-frame reference for the equivalence \u2192 ` +
      `${fovWideDeg.toFixed(2)}\u00b0 field of view on the wide side, ${scale.toFixed(0)} px/degree.`;
    return scale;
  }

  // ---- wire up all remaining inputs to re-render --------------------------
  [
    'res-wide', 'res-short', 'orientation', 'focal-eq', 'measured-px',
    'center-on', 'pre-min', 'post-min', 'corona-mult', 'hill-deg',
  ].forEach((id) => $(id).addEventListener('input', render));
  $('aim-offset').addEventListener('input', () => {
    $('aim-offset-readout').textContent = `${$('aim-offset').value} px`;
    render();
  });
  $('scrub').addEventListener('input', render);
  $('btn-download-svg').addEventListener('click', downloadSVG);
  $('btn-download-png').addEventListener('click', downloadPNG);

  // ---- core render ----------------------------------------------------------
  function render() {
    const t = effectiveTimes();
    if (!t || !observer) return;

    const wide = parseFloat($('res-wide').value) || 6000;
    const short = parseFloat($('res-short').value) || 4000;
    const portrait = $('orientation').value === 'portrait';
    const W = portrait ? short : wide;
    const H = portrait ? wide : short;

    const centerChoice = $('center-on').value;
    const centerTime = centerChoice === 'c2' ? t.c2 : centerChoice === 'c3' ? t.c3 : t.peak;
    const centerHz = sunHorizontal(centerTime);
    const scale = pxPerDegree(centerTime);
    const aimOffset = parseFloat($('aim-offset').value) || 0;
    const sunRadiusPx = (solarDiameterDeg(centerTime) / 2) * scale;
    const coronaRadiusPx = sunRadiusPx * (parseFloat($('corona-mult').value) || 1);
    const hillDeg = parseFloat($('hill-deg').value) || 0;

    function toXY(alt, az) {
      const dAz = az - centerHz.az;
      const dAlt = alt - centerHz.alt;
      return [W / 2 + dAz * scale, H / 2 - dAlt * scale - aimOffset];
    }
    const horizonY = toXY(0, centerHz.az)[1];
    const hillPeakY = horizonY - hillDeg * scale;

    const preMin = parseFloat($('pre-min').value) || 0;
    const postMin = parseFloat($('post-min').value) || 0;
    const startTime = t.c2.AddDays(-preMin / 1440);
    const endTime = t.c3.AddDays(postMin / 1440);
    const totalDays = endTime.ut - startTime.ut;

    const N = 48;
    const samples = [];
    for (let i = 0; i <= N; i++) {
      const st = totalDays !== 0 ? startTime.AddDays((totalDays * i) / N) : startTime;
      const hz = sunHorizontal(st);
      const [x, y] = toXY(hz.alt, hz.az);
      samples.push({ time: st, alt: hz.alt, az: hz.az, x, y });
    }

    const keyEvents = [
      ['T-start', startTime],
      ['C2', t.c2],
      ['Max', t.peak],
      ['C3', t.c3],
      ['T-end', endTime],
    ].map(([label, time]) => {
      const hz = sunHorizontal(time);
      const [x, y] = toXY(hz.alt, hz.az);
      return { label, time, alt: hz.alt, az: hz.az, x, y };
    });

    const clipped = keyEvents.filter((p) => isClipped(p.x, p.y, sunRadiusPx, W, H));

    // scrub marker
    const frac = parseFloat($('scrub').value) / 1000;
    const scrubTime = totalDays !== 0 ? startTime.AddDays(totalDays * frac) : startTime;
    const scrubHz = sunHorizontal(scrubTime);
    const [scrubX, scrubY] = toXY(scrubHz.alt, scrubHz.az);
    $('scrub-readout').textContent = `${fmtUTC(scrubTime.date)} \u00b7 alt ${scrubHz.alt.toFixed(2)}\u00b0 \u00b7 az ${scrubHz.az.toFixed(2)}\u00b0`;

    drawSVG({ W, H, samples, keyEvents, sunRadiusPx, coronaRadiusPx, horizonY, hillPeakY, hillDeg, scrubX, scrubY, portrait });
    populateReadout(keyEvents, sunRadiusPx, W, H);

    $('clip-warning').textContent = clipped.length
      ? `Off frame: ${clipped.map((c) => c.label).join(', ')} \u2014 widen the window, reduce focal length, or adjust the aim offset.`
      : '';
  }

  function isClipped(x, y, r, W, H) {
    return x - r < 0 || x + r > W || y - r < 0 || y + r > H;
  }

  function populateReadout(keyEvents, r, W, H) {
    let html = '<tr><th>Event</th><th>Time</th><th>Alt</th><th>Az</th><th>Frame margin</th></tr>';
    for (const p of keyEvents) {
      const margins = [p.x - r, W - (p.x + r), p.y - r, H - (p.y + r)];
      const minMargin = Math.min(...margins);
      const clippedRow = minMargin < 0;
      html += `<tr class="${clippedRow ? 'clipped' : ''}"><td class="event-name">${p.label}</td>` +
        `<td>${fmtUTC(p.time.date)}</td><td>${p.alt.toFixed(2)}\u00b0</td><td>${p.az.toFixed(2)}\u00b0</td>` +
        `<td>${clippedRow ? 'CLIPPED' : Math.round(minMargin) + ' px'}</td></tr>`;
    }
    $('readout-table').innerHTML = html;
  }

  // ---- SVG drawing ------------------------------------------------------------
  function drawSVG(d) {
    const frameDiagW = 260;
    const s = frameDiagW / d.W;
    const frameDiagH = d.H * s;
    const frameX = 30, frameY = 30;
    const toD = (x, y) => [frameX + x * s, frameY + y * s];

    const vbW = 660;
    const vbH = Math.max(frameY + frameDiagH + 40, 400);

    let svg = `<svg width="100%" viewBox="0 0 ${vbW} ${vbH}" role="img" xmlns="http://www.w3.org/2000/svg">`;
    svg += `<title>Sun framing preview</title><desc>Preview of the sun's path across the camera frame during the eclipse window.</desc>`;
    svg += `<rect x="${frameX}" y="${frameY}" width="${frameDiagW}" height="${frameDiagH}" fill="none" stroke="#3a4066" stroke-width="1" rx="3"/>`;
    svg += `<text x="${frameX}" y="${frameY - 10}" fill="#9a9cb5" font-size="11" font-family="monospace">${d.W}\u00d7${d.H}px, ${d.portrait ? 'portrait' : 'landscape'}</text>`;

    // hills / horizon, clipped to frame rect
    const clipId = 'fclip';
    svg += `<clipPath id="${clipId}"><rect x="${frameX}" y="${frameY}" width="${frameDiagW}" height="${frameDiagH}"/></clipPath>`;
    const [hx0, hy] = toD(0, d.horizonY);
    const [hx1] = toD(d.W, d.horizonY);
    const [, hillY] = toD(0, d.hillPeakY);
    svg += `<g clip-path="url(#${clipId})">`;
    if (d.hillDeg > 0) {
      svg += `<path d="M${hx0},${frameY + frameDiagH} L${hx0},${hy} Q${(hx0 + hx1) / 2},${hillY} ${hx1},${hy} L${hx1},${frameY + frameDiagH} Z" fill="#2c3253" opacity="0.6"/>`;
    }
    svg += `<line x1="${hx0}" y1="${hy}" x2="${hx1}" y2="${hy}" stroke="#6c6f8e" stroke-width="0.75" stroke-dasharray="3 3"/>`;
    svg += `</g>`;

    // corona clearance at peak
    const peak = d.keyEvents.find((k) => k.label === 'Max');
    if (peak) {
      const [px, py] = toD(peak.x, peak.y);
      svg += `<circle cx="${px}" cy="${py}" r="${d.coronaRadiusPx * s}" fill="none" stroke="#6c6f8e" stroke-width="0.75" stroke-dasharray="3 3"/>`;
    }

    // path
    let pathD = '';
    d.samples.forEach((pt, i) => {
      const [x, y] = toD(pt.x, pt.y);
      pathD += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1) + ' ';
    });
    svg += `<path d="${pathD}" fill="none" stroke="#6c6f8e" stroke-width="1" stroke-dasharray="4 3"/>`;

    // key event markers
    const labelYs = [70, 145, 220, 295, 370];
    d.keyEvents.forEach((k, i) => {
      const [x, y] = toD(k.x, k.y);
      const r = Math.max(d.sunRadiusPx * s, 2.5);
      const isPeak = k.label === 'Max';
      svg += `<circle cx="${x}" cy="${y}" r="${r}" fill="${isPeak ? '#e8734c' : '#f2a23c'}" fill-opacity="0.3" stroke="${isPeak ? '#e8734c' : '#f2a23c'}" stroke-width="1"/>`;
      const ly = labelYs[i];
      svg += `<line x1="${x}" y1="${y}" x2="${vbW - 160}" y2="${ly}" stroke="#6c6f8e" stroke-width="0.75" stroke-dasharray="2 2"/>`;
      svg += `<text x="${vbW - 154}" y="${ly - 4}" fill="#e7e6de" font-size="13" font-family="sans-serif">${k.label} \u00b7 ${fmtUTC(k.time.date)}</text>`;
      svg += `<text x="${vbW - 154}" y="${ly + 12}" fill="#9a9cb5" font-size="11" font-family="sans-serif">alt ${k.alt.toFixed(2)}\u00b0, az ${k.az.toFixed(2)}\u00b0</text>`;
    });

    // live scrub marker
    const [sx, sy] = toD(d.scrubX, d.scrubY);
    svg += `<circle cx="${sx}" cy="${sy}" r="4" fill="#ffffff" stroke="#10131f" stroke-width="1"/>`;

    svg += `</svg>`;
    $('svg-wrap').innerHTML = svg;
  }

  function downloadSVG() {
    const svgEl = document.querySelector('#svg-wrap svg');
    if (!svgEl) return;
    const blob = new Blob([svgEl.outerHTML], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'eclipse-frame-plan.svg';
    a.click();
    URL.revokeObjectURL(url);
  }

  function downloadPNG() {
    const svgEl = document.querySelector('#svg-wrap svg');
    if (!svgEl) return;
    const vb = svgEl.viewBox.baseVal;
    const w = (vb && vb.width) || svgEl.clientWidth || 660;
    const h = (vb && vb.height) || svgEl.clientHeight || 400;
    const scale = 2; // render at 2x the diagram's own coordinate space for crispness

    const xml = new XMLSerializer().serializeToString(svgEl);
    const svgBlob = new Blob([xml], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(svgBlob);

    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = w * scale;
      canvas.height = h * scale;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#10131f';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      canvas.toBlob((blob) => {
        if (!blob) return;
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'eclipse-frame-plan.png';
        a.click();
      }, 'image/png');
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      alert('PNG export failed \u2014 try Download SVG instead.');
    };
    img.src = url;
  }

  // initial UI wiring for hidden sections
  $('mode-focal').hidden = false;
  $('mode-measured').hidden = true;
})();