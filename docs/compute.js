/* Pure computation for the tracker front end. No DOM access, so it can be tested in Node.
   Loaded in the browser as a global (window.TrackerCompute) and in Node via module.exports. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) { module.exports = factory(); }
  else { root.TrackerCompute = factory(); }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var EMPTY = {A: 0, B: 0, C: 0, N: 0, Ar: 0, Al: 0, Ah: 0, Br: 0, Bl: 0, Bh: 0, rev: 0, cls: 0, disc: 0, rel: 0,
               fetched: 0, paywalled: 0, failed: 0, blocked: 0, pending: 0, uniqA: 0, uniqAB: 0};

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

  var METRICS = {
    share_ab: {label: "Share of China coverage that is A or B", format: "pct", needsB: true},
    share_a: {label: "Share of China coverage that is A", format: "pct", needsB: false},
    count_a: {label: "Category A articles", format: "int", needsB: false},
    count_ab: {label: "Category A plus B articles", format: "int", needsB: true},
    per_outlet_ab: {label: "A plus B articles per monitored outlet", format: "dec", needsB: true},
    per_outlet_a: {label: "A articles per monitored outlet", format: "dec", needsB: false}
  };

  /* Returns {value, chinaTotal, ab, a, b} for one country under a metric and mode. */
  function metricValue(counts, metric, outletsActive, mode, reviewedCounts) {
    var a, b, c;
    if (mode === "reviewed") {
      var r = reviewedCounts || {A: 0, B: 0, C: 0, N: 0};
      a = r.A; b = r.B; c = r.C;
    } else {
      var k = counts || emptyCounts();
      a = k.A; b = k.B; c = k.C;
    }
    var china = a + b + c;
    var value = null;
    switch (metric) {
      case "share_ab": value = china ? (a + b) / china : null; break;
      case "share_a": value = china ? a / china : null; break;
      case "count_a": value = a; break;
      case "count_ab": value = a + b; break;
      case "per_outlet_ab": value = outletsActive ? (a + b) / outletsActive : null; break;
      case "per_outlet_a": value = outletsActive ? a / outletsActive : null; break;
      default: value = null;
    }
    return {value: value, chinaTotal: china, a: a, b: b, c: c, ab: a + b};
  }

  /* Coverage class for the fill. Distinguishes absence of data from absence of content.
       nocoverage  no monitored outlets and not in the gaps file
       gap         no working feed could be found; reason recorded
       inactive    outlets registered but all inactive
       nodata      monitored, but no China coverage in the window (share metrics undefined)
       zero        monitored, China coverage present, zero A or B
       value       positive value on the scale */
  function fillClass(latestEntry, mv) {
    if (!latestEntry) return "nocoverage";
    if (latestEntry.coverage === "gap") return "gap";
    if (latestEntry.coverage === "no_active_outlets" || !latestEntry.outlets_active) return "inactive";
    if (!mv || mv.chinaTotal === 0) return "nodata";
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
      var mv = metricValue(agg.countries[iso], metric, entry.outlets_active, mode, agg.reviewed[iso]);
      var cls = fillClass(entry, mv);
      rows.push({iso: iso, name: (names && names[iso]) || iso, value: mv.value, fill: cls, a: mv.a, b: mv.b, c: mv.c,
                 china_total: mv.chinaTotal, outlets_active: entry.outlets_active, warnings: (entry.warnings || []).map(function (w) { return w.text; }).join("; ")});
    });
    rows.sort(function (x, y) {
      var xv = x.value === null ? -1 : x.value, yv = y.value === null ? -1 : y.value;
      return yv - xv || x.name.localeCompare(y.name);
    });
    return rows;
  }

  function citation(accessDate, author) {
    return (author || "China State Media Tracker project") + ". China State Media Tracker: daily counts of Chinese state-origin and state-sourced news content by country. " +
      "Stanford University. Accessed " + accessDate + ".";
  }

  return {EMPTY: EMPTY, emptyCounts: emptyCounts, addInto: addInto, listDays: listDays, dayEntry: dayEntry, shiftDate: shiftDate,
          aggregateWindow: aggregateWindow, METRICS: METRICS, metricValue: metricValue, fillClass: fillClass,
          formatValue: formatValue, toCSV: toCSV, rankCountries: rankCountries, citation: citation};
}));
