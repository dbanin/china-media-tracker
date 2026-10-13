/* Pure computation for the tracker front end. No DOM access, so it can be tested in Node.
   Loaded in the browser as a global (window.TrackerCompute) and in Node via module.exports. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) { module.exports = factory(); }
  else { root.TrackerCompute = factory(); }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var EMPTY = {A: 0, B: 0, C: 0, N: 0, Ar: 0, Al: 0, Ah: 0, Br: 0, Bl: 0, Bh: 0, rev: 0, cls: 0, disc: 0, rel: 0,
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
  var RELAY_WITHHELD = "Unchecked state sourcing counts are withheld until hand coding of a fixed sample puts agreement with the model on unchecked sourcing versus independent journalism at kappa 0.6 or above.";

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
    var allItems = k.tdisc || 0;
    var relay = ctx.relay || {measured: true, publishable: true};
    /* Articles carrying official Chinese sourcing whose verification judgement is pending count as China
       coverage and in no other measure. Once judged they become unchecked state sourcing or independent journalism. */
    var china = a + b + c + p;
    var value = null, sparse = false, note = null, withheld = false;
    var num = def.measure === "a" ? aSel : def.measure === "b" ? b : def.measure === "china" ? china : null;
    var provisional = !!((def.relay || def.relayInside) && relay.provisional && !relay.publishable);
    if (def.relay && !(relay.publishable || relay.provisional)) {
      withheld = true;
      note = relay.measured ? RELAY_WITHHELD : RELAY_NOT_MEASURED;
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
    if (provisional && !note) note = RELAY_PROVISIONAL;
    /* Underlying items are counted per label, so unchecked sourcing alone is the combined figure less state origin. */
    return {value: value, chinaTotal: china, a: aSel, aAll: a, b: b, c: c, pending: p, ab: a + b, sparse: sparse, withheld: withheld, provisional: provisional, note: note,
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
    var vals = [];
    var countries = (latest && latest.countries) || {};
    pick.forEach(function (d) {
      var agg = aggregateWindow(months, d, windowDays);
      Object.keys(countries).forEach(function (iso) {
        if (iso === WORLDWIDE_ISO) return;   /* the scale colors the map, and the stratum is drawn nowhere on it */
        var entry = countries[iso];
        var ctx = ctxFor ? ctxFor(iso, entry, agg) : {};
        var mv = metricValue(agg.countries[iso], metric, entry.outlets_active, mode, agg.reviewed[iso], ctx);
        if (fillClass(entry, mv) === "value") vals.push(mv.value);
      });
    });
    var max = null;
    vals.forEach(function (v) { if (max === null || v > max) max = v; });
    return {cap: percentile(vals, 0.95), max: max, values: vals.length, dates: pick.length};
  }

  /* The theme counter's version of scaleCap: the 95th percentile of every positive theme count in one
     measure slot, pooled over every sampled end date, so the grid's shades keep their meaning while its
     own day moves. iso, when given, reads that country alone. */
  function themeScaleCap(months, endDates, windowDays, measureIndex, themeIds, iso, maxSamples) {
    var dates = endDates || [], n = maxSamples || 60, pick = [];
    if (dates.length <= n) pick = dates.slice();
    else for (var i = 0; i < n; i++) pick.push(dates[Math.round(i * (dates.length - 1) / (n - 1))]);
    var vals = [], max = null;
    pick.forEach(function (d) {
      var by = aggregateThemes(months, d, windowDays);
      Object.keys(by).forEach(function (c) {
        if (iso && c !== iso) return;
        (themeIds || Object.keys(by[c])).forEach(function (t) {
          var v = (by[c][t] || [0, 0, 0, 0])[measureIndex] || 0;
          if (v > 0) { vals.push(v); if (max === null || v > max) max = v; }
        });
      });
    });
    return {cap: percentile(vals, 0.95), max: max, values: vals.length, dates: pick.length};
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
  function keyTiers(cap, unit, steps) {
    unit = unit || WHOLE_UNIT;
    var n = steps || KEY_STEPS;
    var c = cap > 0 && isFinite(cap) ? cap : 1;
    var edges = stepEdges(c, n).map(function (e) {
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
    var tail = " in this window, reaching every country and drawn in none.";
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
    var stateOrigin = part("state_origin"), unchecked = part("unchecked"), independent = part("independent"), china = part("china_total");
    var share = stateOrigin === null || !china ? null : stateOrigin / china;
    var world30 = last30.state_origin, wires30 = typeof last30.state_origin_wires === "number" ? last30.state_origin_wires : 0;
    var topShare = null;
    if (top && distribution !== "wires") {
      if (distribution === "country" && typeof world30 === "number") topShare = world30 - wires30 > 0 ? top.state_origin / (world30 - wires30) : null;
      else topShare = top.share_of_world === undefined ? null : top.share_of_world;
    }
    return {state_origin: stateOrigin, unchecked: unchecked, independent: independent, china_total: china,
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
    var sum = {A: 0, B: 0, C: 0, pending: 0}, w = agg.countries[WORLDWIDE_ISO] || {};
    Object.keys(agg.countries).forEach(function (iso) {
      var k = agg.countries[iso] || {};
      sum.A += k.A || 0; sum.B += k.B || 0; sum.C += k.C || 0; sum.pending += k.pending || 0;
    });
    var withheld = at.unchecked === null;
    var china = sum.A + sum.B + sum.C;
    return {state_origin: sum.A, unchecked: withheld ? null : sum.B, independent: sum.C, china_total: china,
            state_share_of_china: china ? sum.A / china : null,
            state_origin_wires: w.A || 0, unchecked_wires: withheld ? null : (w.B || 0), independent_wires: w.C || 0,
            china_total_wires: (w.A || 0) + (w.B || 0) + (w.C || 0), pending: sum.pending, pending_wires: w.pending || 0};
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
              top_outlet: top.top_outlet || null, top_route: top.top_route || null};
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
    return {iso: best.iso, state_origin: best.n, share_of_world: total ? best.n / total : null,
            top_outlet: same ? top.top_outlet || null : null, top_route: same ? top.top_route || null : null};
  }

  /* Display names for the internal codes. The codes stay in the data files; readers never see them. */
  var NAMES = {A: "State origin", B: "Unchecked state sourcing", C: "Independent journalism", N: "Not relevant", not_relevant: "Not relevant",
               pending: "Official Chinese sourcing, verification pending"};
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
  /* Fills the four findings_template sentences from meta.findings: percentages to one decimal,
     the top country's name from the existing lookup, and the ownership rate ratio as the top
     group's per-outlet rate over the next highest group's, rounded to one decimal. Returns null
     when there is nothing to fill, so the caller hides the box instead of showing empty prose. */
  function fillFindingsTemplate(templates, findings, names, ownershipLabels) {
    if (!templates || !findings || !findings.all_time) return null;
    var f = findings.all_time, tc = findings.top_country, own = findings.by_ownership || {};
    var label = function (id) { return id ? ((ownershipLabels && ownershipLabels[id]) || id) : "No"; };
    /* Outlets with no sourced owner are not a group anyone can compare against, so they stay out
       of both the per outlet ranking and the largest share. */
    var groups = (own.groups || []).filter(function (g) {
      return g && g.id !== "unassessed" && g.per_outlet_rate !== null && g.per_outlet_rate !== undefined;
    });
    var sorted = groups.slice().sort(function (a, b) { return b.per_outlet_rate - a.per_outlet_rate; });
    var rateRatio = sorted.length >= 2 && sorted[1].per_outlet_rate ? sorted[0].per_outlet_rate / sorted[1].per_outlet_rate : null;
    var topGroupId = (own.top && own.top.id) || (sorted[0] && sorted[0].id);
    var byArticles = groups.slice().sort(function (a, b) { return (b.articles || 0) - (a.articles || 0); });
    var groupTotal = groups.reduce(function (t, g) { return t + (g.articles || 0); }, 0);
    var largest = byArticles[0];
    var vals = {
      state_share_of_china: findingsPct1(f.state_share_of_china),
      since: findings.since || "the start of data collection",
      top_country: tc ? ((names && names[tc.iso]) || tc.iso) : "no country yet",
      top_country_count: tc && tc.state_origin !== undefined ? Number(tc.state_origin).toLocaleString("en-US") : "n/a",
      top_country_share: tc && tc.share_of_world !== undefined && tc.share_of_world !== null ? Math.round(tc.share_of_world * 100) + "%" : "n/a",
      top_carrier: tc && tc.top_outlet ? tc.top_outlet.name : "no single outlet",
      rate_ratio: rateRatio === null ? "an unmeasured multiple of" : rateRatio.toFixed(1) + " times",
      top_ownership_group: label(topGroupId),
      next_ownership_group: sorted[1] ? label(sorted[1].id).toLowerCase() : "other",
      largest_group: largest ? label(largest.id).toLowerCase() : "no",
      largest_group_share: largest && groupTotal ? Math.round(100 * largest.articles / groupTotal) + "%" : "n/a"
    };
    return templates.map(function (t) {
      return String(t).replace(/\{(\w+)\}/g, function (whole, key) {
        return Object.prototype.hasOwnProperty.call(vals, key) ? vals[key] : whole;
      });
    });
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
  function collectorBadge(collector) {
    if (collector === "self_hosted") return {label: "relayed", title: "Collected only from the owner's machine, because the hosted runner cannot reach it"};
    if (collector === "both") return {label: "also relayed", title: "Also polled from the owner's machine; the hosted runner polls it too and fetches its pages"};
    return null;
  }
  function nOf(k, one, many) { return k + " " + (k === 1 ? one : many); }
  function relayOutletsSummary(rc) {
    rc = rc || {};
    return nOf(rc.outlets || 0, "outlet", "outlets") + " collected only there, " + (rc.shared_outlets || 0) + " also polled by the hosted runner";
  }
  /* The relay heartbeat as a short clause list and full sentences for the status block. Empty when the
     owner's machine has no outlets of either kind. */
  function relayCollectorNotes(rc) {
    var out = {short: [], parts: []};
    if (!rc) return out;
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
  /* The sentence under the running total of items screened. */
  function rediscoveryNote(meta) {
    var r = rediscoveryOf(meta);
    return r ? "Before " + r.fixed_from + ", items a feed kept listing were counted again after a prune, an estimated " +
      Math.round(r.estimated_share * 100) + " percent of this total; see the methodology." : "";
  }
  /* The same caveat for % of all its news, whose denominator carries the repeats unevenly by country, when
     the window starts before fixed_from (a windowStart of "" or null means from the first day). */
  function rediscoveryShareNote(meta, windowStart) {
    var r = rediscoveryOf(meta);
    if (!r || (windowStart && windowStart >= r.fixed_from)) return "";
    return "Before " + r.fixed_from + ", items a feed kept listing were counted again after a prune, so the published items this share divides by are too many, by an estimated " +
      Math.round(r.estimated_share * 100) + " percent overall and unevenly by country, and the share reads low over those days; see the methodology.";
  }

  var planted = {summarise: plantedSummarise, emptyState: plantedEmptyState, detectorLabel: plantedDetectorLabel, DETECTOR_LABELS: PLANTED_DETECTOR_LABELS};

  return {EMPTY: EMPTY, WORLDWIDE_ISO: WORLDWIDE_ISO, WORLDWIDE_NAME: WORLDWIDE_NAME, worldwideLegendLine: worldwideLegendLine,
          DISTRIBUTIONS: DISTRIBUTIONS, DISTRIBUTION_NAMES: DISTRIBUTION_NAMES, distributionKeeps: distributionKeeps, distributionIsos: distributionIsos,
          distributionTotals: distributionTotals, distributionFindings: distributionFindings, stateOriginInWindow: stateOriginInWindow, allTimeInWindow: allTimeInWindow, topCountryInWindow: topCountryInWindow, MIN_SHARE_DENOMINATOR: MIN_SHARE_DENOMINATOR, MIN_ALL_ITEMS_DENOMINATOR: MIN_ALL_ITEMS_DENOMINATOR, MIN_OUTLETS_FOR_OUTPUT_SHARE: MIN_OUTLETS_FOR_OUTPUT_SHARE,
          RELAY_NOT_MEASURED: RELAY_NOT_MEASURED, RELAY_WITHHELD: RELAY_WITHHELD, RELAY_PROVISIONAL: RELAY_PROVISIONAL, UNDERLYING_SUMMED: UNDERLYING_SUMMED,
          emptyCounts: emptyCounts, addInto: addInto, listDays: listDays, dayEntry: dayEntry, shiftDate: shiftDate,
          aggregateWindow: aggregateWindow, aggregateThemes: aggregateThemes, MEASURE_INDEX: MEASURE_INDEX, METRICS: METRICS, BASES: BASES, GRID: GRID, gridMetric: gridMetric, basisAvailable: basisAvailable,
          metricValue: metricValue, routeCount: routeCount, fillClass: fillClass, scaleCap: scaleCap, relayStatus: relayStatus,
          formatValue: formatValue, percentile: percentile, themeScaleCap: themeScaleCap, CAP_UPDATE_NOTE: CAP_UPDATE_NOTE,
          KEY_STEPS: KEY_STEPS, MEASURE_NAMES: MEASURE_NAMES, BASIS_NAMES: BASIS_NAMES, metricParts: metricParts, lowerFirst: lowerFirst, PROPER_OPENERS: PROPER_OPENERS, WHOLE_UNIT: WHOLE_UNIT, keyUnit: keyUnit,
          stepEdges: stepEdges, roundSig: roundSig, formatKeyNumber: formatKeyNumber, keyIndex: keyIndex, keyTiers: keyTiers, toCSV: toCSV, rankCountries: rankCountries, citation: citation, NAMES: NAMES, nameOf: nameOf,
          fillFindingsTemplate: fillFindingsTemplate, wholeWordSnippet: wholeWordSnippet, translateLinks: translateLinks, contentRouteName: contentRouteName, ROUTE_FALLBACK_LABELS: ROUTE_FALLBACK_LABELS, withRouteFallbacks: withRouteFallbacks, themesChangeMarks: themesChangeMarks, planted: planted,
          leaningSourceWeak: leaningSourceWeak, leaningSourceKind: leaningSourceKind, leaningSources: leaningSources, leaningSourcesSentence: leaningSourcesSentence, leaningWeakCaveat: leaningWeakCaveat, isUsRater: isUsRater,
          collectorBadge: collectorBadge, relayOutletsSummary: relayOutletsSummary, relayCollectorNotes: relayCollectorNotes,
          stabilityStatusText: stabilityStatusText, rediscoveryOf: rediscoveryOf, rediscoveryNote: rediscoveryNote, rediscoveryShareNote: rediscoveryShareNote};
}));
