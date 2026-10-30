/* Pure computation for the tracker front end. No DOM access, so it can be tested in Node.
   Loaded in the browser as a global (window.TrackerCompute) and in Node via module.exports. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) { module.exports = factory(); }
  else { root.TrackerCompute = factory(); }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* U: undetermined, who wrote or commissioned it could not be settled; in no category count or denominator (export.py). */
  var EMPTY = {A: 0, B: 0, C: 0, N: 0, U: 0, Ar: 0, Al: 0, Ah: 0, Br: 0, Bl: 0, Bh: 0, rev: 0, cls: 0, disc: 0, rel: 0,
               fetched: 0, paywalled: 0, failed: 0, blocked: 0, pending: 0, uniqA: 0, uniqAB: 0,
               tdisc: 0, ttarget: 0, tchina: 0, ta: 0, tb: 0, polls: 0, sat: 0, miss: 0, mpolls: 0};

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

  /* The worldwide distribution stratum: the press release wires, whose releases reach audiences in every
     country. It is listed beside the countries under the World Bank's code for the world, is never added
     into any country, has no population and no outline on the map. */
  var WORLDWIDE_ISO = "WLD";
  var WORLDWIDE_NAME = "Worldwide distribution";

  /* The Distribution toggle. Every figure that sums across countries follows it:
       both     the country outlets and the worldwide wires together, which is the whole dataset
       country  the country outlets alone: the stratum is dropped from every sum, row and line
       wires    the worldwide wires alone: only the stratum remains
     The stratum's own figures never change under it; the toggle decides whether they are in the sum. */
  var DISTRIBUTIONS = ["both", "country", "wires"];
  var DISTRIBUTION_NAMES = {both: "Both", country: "Country outlets", wires: "Worldwide wires"};
  function distributionKeeps(distribution, iso) {
    if (distribution === "country") return iso !== WORLDWIDE_ISO;
    if (distribution === "wires") return iso === WORLDWIDE_ISO;
    return true;
  }
  /* The keys of a per country object, or of a list of codes, that the distribution keeps. */
  function distributionIsos(countries, distribution) {
    var isos = Array.isArray(countries) ? countries : Object.keys(countries || {});
    return isos.filter(function (iso) { return distributionKeeps(distribution, iso); });
  }

  /* Sum per-country counts over the window ending at endDate.
     windowDays null means all time up to and including endDate.
     distribution (optional) drops the countries the Distribution toggle hides; absent means both.

     Underlying items (uniqA, uniqAB) are not summed: a story placed on several days of the window would
     count once per day. Each day of a schema 12 export carries the item ids behind its counts
     (underlying_items), and a country's figure is the number of distinct ids over the window. world holds
     the same figures across every country kept, where a story placed in several countries is one item.
     A day from an older export carries no ids; its per day figures are then added as they are, and
     underlyingSummed says that the window's underlying items are a per day sum, an upper bound. */
  function aggregateWindow(months, endDate, windowDays, distribution) {
    var out = {countries: {}, reviewed: {}, routes: {}, arrivals: {}, ceilingDays: [], relayIncompleteDays: [], olderRulesetDays: [],
               world: {uniqA: 0, uniqAB: 0}, underlyingSummed: false};
    if (!endDate) return out;
    var start = windowDays ? shiftDate(endDate, -(windowDays - 1)) : null;
    var keep = function (iso) { return distributionKeeps(distribution, iso); };
    var ids = {}, worldIds = {A: {}, B: {}}, summed = {}, worldSummed = {A: 0, B: 0};
    listDays(months).forEach(function (d) {
      if (d > endDate) return;
      if (start && d < start) return;
      var e = dayEntry(months, d);
      if (!e) return;
      if (e.llm_ceiling_hit) out.ceilingDays.push(d);
      if (e.relay_incomplete) out.relayIncompleteDays.push(d);
      if (e.labels_on_older_ruleset) out.olderRulesetDays.push(d);
      var items = e.underlying_items;
      Object.keys(e.countries || {}).filter(keep).forEach(function (c) {
        if (!out.countries[c]) out.countries[c] = emptyCounts();
        addInto(out.countries[c], e.countries[c]);
        var k = e.countries[c] || {};
        if (!items || (!items[c] && (k.uniqA || k.uniqAB))) {
          var s = summed[c] || (summed[c] = {A: 0, B: 0});
          var sb = Math.max(0, (k.uniqAB || 0) - (k.uniqA || 0));
          s.A += k.uniqA || 0; s.B += sb; worldSummed.A += k.uniqA || 0; worldSummed.B += sb;
          if (k.uniqA || k.uniqAB) out.underlyingSummed = true;
          return;
        }
        var own = ids[c] || (ids[c] = {A: {}, B: {}}), src = items[c] || {};
        ["A", "B"].forEach(function (cat) {
          (src[cat] || []).forEach(function (id) { own[cat][id] = 1; worldIds[cat][id] = 1; });
        });
      });
      Object.keys(e.reviewed || {}).filter(keep).forEach(function (c) {
        if (!out.reviewed[c]) out.reviewed[c] = {A: 0, B: 0, C: 0, N: 0};
        var r = e.reviewed[c];
        out.reviewed[c].A += r.A || 0; out.reviewed[c].B += r.B || 0; out.reviewed[c].C += r.C || 0; out.reviewed[c].N += r.N || 0;
      });
      addNested(out.routes, pickKeys(e.routes, keep));
      addNested(out.arrivals, pickKeys(e.arrivals, keep));
    });
    var size = function (o) { return Object.keys(o).length; };
    Object.keys(out.countries).forEach(function (c) {
      var own = ids[c] || {A: {}, B: {}}, s = summed[c] || {A: 0, B: 0};
      var nA = size(own.A) + s.A, nB = size(own.B) + s.B;
      out.countries[c].uniqA = nA;
      out.countries[c].uniqAB = nA + nB;
    });
    var wA = size(worldIds.A) + worldSummed.A, wB = size(worldIds.B) + worldSummed.B;
    out.world = {uniqA: wA, uniqAB: wA + wB};
    return out;
  }
  function pickKeys(obj, keep) {
    var out = {};
    Object.keys(obj || {}).forEach(function (k) { if (keep(k)) out[k] = obj[k]; });
    return out;
  }

  /* Share metrics are undefined on tiny denominators: two A items out of two is not a 100 percent country. */
  var MIN_SHARE_DENOMINATOR = 5;
  /* A share of monitored output needs a real stream of items behind it. */
  var MIN_ALL_ITEMS_DENOMINATOR = 50;
  /* ...and enough outlets that the stream describes a country rather than a handful of feeds. */
  var MIN_OUTLETS_FOR_OUTPUT_SHARE = 5;

  /* measure: which count the metric reads (a, b, china). There is no combined state-linked measure: it added
     candidates still waiting for the model to judged counts, mixing a measured quantity with an unmeasured
     one, so the waiting candidates are shown only as their own row in the country panel and the CSV. relay: the metric is unverified relay,
     which is shown once the hand coding study has cleared it, shown marked provisional until then, and
     withheld when hand coding puts kappa below the threshold or a model stability check on the current
     prompt does; a passing stability check never clears it. relayInside: the metric is a sum that contains unverified relay without
     being it. Such a metric is never withheld, because it is a real quantity either way, but it carries
     the provisional marking for as long as the relay count inside it is provisional. */
  var METRICS = {
    count_a: {label: "State origin articles", format: "int", measure: "a"},
    per_outlet_a: {label: "State origin articles per monitored outlet", format: "dec", measure: "a"},
    share_of_output_a: {label: "% of all its news: state origin articles as a share of every item the monitored outlets published", format: "pct", measure: "a", allItems: true},
    count_b: {label: "Unchecked state sourcing articles", format: "int", measure: "b", relay: true},
    per_outlet_b: {label: "Unchecked state sourcing articles per monitored outlet", format: "dec", measure: "b", relay: true},
    share_of_output_b: {label: "% of all its news: unchecked state sourcing articles as a share of every item the monitored outlets published", format: "pct", measure: "b", allItems: true, relay: true},
    count_china: {label: "All China coverage: every article that concerns China", format: "int", measure: "china", relayInside: true},
    per_outlet_china: {label: "All China coverage per monitored outlet", format: "dec", measure: "china", relayInside: true},
    share_of_output_china: {label: "% of all its news: all China coverage as a share of every item the monitored outlets published", format: "pct", measure: "china", allItems: true, relayInside: true},
    share_a: {label: "Share of China coverage that is state origin", format: "pct", measure: "a"},
    share_b: {label: "Share of China coverage that is unchecked state sourcing", format: "pct", measure: "b", relay: true}
  };

  var RELAY_NOT_MEASURED = "Unchecked state sourcing has not been measured: the verification stage has not run. It is not zero.";
  var RELAY_PROVISIONAL = "Provisional: unchecked state sourcing is one model's judgement and has not been checked against hand coding yet.";
  /* Said wherever underlying items are shown from data that predates the per day item ids (schema 12). */
  var UNDERLYING_SUMMED = "Underlying items here are summed day by day, because this data predates the item ids that count each item once per window, so an item placed on several days counts once for each.";
  /* Undetermined items (meta.undetermined, code U): the route filter's undetermined option draws them as a
     layer of their own, marked undetermined and not counted. They never rejoin state origin. */
  var UNDETERMINED_LAYER = "Undetermined, not counted: who wrote or commissioned these could not be settled, because the model named no credit line, stamp, byline or copy match, or a sponsored disclosure the rules did not find on the article. As in the original study, they are excluded from every count.";
  var UNDETERMINED_NO_SHARE = "Undetermined items are in no share: they are excluded from every count and denominator.";
  var RELAY_WITHHELD = "Unchecked state sourcing counts are withheld until hand coding of a fixed sample puts agreement with the model on unchecked sourcing versus independent journalism at kappa 0.7 or above.";

  /* Returns {value, chinaTotal, ab, a, b, pending, sparse, withheld, note} for one country under a metric and mode.
     ctx carries what the counts do not:
       topOutlets    outlets behind the published-items denominator
       relay         {measured, publishable, provisional} for this country; absent means publishable
       noRelayOutput the data predates the relay count among published items (schema 5), so that share is unknown
       routeCount    state origin in the selected route or arrival, replacing the state origin count
       worldwide     the row is the worldwide distribution stratum: its share of output divides by everything the
                     wires themselves published, so the minimum outlet count, which guards against a handful of
                     feeds standing for a country, does not apply */
  function metricValue(counts, metric, outletsActive, mode, reviewedCounts, ctx) {
    ctx = ctx || {};
    var a, b, c, p, u = 0;
    var k = counts || emptyCounts();
    if (mode === "reviewed") {
      var r = reviewedCounts || {A: 0, B: 0, C: 0, N: 0};
      a = r.A; b = r.B; c = r.C; p = 0;
    } else {
      a = k.A; b = k.B; c = k.C; p = k.pending || 0; u = k.U || 0;
    }
    var def = METRICS[metric] || {};
    var byRoute = ctx.routeCount !== undefined && ctx.routeCount !== null;
    var aSel = byRoute ? ctx.routeCount : a;
    var allItems = k.tdisc || 0;
    var relay = ctx.relay || {measured: true, publishable: true};
    /* Articles carrying official Chinese sourcing whose verification judgement is pending count as China
       coverage and in no other measure. Once judged they become unchecked state sourcing or independent journalism. */
    /* Undetermined items (U) are in no measure and no denominator, China coverage included. */
    var china = a + b + c + p;
    var value = null, sparse = false, note = null, withheld = false;
    /* ctx.routeUndetermined: the route filter shows the undetermined layer, which is in no count. */
    var undeterminedLayer = !!(byRoute && def.measure === "a" && ctx.routeUndetermined);
    var num = def.measure === "a" ? aSel : def.measure === "b" ? b : def.measure === "china" ? china : null;
    var provisional = !!((def.relay || def.relayInside) && relay.provisional && !relay.publishable);
    if (def.relay && !(relay.publishable || relay.provisional)) {
      withheld = true;
      note = relay.measured ? RELAY_WITHHELD : RELAY_NOT_MEASURED;
    } else if (undeterminedLayer && metric === "share_a") {
      sparse = true;
      note = UNDETERMINED_NO_SHARE;
    } else if (byRoute && def.measure !== "a") {
      sparse = true;
      note = "The route filter applies to state origin only.";
    } else {
      switch (metric) {
        case "count_a": case "count_b": case "count_china": value = num; break;
        case "share_a": case "share_b":
          sparse = china > 0 && china < MIN_SHARE_DENOMINATOR; value = china >= MIN_SHARE_DENOMINATOR ? num / china : null; break;
        case "per_outlet_a": case "per_outlet_b": case "per_outlet_china": value = outletsActive ? num / outletsActive : null; break;
        case "share_of_output_a": case "share_of_output_b": case "share_of_output_china":
          if (metric === "share_of_output_b" && ctx.noRelayOutput) { sparse = true; note = "This share needs data from a newer export, which counts unchecked state sourcing among the published items."; }
          else if (mode === "reviewed") { sparse = true; note = "The share of monitored output is not available for human-reviewed labels only."; }
          else if (byRoute) { sparse = true; note = "The share of monitored output is not available by route, because the published items are not split by route."; }
          else if (!ctx.worldwide && ctx.topOutlets !== undefined && ctx.topOutlets !== null && ctx.topOutlets < MIN_OUTLETS_FOR_OUTPUT_SHARE) {
            sparse = allItems > 0 || china > 0;
            note = "Fewer than " + MIN_OUTLETS_FOR_OUTPUT_SHARE + " monitored outlets, so a share of their output would describe a handful of feeds, not a country.";
          } else if (allItems < MIN_ALL_ITEMS_DENOMINATOR) {
            sparse = allItems > 0 || china > 0;
            note = "Fewer than " + MIN_ALL_ITEMS_DENOMINATOR + " items were published by the monitored outlets in this window, so a share is not shown.";
          } else value = (metric === "share_of_output_china" ? (k.tchina || 0) : metric === "share_of_output_a" ? (k.ta || 0) : (k.tb || 0)) / allItems;
          break;
        default: value = null;
      }
    }
    if (sparse && !note) note = "Fewer than " + MIN_SHARE_DENOMINATOR + " China items in this window, so a share is not shown. Switch to a count metric to see them.";
    if (undeterminedLayer && !note) note = UNDETERMINED_LAYER;
    if (provisional && !note) note = RELAY_PROVISIONAL;
    /* Underlying items are counted per label, so unchecked sourcing alone is the combined figure less state origin. */
    return {value: value, chinaTotal: china, a: aSel, aAll: a, b: b, c: c, pending: p, undetermined: u, undeterminedLayer: undeterminedLayer, ab: a + b, sparse: sparse, withheld: withheld, provisional: provisional, note: note,
            underlyingB: Math.max(0, (k.uniqAB || 0) - (k.uniqA || 0)), allItemsB: k.tb || 0,
            allItems: allItems, allItemsA: k.ta || 0, allItemsChina: k.tchina || 0, underlying: k.uniqA || 0};
  }

  /* State origin in the window for one route or arrival: filter {kind: "route" | "arrival", id}. */
  function routeCount(agg, iso, filter) {
    if (!filter || !filter.id) return null;
    var src = filter.kind === "arrival" ? agg.arrivals : agg.routes;
    return ((src && src[iso]) || {})[filter.id] || 0;
  }

  /* Theme counts over the same window as aggregateWindow.
     Returns {iso: {themeId: [all China coverage, combined slot, state origin, unchecked state sourcing]}}.
     The combined slot (state origin, unchecked state sourcing and candidates still waiting for the model)
     stays in the data files, but no measure reads it, since it mixes judged and unjudged counts.
     Files written before the fourth slot existed carry three; the relay slot then reads zero. */
  function aggregateThemes(months, endDate, windowDays, distribution) {
    var out = {};
    if (!endDate) return out;
    var start = windowDays ? shiftDate(endDate, -(windowDays - 1)) : null;
    listDays(months).forEach(function (d) {
      if (d > endDate || (start && d < start)) return;
      var e = dayEntry(months, d);
      if (!e || !e.themes) return;
      distributionIsos(e.themes, distribution).forEach(function (iso) {
        var src = e.themes[iso];
        var dst = out[iso] || (out[iso] = {});
        Object.keys(src || {}).forEach(function (t) {
          var v = src[t] || [], cur = dst[t] || (dst[t] = [0, 0, 0, 0]);
          cur[0] += v[0] || 0; cur[1] += v[1] || 0; cur[2] += v[2] || 0; cur[3] += v[3] || 0;
        });
      });
    });
    return out;
  }
  /* Which slot of a theme count each Measure reads. */
  var MEASURE_INDEX = {china: 0, a: 2, b: 3};

  /* The grid behind the Measure and Basis toggles. No per capita basis is offered: population is a poor
     denominator for what outlets publish. Population is still read, only for the population column of
     the CSV of the current view (rankCountries). */
  /* The denominators, widening from the country's own China coverage to its whole output.
     Share of China coverage is meaningless for the All China coverage measure, which is its own
     denominator, so GRID leaves that pair out and the interface disables the button. */
  var BASES = ["count", "per_outlet", "share_of_china", "share_of_output"];
  var GRID = {
    a: {count: "count_a", per_outlet: "per_outlet_a", share_of_china: "share_a", share_of_output: "share_of_output_a"},
    b: {count: "count_b", per_outlet: "per_outlet_b", share_of_china: "share_b", share_of_output: "share_of_output_b"},
    china: {count: "count_china", per_outlet: "per_outlet_china", share_of_output: "share_of_output_china"}
  };
  /* An unavailable pair falls back within the same measure, never to another measure's number. A measure
     that no longer exists (a saved view of the removed state-linked measure) falls back to state origin. */
  function gridMetric(measure, basis) { var g = GRID[measure] || GRID.a; return g[basis] || g.count || GRID.a.count; }
  function basisAvailable(measure, basis) { return !!(GRID[measure] || GRID.a)[basis]; }

  /* Coverage class for the fill. Distinguishes absence of data from absence of content.
       nocoverage  no monitored outlets and not in the gaps file
       gap         no working feed could be found; reason recorded
       inactive    outlets registered but all inactive
       withheld    the metric contains unchecked state sourcing, which is not measured, or failed its reliability check
       unreadable  monitored, but no active outlet's language has a keyword list, and nothing was found
       nodata      monitored, but no China coverage in the window (share metrics undefined)
       sparse      monitored, but this value cannot be computed here: the denominator is too small, or the
                   view the reader chose does not carry it. Never painted as an observed zero.
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
     means the same number on every date. At most maxSamples end dates are read, spread evenly.
     The cap is a function of the shipped data alone, so it holds until the next export; each export
     adds days and recomputes it, so it moves between updates, and CAP_UPDATE_NOTE says so in the keys. */
  var CAP_UPDATE_NOTE = "Each data update, about once a day, recomputes the scale over the timeline as it then stands, so as new days arrive a shade can stand for a different number than it did a week earlier.";
  function scaleCap(months, endDates, latest, metric, windowDays, mode, ctxFor, maxSamples) {
    var dates = endDates || [], n = maxSamples || 60, pick = [];
    if (dates.length <= n) pick = dates.slice();
    else for (var i = 0; i < n; i++) pick.push(dates[Math.round(i * (dates.length - 1) / (n - 1))]);
    var vals = [], fullVals = [];
    var countries = (latest && latest.countries) || {};
    var firstDay = listDays(months)[0];
    pick.forEach(function (d) {
      var agg = aggregateWindow(months, d, windowDays);
      /* A window that reaches back before the first collected day holds only part of its days. */
      var full = !windowDays || !firstDay || shiftDate(d, -(windowDays - 1)) >= firstDay;
      Object.keys(countries).forEach(function (iso) {
        if (iso === WORLDWIDE_ISO) return;   /* the scale colors the map, and the stratum is drawn nowhere on it */
        var entry = countries[iso];
        var ctx = ctxFor ? ctxFor(iso, entry, agg) : {};
        var mv = metricValue(agg.countries[iso], metric, entry.outlets_active, mode, agg.reviewed[iso], ctx);
        if (fillClass(entry, mv) === "value") {
          vals.push(mv.value);
          if (full) fullVals.push(mv.value);
        }
      });
    });
    var max = null;
    vals.forEach(function (v) { if (max === null || v > max) max = v; });
    /* breaks: the cut points that give each of the KEY_STEPS shades about an equal share of the pooled
       values, for a measure such as all China coverage that runs from a handful of articles to thousands,
       where steps up to the cap leave most of the map in the darkest two shades. */
    /* They are pooled over the dates whose window is complete, when there are any: the short windows of the
       first weeks would otherwise fill the lower shades and push every full window to the dark end. */
    var pool = fullVals.length ? fullVals : vals, breaks = [];
    for (var b = 1; b < KEY_STEPS; b++) breaks.push(percentile(pool, b / KEY_STEPS));
    return {cap: percentile(vals, 0.95), max: max, values: vals.length, dates: pick.length,
            breaks: pool.length ? breaks : null};
  }

  /* The theme counter's version of scaleCap: the 95th percentile of every positive theme count in one
     measure slot, pooled over every sampled end date, so the grid's shades keep their meaning while its
     own day moves. iso, when given, reads that country alone; without it the worldwide distribution
     stratum is left out, since the grid these shades color never has a row for it (themeGridRows). */
  function themeScaleCap(months, endDates, windowDays, measureIndex, themeIds, iso, maxSamples) {
    var dates = endDates || [], n = maxSamples || 60, pick = [];
    if (dates.length <= n) pick = dates.slice();
    else for (var i = 0; i < n; i++) pick.push(dates[Math.round(i * (dates.length - 1) / (n - 1))]);
    var vals = [], max = null;
    pick.forEach(function (d) {
      var by = aggregateThemes(months, d, windowDays);
      Object.keys(by).forEach(function (c) {
        if (iso ? c !== iso : c === WORLDWIDE_ISO) return;
        (themeIds || Object.keys(by[c])).forEach(function (t) {
          var v = (by[c][t] || [0, 0, 0, 0])[measureIndex] || 0;
          if (v > 0) { vals.push(v); if (max === null || v > max) max = v; }
        });
      });
    });
    return {cap: percentile(vals, 0.95), max: max, values: vals.length, dates: pick.length};
  }
  /* The rows of the theme grid, which compares countries: the worldwide distribution stratum is not a
     country, so it never has a row, under any Distribution mode, sort or window; its themes stay in the
     theme mix beside the grid. rows are {iso, name, n, t}; sortKey, when given, ranks by that theme,
     then by n, then by name. Returns every country row in order (all) and the first limit of them (shown). */
  function themeGridRows(rows, sortKey, limit) {
    var all = (rows || []).filter(function (r) { return r && r.iso !== WORLDWIDE_ISO; }).sort(function (a, b) {
      if (sortKey) {
        var d = ((b.t || {})[sortKey] || 0) - ((a.t || {})[sortKey] || 0);
        if (d) return d;
      }
      return b.n - a.n || String(a.name).localeCompare(String(b.name));
    });
    return {all: all, shown: limit ? all.slice(0, limit) : all.slice()};
  }

  /* ------------------------------------------------------------ color key
     Seven steps whose edges follow a square law up to the cap. The edges are snapped to what the key
     prints, and the map colors with the snapped edges, so every printed tier is exactly the set of
     values that takes its shade:
       counts        whole numbers; a step that holds no whole number is dropped, never printed twice
       other values  two significant figures, which keeps every printed bound distinct and above zero
                     at any scale (consecutive square law edges differ by at least 44 percent)
     A share of monitored output is a small fraction of one percent almost everywhere, so its key reads
     per 1,000 published items, as the tooltip does. */
  var KEY_STEPS = 7;
  var MEASURE_NAMES = {a: "State origin", b: "Unchecked state sourcing", china: "All China coverage"};
  var BASIS_NAMES = {count: "Number of articles", per_outlet: "Per outlet we monitor", share_of_china: "% of its China coverage", share_of_output: "% of all its news"};
  /* A sentence case label set inside a sentence: its first letter goes lower case unless the label opens
     with a proper noun (China, Chinese, a place, or a country name passed in names) or an acronym. Every
     other letter keeps its case, because a capital after the first word of a sentence case label is a
     proper noun on purpose. Use this, never toLowerCase, on any label or unit placed mid-sentence. */
  var PROPER_OPENERS = ["China", "Chinese", "Taiwan", "Hong Kong", "Macau", "Macao", "Tibet", "Xinjiang", "Beijing", "Belt and Road", "Xinhua"];
  function lowerFirst(text, names) {
    var s = String(text === null || text === undefined ? "" : text);
    if (!s) return s;
    var c0 = s.charAt(0), c1 = s.charAt(1);
    if (c0.toLowerCase() === c0) return s;
    if (c1 && c1.toUpperCase() === c1 && c1.toLowerCase() !== c1) return s;
    var opens = function (n) {
      n = String(n || "");
      if (!n || s.indexOf(n) !== 0) return false;
      var next = s.charAt(n.length);
      return !next || !/[A-Za-z\u00C0-\u024F]/.test(next);
    };
    if (PROPER_OPENERS.some(opens) || (names || []).some(opens)) return s;
    return c0.toLowerCase() + s.slice(1);
  }
  /* The Measure and Basis a metric sits at in the grid, for naming it the way the buttons do. */
  function metricParts(metric) {
    var hit = {measure: (METRICS[metric] || {}).measure || null, basis: null};
    Object.keys(GRID).forEach(function (m) {
      Object.keys(GRID[m]).forEach(function (b) { if (GRID[m][b] === metric) { hit.measure = m; hit.basis = b; } });
    });
    return hit;
  }
  var WHOLE_UNIT = {factor: 1, suffix: "", whole: true, per1000: false};
  function keyUnit(metric) {
    var def = METRICS[metric] || {};
    if (def.format === "int") return WHOLE_UNIT;
    if (def.format === "pct" && def.allItems) return {factor: 1000, suffix: "", whole: false, per1000: true};
    if (def.format === "pct") return {factor: 100, suffix: "%", whole: false, per1000: false};
    return {factor: 1, suffix: "", whole: false, per1000: false};
  }
  function stepEdges(cap, steps) {
    var out = [], n = steps || KEY_STEPS;
    for (var i = 1; i < n; i++) out.push(cap * Math.pow(i / n, 2));
    return out;
  }
  function roundSig(x, sig) {
    if (!x || !isFinite(x)) return 0;
    var p = Math.pow(10, sig - 1 - Math.floor(Math.log(Math.abs(x)) / Math.LN10));
    return Math.round(x * p) / p;
  }
  /* A number in the key's own unit: v is a raw value, already rounded where the key rounds it. */
  function formatKeyNumber(v, unit, sig) {
    var x = v * unit.factor;
    if (unit.whole) return Math.round(x).toLocaleString("en-US");
    x = roundSig(x, sig || 2);
    var d = Math.max(0, (sig || 2) - 1 - Math.floor(Math.log(Math.abs(x)) / Math.LN10 + 1e-9));
    var s = x.toFixed(Math.min(d, 12));
    if (s.indexOf(".") !== -1) s = s.replace(/0+$/, "").replace(/\.$/, "");
    if (Math.abs(x) >= 1000) s = Number(s).toLocaleString("en-US", {maximumFractionDigits: 12});
    return s + unit.suffix;
  }
  /* Which step a positive value takes: the number of snapped edges at or below it. */
  function keyIndex(v, edges) {
    var i = 0;
    while (i < edges.length && v >= edges[i]) i++;
    return i;
  }
  /* The key for one cap: the snapped edges the map colors with, and one tier per step that can hold a
     value, each carrying the step index of its shade. */
  function keyTiers(cap, unit, steps, breaks) {
    unit = unit || WHOLE_UNIT;
    var n = steps || KEY_STEPS;
    var c = cap > 0 && isFinite(cap) ? cap : 1;
    /* With breaks (scaleCap's equal share cut points, n - 1 of them) the edges are those, not steps up to the cap. */
    var raw = breaks && breaks.length === n - 1 ? breaks : stepEdges(c, n);
    var edges = raw.map(function (e) {
      return unit.whole ? Math.max(1, Math.ceil(e - 1e-9)) : roundSig(e * unit.factor, 2) / unit.factor;
    });
    var tiers = [];
    for (var i = 0; i < n; i++) {
      var lo = i === 0 ? 0 : edges[i - 1], hi = i < edges.length ? edges[i] : Infinity, label;
      if (unit.whole) {
        var a = Math.max(1, lo), b = hi - 1;
        if (hi !== Infinity && b < a) continue;
        label = hi === Infinity ? formatKeyNumber(a, unit) + " or more" : (a === b ? formatKeyNumber(a, unit) : formatKeyNumber(a, unit) + " to " + formatKeyNumber(b, unit));
        tiers.push({index: i, lo: a, hi: hi === Infinity ? null : b, label: label});
      } else {
        if (hi !== Infinity && !(hi > lo)) continue;
        label = i === 0 ? "Above 0, under " + formatKeyNumber(hi, unit)
          : hi === Infinity ? formatKeyNumber(lo, unit) + " or more"
          : formatKeyNumber(lo, unit) + " to " + formatKeyNumber(hi, unit);
        tiers.push({index: i, lo: lo, hi: hi === Infinity ? null : hi, label: label});
      }
    }
    return {edges: edges, tiers: tiers, unit: unit, cap: c};
  }

  function formatValue(v, fmt) {
    if (v === null || v === undefined || isNaN(v)) return "n/a";
    if (fmt === "pct") return (v * 100).toFixed(1) + "%";
    if (fmt === "dec") return v.toFixed(2);
    return String(v);
  }

  /* A lone carriage return breaks a row as surely as a newline does, so it forces quoting too. A value
     that a spreadsheet would run rather than read (a leading =, +, - or @, or a leading tab or return
     that hides one) is quoted and prefixed with an apostrophe, which spreadsheets read as "this is text".
     Plain numbers are exempt, so a negative figure stays a number. */
  var CSV_FORMULA = /^[=+\-@\t\r]/;
  var CSV_NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;
  function csvEscape(v) {
    if (v === null || v === undefined) return "";
    var s = String(v);
    if (CSV_FORMULA.test(s) && !CSV_NUMBER.test(s)) return '"\'' + s.replace(/"/g, '""') + '"';
    if (/[",\n\r]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  function toCSV(rows, columns) {
    var lines = [columns.map(csvEscape).join(",")];
    rows.forEach(function (r) { lines.push(columns.map(function (c) { return csvEscape(r[c]); }).join(",")); });
    return lines.join("\n") + "\n";
  }

  function relayStatus(relay) {
    if (!relay || relay.publishable) return "published";
    if (!relay.measured) return "not measured";
    return relay.provisional ? "provisional" : "withheld";
  }

  /* Ranked country rows for the current view, used by the bar chart, the CSV export and the table.
     Unchecked state sourcing is left blank wherever it is withheld or unmeasured, so a CSV never carries it as
     zero; provisional counts are written, and relay_status says that is what they are.

     Withholding one column is worthless if the columns beside it subtract to the same number, so when the
     relay count is withheld every sum that contains it leaves with it, and, where the shown value is one of
     those sums, so do the counts it would be differenced against.

     A route filter narrows state origin and nothing else, so under one the counts that are not split by
     route are left blank rather than written beside a filtered figure as though they described it. */
  function rankCountries(agg, latest, metric, mode, names, ctxFor, distribution) {
    var rows = [];
    var def = METRICS[metric] || {};
    var carriesRelay = !!(def.relay || def.relayInside);
    var countries = (latest && latest.countries) || {};
    distributionIsos(countries, distribution).forEach(function (iso) {
      var entry = countries[iso];
      var ctx = ctxFor ? ctxFor(iso, entry, agg) : {};
      var mv = metricValue(agg.countries[iso], metric, entry.outlets_active, mode, agg.reviewed[iso], ctx);
      var cls = fillClass(entry, mv);
      var status = relayStatus(ctx.relay), published = status === "published" || status === "provisional";
      var byRoute = ctx.routeCount !== undefined && ctx.routeCount !== null && def.measure === "a";
      /* contains the relay count; would be differenced against the shown value; not filtered by the route */
      var hasB = function (v) { return published ? v : ""; };
      var part = function (v) { return published || !carriesRelay ? v : ""; };
      var whole = function (v) { return byRoute ? "" : v; };
      var note = mv.note || (byRoute ? "Only state_origin is filtered by the route. The counts that are not split by route are left blank." : "");
      if (agg.underlyingSummed && !byRoute && (mv.underlying || mv.underlyingB)) note = (note ? note + " " : "") + UNDERLYING_SUMMED;
      rows.push({iso: iso, name: (names && names[iso]) || iso, value: mv.value, fill: cls, state_origin: part(mv.a), state_origin_underlying_items: whole(part(mv.underlying)),
                 undetermined: whole(mv.undetermined),
                 unverified_relay: whole(hasB(mv.b)), unverified_relay_underlying_items: whole(hasB(mv.underlyingB)), relay_status: status, official_sourcing_pending: whole(part(mv.pending)), independent: whole(mv.c),
                 china_total: whole(hasB(mv.chinaTotal)), outlets_active: entry.outlets_active, population: entry.population || "",
                 items_published_monitored_outlets: whole(mv.allItems), china_in_published_items: whole(hasB(mv.allItemsChina)),
                 language_support: entry.language_support || "", note: note,
                 warnings: (entry.warnings || []).map(function (w) { return w.text; }).join("; ")});
    });
    rows.sort(function (x, y) {
      var xv = x.value === null ? -1 : x.value, yv = y.value === null ? -1 : y.value;
      return yv - xv || x.name.localeCompare(y.name);
    });
    return rows;
  }

  /* The one line under the map legend for the worldwide distribution stratum, which the map cannot draw.
     mv is the stratum's metricValue for the view shown, so N follows the measure, basis, window, day and
     route exactly as a country's figure does; the noun follows the measure. Returns null when the stratum
     holds nothing in the window, or is absent, so the caller hides the line. */
  var WORLDWIDE_NOUNS = {a: "state-origin articles", b: "unchecked state sourcing articles", china: "China-related articles"};
  function worldwideLegendLine(mv, metric) {
    if (!mv) return null;
    var def = METRICS[metric] || {}, parts = metricParts(metric), measure = parts.measure || def.measure || "a";
    var count = measure === "b" ? mv.b : measure === "china" ? mv.chinaTotal : mv.a;
    if (!count || mv.withheld) return null;
    var noun = WORLDWIDE_NOUNS[measure] || "articles";
    var head = "Worldwide distribution via press release wires: ";
    var tail = " in this window, not tied to any one country and drawn in none.";
    var n = formatValue(mv.value, def.format);
    var body;
    if (mv.value === null || mv.value === undefined) body = count.toLocaleString("en-US") + " " + noun + ", with no " + lowerFirst(BASIS_NAMES[parts.basis] || "rate") + " shown" + (mv.note ? " (" + mv.note.replace(/\.$/, "") + ")" : "");
    else if (parts.basis === "per_outlet") body = n + " " + noun + " per wire monitored";
    else if (parts.basis === "share_of_china") body = noun + " at " + n + " of its China coverage";
    else if (parts.basis === "share_of_output") body = noun + " at " + n + " of every release the wires published";
    else body = n + (mv.provisional ? " (provisional)" : "") + " " + noun;
    return head + body + tail;
  }

  /* World totals under the Distribution toggle. totals is a block of sums over every country and the
     stratum (latest.json totals, or a global_series day); wld is the stratum's own block of the same
     shape, absent or null when the data predates it, which means zero. Country outlets subtracts the
     stratum from every numeric key the two share; worldwide wires returns the stratum alone, with every
     key of totals present so a reader of the result finds the same fields in every mode. */
  function distributionTotals(totals, wld, distribution) {
    if (!totals) return totals;
    if (distribution !== "country" && distribution !== "wires") return totals;
    var out = {};
    Object.keys(totals).forEach(function (k) {
      var t = totals[k], w = wld ? wld[k] : undefined;
      if (typeof t !== "number") { out[k] = t; return; }
      var wv = typeof w === "number" ? w : 0;
      out[k] = distribution === "country" ? t - wv : wv;
    });
    return out;
  }

  /* The findings block under the Distribution toggle. at is findings.all_time from meta.json, where the
     export writes beside each total its worldwide wires part (state_origin_wires and so on); last30 is
     findings.last_30d, {state_origin, state_origin_wires}; top is findings.top_country. Files written
     before the wires fields existed carry none, which reads as zero, so Country outlets then equals Both.
     Returns the four totals for the mode, the state share of China coverage recomputed over them (null
     when either side is missing or the denominator is zero), the top country's share of the world that
     the mode leaves (null under worldwide wires, where no country is ranked) and the wires' own count
     for the last 30 days. */
  function distributionFindings(at, last30, top, distribution) {
    at = at || {}; last30 = last30 || {};
    var part = function (key) {
      var total = at[key], wires = at[key + "_wires"];
      if (total === null || total === undefined) return null;
      var w = typeof wires === "number" ? wires : 0;
      if (distribution === "country") return total - w;
      if (distribution === "wires") return w;
      return total;
    };
    var stateOrigin = part("state_origin"), unchecked = part("unchecked"), independent = part("independent"), china = part("china_total"), undetermined = part("undetermined");
    var share = stateOrigin === null || !china ? null : stateOrigin / china;
    var world30 = last30.state_origin, wires30 = typeof last30.state_origin_wires === "number" ? last30.state_origin_wires : 0;
    var topShare = null;
    if (top && distribution !== "wires") {
      if (distribution === "country" && typeof world30 === "number") topShare = world30 - wires30 > 0 ? top.state_origin / (world30 - wires30) : null;
      else topShare = top.share_of_world === undefined ? null : top.share_of_world;
    }
    return {state_origin: stateOrigin, unchecked: unchecked, independent: independent, china_total: china, undetermined: undetermined,
            state_share_of_china: share, top_share_of_world: topShare,
            wires_last_30d: typeof last30.state_origin_wires === "number" ? last30.state_origin_wires : null,
            wires_all_time: typeof at.state_origin_wires === "number" ? at.state_origin_wires : null};
  }

  /* findings.all_time recomputed from the daily files: agg is aggregateWindow over every day up to the map's
     last complete day (windowDays null, distribution both), so the all time cards and the summary end on the
     day the map's All time view ends, where the export's totals also count the partial day it ran. Returns
     the fields of findings.all_time (state_origin, unchecked, independent, china_total, state_share_of_china
     and each total's worldwide wires part), which distributionFindings then reads under the toggle: the
     wires part is the stratum's own sum, so country outlets plus wires is Both, and each mode equals the sum
     over the countries the map keeps under it. at is the export's findings.all_time: unchecked stays null
     when the export withholds it, and with no aggregate (daily files absent) at is returned unchanged.
     pending and pending_wires are the articles still awaiting the model over the same days: the map's All
     China coverage counts them, the China-related total does not, so they are the whole difference. */
  function allTimeInWindow(agg, at) {
    at = at || {};
    if (!agg || !agg.countries) return at;
    var sum = {A: 0, B: 0, C: 0, U: 0, pending: 0}, w = agg.countries[WORLDWIDE_ISO] || {};
    Object.keys(agg.countries).forEach(function (iso) {
      var k = agg.countries[iso] || {};
      sum.A += k.A || 0; sum.B += k.B || 0; sum.C += k.C || 0; sum.U += k.U || 0; sum.pending += k.pending || 0;
    });
    var withheld = at.unchecked === null;
    /* Undetermined items (U) are not China-related articles in any count: they are excluded from every one. */
    var china = sum.A + sum.B + sum.C;
    return {state_origin: sum.A, unchecked: withheld ? null : sum.B, independent: sum.C, china_total: china,
            state_share_of_china: china ? sum.A / china : null,
            state_origin_wires: w.A || 0, unchecked_wires: withheld ? null : (w.B || 0), independent_wires: w.C || 0,
            china_total_wires: (w.A || 0) + (w.B || 0) + (w.C || 0), pending: sum.pending, pending_wires: w.pending || 0,
            undetermined: sum.U, undetermined_wires: w.U || 0};
  }

  /* State-origin articles over a window under the Distribution toggle: the sum of A over the countries
     of an aggregateWindow result, keeping only the countries the mode keeps, so it equals the sum of the
     ranked list for the same window and mode. When agg is null (no daily files loaded) it falls back to
     findings.last_30d, {state_origin, state_origin_wires}: the total under both, the total minus the
     wires under country outlets, the wires alone under worldwide wires. Returns null when neither
     source holds a number. */
  function stateOriginInWindow(agg, last30, distribution) {
    if (agg && agg.countries) {
      return distributionIsos(agg.countries, distribution).reduce(function (t, iso) {
        return t + ((agg.countries[iso] && agg.countries[iso].A) || 0);
      }, 0);
    }
    var total = last30 && typeof last30.state_origin === "number" ? last30.state_origin : null;
    var wires = last30 && typeof last30.state_origin_wires === "number" ? last30.state_origin_wires : 0;
    if (distribution === "wires") return last30 && typeof last30.state_origin_wires === "number" ? wires : (total === null ? null : 0);
    if (total === null) return null;
    return distribution === "country" ? total - wires : total;
  }

  /* The country with the most state origin over a window, an aggregateWindow result, and its share of
     the state origin the Distribution toggle keeps there (stateOriginInWindow): under both the wires are
     in the denominator, under country outlets they are not, and under worldwide wires no country is
     ranked, so it returns null. The stratum is never the top country; a tie goes to the later code, as
     in the export. top is findings.top_country: the daily files carry no outlets, so its outlet and route
     are kept when it names the same country. With no aggregate (daily files absent) it falls back to top,
     with the share distributionFindings takes from findings.last_30d. share_of_world keeps the export's
     field name; under country outlets it is the share of the country outlets' total. */
  function topCountryInWindow(agg, distribution, top, last30) {
    if (distribution === "wires") return null;
    if (!agg || !agg.countries) {
      if (!top || !top.iso) return null;
      return {iso: top.iso, state_origin: top.state_origin, share_of_world: distributionFindings(null, last30, top, distribution).top_share_of_world,
              underlying: typeof top.underlying === "number" ? top.underlying : null, underlying_summed: false,
              top_outlet: top.top_outlet || null, top_route: top.top_route || null, top_route_detail: top.top_route_detail || null};
    }
    var best = null;
    distributionIsos(agg.countries, distribution).forEach(function (iso) {
      if (iso === WORLDWIDE_ISO) return;
      var a = (agg.countries[iso] && agg.countries[iso].A) || 0;
      if (a > 0 && (!best || a > best.n || (a === best.n && iso > best.iso))) best = {iso: iso, n: a};
    });
    if (!best) return null;
    var total = stateOriginInWindow(agg, null, distribution);
    var same = !!(top && top.iso === best.iso);
    var uniq = agg.countries[best.iso].uniqA;
    return {iso: best.iso, state_origin: best.n, share_of_world: total ? best.n / total : null, world_total: total,
            underlying: typeof uniq === "number" ? uniq : null, underlying_summed: !!agg.underlyingSummed,
            top_outlet: same ? top.top_outlet || null : null, top_route: same ? top.top_route || null : null,
            top_route_detail: same ? top.top_route_detail || null : null};
  }

  /* The countries with the most state origin over a window, for the Top 10 block under What we found.
     agg is aggregateWindow over the 30 complete days ending the map's last complete day, under both
     distributions: the same aggregate the map and the ranked list read for that window, so each count is
     the country's count_a there (metricValue, every label, as the map opens) and each share its share_a,
     null below MIN_SHARE_DENOMINATOR as everywhere else on the site. The worldwide distribution stratum
     is not a country and is never ranked; its own count comes back beside the rows. Countries with no
     state origin in the window are left out. Ranks are competition ranks (1, 2, 2, 4): equal counts share
     a rank and are marked tied, ordered by name. The list stops at n rows; countries past the last row
     that tie with it come back as tiedBeyond, so a cut through a tie is stated, not hidden. endDate and
     windowDays only name the window (start, end), which the caller has already summed. */
  function topCountriesInWindow(agg, n, names, endDate, windowDays) {
    n = n || 10;
    var out = {rows: [], tiedBeyond: [], worldwide: null, max: 0, underlyingSummed: !!(agg && agg.underlyingSummed),
               window: endDate ? {start: windowDays ? shiftDate(endDate, -(windowDays - 1)) : null, end: endDate} : null};
    if (!agg || !agg.countries) return out;
    var all = [];
    Object.keys(agg.countries).forEach(function (iso) {
      var k = agg.countries[iso];
      var count = metricValue(k, "count_a", null, "all", null, {worldwide: iso === WORLDWIDE_ISO});
      if (iso === WORLDWIDE_ISO) { out.worldwide = {count: count.value || 0, underlying: count.underlying}; return; }
      if (!count.value) return;
      var share = metricValue(k, "share_a", null, "all", null, {});
      all.push({iso: iso, name: (names && names[iso]) || iso, count: count.value, underlying: count.underlying,
                share: share.value, shareSparse: share.sparse, chinaTotal: share.chinaTotal});
    });
    all.sort(function (x, y) { return y.count - x.count || x.name.localeCompare(y.name); });
    var perCount = {};
    all.forEach(function (r, i) {
      r.rank = i && r.count === all[i - 1].count ? all[i - 1].rank : i + 1;
      perCount[r.count] = (perCount[r.count] || 0) + 1;
    });
    all.forEach(function (r) { r.tied = perCount[r.count] > 1; });
    out.rows = all.slice(0, n);
    var last = out.rows[out.rows.length - 1];
    out.tiedBeyond = last ? all.slice(n).filter(function (r) { return r.count === last.count; }) : [];
    out.max = out.rows.length ? out.rows[0].count : 0;
    return out;
  }

  /* Display names for the internal codes. The codes stay in the data files; readers never see them. */

  var NAMES = {A: "State origin", B: "Unchecked state sourcing", C: "Independent journalism", N: "Not relevant", not_relevant: "Not relevant",
               pending: "Official Chinese sourcing, verification pending", U: "Undetermined: who wrote or commissioned it could not be settled"};
  function nameOf(code) { return NAMES[code] || code; }

  function citation(accessDate, author) {
    return (author || "China State Media Tracker project") + ". China State Media Tracker: daily counts of Chinese state-origin and state-sourced news content by country. " +
      "Accessed " + accessDate + ".";
  }

  /* ------------------------------------------------------- reader-facing content
     The "What we found" box and the map/tooltip route naming both read content.json,
     which carries the site's prose. These two helpers are pure so they can be tested
     without a DOM, the same way every metric above is. */
  function findingsPct1(v) { return v === null || v === undefined ? "n/a" : (v * 100).toFixed(1) + "%"; }
  /* The generated findings phrases follow DECISIONS 6: "most" or "mostly" only above half, a tie said as a
     tie, and underlying items beside placements. A leader entry is the export's {id, name, count, total,
     share, tied, tied_names, network} (_leader in pipeline/export.py); an entry from an export before
     2026-10-17 has no total, so its share and ties are unknown and nothing is claimed about it. */
  function findingsPct0(v) { return Math.round(v * 100) + "%"; }
  function findingsInt(n) { return Number(n).toLocaleString("en-US"); }
  function findingsPlural(n, word) { return findingsInt(n) + " " + (n === 1 ? word : word + "s"); }
  function findingsList(items) {
    return items.length <= 1 ? items.join("") : items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
  }
  function leaderKnown(c) { return !!(c && typeof c.count === "number" && typeof c.total === "number" && c.total > 0); }
  function leaderTied(c) { return typeof c.tied === "number" && c.tied > 1; }
  /* "Network Paper 1 and Network Paper 2" when every tied outlet is named (three at most), otherwise
     "10 ACM outlets" (the network named when they all belong to one) or "10 outlets". */
  function tiedCarriers(c) {
    var names = c.tied_names || [];
    if (c.tied <= 3 && names.length === c.tied) return findingsList(names);
    return findingsInt(c.tied) + " " + (c.network ? c.network + " " : "") + "outlets";
  }
  /* The carrier clause of the top country sentence, starting with its comma, or "" when unknown. expected,
     when given, is the placements the sentence states: a carrier counted over other placements (another
     window, or a counting rule the carriers do not share) is left out rather than set beside them. */
  function findingsCarrierClause(c, expected) {
    if (!leaderKnown(c) || (typeof expected === "number" && c.total !== expected)) return "";
    if (leaderTied(c)) {
      if (c.tied * c.count === c.total) return ", spread evenly across " + tiedCarriers(c) + " (" + findingsInt(c.count) + " each)";
      return ", led jointly by " + tiedCarriers(c) + " at " + findingsInt(c.count) + " each (" + findingsPct0(c.tied * c.count / c.total) + " together)";
    }
    if (c.count / c.total > 0.5) return ", most of them (" + findingsPct0(c.count / c.total) + ") through " + c.name;
    return ", with " + c.name + " carrying the most (" + findingsInt(c.count) + ", " + findingsPct0(c.count / c.total) + ")";
  }
  /* The map tooltip's carrier line; windowText (" in the 30 complete days ending ...") or "". */
  function carrierTooltipLine(c, windowText) {
    if (!leaderKnown(c)) return null;
    var w = windowText || "";
    if (leaderTied(c)) {
      if (c.tied * c.count === c.total) return "Spread evenly across " + tiedCarriers(c) + " (" + findingsPlural(c.count, "placement") + " each" + w + ")";
      return "Led jointly by " + tiedCarriers(c) + " (" + findingsPlural(c.count, "placement") + " each, of " + findingsInt(c.total) + w + ")";
    }
    return (c.count / c.total > 0.5 ? "Carried mostly by " : "Largest carrier: ") + c.name + " (" + findingsInt(c.count) + " of " + findingsPlural(c.total, "placement") + w + ")";
  }
  /* Route names for tied routes: every one when the export listed them all, otherwise how many. */
  function tiedRoutes(r, label) {
    var ids = r.tied_ids || [];
    return ids.length === r.tied ? findingsList(ids.map(label)) : findingsInt(r.tied) + " signed routes";
  }
  function unsignedTail(r, sep) {
    return r.unattributed ? sep + findingsInt(r.unattributed) + " carried no route signature" : "";
  }
  /* The route sentence after the findings: r is findings.top_country.top_route_detail, whose total keeps
     the placements with no route signature, so "most" is over every placement, signed or not. */
  function findingsRouteSentence(r, countryName, label, expected) {
    if (!leaderKnown(r) || !countryName || (typeof expected === "number" && r.total !== expected)) return "";
    label = label || function (id) { return id; };
    if (leaderTied(r)) return "In " + countryName + ", no single signed route led: " + tiedRoutes(r, label) + " carried " + findingsInt(r.count) +
      " placements each of " + findingsInt(r.total) + unsignedTail(r, ", and ") + ".";
    if (r.count / r.total > 0.5) return "In " + countryName + ", most of it (" + findingsInt(r.count) + " of " + findingsPlural(r.total, "placement") + ") arrived by the " + label(r.id) + " route.";
    return "In " + countryName + ", the " + label(r.id) + " route was the most common signed route, with " + findingsInt(r.count) + " of " +
      findingsPlural(r.total, "placement") + unsignedTail(r, "; ") + ".";
  }
  /* The map tooltip's route line, over latest.json's per country top_route. */
  function routeTooltipLine(r, label) {
    if (!leaderKnown(r)) return null;
    label = label || function (id) { return id; };
    var tail = unsignedTail(r, "; ");
    if (leaderTied(r)) return "Most common signed routes, tied: " + tiedRoutes(r, label) + " (" + findingsInt(r.count) + " of " + findingsPlural(r.total, "placement") + " each" + tail + ")";
    if (r.count / r.total > 0.5) return "Mostly arrives as " + label(r.id) + " (" + findingsInt(r.count) + " of " + findingsPlural(r.total, "placement") + ")";
    return "Most common signed route: " + label(r.id) + " (" + findingsInt(r.count) + " of " + findingsPlural(r.total, "placement") + tail + ")";
  }
  /* Fills the findings_template sentences from meta.findings: percentages to one decimal, the top
     country's name from the existing lookup, and the ownership comparisons over the groups with a known
     owner. Outlets with no sourced owner are never the subject of a finding: they are not a group anyone
     can compare against, so they stay out of the per outlet ranking and the largest share, whatever
     by_ownership.top says (exports before 2026-10-17 let them lead). A sentence whose generated phrase
     cannot be made (no top country, fewer than two groups) is left out rather than filled with filler.
     Returns null when there is nothing to fill, so the caller hides the box instead of showing empty prose. */
  function fillFindingsTemplate(templates, findings, names, ownershipLabels) {
    if (!templates || !findings || !findings.all_time) return null;
    var f = findings.all_time, tc = findings.top_country, own = findings.by_ownership || {};
    var label = function (id) { return id ? ((ownershipLabels && ownershipLabels[id]) || id) : "No"; };
    var lower = function (id) { return label(id).toLowerCase(); };
    var groups = (own.groups || []).filter(function (g) {
      return g && g.id !== "unassessed" && g.per_outlet_rate !== null && g.per_outlet_rate !== undefined;
    });
    var sorted = groups.slice().sort(function (a, b) { return b.per_outlet_rate - a.per_outlet_rate; });
    var rateRatio = sorted.length >= 2 && sorted[1].per_outlet_rate ? sorted[0].per_outlet_rate / sorted[1].per_outlet_rate : null;
    var topGroupId = sorted[0] && sorted[0].id;
    var byArticles = groups.slice().sort(function (a, b) { return (b.articles || 0) - (a.articles || 0); });
    var groupTotal = groups.reduce(function (t, g) { return t + (g.articles || 0); }, 0);
    var outletTotal = groups.reduce(function (t, g) { return t + (g.outlets || 0); }, 0);
    var largest = byArticles[0];
    var rate2 = function (g) { return g.per_outlet_rate.toFixed(2); };
    var rateComparison = null;
    if (sorted.length >= 2 && sorted[0].per_outlet_rate > 0) {
      var topTied = sorted.filter(function (g) { return g.per_outlet_rate === sorted[0].per_outlet_rate; });
      var next = sorted[topTied.length];
      if (topTied.length > 1) {
        rateComparison = findingsList(topTied.map(function (g) { return lower(g.id); })) + " outlets publish state-origin articles at the same rate, " + rate2(sorted[0]) + " per outlet";
      } else if (!next.per_outlet_rate) {
        rateComparison = lower(sorted[0].id) + " outlets publish state-origin articles at " + rate2(sorted[0]) + " per outlet, and no other group with a known owner has published any";
      } else {
        var ratio = sorted[0].per_outlet_rate / next.per_outlet_rate;
        rateComparison = lower(sorted[0].id) + " outlets publish state-origin articles at " + (ratio < 1.05 ? "about the same rate as " : "about " + ratio.toFixed(1) + " times the rate of ") +
          lower(next.id) + " outlets (" + rate2(sorted[0]) + " against " + rate2(next) + " per outlet)";
      }
    }
    var largestComparison = null;
    if (largest && groupTotal) {
      var lTied = byArticles.filter(function (g) { return (g.articles || 0) === (largest.articles || 0); });
      var lShare = largest.articles / groupTotal;
      var outletsPart = function (g) { return outletTotal ? ", which make up " + findingsPct0(g.outlets / outletTotal) + " of those outlets" : ""; };
      if (lTied.length > 1) largestComparison = "state-origin placements in outlets with a known owner are split evenly between " +
        findingsList(lTied.map(function (g) { return lower(g.id); })) + " outlets (" + findingsPct0(lShare) + " each)";
      else if (lShare > 0.5) largestComparison = "most state-origin placements in outlets with a known owner (" + findingsPct0(lShare) + ") are in " + lower(largest.id) + " outlets" + outletsPart(largest);
      else largestComparison = "the largest share of state-origin placements in outlets with a known owner (" + findingsPct0(lShare) + ") is in " + lower(largest.id) + " outlets" + outletsPart(largest);
    }
    var items = tc && typeof tc.underlying === "number" ? " (" + findingsPlural(tc.underlying, "underlying item") + (tc.underlying_summed ? ", summed day by day" : "") + ")" : "";
    var vals = {
      state_share_of_china: findingsPct1(f.state_share_of_china),
      /* The share's numerator and denominator, so the sentence states what it divides. */
      state_origin_count: typeof f.state_origin === "number" ? findingsInt(f.state_origin) : "n/a",
      china_total_count: typeof f.china_total === "number" ? findingsInt(f.china_total) : "n/a",
      since: findings.since || "the start of data collection",
      top_country: tc ? ((names && names[tc.iso]) || tc.iso) : "no country yet",
      top_country_count: tc && tc.state_origin !== undefined ? Number(tc.state_origin).toLocaleString("en-US") : "n/a",
      top_country_share: tc && tc.share_of_world !== undefined && tc.share_of_world !== null ? Math.round(tc.share_of_world * 100) + "%" : "n/a",
      /* What the top country's share is of: the window's state origin, wires included, when it is known. */
      top_country_world: tc && typeof tc.world_total === "number" && tc.world_total > 0 ? "the world's " + findingsPlural(tc.world_total, "placement") + " in those days" : "the world's total",
      top_country_items: tc ? items : null,
      top_carrier: tc && tc.top_outlet ? tc.top_outlet.name : "no single outlet",
      top_carrier_clause: tc ? findingsCarrierClause(tc.top_outlet, typeof tc.state_origin === "number" ? tc.state_origin : undefined) : null,
      rate_ratio: rateRatio === null ? "an unmeasured multiple of" : rateRatio.toFixed(1) + " times",
      top_ownership_group: label(topGroupId),
      next_ownership_group: sorted[1] ? label(sorted[1].id).toLowerCase() : "other",
      ownership_rate_comparison: rateComparison,
      largest_group: largest ? label(largest.id).toLowerCase() : "no",
      largest_group_share: largest && groupTotal ? Math.round(100 * largest.articles / groupTotal) + "%" : "n/a",
      largest_group_comparison: largestComparison
    };
    var out = [];
    templates.forEach(function (t) {
      var dropped = false;
      var filled = String(t).replace(/\{(\w+)\}/g, function (whole, key) {
        if (!Object.prototype.hasOwnProperty.call(vals, key)) return whole;
        if (vals[key] === null) { dropped = true; return ""; }
        return vals[key];
      });
      if (!dropped) out.push(filled);
    });
    return out;
  }
  /* Older exports stored the tell as a raw character window, so it can start and end inside a word:
     trim both ends back to whole words, mark each cut with an ellipsis, and fold line breaks into
     spaces, keeping the matched phrase's offsets pointing at the same text. The export now trims on
     word boundaries itself and marks every cut with an ellipsis, so a snippet with an ellipsis at
     either edge is already whole words at both edges (an unmarked edge there is the text's own start
     or end) and is only folded, never cut again: a whole lowercase or uncased word such as
     "provincia" or Arabic text stays. */
  function wholeWordSnippet(snippet, start, end) {
    var s = String(snippet || ""), st = Math.max(0, Math.min(s.length, start || 0)), en = Math.max(st, Math.min(s.length, end || 0));
    var tidy = function (t) { return t.replace(/\s+/g, " "); };
    var pre = s.slice(0, st), mid = s.slice(st, en), post = s.slice(en), lead = false, trail = false;
    var exported = /^\s*\u2026/.test(s) || /\u2026\s*$/.test(s);
    if (pre && !exported && !/^[A-Z0-9"“(\[]/.test(pre)) {
      var sp = pre.search(/\s/);
      if (sp >= 0) { pre = pre.slice(sp); lead = true; }
    }
    if (post && !exported && !/[.!?"”)\]]\s*$/.test(post)) {
      var last = post.search(/\s\S*$/);
      if (last >= 0) { post = post.slice(0, last); trail = true; }
    }
    pre = tidy(pre).replace(/^\s+/, ""); mid = tidy(mid); post = tidy(post).replace(/\s+$/, "");
    if (lead) pre = "\u2026 " + pre;
    if (trail) post = post + " \u2026";
    return {snippet: pre + mid + post, start: pre.length, end: pre.length + mid.length};
  }
  /* Translation is left to the reader's own translator: the site sends nothing anywhere. For an outlet that
     does not publish in English, two links: the headline and quote as text, and the article page itself.
     Returns null for English or unknown languages, so English items stay uncluttered. */
  function translateLinks(lang, text, articleUrl) {
    var l = String(lang || "").toLowerCase();
    if (!l || l === "en" || l.indexOf("en-") === 0) return null;
    var t = String(text || "").replace(/\s+/g, " ").trim().slice(0, 4000);
    var out = {};
    if (t) out.text = "https://translate.google.com/?sl=auto&tl=en&op=translate&text=" + encodeURIComponent(t);
    if (articleUrl && /^https?:\/\//i.test(articleUrl)) out.page = "https://translate.google.com/translate?sl=auto&tl=en&u=" + encodeURIComponent(articleUrl);
    return (out.text || out.page) ? out : null;
  }
  /* A route id to the plain name a reader recognises, read from content.routes. Falls back to the
     id itself, so a route the content file has not named yet still shows something rather than
     nothing. */
  function contentRouteName(routes, id) {
    var r = routes && routes[id];
    return (r && r.plain_name) || id;
  }
  /* The preliminary figures banner under the title (content.preliminary_banner). It always says what state
     origin counts and that undetermined items are excluded from every count, as in the original study; they
     never rejoin, so nothing about them is read from meta. Only the unchecked state sourcing sentences are
     chosen from meta, so the banner never says that figure is unchecked once its hand check has passed, nor
     provisional once it is withheld: meta.relay_human_study (passed, or a failed state) or the export's own
     provisional and publishable flags. Returns null without the content block. */
  function preliminaryStatuses(meta) {
    var m = meta || {}, hs = m.relay_human_study || {}, relay = "provisional";
    if (hs.passed === true || hs.state === "passed") relay = "passed";
    else if (hs.state === "failed" || (m.relay_measured === true && m.relay_publishable === false && m.relay_provisional === false)) relay = "withheld";
    return {relay: relay};
  }
  function preliminaryBanner(banner, meta) {
    if (!banner || !banner.lead || !banner.evidence) return null;
    var st = preliminaryStatuses(meta), out = [];
    var apart = banner.apart || {}, withheld = banner.withheld || {}, checked = banner.checked || {};
    out.push(st.relay === "provisional" || !banner.lead_settled ? banner.lead : banner.lead_settled);
    out.push(banner.evidence);
    if (banner.undetermined) out.push(banner.undetermined);
    if (st.relay === "provisional" && apart.relay) out.push(apart.relay);
    if (st.relay === "withheld" && withheld.relay) out.push(withheld.relay);
    var c = st.relay === "passed" ? "relay" : st.relay === "withheld" && checked.none_published ? "none_published" : "none";
    if (checked[c]) out.push(checked[c]);
    return {heading: banner.heading || "", headingEm: banner.heading_em || "", sentences: out, text: out.join(" "),
            linkText: banner.link_text || "", linkHref: banner.link_href || "#method", status: st};
  }
  /* Routes the interface names even when the exported meta.routes predates them: uncredited_copy, a
     model state origin label on an article the lookalike check sent (a near repeat of a credited
     state origin item, run without the credit). withRouteFallbacks adds a missing one to a meta.routes
     list, before unattributed as the pipeline orders it, so its card and select option always work. */
  var ROUTE_FALLBACK_LABELS = {uncredited_copy: "Uncredited copy of a credited state item"};
  function withRouteFallbacks(routes) {
    var list = (routes || []).slice();
    if (!list.length) return list;
    Object.keys(ROUTE_FALLBACK_LABELS).forEach(function (id) {
      if (list.some(function (r) { return r && r.id === id; })) return;
      var at = list.length;
      list.forEach(function (r, i) { if (r && r.id === "unattributed" && at === list.length) at = i; });
      list.splice(at, 0, {id: id, label: ROUTE_FALLBACK_LABELS[id]});
    });
    return list;
  }

  /* Planted bylines: pure helpers for the section built from data/planted.json. A flag means the
     byline, or an organisation the article cites, matches a persona or front named in a published
     investigation. It never means the byline is proven fictitious, and absence of a match proves
     nothing, so nothing here feeds a category count. */
  var PLANTED_DETECTOR_LABELS = {
    author_field: "byline in the author field",
    byline_head: "byline at the top of the text",
    front_cited: "names a front organisation"
  };
  function plantedDetectorLabel(detector) {
    var d = String(detector || "");
    return PLANTED_DETECTOR_LABELS[d] || d.replace(/_/g, " ");
  }
  /* Counts for the section: how many flags, personas, fronts and investigations the file holds,
     and the flags by country as a list sorted by count then ISO code. The file's own by_country
     tally is preferred, since the flags list is capped; when it is absent, or adds up to nothing
     while the flags list has entries, the flags are counted instead. The file's flags_total, when
     present, is the headline count. */
  function plantedSummarise(json) {
    var j = json && typeof json === "object" ? json : {};
    var flags = Array.isArray(j.flags) ? j.flags : [];
    var tally = {};
    var fileTally = j.by_country && typeof j.by_country === "object" && !Array.isArray(j.by_country) ? j.by_country : {};
    Object.keys(fileTally).forEach(function (iso) { var n = Number(fileTally[iso]); if (n > 0) tally[iso] = n; });
    var fromFile = Object.keys(tally).length > 0;
    if (!fromFile) {
      flags.forEach(function (f) { var iso = (f && f.country) || "unknown"; tally[iso] = (tally[iso] || 0) + 1; });
    }
    var byCountry = Object.keys(tally).map(function (iso) { return {iso: iso, n: tally[iso]}; });
    byCountry.sort(function (a, b) { return b.n - a.n || (a.iso < b.iso ? -1 : a.iso > b.iso ? 1 : 0); });
    var total = fromFile ? byCountry.reduce(function (acc, r) { return acc + r.n; }, 0) : flags.length;
    var fileTotal = Number(j.flags_total);
    if (j.flags_total !== undefined && j.flags_total !== null && isFinite(fileTotal) && fileTotal >= 0) total = Math.max(fileTotal, 0);
    return {
      flags: total,
      personas: Array.isArray(j.personas) ? j.personas.length : 0,
      fronts: Array.isArray(j.fronts) ? j.fronts.length : 0,
      operations: Array.isArray(j.operations) ? j.operations.length : 0,
      by_country: byCountry,
      list_version: j.list_version || null
    };
  }
  /* The sentence shown when no flag exists, including when the file has not been exported yet.
     The counts are left to the section's intro paragraph, which already states them. */
  function plantedEmptyState() {
    return "No byline in the monitored outlets has matched a listed persona since monitoring began.";
  }
  /* Themes version changes placed on a timeline of days: each lands on its own date, or on the first
     day after it when that date has no entry, and a change after the last day is left off. The title is
     the mark's tooltip. */
  function themesChangeMarks(days, changes) {
    var out = [];
    (changes || []).forEach(function (c) {
      if (!c || !c.date || !c.version) return;
      var idx = -1;
      for (var i = 0; i < (days || []).length; i++) { if (days[i] >= c.date) { idx = i; break; } }
      if (idx === -1) return;
      out.push({index: idx, date: c.date, version: c.version,
                title: "Themes version " + c.version + ": " + (c.change || "terms changed") + " (from " + c.date + ")"});
    });
    return out;
  }

  /* The three themes of themes version 2026.10.1 crossed with a grouping of outlets (political leaning by
     default), from outlets.json theme_cross ({china, A, cur, t: {theme: [articles, state origin]}}, the theme
     counter's basis: China coverage is state origin, unchecked state sourcing, independent journalism and
     candidates waiting for the model). Per group: the share of its China coverage carrying each theme, and
     the state origin share within those articles, each with its denominator. A share whose denominator is
     under minN (20 by default) is null, shown as too few. cur counts the China articles already tagged
     under the current themes version: until it equals china the theme counts are partial (provisional).
     Active outlets only, without the worldwide distribution wires, in one country when iso is given. */
  var THEME_CROSS_MIN_N = 20;

  /* While a themes version is still being applied to older articles (meta.themes_progress complete false),
     the counts of the themes it added or whose terms it changed are partial on older days: an older article
     keeps the tags of the version it was last tagged under, which may lack the theme or read it with other
     terms. The sentence the theme counter and the leaning table show then; "" once complete or with no
     progress record. labels: {theme id: label}. */
  function themesRetagNote(prog, labels) {
    if (!prog || prog.complete !== false || !prog.relevant) return "";
    labels = labels || {};
    var ids = [];
    var want = (prog.added || []).concat(prog.changed || []);
    Object.keys(labels).forEach(function (id) { if (want.indexOf(id) !== -1) ids.push(id); });   /* catalog order */
    var names = ids.map(function (id) { return labels[id]; });
    var list = names.length < 2 ? names.join("") : names.slice(0, -1).join(", ") + (names.length > 2 ? ", and " : " and ") + names[names.length - 1];
    return "Themes version " + prog.version + " is still being applied to older articles, newest first: " +
      (prog.current || 0).toLocaleString("en-US") + " of " + prog.relevant.toLocaleString("en-US") + " relevant articles carry its tags so far. " +
      "Until it is complete an older article keeps the tags of the version it was last tagged under" +
      (list ? ", so " + list + " " + (names.length > 1 ? "are" : "is") + " undercounted or counted with earlier terms on older days." : ".");
  }
  function themeCross(outlets, groups, key, themeIds, opts) {
    opts = opts || {};
    var minN = opts.minN || THEME_CROSS_MIN_N, iso = opts.iso || null;
    /* As for the share of monitored output, a group whose China coverage comes from fewer than
       MIN_OUTLETS_FOR_OUTPUT_SHARE outlets gets no share (fewOutlets): it would describe a handful of
       feeds, not a kind of outlet. */
    var minOutlets = opts.minOutlets || MIN_OUTLETS_FOR_OUTPUT_SHARE;
    function blank(g) {
      var r = {id: g[0], label: g[1], color: g[2] || null, outlets: 0, chinaOutlets: 0, china: 0, A: 0, cur: 0, t: {}};
      (themeIds || []).forEach(function (tid) { r.t[tid] = {n: 0, a: 0}; });
      return r;
    }
    var rows = (groups || []).map(blank), byId = {};
    rows.forEach(function (r) { byId[r.id] = r; });
    var total = blank(["all", "All outlets", null]);
    (outlets || []).forEach(function (o) {
      if (!o || !o.active || o.tier === "distribution_wire" || (iso && o.country !== iso)) return;
      var r = byId[o[key]] || byId.unassessed;
      if (!r) return;
      var x = o.theme_cross || null;
      [r, total].forEach(function (g) {
        g.outlets += 1;
        if (!x) return;
        g.china += x.china || 0; g.A += x.A || 0; g.cur += x.cur || 0;
        if (x.china) g.chinaOutlets += 1;
        (themeIds || []).forEach(function (tid) {
          var v = (x.t || {})[tid] || [0, 0];
          g.t[tid].n += v[0] || 0; g.t[tid].a += v[1] || 0;
        });
      });
    });
    function finish(g) {
      g.complete = g.cur >= g.china;
      g.fewOutlets = g.chinaOutlets > 0 && g.chinaOutlets < minOutlets;
      (themeIds || []).forEach(function (tid) {
        var c = g.t[tid];
        c.share = g.china >= minN && !g.fewOutlets ? c.n / g.china : null;
        c.soShare = c.n >= minN && !g.fewOutlets ? c.a / c.n : null;
      });
      return g;
    }
    rows.forEach(finish); finish(total);
    return {rows: rows, total: total, minN: minN, minOutlets: minOutlets, complete: total.complete};
  }

  /* Where an outlet's political leaning comes from. Media Bias/Fact Check is a US based rater that
     places outlets on a United States spectrum, so where it is the only source behind a leaning
     outside the United States the leaning is shown with a caution. The export writes the mark as
     leaning_source_weak (pipeline/registry.py leaning_source_weak); an older export without it is
     read the same way from the source URL and the country. State controlled and not assessed are
     not ratings, so they are never weak. */
  var US_RATER_HOSTS = ["mediabiasfactcheck.com"];
  var LEANING_NOT_RATED = {unassessed: true, state_controlled: true};
  function urlHost(u) {
    var m = /^[a-z]+:\/\/([^\/?#]+)/i.exec(String(u || ""));
    var h = m ? m[1].toLowerCase() : "";
    return h.indexOf("www.") === 0 ? h.slice(4) : h;
  }
  function isUsRater(u) {
    var h = urlHost(u);
    return US_RATER_HOSTS.some(function (r) { return h === r || h.slice(-(r.length + 1)) === "." + r; });
  }
  function leaningSourceWeak(o) {
    if (!o) return false;
    if (typeof o.leaning_source_weak === "boolean") return o.leaning_source_weak;
    if (LEANING_NOT_RATED[o.political_leaning || "unassessed"]) return false;
    if (o.country === "USA" || !isUsRater(o.leaning_source)) return false;
    return !(o.leaning_second_source && !isUsRater(o.leaning_second_source));
  }
  /* The kind of source behind one outlet's leaning: ownership for state controlled (the ownership
     source stands for it), us_rater, wikipedia, other, or null when not assessed. */
  function leaningSourceKind(o) {
    var lean = (o && o.political_leaning) || "unassessed";
    if (lean === "unassessed") return null;
    if (lean === "state_controlled") return "ownership";
    if (isUsRater(o.leaning_source)) return "us_rater";
    return /(^|\.)wikipedia\.org$/.test(urlHost(o.leaning_source)) ? "wikipedia" : "other";
  }
  /* The caution beside a leaning group in the table and its tooltip. */
  function leaningWeakCaveat(weak, outlets) {
    if (!weak) return "";
    return (weak !== outlets ? weak + " of " + outlets : weak === 1 ? "Leaning" : "All " + weak) +
      " from a US rater alone; treat with caution";
  }
  /* Tally of the leaning sources over a list of outlets, for the sentence that names them. */
  function leaningSources(outlets) {
    var t = {wikipedia: 0, us_rater: 0, us_rater_weak: 0, ownership: 0, other: 0, assessed: 0};
    (outlets || []).forEach(function (o) {
      var k = leaningSourceKind(o);
      if (!k) return;
      t[k] += 1; t.assessed += 1;
      if (leaningSourceWeak(o)) t.us_rater_weak += 1;
    });
    return t;
  }
  function leaningSourcesSentence(t) {
    if (!t || !t.assessed) return "";
    var parts = [];
    var n = function (k) { return t[k] + (t[k] === 1 ? " outlet" : " outlets"); };
    if (t.wikipedia) parts.push("Wikipedia media lists for " + n("wikipedia"));
    if (t.us_rater) parts.push("Media Bias/Fact Check, a US based rater, for " + n("us_rater") +
      (t.us_rater_weak ? " (" + t.us_rater_weak + " of them outside the United States, where its US scale is a weak guide)" : ""));
    if (t.other) parts.push("other named sources such as press freedom monitors, eurotopics and academic studies for " + n("other"));
    if (t.ownership) parts.push("the ownership source for " + (t.ownership === 1 ? "the 1 state controlled outlet" : "the " + t.ownership + " state controlled outlets"));
    var list = parts.length > 1 ? parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1] : parts[0];
    return "Where the leanings come from: " + list + ". " +
      (t.us_rater_weak ? "A leaning that rests on Media Bias/Fact Check alone outside the United States is marked in the table: treat it with caution. " : "");
  }

  /* The owner's machine collects some outlets alone (collector self_hosted, "relayed") and polls others
     beside the hosted runner, which still polls them and fetches their pages (collector both). A silent
     relay leaves only the first kind uncollected, so the two are never counted or badged as one. */
  /* retiredOn (meta.relay_collector.retired_on): the relay was stopped that day, so a self_hosted outlet is
     badged as stopped and a collector: both outlet, polled by the hosted runner alone since, carries no badge. */
  function collectorBadge(collector, retiredOn) {
    if (retiredOn) {
      if (collector === "self_hosted") return {label: "relay stopped", title: "Refuses the crawler from its server addresses. It was collected from the owner's network until that collection stopped on " + retiredOn + ", and is inactive since"};
      return null;
    }
    if (collector === "self_hosted") return {label: "relayed", title: "Collected only from the owner's machine, because the hosted runner cannot reach it"};
    if (collector === "both") return {label: "also relayed", title: "Also polled from the owner's machine; the hosted runner polls it too and fetches its pages"};
    return null;
  }
  function nOf(k, one, many) { return k + " " + (k === 1 ? one : many); }
  function relayOutletsSummary(rc) {
    rc = rc || {};
    if (rc.retired_on) return "stopped on " + rc.retired_on + "; " + nOf(rc.dropped_outlets || 0, "outlet", "outlets") + " it alone collected inactive since, in " +
      nOf(Object.keys(rc.dropped_by_country || {}).length, "country", "countries");
    return nOf(rc.outlets || 0, "outlet", "outlets") + " collected only there, " + (rc.shared_outlets || 0) + " also polled by the hosted runner";
  }
  /* The relay heartbeat as a short clause list and full sentences for the status block. Empty when the
     owner's machine has no outlets of either kind. */
  function relayCollectorNotes(rc, nameOf) {
    var out = {short: [], parts: []};
    if (!rc) return out;
    if (rc.retired_on) return retiredRelayNotes(rc, nameOf || function (c) { return c; });
    var only = rc.outlets || 0, shared = rc.shared_outlets || 0;
    if (!only && !shared) return out;
    var hours = Math.round(rc.hours_since_last_run || 0);
    var sharedClause = shared ? "The " + nOf(shared, "outlet", "outlets") + " it also polls, in " + nOf((rc.shared_countries || []).length, "country", "countries") +
      ", fall back to the hosted runner's hourly polls alone, which GitHub often runs late, so their short feeds may lose items between polls; their counts are not marked incomplete." : "";
    if (!rc.last_run) {
      out.short.push("the owner's collector has not reported yet");
      out.parts.push("The collector on the owner's machine has not reported a heartbeat yet" +
        (only ? ", so gaps in the " + nOf(only, "outlet", "outlets") + " only it collects, because the hosted runner cannot reach them, cannot yet be told from quiet days." : ".") +
        (sharedClause ? " " + sharedClause : ""));
    } else if (rc.stale) {
      out.short.push("the owner's collector has been silent " + nOf(hours, "hour", "hours"));
      out.parts.push("The collector on the owner's machine has not collected for " + nOf(hours, "hour", "hours") +
        (only ? ", so recent counts for the " + nOf(only, "outlet", "outlets") + " only it collects, in " + nOf((rc.countries || []).length, "country", "countries") + ", are incomplete." : ".") +
        (sharedClause ? " " + sharedClause : ""));
    }
    var gaps = (rc.incomplete_days || []).length;
    if (gaps) out.parts.push("It ran in too few hours on " + nOf(gaps, "day", "days") + ", marked in red along the timeline" +
      (only ? "; counts for the outlets only it collects are incomplete on those days" : "") +
      (shared ? (only ? ", while" : ";") + " the hosted runner still polled the outlets it shares" : "") + ".");
    return out;
  }

  /* The relay was stopped on rc.retired_on: what it left inactive, said once, and the red days before then,
     which still explain the historic counts. Matches the Known limits paragraph of pipeline/methodology.py. */
  function retiredRelayNotes(rc, nameOf) {
    var out = {short: [], parts: []};
    var dropped = rc.dropped_outlets || 0, day = rc.retired_on;
    var lost = (rc.countries_all_inactive || []).map(nameOf), part = (rc.countries_partial || []).length;
    var list = function (xs) { return xs.length < 2 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1]; };
    if (dropped) {
      out.short.push(nOf(dropped, "outlet", "outlets") + " that refuse the crawler inactive since " + day);
      out.parts.push("Until " + day + " a relay on the owner's machine collected the outlets that refuse the crawler from its server addresses. " +
        "Collecting from another network works around a site's choice to refuse the crawler, so it was stopped that day, and its " +
        nOf(dropped, "outlet", "outlets") + " are inactive since" +
        (lost.length ? ": every monitored outlet in " + list(lost) + ", which " + (lost.length === 1 ? "is" : "are") + " no longer monitored" + (part ? ", and some outlets in " + nOf(part, "country", "countries") + " more" : "") : (part ? ", in " + nOf(part, "country", "countries") : "")) +
        ". Counts from that day leave them out; what they published before stays in every figure.");
    }
    var gaps = (rc.incomplete_days || []).length;
    if (gaps) out.parts.push("Before then the relay ran in too few hours on " + nOf(gaps, "day", "days") + ", still marked in red along the timeline, because counts for the outlets only it collected are incomplete on those days.");
    return out;
  }

  /* The stability check state when no check under the current classifier instructions is on record, as
     one sentence matching pipeline/methodology.py: an older-prompt check kept as history
     (meta.relay_reliability_history), or none run yet, with when the next runs (meta.relay_study). Empty
     when a current check exists (meta.relay_reliability), which is described on its own, or meta is absent. */
  function stabilityStatusText(meta) {
    if (!meta || meta.relay_reliability) return "";
    var hist = meta.relay_reliability_history, st = meta.relay_study || {};
    var old = !!(hist && hist.stale !== false);
    var noun = old ? "a new one" : "one";
    var runs = st.auto === false ? " can be run by hand" : " runs";
    var need = st.min_pool, next = "";
    if (st.state === "waiting_for_labels" && need) next = noun + runs + " once " + need + " articles are labelled under the current instructions" + (st.pool !== null && st.pool !== undefined ? " (" + st.pool + " so far)" : "");
    else if (st.state === "due" || st.state === "waiting_for_budget") next = "enough articles are now labelled and " + noun + runs + " when the daily model allowance permits" + (st.earliest ? ", on " + st.earliest + " at the earliest" : "");
    else if (st.state === "submitted") next = noun + " has been submitted and is reported when it returns";
    else if (st.state === "done" || st.state === "not_needed") next = "none is needed, because the hand coding study decides publication";
    if (!old) return "No stability check has been run under the current instructions yet" + (next ? "; " + next : "") + ".";
    var kappa = hist.kappa_bc === null || hist.kappa_bc === undefined ? "n/a" : hist.kappa_bc.toFixed(2);
    return "The last stability check (" + ((hist.computed_at || "").slice(0, 10) || "date unknown") + ", " + (hist.n || 0) + " articles, kappa " + kappa +
      " on unchecked sourcing versus independent) was run under earlier instructions" + (hist.prompt_version ? ", dated " + hist.prompt_version : "") +
      " and is kept as history: it neither supports nor withholds any count" + (next ? ", and " + next : "") + ".";
  }

  /* Before meta.rediscovery.fixed_from an item a feed kept listing was counted again after each prune of
     rejected items, and those days stay as counted (pipeline/export.py rediscovery; the same figures in
     pipeline/methodology.py). Null, and every sentence below empty, for an export without the field. */
  function rediscoveryOf(meta) {
    var r = meta && meta.rediscovery;
    return r && typeof r.fixed_from === "string" && typeof r.estimated_share === "number" ? r : null;
  }
  /* The repeats as a whole percent of the running total of items screened at this export. estimated_share is
     their share of the measured_of items screened when it was measured; the repeats stopped at fixed_from, so
     the share of a total that has grown since is estimated_share * measured_of / articles_discovered. Without
     both totals, or before the total has grown past measured_of, estimated_share itself. Null without the field. */
  function rediscoveryPercent(meta) {
    var r = rediscoveryOf(meta);
    if (!r) return null;
    var total = meta.articles_discovered;
    if (typeof r.measured_of === "number" && typeof total === "number" && total > r.measured_of)
      return Math.round(100 * r.estimated_share * r.measured_of / total);
    return Math.round(r.estimated_share * 100);
  }
  /* The sentence under the running total of items screened. */
  function rediscoveryNote(meta) {
    var r = rediscoveryOf(meta);
    return r ? "Before " + r.fixed_from + ", items a feed kept listing were counted again after a prune, an estimated " +
      rediscoveryPercent(meta) + " percent of this total; see the methodology." : "";
  }
  /* The same caveat for % of all its news, whose denominator carries the repeats unevenly by country, when
     the window starts before fixed_from (a windowStart of "" or null means from the first day). */
  function rediscoveryShareNote(meta, windowStart) {
    var r = rediscoveryOf(meta);
    if (!r || (windowStart && windowStart >= r.fixed_from)) return "";
    return "Before " + r.fixed_from + ", items a feed kept listing were counted again after a prune, so the published items this share divides by are too many, by an estimated " +
      Math.round(r.estimated_share * 100) + " percent overall and unevenly by country, and the share reads low over those days; see the methodology.";
  }

  /* Licensed archive checks (data/archive_checks.json, written from sources/archive_checks.yaml by
     pipeline/archive_checks.py): a separate source and method, joined to no count, map, share, top 10 or
     finding. Every figure is read from the side file or summed here from its title rows, never typed into
     the page; the wording is in content.json (archive_check). */
  var ARCHIVE_NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
  function archiveInt(n) { return String(Math.round(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function archiveTitles(n) { return (n >= 0 && n < ARCHIVE_NUMBER_WORDS.length ? ARCHIVE_NUMBER_WORDS[n] : archiveInt(n)) + (n === 1 ? " title" : " titles"); }
  function archiveAnd(items) { return items.length < 2 ? items.join("") : items.slice(0, -1).join(", ") + " and " + items[items.length - 1]; }
  function archiveFill(tpl, tokens) {
    return String(tpl || "").replace(/\{(\w+)\}/g, function (m, k) { return Object.prototype.hasOwnProperty.call(tokens, k) ? String(tokens[k]) : m; });
  }
  function archiveList(data) {
    return data && Array.isArray(data.checks) ? data.checks.filter(function (c) { return c && Array.isArray(c.titles) && c.titles.length && c.totals; }) : [];
  }
  function archiveCountrySums(check) {
    var out = {};
    (check.titles || []).forEach(function (t) {
      var s = out[t.country] || (out[t.country] = {titles: [], china_relevant: 0, awaiting_model: 0, no_state_signature: 0, not_about_china: 0});
      s.titles.push(t.name);
      ["china_relevant", "awaiting_model", "no_state_signature", "not_about_china"].forEach(function (k) { s[k] += Number(t[k]) || 0; });
    });
    return out;
  }
  function archiveTokens(check) {
    var t = check.totals || {};
    return {source: check.source || "", short_name: check.short_name || check.source || "", period: check.period || "", scope: check.scope || "",
      method: check.method || "", run_date: check.run_date || "", ruleset: check.ruleset_version || "",
      titles: archiveInt((check.titles || []).length), titles_phrase: archiveTitles((check.titles || []).length),
      china: archiveInt(t.china_relevant), awaiting: archiveInt(t.awaiting_model), no_signature: archiveInt(t.no_state_signature),
      not_china: archiveInt(t.not_about_china), records: archiveInt(t.records), pulled: archiveInt(check.records_pulled),
      deduplicated: archiveInt(check.records_deduplicated), signatures: archiveInt(check.state_origin_signatures),
      signatures_note: check.state_origin_signatures_note || "",
      triggers: (check.triggers || []).map(function (tr) { return tr.label + " " + archiveInt(tr.count); }).join(", ")};
  }
  /* The one line in a country panel: the first check with titles from iso, or "" when none has any. The
     no-signature wording is used only when the check found no state origin signature at all. */
  function archiveCountryLine(data, iso, countryName, tpl) {
    tpl = tpl || {};
    var checks = archiveList(data);
    for (var i = 0; i < checks.length; i++) {
      var s = archiveCountrySums(checks[i])[iso];
      if (!s) continue;
      var a = s.awaiting_model;
      var tokens = archiveTokens(checks[i]);
      tokens.country = countryName || iso; tokens.names = archiveAnd(s.titles); tokens.titles_phrase = archiveTitles(s.titles.length);
      tokens.china = archiveInt(s.china_relevant); tokens.awaiting = archiveInt(a); tokens.no_signature = archiveInt(s.no_state_signature);
      tokens.awaiting_clause = archiveFill(a === 0 ? tpl.awaiting_none : a === 1 ? tpl.awaiting_one : tpl.awaiting_many, tokens);
      return archiveFill(Number(checks[i].state_origin_signatures) === 0 ? tpl.country_line : tpl.country_line_signatures, tokens);
    }
    return "";
  }
  var archiveCheck = {list: archiveList, countrySums: archiveCountrySums, tokens: archiveTokens, fill: archiveFill, countryLine: archiveCountryLine, int: archiveInt, and: archiveAnd};

  var planted = {summarise: plantedSummarise, emptyState: plantedEmptyState, detectorLabel: plantedDetectorLabel, DETECTOR_LABELS: PLANTED_DETECTOR_LABELS};

  /* The country panel's line for each sourced foreign state stake on an outlet (outlets.json foreign_state_stake,
     decision D11). No field means no stake is recorded, never that the outlet has none, so nothing is shown then.
     The status is said in words in the line itself (status_label is the visible tag), never only in a tooltip,
     which a touch screen never shows; STAKE_CAVEAT says the record is about ownership, not editorial control. */
  var STAKE_STATE_ADJ = {CHN: "Chinese"};
  var STAKE_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  var STAKE_STATUS_LABELS = {current: "Current", ended: "Ended", disputed: "Disputed"};
  var STAKE_CAVEAT = "A stake records ownership; it says nothing about editorial control.";
  function stakeDate(d) {
    var s = String(d || "");
    if (!/^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$/.test(s)) return s || "an unrecorded date";
    var p = s.split("-"), m = p.length > 1 ? STAKE_MONTHS[Number(p[1]) - 1] : null;
    if (p.length > 1 && !m) return s;
    if (p.length === 3) return Number(p[2]) + " " + m + " " + p[0];
    if (p.length === 2) return m + " " + p[0];
    return p[0];
  }
  function stakeOn(d) { return (/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(String(d || "")) ? "on " : "in ") + stakeDate(d); }
  function stakeNotes(stakes, countryName) {
    return (Array.isArray(stakes) ? stakes : []).filter(function (s) { return s && typeof s === "object"; }).map(function (s) {
      var who = STAKE_STATE_ADJ[s.state] ? STAKE_STATE_ADJ[s.state] + " state-linked stake" : "Foreign state-linked stake (" + ((countryName && countryName(s.state)) || s.state) + ")";
      var share = typeof s.share_percent === "number" && isFinite(s.share_percent) ? s.share_percent + "% of " : "an undisclosed share of ";
      var when = s.status === "ended" ? "from " + stakeDate(s.from) + ", ended for this outlet " + stakeOn(s.to)
        : s.status === "disputed" ? "since " + stakeDate(s.from) + "; sources disagree on whether it still reaches this outlet"
        : "since " + stakeDate(s.from) + ", still held according to a source dated " + stakeDate(s.as_of);
      return {text: who + ": " + share + (s.held_in || "an unnamed company") + ", held by " + (s.holder || "an unnamed holder") + ", " + when + ".",
              status: s.status, status_label: STAKE_STATUS_LABELS[s.status] || "Status unstated", caveat: STAKE_CAVEAT,
              source_url: s.source_url, source_title: s.source_title || "",
              status_url: s.status_source_url, status_title: s.status_source_title || "", note: s.note || ""};
    });
  }

  /* Outlets added for their China coverage or documented pro-China content, not for reach (outlets.json
     reach_selected false, RULES D12). They add to their country's counts because of how they were chosen, so the
     export warns on every country that has one (warning type china_selected), the country panel tags each such
     outlet with what its source documents and a link to it, and the top 10 marks its country. Data from before
     the field (schema 18 and older) reads as no outlet selected this way. */
  var SELECTION_LABEL = "selected for China coverage";
  var SELECTION_MARK = "\u2020";
  function selectionBadge(o) {
    if (!o || o.reach_selected !== false) return null;
    var basis = String(o.selection_basis || "").replace(/;\s*evidence:\s*\S+\s*$/, "").trim();
    var text = basis ? basis.charAt(0).toUpperCase() + basis.slice(1) + "." : "Selected for its China coverage, not reach.";
    return {label: SELECTION_LABEL, text: text, url: typeof o.selection_evidence === "string" ? o.selection_evidence : ""};
  }
  function chinaSelectedCountry(entry) {
    return !!(entry && Array.isArray(entry.warnings) && entry.warnings.some(function (w) { return w && w.type === "china_selected"; }));
  }
  function selectionTop10Note(names) {
    if (!names || !names.length) return "";
    var list = names.length === 1 ? names[0] : names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
    return SELECTION_MARK + " Figures for " + list + " include outlets added for their China coverage or documented pro-China content, not for their reach; they add to " +
      (names.length === 1 ? "its" : "those countries'") + " counts because of how they were chosen, and the country view names them.";
  }

  return {EMPTY: EMPTY, UNDETERMINED_LAYER: UNDETERMINED_LAYER, UNDETERMINED_NO_SHARE: UNDETERMINED_NO_SHARE, preliminaryBanner: preliminaryBanner, preliminaryStatuses: preliminaryStatuses, WORLDWIDE_ISO: WORLDWIDE_ISO, WORLDWIDE_NAME: WORLDWIDE_NAME, worldwideLegendLine: worldwideLegendLine,
          DISTRIBUTIONS: DISTRIBUTIONS, DISTRIBUTION_NAMES: DISTRIBUTION_NAMES, distributionKeeps: distributionKeeps, distributionIsos: distributionIsos,
          distributionTotals: distributionTotals, distributionFindings: distributionFindings, stateOriginInWindow: stateOriginInWindow, allTimeInWindow: allTimeInWindow, topCountryInWindow: topCountryInWindow, topCountriesInWindow: topCountriesInWindow, MIN_SHARE_DENOMINATOR: MIN_SHARE_DENOMINATOR, MIN_ALL_ITEMS_DENOMINATOR: MIN_ALL_ITEMS_DENOMINATOR, MIN_OUTLETS_FOR_OUTPUT_SHARE: MIN_OUTLETS_FOR_OUTPUT_SHARE,
          RELAY_NOT_MEASURED: RELAY_NOT_MEASURED, RELAY_WITHHELD: RELAY_WITHHELD, RELAY_PROVISIONAL: RELAY_PROVISIONAL, UNDERLYING_SUMMED: UNDERLYING_SUMMED,
          emptyCounts: emptyCounts, addInto: addInto, listDays: listDays, dayEntry: dayEntry, shiftDate: shiftDate,
          aggregateWindow: aggregateWindow, aggregateThemes: aggregateThemes, MEASURE_INDEX: MEASURE_INDEX, METRICS: METRICS, BASES: BASES, GRID: GRID, gridMetric: gridMetric, basisAvailable: basisAvailable,
          metricValue: metricValue, routeCount: routeCount, fillClass: fillClass, scaleCap: scaleCap, relayStatus: relayStatus,
          formatValue: formatValue, percentile: percentile, themeScaleCap: themeScaleCap, themeGridRows: themeGridRows, CAP_UPDATE_NOTE: CAP_UPDATE_NOTE,
          KEY_STEPS: KEY_STEPS, MEASURE_NAMES: MEASURE_NAMES, BASIS_NAMES: BASIS_NAMES, metricParts: metricParts, lowerFirst: lowerFirst, PROPER_OPENERS: PROPER_OPENERS, WHOLE_UNIT: WHOLE_UNIT, keyUnit: keyUnit,
          stepEdges: stepEdges, roundSig: roundSig, formatKeyNumber: formatKeyNumber, keyIndex: keyIndex, keyTiers: keyTiers, toCSV: toCSV, rankCountries: rankCountries, citation: citation, NAMES: NAMES, nameOf: nameOf,
          fillFindingsTemplate: fillFindingsTemplate, findingsCarrierClause: findingsCarrierClause, findingsRouteSentence: findingsRouteSentence, carrierTooltipLine: carrierTooltipLine, routeTooltipLine: routeTooltipLine, wholeWordSnippet: wholeWordSnippet, translateLinks: translateLinks, contentRouteName: contentRouteName, ROUTE_FALLBACK_LABELS: ROUTE_FALLBACK_LABELS, withRouteFallbacks: withRouteFallbacks, themesChangeMarks: themesChangeMarks, themeCross: themeCross, THEME_CROSS_MIN_N: THEME_CROSS_MIN_N, themesRetagNote: themesRetagNote, planted: planted,
          stakeNotes: stakeNotes, stakeDate: stakeDate, STAKE_CAVEAT: STAKE_CAVEAT, leaningSourceWeak: leaningSourceWeak, leaningSourceKind: leaningSourceKind, leaningSources: leaningSources, leaningSourcesSentence: leaningSourcesSentence, leaningWeakCaveat: leaningWeakCaveat, isUsRater: isUsRater,
          collectorBadge: collectorBadge, relayOutletsSummary: relayOutletsSummary, relayCollectorNotes: relayCollectorNotes,
          selectionBadge: selectionBadge, chinaSelectedCountry: chinaSelectedCountry, selectionTop10Note: selectionTop10Note, SELECTION_MARK: SELECTION_MARK, SELECTION_LABEL: SELECTION_LABEL,
          stabilityStatusText: stabilityStatusText, rediscoveryOf: rediscoveryOf, rediscoveryPercent: rediscoveryPercent, rediscoveryNote: rediscoveryNote, rediscoveryShareNote: rediscoveryShareNote, archiveCheck: archiveCheck};
}));
