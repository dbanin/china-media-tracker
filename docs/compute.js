/* Pure computation for the tracker front end. No DOM access, so it can be tested in Node.
   Loaded in the browser as a global (window.TrackerCompute) and in Node via module.exports. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) { module.exports = factory(); }
  else { root.TrackerCompute = factory(); }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var EMPTY = {A: 0, B: 0, C: 0, N: 0, Ar: 0, Al: 0, Ah: 0, Br: 0, Bl: 0, Bh: 0, rev: 0, cls: 0, disc: 0, rel: 0,
               fetched: 0, paywalled: 0, failed: 0, blocked: 0, pending: 0, uniqA: 0, uniqAB: 0,
               tdisc: 0, ttarget: 0, tchina: 0, ta: 0, polls: 0, sat: 0, miss: 0};

  function emptyCounts() { var o = {}; for (var k in EMPTY) o[k] = 0; return o; }

  function addInto(target, src) {
    for (var k in EMPTY) target[k] += (src && src[k]) || 0;
    return target;
  }

  /* dst[iso][key] += src[iso][key], for the per country route and arrival tallies. */
  function addNested(dst, src) {
    Object.keys(src || {}).forEach(function (iso) {
      var s = src[iso], t = dst[iso] || (dst[iso] = {});
      Object.keys(s || {}).forEach(function (k) { t[k] = (t[k] || 0) + (s[k] || 0); });
    });
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
    var out = {countries: {}, reviewed: {}, routes: {}, arrivals: {}, ceilingDays: [], relayIncompleteDays: [], olderRulesetDays: []};
    if (!endDate) return out;
    var start = windowDays ? shiftDate(endDate, -(windowDays - 1)) : null;
    listDays(months).forEach(function (d) {
      if (d > endDate) return;
      if (start && d < start) return;
      var e = dayEntry(months, d);
      if (!e) return;
      if (e.llm_ceiling_hit) out.ceilingDays.push(d);
      if (e.relay_incomplete) out.relayIncompleteDays.push(d);
      if (e.labels_on_older_ruleset) out.olderRulesetDays.push(d);
      Object.keys(e.countries || {}).forEach(function (c) {
        if (!out.countries[c]) out.countries[c] = emptyCounts();
        addInto(out.countries[c], e.countries[c]);
      });
      Object.keys(e.reviewed || {}).forEach(function (c) {
        if (!out.reviewed[c]) out.reviewed[c] = {A: 0, B: 0, C: 0, N: 0};
        var r = e.reviewed[c];
        out.reviewed[c].A += r.A || 0; out.reviewed[c].B += r.B || 0; out.reviewed[c].C += r.C || 0; out.reviewed[c].N += r.N || 0;
      });
      addNested(out.routes, e.routes);
      addNested(out.arrivals, e.arrivals);
    });
    return out;
  }

  /* Share metrics are undefined on tiny denominators: two A items out of two is not a 100 percent country. */
  var MIN_SHARE_DENOMINATOR = 5;
  /* A share of monitored output needs a real stream of items behind it. */
  var MIN_ALL_ITEMS_DENOMINATOR = 50;
  /* ...and enough outlets that the stream describes a country rather than a handful of feeds. */
  var MIN_OUTLETS_FOR_OUTPUT_SHARE = 5;
  /* Per capita values from a few thousand residents are noise and would set the whole color scale. */
  var MIN_POPULATION = 100000;

  /* measure: which count the metric reads (a, target, china). relay: the metric contains confirmed
     unverified relay and is withheld until relay is publishable. */
  var METRICS = {
    count_a: {label: "State origin articles", format: "int", measure: "a"},
    per_outlet_a: {label: "State origin articles per monitored outlet", format: "dec", measure: "a"},
    share_of_output_a: {label: "Share of monitored output: state origin articles as a share of every item the monitored outlets published", format: "pct", measure: "a", allItems: true},
    per_million_a: {label: "State origin articles per million people", format: "dec", measure: "a", population: true},
    count_target: {label: "Target articles: state origin, confirmed unverified relay and sourcing candidates not yet verified", format: "int", measure: "target"},
    per_outlet_target: {label: "Target articles per monitored outlet", format: "dec", measure: "target"},
    share_of_output_target: {label: "Share of monitored output: target articles as a share of every item the monitored outlets published", format: "pct", measure: "target", allItems: true},
    per_million_target: {label: "Target articles per million people", format: "dec", measure: "target", population: true},
    count_china: {label: "All China coverage: every article that concerns China", format: "int", measure: "china"},
    per_outlet_china: {label: "All China coverage per monitored outlet", format: "dec", measure: "china"},
    share_of_output_china: {label: "Share of monitored output: all China coverage as a share of every item the monitored outlets published", format: "pct", measure: "china", allItems: true},
    per_million_china: {label: "All China coverage per million people", format: "dec", measure: "china", population: true},
    share_a: {label: "Share of China coverage that is state origin", format: "pct", measure: "a"},
    share_target: {label: "Share of China coverage that is state origin, confirmed relay or a sourcing candidate", format: "pct", measure: "target"},
    count_ab: {label: "State origin plus confirmed unverified relay", format: "int", measure: "target", relay: true},
    share_ab: {label: "Share of China coverage that is state origin or confirmed unverified relay", format: "pct", measure: "target", relay: true},
    per_outlet_ab: {label: "State origin plus confirmed unverified relay per monitored outlet", format: "dec", measure: "target", relay: true}
  };

  var RELAY_NOT_MEASURED = "Unverified relay has not been measured: the verification stage has not run. It is not zero.";
  var RELAY_WITHHELD = "Unverified relay counts are withheld until the agreement study shows the relay versus independent journalism judgement is reliable.";

  /* Returns {value, chinaTotal, ab, a, b, pending, target, sparse, withheld, note} for one country under a metric and mode.
     ctx carries what the counts do not:
       population    residents, for the per million metrics
       topOutlets    outlets behind the published-items denominator
       relay         {measured, publishable} for this country; absent means publishable
       routeCount    state origin in the selected route or arrival, replacing the state origin count */
  function metricValue(counts, metric, outletsActive, mode, reviewedCounts, ctx) {
    ctx = ctx || {};
    var a, b, c, p;
    var k = counts || emptyCounts();
    if (mode === "reviewed") {
      var r = reviewedCounts || {A: 0, B: 0, C: 0, N: 0};
      a = r.A; b = r.B; c = r.C; p = 0;
    } else {
      a = k.A; b = k.B; c = k.C; p = k.pending || 0;
    }
    var def = METRICS[metric] || {};
    var byRoute = ctx.routeCount !== undefined && ctx.routeCount !== null;
    var aSel = byRoute ? ctx.routeCount : a;
    var population = ctx.population;
    var allItems = k.tdisc || 0;
    var relay = ctx.relay || {measured: true, publishable: true};
    /* Articles carrying official Chinese sourcing whose verification judgement is pending count as China
       coverage and as targets. Once judged they become unverified relay or independent journalism. */
    var china = a + b + c + p;
    var target = a + b + p;
    var value = null, sparse = false, note = null, withheld = false;
    var num = def.measure === "a" ? aSel : def.measure === "china" ? china : target;
    if (def.relay && !relay.publishable) {
      withheld = true;
      note = relay.measured ? RELAY_WITHHELD : RELAY_NOT_MEASURED;
    } else if (byRoute && def.measure !== "a") {
      note = "The route filter applies to state origin only.";
    } else {
      switch (metric) {
        case "count_a": case "count_target": case "count_china": value = num; break;
        case "count_ab": value = a + b; break;
        case "share_a": case "share_target":
          sparse = china > 0 && china < MIN_SHARE_DENOMINATOR; value = china >= MIN_SHARE_DENOMINATOR ? num / china : null; break;
        case "share_ab": sparse = china > 0 && china < MIN_SHARE_DENOMINATOR; value = china >= MIN_SHARE_DENOMINATOR ? (a + b) / china : null; break;
        case "per_outlet_a": case "per_outlet_target": case "per_outlet_china": value = outletsActive ? num / outletsActive : null; break;
        case "per_outlet_ab": value = outletsActive ? (a + b) / outletsActive : null; break;
        case "per_million_a": case "per_million_target": case "per_million_china":
          if (!population) { sparse = china > 0; note = "No resident population is recorded for this territory, so a per capita value is not shown."; }
          else if (population < MIN_POPULATION) { sparse = china > 0; note = "Fewer than " + MIN_POPULATION.toLocaleString("en-US") + " residents, so a per capita value is not shown; a single article would dominate the scale."; }
          else value = num / population * 1e6;
          break;
        case "share_of_output_a": case "share_of_output_target": case "share_of_output_china":
          if (mode === "reviewed") note = "The share of monitored output is not available for human-reviewed labels only.";
          else if (byRoute) note = "The share of monitored output is not available by route, because the published items are not split by route.";
          else if (ctx.topOutlets !== undefined && ctx.topOutlets !== null && ctx.topOutlets < MIN_OUTLETS_FOR_OUTPUT_SHARE) {
            sparse = allItems > 0 || china > 0;
            note = "Fewer than " + MIN_OUTLETS_FOR_OUTPUT_SHARE + " monitored outlets, so a share of their output would describe a handful of feeds, not a country.";
          } else if (allItems < MIN_ALL_ITEMS_DENOMINATOR) {
            sparse = allItems > 0 || china > 0;
            note = "Fewer than " + MIN_ALL_ITEMS_DENOMINATOR + " items were published by the monitored outlets in this window, so a share is not shown.";
          } else value = (metric === "share_of_output_china" ? (k.tchina || 0) : metric === "share_of_output_a" ? (k.ta || 0) : (k.ttarget || 0)) / allItems;
          break;
        default: value = null;
      }
    }
    if (sparse && !note) note = "Fewer than " + MIN_SHARE_DENOMINATOR + " China items in this window, so a share is not shown. Switch to a count metric to see them.";
    return {value: value, chinaTotal: china, a: aSel, aAll: a, b: b, c: c, pending: p, target: target, ab: a + b, sparse: sparse, withheld: withheld, note: note,
            allItems: allItems, allItemsA: k.ta || 0, allItemsTarget: k.ttarget || 0, allItemsChina: k.tchina || 0, underlying: k.uniqA || 0, population: population || null};
  }

  /* State origin in the window for one route or arrival: filter {kind: "route" | "arrival", id}. */
  function routeCount(agg, iso, filter) {
    if (!filter || !filter.id) return null;
    var src = filter.kind === "arrival" ? agg.arrivals : agg.routes;
    return ((src && src[iso]) || {})[filter.id] || 0;
  }

  /* Theme counts over the same window as aggregateWindow.
     Returns {iso: {themeId: [all China coverage, target articles, state origin]}}. */
  function aggregateThemes(months, endDate, windowDays) {
    var out = {};
    if (!endDate) return out;
    var start = windowDays ? shiftDate(endDate, -(windowDays - 1)) : null;
    listDays(months).forEach(function (d) {
      if (d > endDate || (start && d < start)) return;
      var e = dayEntry(months, d);
      if (!e || !e.themes) return;
      Object.keys(e.themes).forEach(function (iso) {
        var src = e.themes[iso];
        var dst = out[iso] || (out[iso] = {});
        Object.keys(src).forEach(function (t) {
          var v = src[t], cur = dst[t] || (dst[t] = [0, 0, 0]);
          cur[0] += v[0] || 0; cur[1] += v[1] || 0; cur[2] += v[2] || 0;
        });
      });
    });
    return out;
  }
  /* Which slot of a theme count each Measure reads. */
  var MEASURE_INDEX = {china: 0, target: 1, a: 2};

  /* The grid behind the Measure and Basis toggles. Per capita comes last: population measures people,
     not media saturation, so it is the least defensible of the denominators. */
  var BASES = ["count", "per_outlet", "share_of_output", "per_million"];
  var GRID = {
    a: {count: "count_a", per_outlet: "per_outlet_a", share_of_output: "share_of_output_a", per_million: "per_million_a"},
    target: {count: "count_target", per_outlet: "per_outlet_target", share_of_output: "share_of_output_target", per_million: "per_million_target"},
    china: {count: "count_china", per_outlet: "per_outlet_china", share_of_output: "share_of_output_china", per_million: "per_million_china"}
  };
  function gridMetric(measure, basis) { return (GRID[measure] || GRID.a)[basis] || GRID.a.count; }

  /* Coverage class for the fill. Distinguishes absence of data from absence of content.
       nocoverage  no monitored outlets and not in the gaps file
       gap         no working feed could be found; reason recorded
       inactive    outlets registered but all inactive
       withheld    the metric contains unverified relay, which is not measured or not yet publishable
       unreadable  monitored, but no active outlet's language has a keyword list, and nothing was found
       nodata      monitored, but no China coverage in the window (share metrics undefined)
       sparse      monitored, denominator too small, value not shown
       zero        monitored, China coverage present, nothing in the measure
       value       positive value on the scale */
  function fillClass(latestEntry, mv) {
    if (!latestEntry) return "nocoverage";
    if (latestEntry.coverage === "gap") return "gap";
    if (latestEntry.coverage === "no_active_outlets" || !latestEntry.outlets_active) return "inactive";
    if (mv && mv.withheld) return "withheld";
    if (latestEntry.language_support === "none" && !(mv && mv.value)) return "unreadable";
    if (!mv || (mv.chinaTotal === 0 && !(mv.allItems > 0))) return "nodata";
    if (mv.sparse) return "sparse";
    if (!mv.value) return "zero";
    return "value";
  }

  /* Linear interpolation percentile, p in [0, 1]. Used to cap the color scale. */
  function percentile(values, p) {
    var v = values.filter(function (x) { return x !== null && x !== undefined && !isNaN(x); }).slice().sort(function (a, b) { return a - b; });
    if (!v.length) return null;
    var pos = (v.length - 1) * p, lo = Math.floor(pos), hi = Math.ceil(pos);
    return v[lo] + (v[hi] - v[lo]) * (pos - lo);
  }

  /* The color scale is fixed for a metric and window across the whole timeline: the cap is the 95th
     percentile of every positive value on every sampled end date, not of the day shown, so a shade
     means the same number on every date. At most maxSamples end dates are read, spread evenly. */
  function scaleCap(months, endDates, latest, metric, windowDays, mode, ctxFor, maxSamples) {
    var dates = endDates || [], n = maxSamples || 60, pick = [];
    if (dates.length <= n) pick = dates.slice();
    else for (var i = 0; i < n; i++) pick.push(dates[Math.round(i * (dates.length - 1) / (n - 1))]);
    var vals = [];
    var countries = (latest && latest.countries) || {};
    pick.forEach(function (d) {
      var agg = aggregateWindow(months, d, windowDays);
      Object.keys(countries).forEach(function (iso) {
        var entry = countries[iso];
        var ctx = ctxFor ? ctxFor(iso, entry, agg) : {population: entry.population};
        var mv = metricValue(agg.countries[iso], metric, entry.outlets_active, mode, agg.reviewed[iso], ctx);
        if (fillClass(entry, mv) === "value") vals.push(mv.value);
      });
    });
    var max = null;
    vals.forEach(function (v) { if (max === null || v > max) max = v; });
    return {cap: percentile(vals, 0.95), max: max, values: vals.length, dates: pick.length};
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

  function relayStatus(relay) {
    if (!relay || relay.publishable) return "published";
    return relay.measured ? "withheld" : "not measured";
  }

  /* Ranked country rows for the current view, used by the bar chart, the CSV export and the table.
     Unverified relay is left blank wherever it is not publishable, so a CSV never carries it as zero. */
  function rankCountries(agg, latest, metric, mode, names, ctxFor) {
    var rows = [];
    var countries = (latest && latest.countries) || {};
    Object.keys(countries).forEach(function (iso) {
      var entry = countries[iso];
      var ctx = ctxFor ? ctxFor(iso, entry, agg) : {population: entry.population};
      var mv = metricValue(agg.countries[iso], metric, entry.outlets_active, mode, agg.reviewed[iso], ctx);
      var cls = fillClass(entry, mv);
      var published = relayStatus(ctx.relay) === "published";
      rows.push({iso: iso, name: (names && names[iso]) || iso, value: mv.value, fill: cls, state_origin: mv.a, state_origin_underlying_items: mv.underlying,
                 unverified_relay: published ? mv.b : "", relay_status: relayStatus(ctx.relay), official_sourcing_pending: mv.pending, target: mv.target, independent: mv.c,
                 china_total: mv.chinaTotal, outlets_active: entry.outlets_active, population: entry.population || "",
                 items_published_monitored_outlets: mv.allItems, target_in_published_items: mv.allItemsTarget, china_in_published_items: mv.allItemsChina,
                 language_support: entry.language_support || "", note: mv.note || "",
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
      "Accessed " + accessDate + ".";
  }

  return {EMPTY: EMPTY, MIN_SHARE_DENOMINATOR: MIN_SHARE_DENOMINATOR, MIN_ALL_ITEMS_DENOMINATOR: MIN_ALL_ITEMS_DENOMINATOR, MIN_OUTLETS_FOR_OUTPUT_SHARE: MIN_OUTLETS_FOR_OUTPUT_SHARE,
          MIN_POPULATION: MIN_POPULATION, RELAY_NOT_MEASURED: RELAY_NOT_MEASURED, RELAY_WITHHELD: RELAY_WITHHELD,
          emptyCounts: emptyCounts, addInto: addInto, listDays: listDays, dayEntry: dayEntry, shiftDate: shiftDate,
          aggregateWindow: aggregateWindow, aggregateThemes: aggregateThemes, MEASURE_INDEX: MEASURE_INDEX, METRICS: METRICS, BASES: BASES, GRID: GRID, gridMetric: gridMetric,
          metricValue: metricValue, routeCount: routeCount, fillClass: fillClass, scaleCap: scaleCap, relayStatus: relayStatus,
          formatValue: formatValue, percentile: percentile, toCSV: toCSV, rankCountries: rankCountries, citation: citation, NAMES: NAMES, nameOf: nameOf};
}));
