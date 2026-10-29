/* Republication speed (measure speed): the second panel of #copies. It registers with
   window.TrackerMeasures and renders data/speed.json (pipeline/speed.py): a bucket bar per route, for
   lags behind the state original (absolute) and, apart, behind the first monitored copy (relative),
   a ranked table of the fastest outlets, the counts kept outside the bars, the reference sources
   whose times look mislabelled and their fetch status. The two kinds of lag are never pooled, and
   every count of pairs says how many distinct originals stand behind it.
   Loaded in the browser after measures.js, and in Node or jsc with module.exports. */
(function (root, factory) {
  var M = root && root.TrackerMeasures;
  if (!M && typeof require === "function") M = require("./measures.js");
  var mod = factory(M);
  if (typeof module === "object" && module.exports) module.exports = mod;
}(typeof self !== "undefined" ? self : this, function (M) {
  "use strict";
  var ID = "speed";
  var TIMED = ["same_hour", "same_day", "days_1_3", "later"];
  var DATED = ["same_date", "date_1_3", "date_later"];
  var SEG = TIMED.concat(DATED);
  var LABELS = {same_hour: "Same hour", same_day: "Same day", days_1_3: "1 to 3 days", later: "Later",
                same_date: "Same date (date only)", date_1_3: "1 to 3 days (date only)", date_later: "Later (date only)"};
  var SOURCES = {ref_xinhua_en: "Xinhua English", ref_cgtn_en: "CGTN English", ref_china_daily_en: "China Daily",
                 ref_global_times_en: "Global Times", ref_peoples_daily_en: "People's Daily English",
                 ref_peoples_daily_it: "People's Daily Italian", ref_ecns_en: "ECNS",
                 ref_cgtn_africa_en: "CGTN Africa", ref_china_daily_africa_en: "China Daily Africa",
                 ref_xinhua_africa_en: "Xinhua Africa (English)", ref_xinhua_africa_fr: "Xinhua Afrique (French)"};
  var TOP_OUTLETS = 10;

  function num(v) { return typeof v === "number" && isFinite(v) ? v : 0; }
  function plural(n, word, many) { return n + " " + (n === 1 ? word : (many || word + "s")); }

  /* A lag in hours as words: "40 min", "6 h", "2.5 days". */
  function fmtHours(h) {
    if (typeof h !== "number" || !isFinite(h)) return "n/a";
    if (h < 1 / 60) return "under 1 min";
    if (h < 1) return Math.round(h * 60) + " min";
    if (h < 48) return (h < 10 ? Math.round(h * 10) / 10 : Math.round(h)) + " h";
    return Math.round(h / 24 * 10) / 10 + " days";
  }
  function pct(n, d) { return d ? Math.round(100 * n / d) + "%" : "n/a"; }
  function share(x) { return Math.round(100 * num(x)) + "%"; }
  function sourceName(id) { return SOURCES[id] || String(id || "unknown"); }

  /* One stacked bar of the buckets inside the distribution. */
  function bar(label, s, esc) {
    var b = (s && s.buckets) || {}, n = num(s && s.n);
    var segs = SEG.filter(function (k) { return num(b[k]) > 0; }).map(function (k) {
      return '<span class="sp-seg sp-' + k + '" style="width:' + (100 * num(b[k]) / n).toFixed(2) + '%" title="' +
        esc(LABELS[k] + ": " + num(b[k]) + " (" + pct(num(b[k]), n) + ")") + '"></span>';
    }).join("");
    var words = SEG.filter(function (k) { return num(b[k]) > 0; }).map(function (k) { return LABELS[k].toLowerCase() + " " + pct(num(b[k]), n); }).join(", ");
    return '<div class="sp-row"><span class="sp-label">' + esc(label) + '</span>' +
      '<span class="sp-bar" role="img" aria-label="' + esc(label + ": " + (words || "no pairs")) + '">' + segs + '</span>' +
      '<span class="sp-n">' + (s && typeof s.median_hours === "number" ? esc(fmtHours(s.median_hours)) : "") + '<em>' + plural(n, "pair") +
      (s && typeof s.originals === "number" ? ", " + plural(s.originals, "original") : "") + '</em></span></div>';
  }

  function legend(esc) {
    return '<div class="sp-legend">' + SEG.map(function (k) {
      return '<span><i class="sp-seg sp-' + k + '"></i>' + esc(LABELS[k]) + '</span>';
    }).join("") + '</div>';
  }

  /* The bars of one kind of lag: all routes, then each route with pairs inside the distribution. */
  function bars(kind, title, esc) {
    var rows = [bar("All routes", kind.overall, esc)];
    (kind.by_route || []).filter(function (r) { return num(r.n) > 0; }).forEach(function (r) { rows.push(bar(r.label || r.key, r, esc)); });
    return '<h4 class="sp-h">' + esc(title) + '</h4><div class="sp-bars">' + rows.join("") + '</div>';
  }

  function outletTable(kind, data, api) {
    var esc = api.esc || M.esc, cn = api.countryName || function (i) { return i; };
    var rows = (kind.by_outlet || []).filter(function (r) { return typeof r.median_hours === "number"; }).slice(0, TOP_OUTLETS);
    var min = num(data.min_outlet_pairs) || 10;
    if (!rows.length) return '<p class="m-empty">No outlet has timed pairs of ' + min + ' distinct originals yet, so no outlet is ranked.</p>';
    return '<h4 class="sp-h">Fastest outlets, behind the state original</h4>' +
      '<div class="m-table-wrap"><table class="m-table sp-table"><thead><tr><th>Outlet</th><th>Country</th>' +
      '<th class="num">Pairs</th><th class="num">Originals</th><th class="num">Median</th><th class="num">Middle half</th><th class="num">Within a day</th></tr></thead><tbody>' +
      rows.map(function (r) {
        var b = r.buckets || {}, day = num(b.same_hour) + num(b.same_day);
        return '<tr><td>' + esc(r.name || r.key) + '</td><td>' + esc(r.country ? cn(r.country) : "") + '</td>' +
          '<td class="num">' + num(r.n_timed) + '</td><td class="num">' + (typeof r.originals_timed === "number" ? num(r.originals_timed) : "n/a") + '</td>' +
          '<td class="num">' + esc(fmtHours(r.median_hours)) + '</td>' +
          '<td class="num">' + esc(fmtHours(r.p25_hours) + " to " + fmtHours(r.p75_hours)) + '</td>' +
          '<td class="num">' + pct(day, num(r.n_timed)) + '</td></tr>';
      }).join("") + '</tbody></table></div>' +
      '<p class="sp-note">Outlets with timed pairs of at least ' + min + ' distinct originals, fastest median first. Middle half is the 25th to 75th percentile.</p>';
  }

  /* The counts kept outside the bars, and coverage. */
  function notes(data, esc) {
    var out = [], cov = data.coverage || {};
    ["absolute", "relative"].forEach(function (k) {
      var b = ((data[k] || {}).overall || {}).buckets || {}, ahead = num(b.copy_precedes_original), none = num(b.no_credible_time);
      if (!ahead && !none) return;
      out.push((k === "absolute" ? "Behind the state original: " : "Behind the first monitored copy: ") +
        plural(ahead, "copy", "copies") + " ran more than an hour ahead of " + (ahead === 1 ? "its" : "their") + " original and " +
        plural(none, "pair") + " had no credible time; both are counted here and kept out of the bars.");
    });
    if (num(cov.copies)) out.push("Of " + plural(num(cov.copies), "state origin copy", "state origin copies") + ", " + num(cov.reference) +
      " were paired with the state outlet's own page, " + num(cov.monitored) + " only with an earlier monitored copy, and " +
      num(cov.none) + " with no original.");
    (data.diagnostics || []).filter(function (d) { return d.flags && d.flags.length; }).forEach(function (d) {
      var why = [];
      if (d.flags.indexOf("published after discovery") !== -1) why.push(share(d.share_negative) + " of its timed items carry a time after the tracker found them, which points to a zone labelled wrongly");
      if (d.flags.indexOf("mostly date only") !== -1) why.push(share(d.share_date_only) + " give only a date");
      if (d.flags.indexOf("date only after day one") !== -1) why.push(share(d.share_date_only_after_day_one) + " still give only a date after their first day, when the page's own time should have been read");
      if (d.flags.indexOf("earlier than expected") !== -1) why.push(share(d.time_check && d.time_check.share_earlier) + " of the items it lists twice read more than an hour earlier on its second listing, which points to a change of time zone");
      if (d.flags.indexOf("later than expected") !== -1) why.push(share(d.time_check && d.time_check.share_later) + " of the items it lists twice read more than an hour later on its second listing");
      if (why.length) out.push("Times from " + sourceName(d.source) + " need care: " + why.join(", and ") + ".");
    });
    var srcs = data.reference_sources || [];
    if (srcs.length) out.push("State outlets' own pages: " + srcs.map(function (d) {
      var st = d.statuses || {}, bits = [num(st.fetched) + " of " + num(d.items) + " fetched"];
      if (st.expired) bits.push(num(st.expired) + " expired unread");
      if (st.failed) bits.push(num(st.failed) + " failed");
      if (st.paywalled) bits.push(num(st.paywalled) + " paywalled");
      return sourceName(d.source) + " " + bits.join(", ");
    }).join("; ") + ".");
    return out.length ? '<ul class="sp-notes">' + out.map(function (l) { return '<li>' + esc(l) + '</li>'; }).join("") + '</ul>' : "";
  }

  function lead(data) {
    var a = (data.absolute || {}).overall || {}, r = (data.relative || {}).overall || {};
    var of = function (s) { return typeof s.originals_timed === "number" ? " (" + plural(s.originals_timed, "distinct original") + ")" : ""; };
    if (typeof a.median_hours === "number") {
      var b = a.buckets || {}, day = num(b.same_hour) + num(b.same_day);
      return "Half of the " + plural(num(a.n_timed), "copy", "copies") + " with a timed state original" + of(a) + " ran within " + fmtHours(a.median_hours) +
        " of it, and " + pct(day, num(a.n_timed)) + " within a day.";
    }
    if (typeof r.median_hours === "number")
      return "No state original has timed pairs of enough distinct originals yet. Behind the first copy at another monitored outlet outside the copy's own media group, half of the later copies" + of(r) + " ran within " + fmtHours(r.median_hours) + ".";
    return "Too few distinct originals carry a time of day on both pages for a median yet.";
  }

  function render(block, data, api) {
    var slot = api && api.slot;
    if (!slot) return;
    if (!M.hideIfOff(slot, block)) { slot.innerHTML = ""; return; }
    var esc = api.esc || M.esc;
    var head = '<h3 class="sp-title">How soon state text is republished ' + M.statusChip(block) + '</h3>';
    if (M.isWithheld(block) || !data || !data.absolute || !data.relative) {
      slot.innerHTML = head + M.emptyState(block, data && data.absolute ? data : null, "republication speed");
      return;
    }
    var a = data.absolute, r = data.relative;
    var anyA = num(a.overall && a.overall.n) > 0, anyR = num(r.overall && r.overall.n) > 0;
    var html = head;
    if (!anyA && !anyR) {
      html += '<p class="m-empty">No paired copy has two credible publication times yet, so there is nothing to show for the republication speed.</p>' + notes(data, esc);
      slot.innerHTML = html;
      return;
    }
    html += '<p class="sp-lead">' + esc(lead(data)) + '</p>' + legend(esc);
    if (anyA) html += bars(a, "Behind the state original", esc);
    if (anyR) html += bars(r, "Behind the first monitored copy (where no state original was found)", esc);
    html += '<p class="sp-note">The figure beside each bar is the median of its timed pairs, shown from ' + (num(data.min_group_median) || 5) +
      ' distinct originals, since one state text run by many outlets is many pairs but one original. A copy up to an hour ahead of its original counts as the same hour.</p>';
    if (anyA) html += outletTable(a, data, api);
    html += notes(data, esc);
    slot.innerHTML = html;
  }

  var spec = {render: render};
  if (M) M.register(ID, spec);
  return {ID: ID, render: render, fmtHours: fmtHours, lead: lead};
}));
