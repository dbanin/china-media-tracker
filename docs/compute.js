/* Pure computation for the tracker front end. No DOM access, so it can be tested in Node.
   Loaded in the browser as a global (window.TrackerCompute) and in Node via module.exports. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) { module.exports = factory(); }
  else { root.TrackerCompute = factory(); }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var EMPTY = {A: 0, B: 0, C: 0, N: 0, Ar: 0, Al: 0, Ah: 0, Br: 0, Bl: 0, Bh: 0, rev: 0, cls: 0, disc: 0, rel: 0,
               fetched: 0, paywalled: 0, failed: 0, blocked: 0, pending: 0, uniqA: 0, uniqAB: 0,
               tdisc: 0, ttarget: 0, tchina: 0};

  function emptyCounts() { var o = {}; for (var k in EMPTY) o[k] = 0; return o; }

  function addInto(target, src) {
    for (var k in EMPTY) target[k] += (src && src[k]) || 0;
    return target;
  }

  /* All dates present across the monthly files, sorted ascending. */
  function listDays(months) {
    var days = [];
    Object.keys(months || {}).forEach(function (m) {
      Object.keys((months[m] && months[m].days) || {}).forEach(function (d) { days.push(d); });
    });
    days.sort();
    return days;
  }

  function dayEntry(months, date) {
    if (!date) return null;
    var m = months && months[date.slice(0, 7)];
    return (m && m.days && m.days[date]) || null;
  }

  function shiftDate(iso, deltaDays) {
    var d = new Date(iso + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + deltaDays);
    return d.toISOString().slice(0, 10);
  }

  /* Sum per-country counts over the window ending at endDate.
     windowDays null means all time up to and including endDate. */
  function aggregateWindow(months, endDate, windowDays) {
    var out = {countries: {}, reviewed: {}, ceilingDays: []};
    if (!endDate) return out;
    var start = windowDays ? shiftDate(endDate, -(windowDays - 1)) : null;
    listDays(months).forEach(function (d) {
      if (d > endDate) return;
      if (start && d < start) return;
      var e = dayEntry(months, d);
      if (!e) return;
      if (e.llm_ceiling_hit) out.ceilingDays.push(d);
      Object.keys(e.countries || {}).forEach(function (c) {
        if (!out.countries[c]) out.countries[c] = emptyCounts();
        addInto(out.countries[c], e.countries[c]);
      });
      Object.keys(e.reviewed || {}).forEach(function (c) {
        if (!out.reviewed[c]) out.reviewed[c] = {A: 0, B: 0, C: 0, N: 0};
        var r = e.reviewed[c];
        out.reviewed[c].A += r.A || 0; out.reviewed[c].B += r.B || 0; out.reviewed[c].C += r.C || 0; out.reviewed[c].N += r.N || 0;
      });
    });
    return out;
  }

  /* Share metrics are undefined on tiny denominators: two A items out of two is not a 100 percent country. */
  var MIN_SHARE_DENOMINATOR = 5;
  /* The share of all published items needs a real stream of items behind it. */
  var MIN_ALL_ITEMS_DENOMINATOR = 50;
  /* Per capita values from a few thousand residents are noise and would set the whole color scale. */
  var MIN_POPULATION = 100000;

  var METRICS = {
    count_target: {label: "Target articles: state placements plus pieces carrying official Chinese sourcing", format: "int", needsB: true},
    share_target: {label: "Share of China coverage that is a state placement or carries official Chinese sourcing", format: "pct", needsB: true},
    share_ab: {label: "Share of China coverage confirmed as state origin or unverified relay", format: "pct", needsB: true},
    count_ab: {label: "Confirmed state origin plus unverified relay articles", format: "int", needsB: true},
    count_a: {label: "State origin articles", format: "int", needsB: false},
    per_outlet_target: {label: "Target articles per monitored outlet", format: "dec", needsB: true},
    per_outlet_a: {label: "State origin articles per monitored outlet", format: "dec", needsB: false},
    per_million_target: {label: "Target articles per million people", format: "dec", needsB: true, population: true},
    per_million_a: {label: "State origin articles per million people", format: "dec", needsB: false, population: true},
    share_of_all_target: {label: "Target articles as a share of every item the country's largest monitored outlets published", format: "pct", needsB: true, allItems: true},
    share_of_all_china: {label: "All China coverage as a share of every item the country's largest monitored outlets published", format: "pct", needsB: false, allItems: true}
  };

  /* Returns {value, chinaTotal, ab, a, b, pending, target, sparse, note} for one country under a metric and mode.
     ctx carries what the counts do not: {population} for the per million metrics. */
  function metricValue(counts, metric, outletsActive, mode, reviewedCounts, ctx) {
    var a, b, c, p;
    var k = counts || emptyCounts();
    if (mode === "reviewed") {
      var r = reviewedCounts || {A: 0, B: 0, C: 0, N: 0};
      a = r.A; b = r.B; c = r.C; p = 0;
    } else {
      a = k.A; b = k.B; c = k.C; p = k.pending || 0;
    }
    var population = ctx && ctx.population;
    var allItems = k.tdisc || 0;
    /* Articles carrying official Chinese sourcing whose verification judgement is pending count as China
       coverage and as targets. Once judged they become unverified relay or independent journalism. */
    var china = a + b + c + p;
    var target = a + b + p;
    var value = null;
    var sparse = false;
    var note = null;
    switch (metric) {
      case "count_target": value = target; break;
      case "share_target": sparse = china > 0 && china < MIN_SHARE_DENOMINATOR; value = china >= MIN_SHARE_DENOMINATOR ? target / china : null; break;
      case "share_ab": sparse = china > 0 && china < MIN_SHARE_DENOMINATOR; value = china >= MIN_SHARE_DENOMINATOR ? (a + b) / china : null; break;
      case "share_a": sparse = china > 0 && china < MIN_SHARE_DENOMINATOR; value = china >= MIN_SHARE_DENOMINATOR ? a / china : null; break;
      case "count_a": value = a; break;
      case "count_ab": value = a + b; break;
      case "per_outlet_target": value = outletsActive ? target / outletsActive : null; break;
      case "per_outlet_ab": value = outletsActive ? (a + b) / outletsActive : null; break;
      case "per_outlet_a": value = outletsActive ? a / outletsActive : null; break;
      case "per_million_target":
      case "per_million_a":
        if (!population) { sparse = china > 0; note = "No resident population is recorded for this territory, so a per capita value is not shown."; value = null; }
        else if (population < MIN_POPULATION) { sparse = china > 0; note = "Fewer than " + MIN_POPULATION.toLocaleString("en-US") + " residents, so a per capita value is not shown; a single article would dominate the scale."; value = null; }
        else value = (metric === "per_million_a" ? a : target) / population * 1e6;
        break;
      case "share_of_all_target":
      case "share_of_all_china":
        if (mode === "reviewed") { value = null; note = "The share of all published items is not available for human-reviewed labels only."; }
        else if (allItems < MIN_ALL_ITEMS_DENOMINATOR) { sparse = allItems > 0 || china > 0; note = "Fewer than " + MIN_ALL_ITEMS_DENOMINATOR + " items were published by the country's largest monitored outlets in this window, so a share is not shown."; value = null; }
        else value = (metric === "share_of_all_china" ? (k.tchina || 0) : (k.ttarget || 0)) / allItems;
        break;
      default: value = null;
    }
    if (sparse && !note) note = "Fewer than " + MIN_SHARE_DENOMINATOR + " China items in this window, so a share is not shown. Switch to a count metric to see them.";
    return {value: value, chinaTotal: china, a: a, b: b, c: c, pending: p, target: target, ab: a + b, sparse: sparse, note: note,
            allItems: allItems, allItemsTarget: k.ttarget || 0, allItemsChina: k.tchina || 0, population: population || null};
  }

  /* Coverage class for the fill. Distinguishes absence of data from absence of content.
       nocoverage  no monitored outlets and not in the gaps file
       gap         no working feed could be found; reason recorded
       inactive    outlets registered but all inactive
       nodata      monitored, but no China coverage in the window (share metrics undefined)
       sparse      monitored, fewer than MIN_SHARE_DENOMINATOR China items, share not shown
       zero        monitored, China coverage present, zero A or B
       value       positive value on the scale */
  function fillClass(latestEntry, mv) {
    if (!latestEntry) return "nocoverage";
    if (latestEntry.coverage === "gap") return "gap";
    if (latestEntry.coverage === "no_active_outlets" || !latestEntry.outlets_active) return "inactive";
    if (!mv || (mv.chinaTotal === 0 && !(mv.allItems > 0))) return "nodata";
    if (mv.sparse) return "sparse";
    if (!mv.value) return "zero";
    return "value";
  }

  function formatValue(v, fmt) {
    if (v === null || v === undefined || isNaN(v)) return "n/a";
    if (fmt === "pct") return (v * 100).toFixed(1) + "%";
    if (fmt === "dec") return v.toFixed(2);
    return String(v);
  }

  function csvEscape(v) {
    if (v === null || v === undefined) return "";
    var s = String(v);
    if (/[",\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  function toCSV(rows, columns) {
    var lines = [columns.map(csvEscape).join(",")];
    rows.forEach(function (r) { lines.push(columns.map(function (c) { return csvEscape(r[c]); }).join(",")); });
    return lines.join("\n") + "\n";
  }

  /* Ranked country rows for the current view, used by the bar chart, the CSV export and the table. */
  function rankCountries(agg, latest, metric, mode, names) {
    var rows = [];
    var countries = (latest && latest.countries) || {};
    Object.keys(countries).forEach(function (iso) {
      var entry = countries[iso];
      var mv = metricValue(agg.countries[iso], metric, entry.outlets_active, mode, agg.reviewed[iso], {population: entry.population});
      var cls = fillClass(entry, mv);
      rows.push({iso: iso, name: (names && names[iso]) || iso, value: mv.value, fill: cls, state_origin: mv.a, unverified_relay: mv.b, official_sourcing_pending: mv.pending, target: mv.target, independent: mv.c,
                 china_total: mv.chinaTotal, outlets_active: entry.outlets_active, population: entry.population || "",
                 all_items_top_outlets: mv.allItems, target_in_top_outlets: mv.allItemsTarget, china_in_top_outlets: mv.allItemsChina,
                 warnings: (entry.warnings || []).map(function (w) { return w.text; }).join("; ")});
    });
    rows.sort(function (x, y) {
      var xv = x.value === null ? -1 : x.value, yv = y.value === null ? -1 : y.value;
      return yv - xv || x.name.localeCompare(y.name);
    });
    return rows;
  }

  /* Display names for the internal codes. The codes stay in the data files; readers never see them. */
  var NAMES = {A: "State origin", B: "Unverified relay", C: "Independent journalism", N: "Not relevant", not_relevant: "Not relevant",
               pending: "Official Chinese sourcing, verification pending"};
  function nameOf(code) { return NAMES[code] || code; }

  function citation(accessDate, author) {
    return (author || "China State Media Tracker project") + ". China State Media Tracker: daily counts of Chinese state-origin and state-sourced news content by country. " +
      "Stanford University. Accessed " + accessDate + ".";
  }

  return {EMPTY: EMPTY, MIN_SHARE_DENOMINATOR: MIN_SHARE_DENOMINATOR, MIN_ALL_ITEMS_DENOMINATOR: MIN_ALL_ITEMS_DENOMINATOR, MIN_POPULATION: MIN_POPULATION, emptyCounts: emptyCounts, addInto: addInto, listDays: listDays, dayEntry: dayEntry, shiftDate: shiftDate,
          aggregateWindow: aggregateWindow, METRICS: METRICS, metricValue: metricValue, fillClass: fillClass,
          formatValue: formatValue, toCSV: toCSV, rankCountries: rankCountries, citation: citation, NAMES: NAMES, nameOf: nameOf};
}));
