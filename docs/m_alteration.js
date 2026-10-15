/* Alteration score (measure alteration): how much of the state text an outlet changed when it ran a
   state origin copy, from data/alteration.json (pipeline/copies.py). Renders into #copies-alteration:
   the status chip, one band bar per route, a ranked outlet table with n and median score, and a
   coverage line. Report only; no label or count changes. Never shows body text.
   Loaded in the browser after measures.js, and in Node or jsc with module.exports. */
(function (root, factory) {
  var M = root && root.TrackerMeasures;
  if (!M && typeof require === "function") M = require("./measures.js");
  var mod = factory(M);
  if (typeof module === "object" && module.exports) module.exports = mod;
}(typeof self !== "undefined" ? self : this, function (M) {
  "use strict";
  var ID = "alteration";
  var BANDS = ["verbatim", "light", "moderate", "substantial"];
  var BAND_TEXT = {verbatim: "Verbatim", light: "Light", moderate: "Moderate", substantial: "Substantial"};
  var BAND_HINT = {verbatim: "under 3% new words", light: "3 to 15%", moderate: "15 to 40%", substantial: "40% or more"};
  var REASONS = {
    no_state_credit: "no state publisher named",
    no_reference_source: "no reference source for the publisher",
    translation: "translations with no earlier copy in their language",
    no_reference_match: "no reference item matched",
    alignment_rejected: "candidates under 30% aligned"
  };

  function esc(s) { return M ? M.esc(s) : String(s === null || s === undefined ? "" : s); }
  function pct(x) { return typeof x === "number" ? Math.round(x * 100) + "%" : "n/a"; }
  function num(n) { return String(Math.round(n || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function plural(n, one, many) { return num(n) + " " + (n === 1 ? one : (many || one + "s")); }

  /* Band shares of a group, from its counts, or null when nothing in it was scored. */
  function shares(g) {
    var total = 0, out = {};
    BANDS.forEach(function (b) { total += ((g && g.bands) || {})[b] || 0; });
    if (!total) return null;
    BANDS.forEach(function (b) { out[b] = (g.bands[b] || 0) / total; });
    return out;
  }

  /* One stacked bar: the four bands of a group, with a text equivalent for screen readers. */
  function bar(label, g) {
    var s = shares(g);
    if (!s) return "";
    var segs = "", words = [];
    BANDS.forEach(function (b) {
      if (!s[b]) return;
      segs += '<span class="alt-seg alt-' + b + '" style="width:' + (Math.round(s[b] * 1000) / 10) + '%" title="' +
        esc(BAND_TEXT[b] + ": " + pct(s[b]) + " (" + g.bands[b] + ")") + '"></span>';
      words.push(BAND_TEXT[b].toLowerCase() + " " + pct(s[b]));
    });
    return '<div class="alt-row"><div class="alt-label">' + esc(label) + ' <span class="alt-n">n = ' + num(g.scored) + '</span></div>' +
      '<div class="alt-bar" role="img" aria-label="' + esc(label + ": " + words.join(", ")) + '">' + segs + '</div></div>';
  }

  function legend() {
    return '<div class="alt-legend">' + BANDS.map(function (b) {
      return '<span class="alt-key"><span class="alt-sw alt-' + b + '"></span>' + esc(BAND_TEXT[b]) + ' <span class="alt-n">' + esc(BAND_HINT[b]) + '</span></span>';
    }).join("") + '</div>';
  }

  function routeLabel(id, data, api) {
    var r = ((data && data.by_route) || []).filter(function (x) { return x.id === id; })[0];
    var metaRoutes = (api && api.meta && api.meta.routes) || [];
    var m = metaRoutes.filter(function (x) { return x.id === id; })[0];
    return (m && m.label) || (r && r.label) || id;
  }

  /* The coverage sentence: how many copies have an original, how many were scored, and why not. */
  function coverageLine(data) {
    var c = (data && data.coverage) || {}, n = c.copies || 0;
    if (!n) return "";
    var paired = (c.reference || 0) + (c.monitored || 0);
    var reasons = c.none_by_reason || {}, parts = [];
    Object.keys(reasons).sort(function (a, b) { return reasons[b] - reasons[a]; }).forEach(function (k) {
      parts.push(num(reasons[k]) + " " + (REASONS[k] || k.replace(/_/g, " ")));
    });
    return "Of " + plural(n, "state origin copy", "state origin copies") + ", " + num(paired) + " have an original (" +
      num(c.reference || 0) + " from a reference source, " + num(c.monitored || 0) + " an earlier monitored copy) and " +
      num(c.scored || 0) + " could be scored with both texts available." +
      (parts.length ? " No original: " + parts.join(", ") + "." : "") +
      (c.translation ? " In all " + plural(c.translation, "copy is a translation", "copies are translations") + " of state text first published in another language; they are never matched across languages." : "") +
      " Bodies are kept only where the tracker runs, so copies whose texts are missing are not scored.";
  }

  function outletTable(data, api) {
    var rows = (data && data.by_outlet) || [];
    if (!rows.length) return '<p class="m-empty">No outlet has ' + num((data && data.outlet_min_n) || 5) + ' scored copies yet.</p>';
    var cn = (api && api.countryName) || function (x) { return x; };
    return '<div class="m-table-wrap"><table class="m-table alt-table"><thead><tr><th>Outlet</th><th>Country</th>' +
      '<th class="num">n</th><th class="num">Median score</th><th class="num">Substantial</th><th class="num">Original kept</th></tr></thead><tbody>' +
      rows.map(function (r) {
        var s = shares(r) || {};
        return '<tr><td>' + esc(r.name || r.id) + '</td><td>' + esc(cn(r.country || "")) + '</td><td class="num">' + num(r.scored) +
          '</td><td class="num">' + pct(r.median_novel) + '</td><td class="num">' + pct(s.substantial) + '</td><td class="num">' + pct(r.median_kept) + '</td></tr>';
      }).join("") + '</tbody></table></div>';
  }

  /* The lead sentence over all scored copies. */
  function lead(data) {
    var t = (data && data.totals) || {};
    var s = shares(t);
    if (!s) return "";
    var hc = t.headline_compared || 0;
    return '<p class="alt-lead">Of ' + plural(t.scored, "copy", "copies") + ' scored against their original, ' +
      pct(s.verbatim) + ' ran the state text verbatim and ' + pct(s.substantial) + ' changed 40% or more of the words. ' +
      'The median copy added or rewrote ' + pct(t.median_novel) + ' of its words and kept ' + pct(t.median_kept) + ' of the original.' +
      (hc ? ' ' + pct((t.headline_rewritten || 0) / hc) + ' carry a rewritten headline.' : '') + '</p>';
  }

  function render(block, data, api) {
    var slot = api && api.slot;
    if (!slot) return;
    if (!M.hideIfOff(slot, block)) { slot.innerHTML = ""; return; }
    var head = '<h3 class="alt-title">Alteration score ' + M.statusChip(block) + '</h3>';
    var scored = data && data.totals && data.totals.scored;
    if (M.isWithheld(block) || !data || !scored) {
      var cov = data ? coverageLine(data) : "";
      slot.innerHTML = head + M.emptyState(block, data, "alteration score") + (cov && !M.isWithheld(block) ? '<p class="alt-coverage">' + esc(cov) + '</p>' : '');
      return;
    }
    var bars = bar("All routes", data.totals);
    ((data.by_route) || []).forEach(function (g) { if (g.scored) bars += bar(routeLabel(g.id, data, api), g); });
    slot.innerHTML = head + lead(data) +
      '<div class="alt-bars">' + bars + '</div>' + legend() +
      '<h4 class="alt-sub">Outlets by median score' + esc(M.provisionalSuffix(block)) + '</h4>' + outletTable(data, api) +
      '<p class="alt-coverage">' + esc(coverageLine(data)) + '</p>';
  }

  var spec = {render: render};
  if (M) M.register(ID, spec);
  return {ID: ID, render: render, shares: shares, coverageLine: coverageLine, BANDS: BANDS};
}));
