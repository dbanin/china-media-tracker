/* Alteration score (measure alteration): how much of the state text an outlet changed when it ran a
   state origin copy, from data/alteration.json (pipeline/copies.py). Renders into #copies-alteration:
   the status chip, a band bar for each kind of original (the state outlet's own page, or an earlier
   monitored copy, never pooled in the lead) and one per route, a ranked outlet table with n, distinct
   originals and median score, a coverage line and the reference sources' fetch status. Lead shares
   count each underlying item once, with the copies beside them. Report only; no label or count
   changes. Never shows body text.
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
  var SOURCES = {ref_xinhua_en: "Xinhua English", ref_cgtn_en: "CGTN", ref_china_daily_en: "China Daily",
                 ref_global_times_en: "Global Times", ref_peoples_daily_en: "People's Daily English",
                 ref_ecns_en: "ECNS"};

  function esc(s) { return M ? M.esc(s) : String(s === null || s === undefined ? "" : s); }
  function pct(x) { return typeof x === "number" ? Math.round(x * 100) + "%" : "n/a"; }
  function num(n) { return String(Math.round(n || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function plural(n, one, many) { return num(n) + " " + (n === 1 ? one : (many || one + "s")); }

  /* Band shares of a group, from its copy counts (or, with byItems, its item weights, each underlying
     item counted once), or null when nothing in it was scored. */
  function shares(g, byItems) {
    var src = (g && (byItems ? g.item_bands : g.bands)) || {}, total = 0, out = {};
    BANDS.forEach(function (b) { total += src[b] || 0; });
    if (!total) return null;
    BANDS.forEach(function (b) { out[b] = (src[b] || 0) / total; });
    return out;
  }

  /* One stacked bar: the four bands of a group, with a text equivalent for screen readers. byItems
     weighs each underlying item once and says so beside the copies. */
  function bar(label, g, byItems) {
    var s = shares(g, byItems);
    if (!s) return "";
    var segs = "", words = [];
    BANDS.forEach(function (b) {
      if (!s[b]) return;
      segs += '<span class="alt-seg alt-' + b + '" style="width:' + (Math.round(s[b] * 1000) / 10) + '%" title="' +
        esc(BAND_TEXT[b] + ": " + pct(s[b]) + (byItems ? "" : " (" + g.bands[b] + ")")) + '"></span>';
      words.push(BAND_TEXT[b].toLowerCase() + " " + pct(s[b]));
    });
    var n = byItems ? plural(g.items, "item") + ", " + plural(g.scored, "copy", "copies") : "n = " + num(g.scored);
    return '<div class="alt-row"><div class="alt-label">' + esc(label) + ' <span class="alt-n">' + esc(n) + '</span></div>' +
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
    return "Of " + plural(n, "state origin copy", "state origin copies") +
      (typeof c.items === "number" ? " (" + plural(c.items, "underlying item") + ")" : "") + ", " + num(paired) + " have an original (" +
      num(c.reference || 0) + " from a reference source, " + num(c.monitored || 0) + " an earlier monitored copy" +
      (typeof c.originals === "number" ? "; " + plural(c.originals, "distinct original") : "") + ") and " +
      num(c.scored || 0) + " could be scored with both texts available." +
      (parts.length ? " No original: " + parts.join(", ") + "." : "") +
      (c.sister_only ? " " + plural(c.sister_only, "copy", "copies") + " had an earlier copy only at a sister outlet of the same media group, which is the same placement and is not taken as an original." : "") +
      (c.translation ? " In all " + plural(c.translation, "copy is a translation", "copies are translations") + " of state text first published in another language; they are never matched across languages." : "") +
      " Bodies are kept only where the tracker runs, so copies whose texts are missing are not scored.";
  }

  /* The reference sources' fetch status: why reference originals are few, source by source. */
  function sourcesLine(data) {
    var rows = (data && data.reference_sources) || [];
    if (!rows.length) return "";
    return "State outlets' own pages read as originals: " + rows.map(function (d) {
      var st = d.statuses || {}, bits = [num(st.fetched) + " of " + num(d.items) + " fetched"];
      if (st.expired) bits.push(num(st.expired) + " expired unread");
      if (st.failed) bits.push(num(st.failed) + " failed");
      if (st.paywalled) bits.push(num(st.paywalled) + " paywalled");
      if ((d.flags || []).indexOf("date only after day one") !== -1) bits.push(pct(d.share_date_only_after_day_one) + " still date only after a day");
      return (SOURCES[d.source] || d.source) + " " + bits.join(", ");
    }).join("; ") + ".";
  }

  function outletTable(data, api) {
    var rows = (data && data.by_outlet) || [];
    if (!rows.length) return '<p class="m-empty">No outlet has scored copies of ' + num((data && data.outlet_min_n) || 5) + ' distinct originals yet.</p>';
    var cn = (api && api.countryName) || function (x) { return x; };
    return '<div class="m-table-wrap"><table class="m-table alt-table"><thead><tr><th>Outlet</th><th>Country</th>' +
      '<th class="num">n</th><th class="num">Originals</th><th class="num">Median score</th><th class="num">Substantial</th><th class="num">Original kept</th></tr></thead><tbody>' +
      rows.map(function (r) {
        var s = shares(r) || {};
        return '<tr><td>' + esc(r.name || r.id) + '</td><td>' + esc(cn(r.country || "")) + '</td><td class="num">' + num(r.scored) +
          '</td><td class="num">' + (typeof r.originals === "number" ? num(r.originals) : "n/a") +
          '</td><td class="num">' + pct(r.median_novel) + '</td><td class="num">' + pct(s.substantial) + '</td><td class="num">' + pct(r.median_kept) + '</td></tr>';
      }).join("") + '</tbody></table></div>' +
      '<p class="alt-note">Outlets with copies of at least ' + num((data && data.outlet_min_n) || 5) + ' distinct originals. n counts copies, Originals the distinct texts they were compared with.</p>';
  }

  /* One kind of original in words: shares over distinct underlying items, with copies and originals beside them. */
  function kindSentence(g, kind) {
    var s = shares(g, true);
    if (!s) return "";
    var counts = plural(g.items, "distinct item") + ", " + plural(g.scored, "copy", "copies") + ", " + plural(g.originals, "original");
    if (kind === "reference")
      return "Against the state outlet's own text (" + counts + "), " + pct(s.verbatim) + " of the items were run verbatim and " +
        pct(s.substantial) + " had 40% or more of the words changed; the median item kept " + pct(g.median_kept_items) + " of the state text.";
    return "Against an earlier copy of the same item at another monitored outlet, outside the copy's own media group (" + counts + "), " +
      pct(s.verbatim) + " of the items repeated that copy verbatim and " + pct(s.substantial) + " changed 40% or more of its words.";
  }

  /* The lead: each kind of original apart, the state text first; shares count each underlying item once. */
  function lead(data) {
    var t = (data && data.totals) || {}, ag = t.against;
    var hc = t.headline_compared || 0;
    var tail = hc ? ' ' + pct((t.headline_rewritten || 0) / hc) + ' of the ' + plural(hc, "copy", "copies") + ' with a headline compared carry a rewritten headline.' : '';
    if (!ag) {
      var s = shares(t);
      if (!s) return "";
      return '<p class="alt-lead">Of ' + plural(t.scored, "copy", "copies") + ' scored against an original (a state outlet\'s page or an earlier monitored copy), ' +
        pct(s.verbatim) + ' repeated it verbatim and ' + pct(s.substantial) + ' changed 40% or more of the words.' + tail + '</p>';
    }
    var ref = ag.reference || {}, mon = ag.monitored || {}, parts = [];
    if (ref.items) parts.push(kindSentence(ref, "reference"));
    else parts.push("No copy has been scored against the state outlet's own text yet, so these figures do not say how much of the state text outlets kept.");
    if (mon.items) parts.push(kindSentence(mon, "monitored"));
    return '<p class="alt-lead">' + esc(parts.join(" ")) + esc(tail) + '</p>';
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
    var ag = data.totals.against || {}, bars = "";
    if (ag.reference && ag.reference.items) bars += bar("Against the state outlet's own text", ag.reference, true);
    if (ag.monitored && ag.monitored.items) bars += bar("Against an earlier monitored copy", ag.monitored, true);
    var routes = "";
    ((data.by_route) || []).forEach(function (g) { if (g.scored) routes += bar(routeLabel(g.id, data, api), g); });
    var src = sourcesLine(data);
    slot.innerHTML = head + lead(data) +
      (bars ? '<div class="alt-bars">' + bars + '</div>' : '') +
      '<h4 class="alt-sub">By route, copies against any original</h4><div class="alt-bars">' + bar("All routes", data.totals) + routes + '</div>' + legend() +
      '<h4 class="alt-sub">Outlets by median score' + esc(M.provisionalSuffix(block)) + '</h4>' + outletTable(data, api) +
      '<p class="alt-coverage">' + esc(coverageLine(data)) + '</p>' +
      (src ? '<p class="alt-coverage">' + esc(src) + '</p>' : '');
  }

  var spec = {render: render};
  if (M) M.register(ID, spec);
  return {ID: ID, render: render, shares: shares, coverageLine: coverageLine, sourcesLine: sourcesLine, lead: lead, BANDS: BANDS};
}));
