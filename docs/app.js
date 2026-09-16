/* China state media tracker front end. Vanilla JavaScript and D3, no build step.
   Reads static JSON from data/. Every view must render with an empty dataset without throwing. */
(function () {
  "use strict";
  var C = window.TrackerCompute;
  var CITATION_AUTHOR = "Daniel Banin";

  var state = {
    metric: "count_a", measure: "a", basis: "count", route: null, windowDays: 30, themeSort: null, themeEnd: null, themePlaying: null, themeWindow: 30, mode: "all", endDate: null, selected: null, playing: null, zoomIso: null, zoomK: 1, lastCountry: null,
    meta: null, latest: null, series: [], months: {}, outlets: [], names: {}, officialNames: {}, numToIso: {}, topo: null,
    articlesCache: {}, scaleCache: {}
  };

  /* ------------------------------------------------------------------ data */
  function getJSON(url) {
    /* The standalone build inlines every data file under window.__TRACKER_DATA keyed by path. */
    if (window.__TRACKER_DATA && Object.prototype.hasOwnProperty.call(window.__TRACKER_DATA, url)) {
      return Promise.resolve(window.__TRACKER_DATA[url]);
    }
    if (window.__TRACKER_DATA) return Promise.reject(new Error(url + " not bundled"));
    return fetch(url, {cache: "no-cache"}).then(function (r) {
      if (!r.ok) throw new Error(url + " " + r.status);
      return r.json();
    });
  }

  function loadAll() {
    return Promise.all([
      getJSON("data/meta.json").catch(function () { return null; }),
      getJSON("data/latest.json").catch(function () { return {countries: {}, totals: {}}; }),
      getJSON("data/global_series.json").catch(function () { return []; }),
      getJSON("data/outlets.json").catch(function () { return {outlets: []}; }),
      getJSON("vendor/countries-110m.json").catch(function () { return null; }),
      getJSON("vendor/iso3166.json").catch(function () { return []; }),
      getJSON("country-names.json").catch(function () { return {names: {}}; })
    ]).then(function (res) {
      state.meta = res[0]; state.latest = res[1] || {countries: {}, totals: {}};
      state.series = res[2] || []; state.outlets = (res[3] && res[3].outlets) || [];
      state.topo = res[4];
      (res[5] || []).forEach(function (r) { state.names[r["alpha-3"]] = r.name; state.officialNames[r["alpha-3"]] = r.name; state.numToIso[String(parseInt(r["country-code"], 10))] = r["alpha-3"]; });
      /* ISO short names are often formal or inverted ("Korea, Republic of"); show the common English name. */
      var common = (res[6] && res[6].names) || {};
      Object.keys(common).forEach(function (iso) { state.names[iso] = common[iso]; });
      var monthsWanted = {};
      state.series.forEach(function (d) { monthsWanted[d.date.slice(0, 7)] = true; });
      return Promise.all(Object.keys(monthsWanted).map(function (m) {
        return getJSON("data/daily/" + m + ".json").then(function (j) { state.months[m] = j; }).catch(function () {});
      }));
    });
  }

  /* ------------------------------------------------------------ projection */
  var K = [[0.9986, -0.062], [1, 0], [0.9986, 0.062], [0.9954, 0.124], [0.99, 0.186], [0.9822, 0.248], [0.973, 0.31],
           [0.96, 0.372], [0.9427, 0.434], [0.9216, 0.4958], [0.8962, 0.5571], [0.8679, 0.6176], [0.835, 0.6769],
           [0.7986, 0.7346], [0.7597, 0.7903], [0.7186, 0.8435], [0.6732, 0.8936], [0.6213, 0.9394], [0.5722, 0.9761], [0.5322, 1]];
  K.forEach(function (d) { d[1] *= 1.0144; });
  function robinsonRaw(lambda, phi) {
    var i = Math.min(18, Math.abs(phi) * 36 / Math.PI), i0 = Math.floor(i), di = i - i0, k;
    var ax = (k = K[i0])[0], ay = k[1], bx = (k = K[++i0])[0], by = k[1], cx = (k = K[Math.min(19, ++i0)])[0], cy = k[1];
    return [lambda * (bx + di * (cx - ax) / 2 + di * di * (cx - 2 * bx + ax) / 2),
            (phi > 0 ? Math.PI / 2 : -Math.PI / 2) * (by + di * (cy - ay) / 2 + di * di * (cy - 2 * by + ay) / 2)];
  }
  function robinson() { return d3.geoProjection(robinsonRaw).scale(152.63); }

  function mapFallback(text) {
    var wrap = el("map-wrap");
    if (!wrap) return;
    var d = document.createElement("div");
    d.id = "map-fallback";
    d.textContent = text + " The ranked list below the map still works.";
    wrap.insertBefore(d, wrap.firstChild);
    document.body.classList.add("force-list");
  }

  /* --------------------------------------------------------------- helpers */
  function el(id) { return document.getElementById(id); }
  function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function pct(v) { return v === null || v === undefined ? "n/a" : (v * 100).toFixed(1) + "%"; }
  function days() { return C.listDays(state.months); }
  function windowArg(w) { return w === "all" ? null : Number(w); }
  function currentAgg() { return C.aggregateWindow(state.months, state.endDate, windowArg(state.windowDays)); }
  function metricDef() { return C.METRICS[state.metric] || {}; }
  function metricLabel() { return metricDef().label || state.metric; }
  function windowLabel() { return windowLabelFor(state.endDate, state.windowDays); }
  function windowLabelFor(date, days) {
    if (!date) return "no data";
    if (days === "all") return "total to " + date;
    if (Number(days) === 1) return date;
    return Number(days) + " days ending " + date;
  }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  /* Unverified relay is published only when the verification stage has run and a reliability study has
     settled it, and never for a country whose outlets publish in a language withheld on its own kappa.
     Data files from before those fields existed fall back to the older flags. */
  function relayFor(entry) {
    var m = state.meta || {};
    var measured = m.relay_measured !== undefined ? !!m.relay_measured : !!m.llm_calls_total;
    var publishable = m.relay_publishable !== undefined ? !!m.relay_publishable : !!(m.b_counts_settled && measured);
    var langs = m.relay_withheld_languages || [];
    if (publishable && entry && (entry.languages || []).some(function (l) { return langs.indexOf(l) !== -1; })) publishable = false;
    return {measured: measured, publishable: publishable};
  }
  function relayBasis() { return (state.meta && state.meta.relay_basis) || null; }
  /* A published relay count says what it rests on. "model agreement" is not a synonym for correct:
     it means a second model applied the codebook the same way, with no human having read anything. */
  function relayText(relay, b) {
    if (!relay.publishable) return relay.measured ? "withheld" : "not yet measured";
    return String(b) + (relayBasis() === "model_vs_model" ? " (model agreement)" : "");
  }

  function routeList() { return ((state.meta && state.meta.routes) || []).map(function (r) { return {kind: "route", id: r.id, label: r.label}; }); }
  function arrivalList() { return ((state.meta && state.meta.arrivals) || []).map(function (r) { return {kind: "arrival", id: r.id, label: r.label}; }); }
  function routeLabel(f) {
    var hit = routeList().concat(arrivalList()).filter(function (r) { return f && r.kind === f.kind && r.id === f.id; })[0];
    return hit ? hit.label : (f ? f.id : "");
  }
  function routeActive() { return !!(state.route && metricDef().measure === "a"); }

  /* Everything metricValue needs beyond the counts, for one country. */
  function ctxFor(iso, entry, agg) {
    return {population: entry.population, topOutlets: entry.top_outlets, relay: relayFor(entry),
            routeCount: routeActive() ? C.routeCount(agg, iso, state.route) : null};
  }

  /* --------------------------------------------------------------- notices */
  function renderNotices() {
    var kn = el("kappa-notice");
    var m = state.meta;
    var kparts = [];
    if (!m) kparts.push("No data has been exported yet. The interface is rendering an empty dataset.");
    else {
      var relay = relayFor(null), k = m.kappa;
      if (!relay.measured) kparts.push("Unverified relay has not been measured: the verification stage that separates it from independent journalism has not run. It is shown as not yet measured, never as zero, and the map defaults to state origin only.");
      else if (!relay.publishable) kparts.push(k && k.bc !== null && k.bc !== undefined
        ? "Unverified relay counts are withheld. Cohen's kappa on the relay versus independent journalism judgement is " + k.bc.toFixed(2) + " (n = " + k.n_bc + "), below the " + m.kappa_warning_threshold + " threshold, so the counts are not published."
        : "Unverified relay counts are withheld until a reliability study checks the relay versus independent journalism judgement. No article is read by a person: this project runs without a human in the loop by the owner\u2019s decision, so the study is a second model re-judging a sample of the same articles, which measures whether two raters apply the codebook the same way rather than whether they apply it correctly.");
      else if (relayBasis() === "same_model_rerun") {
        var rs = m.relay_reliability || {};
        kparts.push("Unverified relay counts are published on the strength of the same model, " + (rs.model_a || "the model") + ", re-judging a sample of " + (rs.n || 0) + " articles and agreeing with its own earlier labels at Cohen's kappa " + (rs.kappa_bc === null || rs.kappa_bc === undefined ? "n/a" : rs.kappa_bc.toFixed(2)) + " on relay versus independent. That is a rerun, not a second opinion: it shows the judgement is stable, and can say nothing about a mistake the model makes every time. No article is read by a person.");
      }
      else if (relayBasis() === "model_vs_model") {
        var rr = m.relay_reliability || {};
        kparts.push("Unverified relay counts are published on the strength of a second model, " + (rr.model_b || "another model") + ", re-judging a sample of " + (rr.n || 0) + " articles and agreeing with " + (rr.model_a || "the first") + " at kappa " + (rr.kappa_bc === null || rr.kappa_bc === undefined ? "n/a" : rr.kappa_bc.toFixed(2)) + " on the relay versus independent judgement. That measures whether two models apply the codebook the same way, not whether they apply it correctly: no article has been read by a person, and two models of one family can share a bias neither of them reveals.");
      }
      var wl = m.relay_withheld_languages || [];
      if (wl.length) kparts.push("They are also withheld for outlets publishing in " + wl.join(", ") + ", where agreement in that language is below the threshold.");
      if (m.official_sourcing_pending) kparts.push(m.official_sourcing_pending + " articles in " + m.official_sourcing_pending_countries + " countries carry official Chinese sourcing and wait for that judgement. The Target articles measure counts them, so it mixes a measured quantity with an unmeasured pile.");
    }
    kn.textContent = kparts.join(" ");
    kn.classList.toggle("hidden", !kparts.length);

    var dn = el("data-notice");
    var parts = [];
    if (m) {
      var flagged = m.paywall_flagged_countries || [];
      if (flagged.length) parts.push("Paywalls removed more than " + Math.round((m.paywall_flag_share || 0.33) * 100) + " percent of retrieved articles in " + flagged.map(function (c) { return state.names[c] || c; }).join(", ") + ". Paywalled articles cannot be read, so they are missing from every count. They stay in the share of monitored output denominator, which counts every item the outlets published, so that share is a lower bound in those countries, and their counts are not comparable to the rest.");
      if (m.countries_monitored && m.countries_monitored < 30) parts.push("Only " + m.countries_monitored + " countries are monitored so far. The map mostly displays the registry, not the world.");
      if (m.countries_in_gaps) parts.push(m.countries_in_gaps + " countries are recorded as coverage gaps with a stated reason.");
      var u = m.registry_unevenness;
      if (u && u.max_over_median && u.max_over_median >= 3) parts.push("The registry is uneven: the densest country has " + u.max + " active outlets against a median of " + u.median + ", and " + u.countries_with_one_outlet + " countries have a single outlet. Raw counts mostly display that sampling; Per outlet and Share of monitored output correct for it.");
      if (!(m.countries_with_audience_ranks || []).length) parts.push("No outlet carries an audience rank yet, so the share of monitored output divides by everything a country's monitored outlets published, not by its largest publications, and it is not shown for countries with fewer than " + (m.min_outlets_for_output_share || C.MIN_OUTLETS_FOR_OUTPUT_SHARE) + " outlets.");
      var rs = m.release_sections;
      if (rs && rs.outlets_active) {
        var searched = rs.outlets_active - (rs.not_searched || 0);
        parts.push(searched + " of " + rs.outlets_active + " active outlets have had their press release, sponsored and partner sections searched" + (searched ? ", and " + (rs.found || 0) + " had one" : "") + ". State origin placed through a section nobody searched cannot be found.");
      }
      var rc = m.relay_collector;
      if (rc && rc.outlets) {
        if (!rc.last_run) parts.push("The collector on the owner's machine, which fetches " + rc.outlets + " outlets the hosted runner cannot reach, has not reported a heartbeat yet, so gaps in those outlets cannot yet be told from quiet days.");
        else if (rc.stale) parts.push("The collector on the owner's machine has not collected for " + Math.round(rc.hours_since_last_run) + " hours, so recent counts for its " + rc.outlets + " outlets in " + (rc.countries || []).length + " countries are incomplete.");
        if ((rc.incomplete_days || []).length) parts.push("It ran in too few hours on " + plural(rc.incomplete_days.length, "day") + ", marked in red along the timeline.");
      }
      if (m.reclassification_complete === false) {
        var older = 0;
        Object.keys(m.ruleset_mix || {}).forEach(function (v) { if (v !== m.ruleset_version) older += m.ruleset_mix[v]; });
        var stuck = m.labels_unreclassifiable || 0;
        parts.push(older > stuck
          ? "Reclassification under ruleset " + m.ruleset_version + " is still running: " + older + " labels carry an older ruleset" + (stuck ? ", of which " + stuck + " can never be redone because the article can no longer be retrieved" : "") + ". The days affected are marked on the timeline, so a jump there is not a trend."
          : older + " labels carry an older ruleset and can never be redone, because the article can no longer be retrieved and its fetch attempts are spent. Reclassification of everything retrievable is complete. The days affected are marked on the timeline.");
      }
      var gc = (m.gate_changes || []).slice(-1)[0];
      if (gc) parts.push("The relevance gate changed on " + gc.date + " (version " + gc.version + "). The gate decides what enters the corpus, not how an article is labelled, and it applies only to items discovered after that date; items rejected earlier are not re-examined.");
      var fs = m.feed_saturation;
      if (fs && fs.polls && fs.saturated) parts.push(fs.saturated + " of " + fs.polls + " feed polls returned a full window with nothing seen before, so items were lost between polls: an estimated " + fs.missed_estimate + " in all. Countries where this passes " + Math.round((fs.warning_share || 0.25) * 100) + " percent of polls carry a warning.");
      if ((m.llm_sampling_days || []).length) parts.push("On " + plural(m.llm_sampling_days.length, "day") + " the model call ceiling bound, and the articles sent were a random draw with the same fraction in every country.");
    }
    dn.textContent = parts.join(" ");
    dn.classList.toggle("hidden", !parts.length);
    /* The human-reviewed switch is hidden until a review has actually been recorded; an empty
       reviewed mode would only blank the map. */
    var reviewed = !!(m && m.articles_reviewed);
    el("mode-control").classList.toggle("hidden", !reviewed);
    el("review-coverage-control").classList.toggle("hidden", !reviewed);
    el("review-coverage").textContent = m ? pct(m.review_coverage) + " of classified articles" : "n/a";
  }

  /* ------------------------------------------------------------------- map */
  var svg, gCountries, gMarkers, path, projection, colorScale;
  var ZERO_COLOR = "#f6f1e8", LOW_COLOR = "#f7d3cb", HIGH_COLOR = "#3d0306";
  /* Seven distinct steps, interpolated in Lab so lightness falls steadily: the darker the shade, the more articles. Bin edges follow a square law, so the low end gets most of the
     resolution: a country at a fifth of the cap is already three steps away from white. */
  var STEPS = 7;
  var RAMP = d3.piecewise(d3.interpolateLab, [LOW_COLOR, "#ef8a73", "#d23a2e", "#9e0f17", HIGH_COLOR]);
  var STEP_COLORS = d3.range(STEPS).map(function (i) { return RAMP(STEPS === 1 ? 1 : i / (STEPS - 1)); });
  function stepEdges(max) { return d3.range(1, STEPS).map(function (i) { return max * Math.pow(i / STEPS, 2); }); }
  function setupMap() {
    svg = d3.select("#map");
    svg.selectAll("*").remove();
    var defs = svg.append("defs");
    var pat = defs.append("pattern").attr("id", "hatch").attr("patternUnits", "userSpaceOnUse").attr("width", 6).attr("height", 6).attr("patternTransform", "rotate(45)");
    pat.append("rect").attr("width", 6).attr("height", 6).attr("fill", "#141618");
    pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 6).attr("stroke", "#2b2e33").attr("stroke-width", 1.4);
    var pat2 = defs.append("pattern").attr("id", "stipple").attr("patternUnits", "userSpaceOnUse").attr("width", 6).attr("height", 6);
    pat2.append("rect").attr("width", 6).attr("height", 6).attr("fill", "#141618");
    pat2.append("circle").attr("cx", 3).attr("cy", 3).attr("r", 0.9).attr("fill", "#3a3d43");
    var pat3 = defs.append("pattern").attr("id", "sparse").attr("patternUnits", "userSpaceOnUse").attr("width", 5).attr("height", 5);
    pat3.append("rect").attr("width", 5).attr("height", 5).attr("fill", "#23262a");
    pat3.append("rect").attr("x", 2).attr("y", 2).attr("width", 1.2).attr("height", 1.2).attr("fill", "#8a7443");
    /* Withheld: unverified relay not measured or not publishable. A cross hatch in dark amber. */
    var pat4 = defs.append("pattern").attr("id", "withheld").attr("patternUnits", "userSpaceOnUse").attr("width", 6).attr("height", 6);
    pat4.append("rect").attr("width", 6).attr("height", 6).attr("fill", "#141618");
    pat4.append("path").attr("d", "M0,0 L6,6 M6,0 L0,6").attr("stroke", "#5a4520").attr("stroke-width", 1);
    /* Unreadable: monitored, but no keyword list for the outlets' language. Horizontal rules. */
    var pat5 = defs.append("pattern").attr("id", "unreadable").attr("patternUnits", "userSpaceOnUse").attr("width", 5).attr("height", 5);
    pat5.append("rect").attr("width", 5).attr("height", 5).attr("fill", "#141618");
    pat5.append("line").attr("x1", 0).attr("y1", 2.5).attr("x2", 5).attr("y2", 2.5).attr("stroke", "#3a4450").attr("stroke-width", 1.2);
    projection = robinson();
    path = d3.geoPath(projection);
    projection.fitSize([960, 500], {type: "Sphere"});
    svg.append("path").attr("class", "sphere").attr("d", path({type: "Sphere"}));
    gCountries = svg.append("g");
    gMarkers = svg.append("g");
    if (!state.topo || !window.topojson) return;
    var features = topojson.feature(state.topo, state.topo.objects.countries).features;
    var byName = {"Kosovo": "XKX"};  /* Natural Earth gives these no ISO numeric id */
    features.forEach(function (f) { f.iso = state.numToIso[String(parseInt(f.id, 10))] || byName[(f.properties && f.properties.name) || ""] || null; });
    gCountries.selectAll("path").data(features).enter().append("path")
      .attr("class", "country").attr("d", path)
      .on("mousemove", function (ev, f) { showTip(ev, f); })
      .on("mouseleave", hideTip)
      .on("click", function (ev, f) { selectCountry(f.iso || ("name:" + ((f.properties && f.properties.name) || "Unknown"))); });
  }

  function fillFor(cls, value) {
    if (cls === "nocoverage") return "url(#hatch)";
    if (cls === "gap") return "url(#stipple)";
    if (cls === "inactive") return "url(#stipple)";
    if (cls === "withheld") return "url(#withheld)";
    if (cls === "unreadable") return "url(#unreadable)";
    if (cls === "nodata") return "#1a1c1f";
    if (cls === "sparse") return "url(#sparse)";
    if (cls === "zero") return ZERO_COLOR;
    return colorScale(value);
  }

  /* The scale cap for the current measure, window, mode and route, pooled over every date of the timeline
     and cached, so moving the day never rescales the map. */
  function scaleFor() {
    var key = [state.metric, state.windowDays, state.mode, state.route ? state.route.kind + ":" + state.route.id : "", (state.meta && state.meta.generated_at) || ""].join("|");
    if (!state.scaleCache[key]) state.scaleCache[key] = C.scaleCap(state.months, tlDays, state.latest, state.metric, windowArg(state.windowDays), state.mode, ctxFor);
    return state.scaleCache[key];
  }

  function renderMap() {
    if (!gCountries) return;
    var agg = currentAgg();
    var vals = [];
    var perIso = {};
    Object.keys(state.latest.countries || {}).forEach(function (iso) {
      var entry = state.latest.countries[iso];
      var mv = C.metricValue(agg.countries[iso], state.metric, entry.outlets_active, state.mode, agg.reviewed[iso], ctxFor(iso, entry, agg));
      var cls = C.fillClass(entry, mv);
      perIso[iso] = {entry: entry, mv: mv, cls: cls};
      if (cls === "value") vals.push(mv.value);
    });
    var fmt = metricDef().format || "int";
    var sc = scaleFor();
    var max = sc.cap || (vals.length ? d3.max(vals) : 1) || 1;
    if (fmt === "pct") max = Math.max(max, 0.05);
    var trueMax = sc.max !== null && sc.max !== undefined ? sc.max : (vals.length ? d3.max(vals) : max);
    var capped = trueMax > max || vals.some(function (v) { return v > max; });
    /* White for exactly zero; any positive value takes one of STEPS tinted steps up to deep red at the cap. */
    var stepScale = d3.scaleThreshold().domain(stepEdges(max)).range(STEP_COLORS);
    colorScale = function (v) { return v > 0 ? stepScale(v) : ZERO_COLOR; };
    state._perIso = perIso; state._agg = agg; state._max = max; state._capped = capped; state._trueMax = trueMax;
    // Fills are set directly. A D3 transition would interpolate strings between pattern
    // URLs and colors and leave an invalid fill behind if a re-render interrupted it;
    // the CSS transition on path.country smooths color to color changes instead.
    gCountries.selectAll("path.country")
      .classed("selected", function (f) { return f.iso && f.iso === state.selected; })
      .attr("fill", function (f) {
        var p = f.iso && perIso[f.iso];
        if (!p) return fillFor("nocoverage");
        return fillFor(p.cls, p.mv.value);
      });
    applyMapFocus();
    gMarkers.selectAll("*").remove();
    gCountries.selectAll("path.country").each(function (f) {
      var p = f.iso && perIso[f.iso];
      if (!p || !countryWarnings(p.entry, agg).length) return;
      var c = path.centroid(f);
      if (isNaN(c[0])) return;
      gMarkers.append("path").attr("class", "warn-marker").attr("d", d3.symbol(d3.symbolTriangle, 40)()).attr("transform", "translate(" + c[0] + "," + c[1] + ") scale(" + (1 / state.zoomK) + ")");
    });
    renderLegend(max, fmt);
    renderBars(agg);
  }

  /* The warnings that travel with every figure for a country: the export's standing warnings plus
     the ones that depend on the window shown. */
  function countryWarnings(entry, agg) {
    var out = (entry.warnings || []).map(function (w) { return w.text; });
    var gaps = (agg && agg.relayIncompleteDays) || [];
    if (entry.relay_outlets && gaps.length) out.push("the collector on the owner's machine, which fetches " + plural(entry.relay_outlets, "outlet") + " here, ran in too few hours on " + plural(gaps.length, "day") + " of this window");
    return out;
  }

  /* Single country view on the map: zoom to the selected country and dim the rest. Shades keep the
     world scale, so a country reads the same in both views. */
  function focusFeature() {
    if (!gCountries || !state.selected) return null;
    var hit = null;
    gCountries.selectAll("path.country").each(function (f) {
      if (!hit && (f.iso || ("name:" + ((f.properties && f.properties.name) || "Unknown"))) === state.selected) hit = f;
    });
    return hit;
  }
  /* Zoom to the largest polygon, so overseas territories do not shrink the country to a dot. */
  function mainlandBounds(f) {
    var g = f.geometry;
    if (!g || g.type !== "MultiPolygon") return path.bounds(f);
    var best = null, bestArea = -1;
    g.coordinates.forEach(function (poly) {
      var part = {type: "Feature", geometry: {type: "Polygon", coordinates: poly}};
      var a = path.area(part);
      if (a > bestArea) { bestArea = a; best = part; }
    });
    return path.bounds(best);
  }
  function applyMapFocus() {
    if (!svg || !gCountries) return;
    var f = focusFeature();
    svg.classed("focused", !!f);
    gCountries.selectAll("path.country").classed("dim", function (d) { return !!f && d !== f; });
    var key = f ? state.selected : null;
    if (key === state.zoomIso) return;
    state.zoomIso = key;
    var k = 1, tx = 0, ty = 0;
    if (f) {
      var b = mainlandBounds(f), dx = b[1][0] - b[0][0], dy = b[1][1] - b[0][1];
      k = Math.max(1, Math.min(12, 0.72 / Math.max(dx / 960, dy / 500, 1e-6)));
      tx = 480 - k * (b[0][0] + b[1][0]) / 2;
      ty = 250 - k * (b[0][1] + b[1][1]) / 2;
    }
    state.zoomK = k;
    var t = "translate(" + tx + "," + ty + ") scale(" + k + ")";
    gCountries.interrupt().transition().duration(650).attr("transform", t);
    gMarkers.interrupt().transition().duration(650).attr("transform", t);
  }

  /* The legend reads top to bottom: what the color measures and over which days, the value range
     each shade stands for, then everything on the map that is not on the color scale. */
  function renderLegend(max, fmt) {
    var bounds = [0].concat(stepEdges(max)).concat([Infinity]);
    var steps = [];
    STEP_COLORS.forEach(function (color, i) {
      var lo = bounds[i], hi = bounds[i + 1], label;
      if (fmt === "int") {
        /* Whole-number metrics: step i covers lo <= v < hi, so list the whole numbers inside it. */
        var a = i === 0 ? 1 : Math.ceil(lo);
        var b = hi === Infinity ? null : Math.ceil(hi) - 1;
        if (b !== null && b < a) return;
        label = b === null ? a + " or more" : (a === b ? String(a) : a + " to " + b);
      } else if (i === 0) {
        label = "Above 0, under " + C.formatValue(hi, fmt);
      } else {
        label = hi === Infinity ? C.formatValue(lo, fmt) + " or more" : C.formatValue(lo, fmt) + " to " + C.formatValue(hi, fmt);
      }
      steps.push('<li><span class="sw" style="background:' + color + '"></span><span>' + esc(label) + '</span></li>');
    });
    var m = metricDef();
    var notShown = m.allItems ? "Fewer than " + C.MIN_OUTLETS_FOR_OUTPUT_SHARE + " outlets, or too few items published in this window, for a share"
      : m.population ? "No population figure, or under " + C.MIN_POPULATION.toLocaleString("en-US") + " residents"
      : m.format === "pct" ? "Fewer than " + C.MIN_SHARE_DENOMINATOR + " China articles, so no share"
      : null;
    var relay = relayFor(null);
    el("legend").innerHTML =
      '<div class="lg-group">' +
        '<div class="lg-kicker">Color scale</div>' +
        '<div class="lg-head">' + esc(metricLabel()) + (routeActive() ? ', ' + esc(routeLabel(state.route).toLowerCase()) : '') + '</div>' +
        '<div class="lg-when">' + esc(windowLabel()) + '</div>' +
        '<ul class="lg-steps">' +
          '<li><span class="sw" style="background:' + ZERO_COLOR + '"></span><span>None found</span></li>' +
          steps.join("") +
        '</ul>' +
        '<p class="lg-note">The scale is fixed for this measure and window across every date on the timeline, so a shade means the same number on every day.' +
          (state._capped ? ' The darkest shade is an overflow step for values above the 95th percentile of all dates; the highest value on any date is ' + esc(C.formatValue(state._trueMax, fmt)) + '.' : '') + '</p>' +
        (state.zoomIso ? '<p class="lg-note">Zoomed to ' + esc(countryName(state.zoomIso)) + '. Shades keep the world scale, so they compare directly with every other country.</p>' : '') +
      '</div>' +
      '<div class="lg-group">' +
        '<div class="lg-kicker">Not on the scale</div>' +
        '<ul class="lg-keys">' +
          '<li><span class="sw" style="background:repeating-linear-gradient(45deg,#141618,#141618 3px,#2b2e33 3px,#2b2e33 4px)"></span><span>Not monitored: no outlets registered</span></li>' +
          '<li><span class="sw" style="background:radial-gradient(#3a3d43 0.9px, #141618 1px) 0 0/6px 6px"></span><span>Coverage gap, or every outlet inactive</span></li>' +
          (m.relay ? '<li><span class="sw" style="background:repeating-linear-gradient(45deg,transparent,transparent 3px,#5a4520 3px,#5a4520 4px),repeating-linear-gradient(-45deg,#141618,#141618 3px,#5a4520 3px,#5a4520 4px)"></span><span>' + esc(relay.measured ? "Unverified relay withheld until agreement is settled" : "Unverified relay not yet measured") + '</span></li>' : '') +
          '<li><span class="sw" style="background:repeating-linear-gradient(0deg,#141618,#141618 3px,#3a4450 3px,#3a4450 4px)"></span><span>Monitored, but no keyword list for the outlets\' language, and nothing found</span></li>' +
          '<li><span class="sw" style="background:#1a1c1f"></span><span>Monitored, but no China coverage in this window</span></li>' +
          (notShown ? '<li><span class="sw" style="background:radial-gradient(#8a7443 0.7px, #23262a 0.8px) 0 0/5px 5px"></span><span>' + esc(notShown) + '</span></li>' : '') +
          '<li><svg class="sw-tri" viewBox="0 0 16 13" aria-hidden="true"><path d="M8,1.5 L14.5,12 L1.5,12 Z" fill="#0c0d0f" stroke="#d7b46a" stroke-width="1.2"/></svg><span>Data warning: hover the country to read it</span></li>' +
        '</ul>' +
      '</div>';
  }

  function showTip(ev, f) {
    var tip = el("tooltip");
    var iso = f.iso;
    var name = (iso && state.names[iso]) || (f.properties && f.properties.name) || "Unknown";
    var p = iso && state._perIso && state._perIso[iso];
    var html = '<div class="t-name">' + esc(name) + '</div>';
    if (!p) html += '<div class="muted">No monitored outlets. Absence of data, not absence of content.</div>';
    else {
      var e = p.entry, mv = p.mv, agg = state._agg || currentAgg();
      var k = agg.countries[iso] || C.emptyCounts();
      if (p.cls === "gap") html += '<div class="muted">Coverage gap: ' + esc(e.gap_reason) + '</div>';
      else if (p.cls === "inactive") html += '<div class="muted">' + e.outlets_total + ' outlets registered, none active.</div>';
      else {
        html += '<div>' + esc(metricLabel()) + (routeActive() ? ', ' + esc(routeLabel(state.route).toLowerCase()) : '') + ': <strong>' + (p.cls === "withheld" ? esc(relayText(relayFor(e), 0)) : C.formatValue(mv.value, metricDef().format)) + '</strong></div>';
        if (mv.note && mv.value === null) html += '<div class="muted">' + esc(mv.note) + '</div>';
        html += '<div class="muted">State origin ' + plural(mv.aAll, "placement") + ' of ' + plural(mv.underlying, "underlying item") + '; unverified relay ' + esc(relayText(relayFor(e), mv.b)) + '; official sourcing pending verification ' + mv.pending + '; independent ' + mv.c + '; ' + esc(windowLabel()) + '</div>';
        html += '<div class="muted">' + e.outlets_active + ' active outlets, ' + e.feeds_ok + ' of ' + e.feeds_total + ' feeds healthy</div>';
        if (k.paywalled) html += '<div class="muted">' + plural(k.paywalled, "paywalled article") + ' unread: missing from every count, still counted in the monitored output denominator.</div>';
        if (k.polls && k.sat) html += '<div class="muted">' + k.sat + ' of ' + k.polls + ' feed polls came back full with nothing seen before; an estimated ' + k.miss + ' items were missed.</div>';
        if (e.language_support === "none") html += '<div class="muted">No keyword list for ' + esc((e.languages || []).join(", ")) + ': only international and English terms are matched.</div>';
        if (p.cls === "nodata") html += '<div class="muted">No China coverage classified in this window.</div>';
        if (metricDef().allItems && mv.value !== null) html += '<div class="muted">Per 1,000 published items: ' + C.formatValue(mv.value * 1000, "dec") + '</div>';
        if (metricDef().allItems) html += '<div class="muted">' + mv.allItems + ' items published by ' + (e.top_outlets_ranked ? 'the ' + e.top_outlets + ' largest monitored outlets' : 'all ' + e.top_outlets + ' monitored outlets (no audience ranks are recorded)') + ', ' + mv.allItemsTarget + ' targets and ' + mv.allItemsChina + ' China items among them</div>';
        if (metricDef().population && e.population) html += '<div class="muted">Population ' + e.population.toLocaleString("en-US") + '</div>';
      }
      countryWarnings(e, agg).forEach(function (w) { html += '<div class="t-warn">Warning: ' + esc(w) + '</div>'; });
    }
    tip.innerHTML = html;
    tip.style.display = "block";
    var wrap = el("map-wrap").getBoundingClientRect();
    var x = ev.clientX - wrap.left + 14, y = ev.clientY - wrap.top + 14;
    if (x + 300 > wrap.width) x = ev.clientX - wrap.left - 310;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideTip() { el("tooltip").style.display = "none"; }

  /* ------------------------------------------------------------------ bars */
  function renderBars(agg) {
    var rows = C.rankCountries(agg, state.latest, state.metric, state.mode, state.names, ctxFor);
    var fmt = metricDef().format || "int";
    var max = d3.max(rows, function (r) { return r.value || 0; }) || 1;
    var showItems = metricDef().measure === "a" && state.basis === "count" && !el("metric").value && !routeActive();
    el("bars-title").textContent = metricLabel() + (routeActive() ? ", " + routeLabel(state.route).toLowerCase() : "") + ", " + windowLabel();
    el("bars").innerHTML = rows.map(function (r) {
      var w = r.value ? Math.max(2, 100 * r.value / max) : 0;
      var cls = r.fill === "value" ? "" : (r.fill === "sparse" ? "zero" : r.fill);
      var entry = state.latest.countries[r.iso] || {};
      var warns = countryWarnings(entry, agg);
      var num = r.fill === "withheld" ? relayText(relayFor(entry), 0) : C.formatValue(r.value, fmt);
      return '<div class="bar-row" data-iso="' + r.iso + '"><span>' + esc(r.name) + '</span><span><span class="bar ' + cls + '" style="width:' + (r.fill === "value" ? w : (r.fill === "nocoverage" ? 100 : 6)) + '%"></span></span>' +
        '<span class="num">' + esc(num) + (showItems && r.value ? '<span class="items" title="Underlying items: syndicated placements of one item count once">' + r.state_origin_underlying_items + ' items</span>' : '') + '</span>' +
        '<span class="bar-warn"' + (warns.length ? ' title="' + esc(warns.join("; ")) + '">!' : '>') + '</span></div>';
    }).join("") || '<p class="muted">No countries in the dataset.</p>';
    Array.prototype.forEach.call(el("bars").querySelectorAll(".bar-row"), function (row) {
      row.addEventListener("click", function () { selectCountry(row.getAttribute("data-iso")); });
    });
  }

  /* ---------------------------------------------------------------- themes */
  /* Cells are shaded by the share of that country's articles in the theme, using swatches taken from the
     map ramp so the two charts read the same way: light for a small share, dark for a large one. */
  var THEME_HEAT = [0, 2, 3, 5, 6].map(function (i) { return STEP_COLORS[i]; });
  function inkOn(color) { return d3.lab(color).l > 62 ? "#0c0d0f" : "#f3ede2"; }
  var THEME_EDGES = [0.05, 0.15, 0.3, 0.5];
  var THEME_BANDS = ["under 5%", "5 to 15%", "15 to 30%", "30 to 50%", "50% or more"];
  var THEME_ROWS = 15;
  function heat(share) {
    if (!share) return null;
    var i = 0;
    while (i < THEME_EDGES.length && share >= THEME_EDGES[i]) i++;
    return THEME_HEAT[i];
  }
  function measureNoun() { return {target: "target articles", a: "state origin articles", china: "China articles"}[state.measure] || "articles"; }
  function measureIndex() { var i = C.MEASURE_INDEX[state.measure]; return i === undefined ? 2 : i; }
  function denominator(k, mi) {
    k = k || C.emptyCounts();
    if (mi === 0) return k.A + k.B + k.C + (k.pending || 0);
    if (mi === 1) return k.A + k.B + (k.pending || 0);
    return k.A;
  }
  function pctText(share) { return share >= 0.995 ? "100%" : (share < 0.005 && share > 0 ? "<1%" : Math.round(100 * share) + "%"); }

  function themeModel(agg) {
    var catalog = (state.meta && state.meta.themes) || [];
    var byIso = C.aggregateThemes(state.months, state.themeEnd, windowArg(state.themeWindow));
    var mi = measureIndex();
    var rows = [];
    var world = {name: "All monitored countries", n: 0, t: {}};
    Object.keys(byIso).forEach(function (iso) {
      var t = {}, most = 0;
      catalog.forEach(function (c) {
        var v = (byIso[iso][c.id] || [0, 0, 0])[mi];
        t[c.id] = v;
        most = Math.max(most, v);
        world.t[c.id] = (world.t[c.id] || 0) + v;
      });
      var n = Math.max(denominator(agg.countries[iso], mi), most);
      world.n += n;
      if (n > 0) rows.push({iso: iso, name: state.names[iso] || iso, n: n, t: t});
    });
    return {catalog: catalog, rows: rows, world: world};
  }

  function renderThemes() {
    if (!el("themes")) return;
    var agg = themeAgg();
    renderThemeSpark();
    var model = themeModel(agg);
    var catalog = model.catalog;
    var noun = measureNoun();
    var langs = (state.meta && state.meta.theme_languages) || [];
    el("themes-sub").textContent = noun.charAt(0).toUpperCase() + noun.slice(1) + " by theme" + (state.selected ? " in " + countryName(state.selected) : "") + ", " + themeWindowLabel() +
      ". Tags read the headline, the feed summary and the whole body. An article can carry more than one theme, so theme counts do not add up to the number of articles." +
      (langs.length ? " Theme terms exist in " + langs.length + " languages; articles in other languages are matched on English terms only." : "") +
      (routeActive() ? " Themes are not split by route, so the route filter does not apply here." : "");
    if (!catalog.length || !model.rows.length) {
      el("theme-focus").innerHTML = '<p class="muted">No ' + esc(noun) + ' with themes in this window. Theme counts are computed at each daily export.</p>';
      el("theme-grid").innerHTML = "";
      el("theme-legend").innerHTML = "";
      el("theme-grid").classList.remove("days");
      return;
    }
    var label = {};
    catalog.forEach(function (c) { label[c.id] = c; });
    if (state.themeSort && !label[state.themeSort]) state.themeSort = null;

    /* Left: the theme mix for the selected country, or for every monitored country together. */
    var sel = null;
    model.rows.forEach(function (r) { if (r.iso === state.selected) sel = r; });
    var selName = state.selected ? (state.selected.indexOf("name:") === 0 ? state.selected.slice(5) : (state.names[state.selected] || state.selected)) : null;
    var focus = sel || model.world;
    var list = catalog.filter(function (c) { return c.id !== "other"; }).map(function (c) { return {c: c, v: focus.t[c.id] || 0}; })
      .sort(function (a, b) { return b.v - a.v; });
    if (label.other) list.push({c: label.other, v: focus.t.other || 0});
    var fmax = d3.max(list, function (x) { return x.v; }) || 1;
    el("theme-focus").innerHTML =
      '<div class="tf-head"><span class="tf-name">' + esc(sel ? sel.name : (selName && !sel ? selName : focus.name)) + '</span>' +
      '<span class="tf-n">' + (sel || !selName ? focus.n + " " + esc(noun) : "") + '</span></div>' +
      (selName && !sel ? '<p class="muted">No ' + esc(noun) + ' for this country in this window. The grid shows the countries that have some.</p>' :
      '<ul class="tf-list">' + list.map(function (x) {
        var share = focus.n ? x.v / focus.n : 0;
        return '<li data-theme="' + x.c.id + '"' + (state.themeSort === x.c.id ? ' class="active"' : '') + ' title="Rank the countries by ' + esc(x.c.label.toLowerCase()) + '">' +
          '<span class="tf-label">' + esc(x.c.label) + '</span>' +
          '<span class="tf-track"><span class="tf-bar" style="width:' + (x.v ? Math.max(1.5, 100 * x.v / fmax) : 0) + '%"></span></span>' +
          '<span class="tf-v">' + x.v + '</span><span class="tf-s">' + (x.v ? pctText(share) : "") + '</span></li>';
      }).join("") + '</ul><p class="tf-hint">' + (state.selected ? 'Click a theme to mark its row in the day grid. Switch to Whole world to compare countries.' : 'Click a theme to rank the countries in the grid by it. Click it again to go back.') + '</p>');

    if (state.selected) { renderCountryThemes(state.selected, sel, catalog, label, noun); return; }
    el("theme-grid").classList.remove("days");

    /* Right: countries down, themes across, the number of articles in each cell. */
    var sortKey = state.themeSort;
    var rows = model.rows.slice().sort(function (a, b) {
      if (sortKey) {
        var d = (b.t[sortKey] || 0) - (a.t[sortKey] || 0);
        if (d) return d;
      }
      return b.n - a.n || a.name.localeCompare(b.name);
    });
    var shown = rows.slice(0, THEME_ROWS);
    if (sel && shown.indexOf(sel) === -1) shown.push(sel);
    el("theme-grid").innerHTML =
      '<thead><tr><th class="c-country" scope="col">Country</th><th class="c-n" scope="col">' + esc(noun.charAt(0).toUpperCase() + noun.slice(1)) + '</th>' +
      catalog.map(function (c) {
        return '<th scope="col" class="c-theme' + (sortKey === c.id ? ' sorted' : '') + '" data-theme="' + c.id + '" title="' + esc(c.label) + '">' + esc(c.short) + '</th>';
      }).join("") + '</tr></thead><tbody>' +
      shown.map(function (r) {
        return '<tr data-iso="' + esc(r.iso) + '"' + (r.iso === state.selected ? ' class="selected"' : '') + '>' +
          '<th scope="row" class="c-country">' + esc(r.name) + '</th><td class="c-n">' + r.n + '</td>' +
          catalog.map(function (c) {
            var v = r.t[c.id] || 0, share = Math.min(1, v / Math.max(r.n, 1)), bg = heat(share);
            return '<td class="cell" data-theme="' + c.id + '" data-v="' + v + '" data-share="' + share.toFixed(4) + '"' + (bg ? ' style="background:' + bg + ';color:' + inkOn(bg) + '"' : '') + '>' + (v || "") + '</td>';
          }).join("") + '</tr>';
      }).join("") + '</tbody>';
    updateGridScroll();
    el("theme-legend").innerHTML =
      '<span class="tl-title">Cell shade: share of that country\'s ' + esc(noun) + ' in the theme</span>' +
      THEME_BANDS.map(function (b, i) { return '<span class="tl-i"><span class="sw" style="background:' + THEME_HEAT[i] + '"></span>' + b + '</span>'; }).join("") +
      '<span class="tl-foot">The ' + Math.min(THEME_ROWS, rows.length) + ' countries with the most ' + esc(noun) +
      (sortKey ? ' in ' + esc(label[sortKey].label.toLowerCase()) : '') + ' in this window' +
      (sel && rows.indexOf(sel) >= THEME_ROWS ? ', plus the selected country' : '') + '. Shares in a row can add up to more than 100 percent, because an article can carry several themes. Click a country to open it, or a theme heading to rank by it.</span>';
  }

  /* Single country view of the theme counter: themes down, days across, ending on the counter's own day.
     "That day" and "7 days" show a week so there is a trend to read; "Total" shows the last 31 days. */
  function countryThemeDays() {
    var n = state.themeWindow === "all" ? 31 : Math.max(7, Math.min(31, Number(state.themeWindow)));
    var end = tlDays.indexOf(state.themeEnd);
    if (end === -1) end = tlDays.length - 1;
    return tlDays.slice(Math.max(0, end - n + 1), end + 1);
  }
  function renderCountryThemes(iso, sel, catalog, label, noun) {
    var mi = measureIndex(), name = countryName(iso), ds = countryThemeDays();
    var perDay = ds.map(function (d) {
      var e = C.dayEntry(state.months, d) || {};
      var t = (e.themes && e.themes[iso]) || {}, vals = {}, most = 0;
      catalog.forEach(function (c) { var v = (t[c.id] || [0, 0, 0])[mi]; vals[c.id] = v; most = Math.max(most, v); });
      return {d: d, n: Math.max(denominator((e.countries || {})[iso], mi), most), t: vals};
    });
    var total = function (id) { return (sel && sel.t[id]) || 0; };
    var order = catalog.filter(function (c) { return c.id !== "other"; }).sort(function (a, b) { return total(b.id) - total(a.id); });
    if (label.other) order.push(label.other);
    /* Shaded by the number of articles, on the map's steps: darker always means more. A share of a
       day's articles would paint a day with one article as dark as the busiest day. */
    var counts = [];
    perDay.forEach(function (p) { order.forEach(function (c) { if (p.t[c.id] > 0) counts.push(p.t[c.id]); }); });
    var cap = counts.length ? Math.max(1, C.percentile(counts, 0.95)) : 1;
    var edges = stepEdges(cap);
    var shade = d3.scaleThreshold().domain(edges).range(STEP_COLORS);
    var bands = [];
    STEP_COLORS.forEach(function (color, i) {
      var lo = i === 0 ? 1 : Math.floor(edges[i - 1]) + 1, hi = i < edges.length ? Math.floor(edges[i]) : null;
      if (hi !== null && hi < lo) return;
      bands.push({color: color, text: hi === null ? lo + " or more" : (lo === hi ? String(lo) : lo + " to " + hi)});
    });
    var grid = el("theme-grid");
    grid.classList.add("days");
    grid.innerHTML =
      '<thead><tr><th class="c-country" scope="col">Theme</th><th class="c-n" scope="col">Window</th>' +
      perDay.map(function (p) {
        return '<th scope="col" class="c-day' + (p.d === state.themeEnd ? ' sorted' : '') + '" title="' + p.d + ': ' + p.n + ' ' + esc(noun) + '">' + p.d.slice(8) + '</th>';
      }).join("") + '</tr></thead><tbody>' +
      order.map(function (c) {
        return '<tr data-theme-row="' + c.id + '"' + (state.themeSort === c.id ? ' class="selected"' : '') + '>' +
          '<th scope="row" class="c-country">' + esc(c.label) + '</th><td class="c-n">' + total(c.id) + '</td>' +
          perDay.map(function (p) {
            var v = p.t[c.id] || 0, share = Math.min(1, v / Math.max(p.n, 1)), bg = v > 0 ? shade(v) : null;
            return '<td class="cell" data-theme="' + c.id + '" data-day="' + p.d + '" data-n="' + p.n + '" data-v="' + v + '" data-share="' + share.toFixed(4) + '"' + (bg ? ' style="background:' + bg + ';color:' + inkOn(bg) + '"' : '') + '>' + (v || "") + '</td>';
          }).join("") + '</tr>';
      }).join("") + '</tbody>';
    updateGridScroll();
    el("theme-legend").innerHTML =
      '<span class="tl-title">Cell shade: number of ' + esc(noun) + ' that day, same steps as the map</span>' +
      bands.map(function (b) { return '<span class="tl-i"><span class="sw" style="background:' + b.color + '"></span>' + b.text + '</span>'; }).join("") +
      '<span class="tl-foot">' + esc(name) + ', the ' + perDay.length + ' days ending ' + esc(state.themeEnd || "") + ' (day of the month across the top). The Window column counts ' + esc(themeWindowLabel()) + '. Switch to Whole world to compare countries.</span>';
  }

  /* The grid scrolls sideways only when the column is too narrow for every theme; say so when it does. */
  function updateGridScroll() {
    var wrap = el("theme-grid-wrap");
    if (!wrap) return;
    var scrolls = wrap.scrollWidth > wrap.clientWidth + 2;
    wrap.classList.toggle("scrolls", scrolls);
    wrap.classList.toggle("at-end", scrolls && wrap.scrollLeft + wrap.clientWidth >= wrap.scrollWidth - 2);
  }

  function bindThemes() {
    if (!el("themes")) return;
    /* The theme counter's own window, independent of the map's Window toggle. */
    Array.prototype.forEach.call(document.querySelectorAll("[data-theme-window-group] button"), function (b) {
      b.addEventListener("click", function () {
        var v = b.getAttribute("data-theme-window");
        state.themeWindow = v === "all" ? "all" : Number(v);
        Array.prototype.forEach.call(document.querySelectorAll("[data-theme-window-group] button"), function (x) { x.classList.toggle("active", x === b); });
        renderThemes();
      });
    });
    el("theme-grid-wrap").addEventListener("scroll", updateGridScroll);
    window.addEventListener("resize", updateGridScroll);
    function toggleSort(id) { state.themeSort = state.themeSort === id ? null : id; renderThemes(); }
    el("theme-focus").addEventListener("click", function (ev) {
      var li = ev.target.closest("li[data-theme]");
      if (li) toggleSort(li.getAttribute("data-theme"));
    });
    el("theme-grid").addEventListener("click", function (ev) {
      var th = ev.target.closest("th[data-theme]");
      if (th) { toggleSort(th.getAttribute("data-theme")); return; }
      var row = ev.target.closest("tr[data-theme-row]");
      if (row) { toggleSort(row.getAttribute("data-theme-row")); return; }
      var tr = ev.target.closest("tr[data-iso]");
      if (tr) selectCountry(tr.getAttribute("data-iso"));
    });
    var tip = el("theme-tip");
    el("theme-grid").addEventListener("mousemove", function (ev) {
      var td = ev.target.closest("td.cell");
      if (!td) { tip.style.display = "none"; return; }
      var tr = td.parentNode, day = td.getAttribute("data-day");
      var iso = day ? state.selected : tr.getAttribute("data-iso");
      var theme = ((state.meta && state.meta.themes) || []).filter(function (c) { return c.id === td.getAttribute("data-theme"); })[0];
      var n = day ? td.getAttribute("data-n") : tr.querySelector("td.c-n").textContent;
      var v = Number(td.getAttribute("data-v")), share = Number(td.getAttribute("data-share"));
      tip.innerHTML = '<div><strong>' + v + '</strong> of ' + esc(n) + ' ' + esc(measureNoun()) + (v ? ' (' + pctText(share) + ')' : '') + '</div>' +
        '<div class="t-name">' + esc(countryName(iso || "")) + '</div><div class="muted">' + esc(theme ? theme.label : "") + ', ' + esc(day || themeWindowLabel()) + '</div>';
      tip.style.display = "block";
      var box = el("themes").getBoundingClientRect();
      var x = ev.clientX - box.left + 14, y = ev.clientY - box.top + 14;
      if (x + 300 > box.width) x = ev.clientX - box.left - 300;
      tip.style.left = x + "px"; tip.style.top = y + "px";
    });
    el("theme-grid").addEventListener("mouseleave", function () { tip.style.display = "none"; });
  }

  /* ---------------------------------------------------------------- panel */
  function selectCountry(iso) {
    state.selected = iso || null;
    if (state.selected) state.lastCountry = state.selected;
    syncCountryPicks();
    renderMap();
    renderThemes();
    renderPanel();
  }
  function countryName(iso) { return String(iso).indexOf("name:") === 0 ? String(iso).slice(5) : (state.names[iso] || iso); }
  /* The Country search boxes above the map and above the theme counter open the same single country
     view. Typing filters the monitored countries: names that start with the query first, then any
     name, ISO code or common alias that contains it, ignoring accents. */
  var COUNTRY_ALIASES = {USA: "usa us america united states", GBR: "uk britain great britain england", KOR: "south korea", PRK: "north korea",
    RUS: "russia", VNM: "vietnam", IRN: "iran", TWN: "taiwan", CZE: "czech republic czechia", TUR: "turkey turkiye", BOL: "bolivia",
    VEN: "venezuela", TZA: "tanzania", SYR: "syria", LAO: "laos", MDA: "moldova", COD: "drc congo kinshasa", COG: "congo brazzaville",
    CIV: "ivory coast", ARE: "uae emirates", HKG: "hong kong", MAC: "macau macao", PSE: "palestine", MMR: "burma myanmar", SWZ: "swaziland eswatini"};
  function foldText(t) { return String(t).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }
  function setupCountryPicks() {
    var entries = Object.keys((state.latest && state.latest.countries) || {}).map(function (iso) {
      var name = String(countryName(iso));
      return {iso: iso, name: name, fold: foldText(name), key: foldText(name + " " + iso + " " + (state.officialNames[iso] || "") + " " + (COUNTRY_ALIASES[iso] || ""))};
    }).sort(function (x, y) { return x.name.localeCompare(y.name); });
    Array.prototype.forEach.call(document.querySelectorAll("[data-country-pick]"), function (box) {
      var input = box.querySelector("input"), list = box.querySelector("ul"), matches = [], active = -1;
      function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); active = -1; }
      function draw() {
        var q = foldText(input.value.trim());
        var words = q.replace(/[,()]/g, " ").split(/\s+/).filter(Boolean);
        var first = [], rest = [];
        /* Every typed word must appear somewhere, in any order, so "republic of korea" finds "Korea, Republic of". */
        entries.forEach(function (e) {
          if (!q || e.fold.indexOf(q) === 0) first.push(e);
          else if (words.every(function (w) { return e.key.indexOf(w) !== -1; })) rest.push(e);
        });
        matches = first.concat(rest);
        if (active >= matches.length) active = matches.length - 1;
        list.innerHTML = matches.length ? matches.map(function (e, i) {
          var at = q ? e.fold.indexOf(q) : -1;
          var label = at >= 0 && e.name.length === e.fold.length ?
            esc(e.name.slice(0, at)) + '<mark>' + esc(e.name.slice(at, at + q.length)) + '</mark>' + esc(e.name.slice(at + q.length)) : esc(e.name);
          return '<li role="option" id="' + input.id + '-o' + i + '" data-iso="' + esc(e.iso) + '" aria-selected="' + (i === active) + '"' + (i === active ? ' class="active"' : '') + '>' + label + '</li>';
        }).join("") : '<li class="none">No monitored country matches</li>';
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
        if (active >= 0) {
          input.setAttribute("aria-activedescendant", input.id + "-o" + active);
          var li = list.children[active];
          if (li && li.scrollIntoView) li.scrollIntoView({block: "nearest"});
        } else input.removeAttribute("aria-activedescendant");
      }
      function restore() { input.value = state.selected ? countryName(state.selected) : ""; }
      function choose(iso) { close(); input.blur(); selectCountry(iso); restore(); }
      input.addEventListener("focus", function () { input.select(); active = -1; draw(); });
      input.addEventListener("input", function () { active = input.value.trim() ? 0 : -1; draw(); });
      input.addEventListener("keydown", function (ev) {
        if (ev.key === "ArrowDown") { ev.preventDefault(); active = Math.min(matches.length - 1, active + 1); draw(); }
        else if (ev.key === "ArrowUp") { ev.preventDefault(); active = Math.max(0, active - 1); draw(); }
        else if (ev.key === "Enter") {
          ev.preventDefault();
          var pick = matches[active >= 0 ? active : 0];
          if (pick && (active >= 0 || input.value.trim())) choose(pick.iso);
        } else if (ev.key === "Escape") { close(); restore(); input.blur(); }
      });
      list.addEventListener("mousedown", function (ev) {
        var li = ev.target.closest("li[data-iso]");
        ev.preventDefault();
        if (li) choose(li.getAttribute("data-iso"));
      });
      input.addEventListener("blur", function () { close(); restore(); });
    });
    /* Whole world and One country flip between the two views; One country reopens the last country
       chosen, or the top country on the map when none has been chosen yet. */
    Array.prototype.forEach.call(document.querySelectorAll("[data-scope-group] button"), function (b) {
      b.addEventListener("click", function () {
        if (b.getAttribute("data-scope") === "world") { selectCountry(null); return; }
        if (state.selected) return;
        var top = C.rankCountries(currentAgg(), state.latest, state.metric, state.mode, state.names, ctxFor).filter(function (r) { return r.value > 0; })[0];
        var first = Object.keys((state.latest && state.latest.countries) || {})[0];
        selectCountry(state.lastCountry || (top && top.iso) || first || null);
      });
    });
    syncCountryPicks();
  }
  function syncCountryPicks() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-country-pick] input"), function (input) {
      if (document.activeElement !== input) input.value = state.selected ? countryName(state.selected) : "";
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-scope-group] button"), function (b) {
      b.classList.toggle("active", (b.getAttribute("data-scope") === "country") === !!state.selected);
    });
  }

  var RELEASE_LABELS = {found: "found", none_found: "none", blocked: "blocked", unreachable: "unreachable", not_searched: "not searched"};

  function renderPanel() {
    var body = el("panel-body");
    var relayAll = relayFor(null);
    if (!state.selected) {
      var t = (state.latest.totals && state.latest.totals.all_time) || null;
      body.innerHTML = '<h2>Select a country</h2><p class="muted">Click a country on the map, or a row in the ranked list on small screens, to see its time series, category breakdown, routes, monitored outlets and recent classified articles.</p>' +
        (t ? '<h3>All monitored countries, all time</h3><table><tr><th>State origin</th><td class="num">' + t.A + '</td></tr><tr><th>Unverified relay</th><td class="num' + (relayAll.publishable ? '' : ' relay-state') + '">' + esc(relayText(relayAll, t.B)) + '</td></tr><tr><th>Official Chinese sourcing, verification pending</th><td class="num">' + t.pending + '</td></tr><tr><th>Independent journalism</th><td class="num">' + t.C + '</td></tr><tr><th>Not relevant</th><td class="num">' + t.N + '</td></tr><tr><th>Paywalled, unread and uncounted</th><td class="num">' + t.paywalled + '</td></tr></table>' : '<p class="muted">No totals available.</p>');
      return;
    }
    var iso = state.selected;
    var entry = (state.latest.countries || {})[iso];
    var name = iso.indexOf("name:") === 0 ? iso.slice(5) : (state.names[iso] || iso);
    var agg = currentAgg();
    var html = '<button class="close" id="panel-close">Close</button><h2>' + esc(name) + '</h2>';
    if (!entry) { html += '<p class="muted">No monitored outlets in ' + esc(name) + '. This is absence of data, not absence of content. To add coverage, add an outlet with a working feed to sources/outlets.yaml, or record the reason in sources/gaps.yaml.</p>'; body.innerHTML = html; bindClose(); return; }
    if (entry.coverage === "gap") { html += '<p class="warn">Coverage gap: ' + esc(entry.gap_reason) + '</p>'; body.innerHTML = html; bindClose(); return; }
    countryWarnings(entry, agg).forEach(function (w) { html += '<p class="warn">Warning: ' + esc(w) + '</p>'; });
    var relay = relayFor(entry);
    var mv = C.metricValue(agg.countries[iso], state.metric, entry.outlets_active, state.mode, agg.reviewed[iso], ctxFor(iso, entry, agg));
    html += '<p>' + esc(metricLabel()) + (routeActive() ? ', ' + esc(routeLabel(state.route).toLowerCase()) : '') + ', ' + esc(windowLabel()) + ': <strong>' + (mv.withheld ? esc(relayText(relay, 0)) : C.formatValue(mv.value, metricDef().format)) + '</strong></p>';
    if (mv.note && mv.value === null) html += '<p class="panel-note">' + esc(mv.note) + '</p>';
    html += '<h3>Time series, all days</h3><svg class="mini" id="mini"></svg>';
    var k = agg.countries[iso] || C.emptyCounts();
    var rv = agg.reviewed[iso] || {A: 0, B: 0, C: 0, N: 0};
    var relayCells = relay.publishable
      ? '<td class="num">' + k.B + '</td><td class="num">' + k.Br + '</td><td class="num">' + k.Bl + '</td><td class="num">' + rv.B + '</td>'
      : '<td class="num relay-state" colspan="4">' + esc(relayText(relay, 0)) + '</td>';
    html += '<h3>Breakdown, ' + esc(windowLabel()) + '</h3><table><tr><th></th><th class="num">All</th><th class="num">Rules</th><th class="num">Model</th><th class="num">Human</th></tr>' +
      '<tr><td>State origin</td><td class="num">' + k.A + '</td><td class="num">' + k.Ar + '</td><td class="num">' + k.Al + '</td><td class="num">' + rv.A + '</td></tr>' +
      '<tr><td class="muted">Underlying items among them</td><td class="num">' + k.uniqA + '</td><td></td><td></td><td></td></tr>' +
      '<tr><td>Unverified relay</td>' + relayCells + '</tr>' +
      '<tr><td>Official Chinese sourcing, verification pending</td><td class="num">' + k.pending + '</td><td class="num">' + k.pending + '</td><td class="num"></td><td class="num"></td></tr>' +
      '<tr><td>Independent journalism</td><td class="num">' + k.C + '</td><td class="num"></td><td class="num"></td><td class="num">' + rv.C + '</td></tr>' +
      '<tr><td>Not relevant</td><td class="num">' + k.N + '</td><td class="num"></td><td class="num"></td><td class="num">' + rv.N + '</td></tr>' +
      '<tr><td class="muted">Fetched / paywalled / failed / robots</td><td class="num" colspan="4">' + k.fetched + ' / ' + k.paywalled + ' / ' + k.failed + ' / ' + k.blocked + '</td></tr>' +
      '<tr><td class="muted">Feed polls full with nothing seen before / estimated items missed</td><td class="num" colspan="4">' + k.sat + ' of ' + k.polls + ' / ' + k.miss + '</td></tr>' +
      '</table>';
    if (k.paywalled) html += '<p class="panel-note">Paywalled articles are never classified, so they are missing from every count above and from the share of China coverage. They remain in the share of monitored output denominator, which counts every item the monitored outlets published, so that share is a lower bound here.</p>';
    var rts = (agg.routes || {})[iso] || {}, arr = (agg.arrivals || {})[iso] || {};
    if (routeList().length) {
      html += '<h3>How state origin arrived, ' + esc(windowLabel()) + '</h3><table><tr><th>Route</th><th class="num">Articles</th></tr>' +
        routeList().map(function (r) { return '<tr><td>' + esc(r.label) + '</td><td class="num">' + (rts[r.id] || 0) + '</td></tr>'; }).join("") + '</table>';
      if (arrivalList().length) html += '<table><tr><th>Collected from</th><th class="num">Articles</th></tr>' +
        arrivalList().map(function (r) { return '<tr><td>' + esc(r.label) + '</td><td class="num">' + (arr[r.id] || 0) + '</td></tr>'; }).join("") + '</table>';
    }
    var rs = entry.release_sections;
    if (rs && rs.outlets_active) {
      var searched = rs.outlets_active - (rs.not_searched || 0);
      html += '<p class="panel-note">Press release, sponsored and partner sections searched at ' + searched + ' of ' + rs.outlets_active + ' active outlets' + (searched ? ', found at ' + (rs.found || 0) : '') + '. Where no section was searched, state origin placed there cannot be found.</p>';
    }
    if (entry.language_support && entry.language_support !== "full") html += '<p class="panel-note">Keyword lists cover ' + (entry.language_support === "none" ? 'none' : 'only some') + ' of this country\'s outlet languages (' + esc((entry.languages || []).join(", ")) + '); the rest are matched on international and English terms only.</p>';
    html += '<h3>Monitored outlets</h3><table><tr><th>Outlet</th><th class="num">State origin</th><th class="num">Relay</th><th class="num">Pending</th><th class="num">Independent</th><th class="num">Paywalled</th><th>Feeds</th><th>Release section</th></tr>';
    state.outlets.filter(function (o) { return o.country === iso; }).sort(function (a, b) { return (b.active - a.active) || a.name.localeCompare(b.name); }).forEach(function (o) {
      /* Health counts editorial feeds; release section feeds are listed in the title but never make an outlet look failing. */
      var editorial = o.feeds.filter(function (f) { return !f.kind || f.kind === "editorial"; });
      var okN = editorial.filter(function (f) { return f.ok; }).length;
      var feedCls = !o.active ? "muted" : (okN === editorial.length ? "ok" : (okN === 0 ? "fail" : "warn"));
      html += '<tr><td>' + esc(o.name) + (o.active ? '' : ' <span class="badge">inactive</span>') + (o.collector === "self_hosted" ? ' <span class="badge" title="Collected from the owner\'s machine">relayed</span>' : '') + '</td><td class="num">' + o.counts.A + '</td><td class="num">' + (relay.publishable ? o.counts.B : '<span class="relay-state">' + esc(relay.measured ? "withheld" : "n/m") + '</span>') + '</td><td class="num">' + (o.counts.pending || 0) + '</td><td class="num">' + o.counts.C + '</td><td class="num">' + o.counts.paywalled + '</td><td class="' + feedCls + '" title="' + esc(o.inactive_reason || o.feeds.map(function (f) { return f.url + (f.kind && f.kind !== "editorial" ? " (" + f.kind + ")" : "") + (f.ok ? " ok" : " " + (f.last_error || "failing")); }).join("\n")) + '">' + (o.active ? okN + '/' + o.feeds.length : '') + '</td><td class="muted">' + esc(RELEASE_LABELS[o.release_sections || "not_searched"] || o.release_sections) + '</td></tr>';
    });
    html += '</table>';
    html += '<h3>Target articles first, then the rest</h3><p class="muted">State placements, unverified relay, and pieces carrying official Chinese sourcing that still await the verification judgement, each with the sentence that triggered it. Independent coverage follows.</p><div id="panel-articles"><p class="muted">Loading</p></div>';
    body.innerHTML = html;
    bindClose();
    renderMini(iso, relay);
    loadArticles(iso);
  }

  function bindClose() { var b = el("panel-close"); if (b) b.addEventListener("click", function () { selectCountry(null); }); }

  function renderMini(iso, relay) {
    var svgm = d3.select("#mini");
    if (svgm.empty()) return;
    var w = 360, h = 88;
    svgm.attr("viewBox", "0 0 " + w + " " + h);
    var pts = days().map(function (d) {
      var e = C.dayEntry(state.months, d);
      var c = (e && e.countries && e.countries[iso]) || C.emptyCounts();
      return {date: new Date(d + "T00:00:00Z"), A: c.A, B: relay.publishable ? c.B : 0, P: c.pending || 0};
    });
    if (!pts.length) { svgm.append("text").attr("x", 4).attr("y", 14).text("No daily data"); return; }
    var x = d3.scaleUtc().domain(d3.extent(pts, function (p) { return p.date; })).range([4, w - 4]);
    var y = d3.scaleLinear().domain([0, d3.max(pts, function (p) { return Math.max(p.A, p.B, p.P); }) || 1]).nice().range([h - 16, 6]);
    var lineA = d3.line().x(function (p) { return x(p.date); }).y(function (p) { return y(p.A); });
    var lineB = d3.line().x(function (p) { return x(p.date); }).y(function (p) { return y(p.B); });
    var lineP = d3.line().x(function (p) { return x(p.date); }).y(function (p) { return y(p.P); });
    svgm.append("path").attr("class", "p").attr("d", lineP(pts));
    svgm.append("path").attr("class", "a").attr("d", lineA(pts));
    if (relay.publishable) svgm.append("path").attr("class", "b").attr("d", lineB(pts));
    svgm.append("text").attr("x", 4).attr("y", h - 4).text(pts[0].date.toISOString().slice(0, 10));
    svgm.append("text").attr("x", w - 4).attr("y", h - 4).attr("text-anchor", "end").text(pts[pts.length - 1].date.toISOString().slice(0, 10));
    svgm.append("text").attr("x", w - 4).attr("y", 12).attr("text-anchor", "end").text("solid state origin, " + (relay.publishable ? "dashed unverified relay, " : "") + "dotted pending, max " + y.domain()[1] + " per day");
  }

  function loadArticles(iso) {
    var target = el("panel-articles");
    var render = function (arts) {
      if (!target) return;
      if (!arts || !arts.length) { target.innerHTML = '<p class="muted">No classified China coverage yet.</p>'; return; }
      var outletName = {};
      state.outlets.forEach(function (o) { outletName[o.id] = o.name; });
      var relay = relayFor((state.latest.countries || {})[iso]);
      target.innerHTML = arts.slice(0, 60).map(function (a) {
        var cat = a.human_category || a.category;
        var prov = a.provenance === "human" ? "human-reviewed" : (a.provenance === "rules" ? "rules" : "model only");
        var srcs = (a.sources && a.sources.length) ? '<div class="a-meta">Chinese sources carried: ' + esc(a.sources.join(", ")) + '</div>' : '';
        var catLabel = cat === "B" && !relay.publishable ? "Relay judgement, " + (relay.measured ? "withheld" : "not settled") : C.nameOf(cat);
        var route = cat === "A" && a.route ? '<div class="a-meta">Route: ' + esc(routeLabel({kind: "route", id: a.route})) + (a.arrival && a.arrival !== "editorial_feed" ? '; collected from ' + esc(routeLabel({kind: "arrival", id: a.arrival}).toLowerCase()) : '') + '</div>' : '';
        return '<div class="article"><a class="a-title" href="' + esc(a.url) + '" target="_blank" rel="noopener">' + esc(a.title || a.url) + '</a>' +
          '<span class="a-meta">' + esc(outletName[a.outlet_id] || a.outlet_id) + ', ' + esc(a.date) + ' <span class="badge cat-' + esc(cat) + '">' + esc(catLabel) + (a.human_category && a.human_category !== a.category ? ' (machine said ' + esc(C.nameOf(a.category)) + ')' : '') + '</span><span class="badge prov-' + esc(a.provenance) + '">' + prov + '</span>' + (a.dup_group ? '<span class="badge" title="One of several placements of the same underlying item">syndicated</span>' : '') + '</span>' +
          srcs + route + (a.evidence_quote ? '<p class="a-quote">' + esc(a.evidence_quote) + '</p>' : '') +
          (a.signatures && a.signatures.length ? '<div class="a-meta">Signatures: ' + esc(a.signatures.join(", ")) + '</div>' : '') + '</div>';
      }).join("");
    };
    /* Cache the request, not just the result, so re-renders while it is in flight (the timeline
       scrubber re-renders the panel on every step) reuse it instead of fetching again. */
    if (!state.articlesCache[iso]) {
      state.articlesCache[iso] = getJSON("data/articles/" + iso + ".json").catch(function () { delete state.articlesCache[iso]; return []; });
    }
    state.articlesCache[iso].then(function (arts) { if (el("panel-articles") === target) render(arts); });
  }

  /* -------------------------------------------------------------- timeline */
  var tlDays = [];
  function setupTimeline() {
    var first = state.meta && state.meta.first_discovered;
    tlDays = days().filter(function (d) { return !first || d >= first; });
    var scrub = el("scrub");
    scrub.max = Math.max(0, tlDays.length - 1);
    /* The day the export ran is always partial, so the scrubber opens on the last complete day. */
    var gen = state.meta && state.meta.generated_at ? state.meta.generated_at.slice(0, 10) : null;
    var start = tlDays.length - 1;
    if (start > 0 && tlDays[start] >= gen) start -= 1;
    scrub.value = Math.max(0, start);
    state.endDate = tlDays.length ? tlDays[scrub.value] : null;
    scrub.addEventListener("input", function () { setDay(Number(scrub.value)); });
    el("step-back").addEventListener("click", function () { setDay(Number(scrub.value) - 1); });
    el("step-fwd").addEventListener("click", function () { setDay(Number(scrub.value) + 1); });
    el("play").addEventListener("click", togglePlay);
    renderSpark();
    updateDateLabel();
  }
  function setDay(i) {
    if (!tlDays.length) return;
    i = Math.max(0, Math.min(tlDays.length - 1, i));
    el("scrub").value = i;
    state.endDate = tlDays[i];
    updateDateLabel();
    renderMap();
    if (state.selected) renderPanel();
  }
  function updateDateLabel() { el("tl-date").textContent = state.endDate || "no days"; }
  function togglePlay() {
    if (state.playing) { clearInterval(state.playing); state.playing = null; el("play").textContent = "Play"; return; }
    if (!tlDays.length) return;
    stopThemePlay();
    if (Number(el("scrub").value) >= tlDays.length - 1) setDay(0);
    el("play").textContent = "Pause";
    state.playing = setInterval(function () {
      var i = Number(el("scrub").value);
      if (i >= tlDays.length - 1) { togglePlay(); return; }
      setDay(i + 1);
    }, 550);
  }
  /* The global daily total of the chosen measure, with the marks that keep a gap from reading as a trend:
     model ceiling days, days the relay collector ran too few hours, ruleset changes, and days whose labels
     still carry an older ruleset. */
  function renderSpark() {
    var s = d3.select("#spark");
    s.selectAll("*").remove();
    var w = 1000, h = 40;
    s.attr("viewBox", "0 0 " + w + " " + h);
    var byRoute = routeActive();
    var pts = tlDays.map(function (d, i) {
      var e = C.dayEntry(state.months, d);
      var tot = 0;
      if (e) {
        if (byRoute) {
          var src = state.route.kind === "arrival" ? e.arrivals : e.routes;
          Object.keys(src || {}).forEach(function (c) { tot += (src[c] || {})[state.route.id] || 0; });
        } else Object.keys(e.countries || {}).forEach(function (c) {
          var k = e.countries[c];
          tot += state.measure === "a" ? (k.A || 0) : (k.A || 0) + (k.B || 0) + (k.pending || 0) + (state.measure === "china" ? (k.C || 0) : 0);
        });
      }
      return {i: i, d: d, v: tot, ceiling: !!(e && e.llm_ceiling_hit), relayGap: !!(e && e.relay_incomplete), older: !!(e && e.labels_on_older_ruleset)};
    });
    var noun = byRoute ? "state origin articles, " + routeLabel(state.route).toLowerCase() : measureNoun();
    el("tl-hint").textContent = "Moves only the map; the theme counter keeps its own day and window. The shaded curve is the global daily number of " + noun + ". Amber bars are days on which more articles were waiting than the daily model call cap allowed, so those days are truncated rather than quiet; the cap binds the draw and a run can stop at its time budget before spending it. Red ticks along the bottom are days the collector on the owner's machine ran too few hours. Dashed vertical lines are ruleset changes, and grey ticks along the top are days whose labels still carry an older ruleset. Gold dotted lines are relevance gate changes, which apply only to items discovered after them.";
    if (!pts.length) return;
    var x = d3.scaleLinear().domain([0, Math.max(1, pts.length - 1)]).range([0, w]);
    var y = d3.scaleLinear().domain([0, d3.max(pts, function (p) { return p.v; }) || 1]).range([h - 1, 2]);
    var area = d3.area().x(function (p) { return x(p.i); }).y0(h - 1).y1(function (p) { return y(p.v); }).curve(d3.curveMonotoneX);
    s.append("path").attr("d", area(pts));
    pts.forEach(function (p) {
      if (p.ceiling) s.append("rect").attr("class", "ceiling").attr("x", x(p.i) - 2).attr("y", 0).attr("width", 4).attr("height", h);
      if (p.relayGap) s.append("rect").attr("class", "relay-gap").attr("x", x(p.i) - 3).attr("y", h - 3).attr("width", 6).attr("height", 3);
      if (p.older) s.append("rect").attr("class", "older-ruleset").attr("x", x(p.i) - 3).attr("y", 0).attr("width", 6).attr("height", 2);
    });
    ((state.meta && state.meta.ruleset_changes) || []).forEach(function (rc) {
      var idx = tlDays.indexOf(rc.date);
      if (idx === -1) return;
      s.append("line").attr("class", "ruleset").attr("x1", x(idx)).attr("x2", x(idx)).attr("y1", 0).attr("y2", h)
        .append("title").text("Ruleset " + rc.version + " from " + rc.date);
    });
    /* Gate changes move what is collected rather than how it is labelled, so they get their own mark. */
    ((state.meta && state.meta.gate_changes) || []).forEach(function (gc) {
      var idx = tlDays.indexOf(gc.date);
      if (idx === -1) return;
      s.append("line").attr("class", "gate").attr("x1", x(idx)).attr("x2", x(idx)).attr("y1", 0).attr("y2", h)
        .append("title").text("Relevance gate " + gc.version + " from " + gc.date + ", applies to items discovered after it");
    });
  }

  /* ------------------------------------------------------ theme timeline */
  /* The theme counter has its own day and its own Play, separate from the map's timeline.
     Starting either animation stops the other, so the two never run at the same time. */
  function themeWindowLabel() { return windowLabelFor(state.themeEnd, state.themeWindow); }
  function themeAgg() {
    return C.aggregateWindow(state.months, state.themeEnd, windowArg(state.themeWindow));
  }
  function setupThemeTimeline() {
    var scrub = el("th-scrub");
    if (!scrub) return;
    scrub.max = Math.max(0, tlDays.length - 1);
    scrub.value = el("scrub").value;
    state.themeEnd = state.endDate;
    el("th-date").textContent = state.themeEnd || "no days";
    scrub.addEventListener("input", function () { stopThemePlay(); setThemeDay(Number(scrub.value)); });
    el("th-back").addEventListener("click", function () { stopThemePlay(); setThemeDay(Number(scrub.value) - 1); });
    el("th-fwd").addEventListener("click", function () { stopThemePlay(); setThemeDay(Number(scrub.value) + 1); });
    el("th-play").addEventListener("click", toggleThemePlay);
  }
  function setThemeDay(i) {
    if (!tlDays.length) return;
    i = Math.max(0, Math.min(tlDays.length - 1, i));
    el("th-scrub").value = i;
    state.themeEnd = tlDays[i];
    el("th-date").textContent = state.themeEnd;
    renderThemes();
  }
  function stopThemePlay() {
    if (!state.themePlaying) return;
    clearInterval(state.themePlaying);
    state.themePlaying = null;
    el("th-play").textContent = "Play";
  }
  function toggleThemePlay() {
    if (state.themePlaying) { stopThemePlay(); return; }
    if (!tlDays.length) return;
    if (state.playing) togglePlay();
    if (Number(el("th-scrub").value) >= tlDays.length - 1) setThemeDay(0);
    el("th-play").textContent = "Pause";
    state.themePlaying = setInterval(function () {
      var i = Number(el("th-scrub").value);
      if (i >= tlDays.length - 1) { stopThemePlay(); return; }
      setThemeDay(i + 1);
    }, 700);
  }
  /* The theme curve follows the counter's own subject: the selected country, or all of them. */
  function renderThemeSpark() {
    var s = d3.select("#th-spark");
    if (s.empty()) return;
    s.selectAll("*").remove();
    var w = 1000, h = 40, mi = measureIndex(), noun = measureNoun();
    s.attr("viewBox", "0 0 " + w + " " + h);
    var pts = tlDays.map(function (d, i) {
      var e = C.dayEntry(state.months, d), v = 0;
      if (e) Object.keys(e.countries || {}).forEach(function (c) { if (!state.selected || state.selected === c) v += denominator(e.countries[c], mi); });
      return {i: i, v: v};
    });
    var who = state.selected ? (state.names[state.selected] || state.selected) : "all monitored countries";
    el("th-hint").textContent = "Moves only the theme counter; the map keeps its own day and window. The curve is the daily number of " + noun + " in " + who + ".";
    if (!pts.length) return;
    var x = d3.scaleLinear().domain([0, Math.max(1, pts.length - 1)]).range([0, w]);
    var y = d3.scaleLinear().domain([0, d3.max(pts, function (p) { return p.v; }) || 1]).range([h - 1, 2]);
    s.append("path").attr("d", d3.area().x(function (p) { return x(p.i); }).y0(h - 1).y1(function (p) { return y(p.v); }).curve(d3.curveMonotoneX)(pts));
  }

  /* ----------------------------------------------------------- methodology */
  function renderMethod() {
    var m = state.meta;
    var dl = el("method-facts");
    if (!m) { dl.innerHTML = '<dt>Status</dt><dd>No export has run yet.</dd>'; el("citation").textContent = C.citation(new Date().toISOString().slice(0, 10), CITATION_AUTHOR); return; }
    var k = m.kappa, relay = relayFor(null);
    var rs = m.release_sections, rc = m.relay_collector, fs = m.feed_saturation;
    var routeTotals = m.route_totals || {};
    var rows = [
      ["Outlets monitored", m.outlets_active + " active of " + m.outlets_total + " registered, across " + m.countries_monitored + " countries"],
      ["Population figures", m.population_source || "not recorded"],
      ["Share of monitored output", (m.countries_with_audience_ranks && m.countries_with_audience_ranks.length ? m.countries_with_audience_ranks.length + " countries carry audience ranks and use their largest outlets; " : "no country carries audience ranks yet; ") + "elsewhere every active outlet counts, and no rate is shown below " + (m.min_outlets_for_output_share || C.MIN_OUTLETS_FOR_OUTPUT_SHARE) + " outlets"],
      ["Countries with zero coverage", (m.countries_in_gaps || 0) + " recorded in the gaps file with a reason; every unhatched country not listed there is simply unregistered"],
      ["Articles", m.articles_discovered + " discovered, " + m.articles_gate_relevant + " passed the relevance gate, " + m.articles_classified + " classified"],
      ["Paywall-blocked proportion", m.paywall_share === null || m.paywall_share === undefined ? "not measured" : pct(m.paywall_share) + " of gated articles: missing from every count, but still counted in the share of monitored output denominator, so that share is a lower bound" + (m.paywall_flagged_countries && m.paywall_flagged_countries.length ? "; flagged: " + m.paywall_flagged_countries.join(", ") : "")],
      ["Unverified relay", !relay.measured ? "not yet measured: the verification stage has not run"
        : (relay.publishable ? "published, " + (m.relay_qualifier || "basis not recorded") : "withheld until a study settles it")
          + ((m.relay_withheld_languages || []).length ? " Withheld for " + m.relay_withheld_languages.join(", ") + "." : "")],
      ["Labels that cannot be reclassified", (m.labels_unreclassifiable || 0) + " of " + (m.articles_classified || 0) + ", because the article can no longer be retrieved"],
      ["Relay reliability study", m.relay_reliability ? (m.relay_reliability.method === "model_vs_model" ? "second model " + m.relay_reliability.model_b + " against " + m.relay_reliability.model_a : m.relay_reliability.method) + ", " + m.relay_reliability.n + " articles, kappa " + (m.relay_reliability.kappa_bc === null || m.relay_reliability.kappa_bc === undefined ? "n/a" : m.relay_reliability.kappa_bc.toFixed(2)) + " on relay versus independent" : "none run"],
      ["Human coding", m.relay_human_coded ? "some articles have been read by a person" : "none: no article has been read by a person, so nothing here is validated against human judgement"],
      ["Current kappa", k && k.bc !== null && k.bc !== undefined ? "all categories " + (k.all === null ? "n/a" : k.all.toFixed(2)) + ", unverified relay versus independent " + k.bc.toFixed(2) + " (n = " + k.n + ", computed " + (k.computed_at || "").slice(0, 10) + ")" : "not yet measured"],
      ["Routes of state origin", (m.routes || []).map(function (r) { return r.label.toLowerCase() + " " + (routeTotals[r.id] || 0); }).join("; ") || "not recorded"],
      ["Release sections searched", rs && rs.outlets_active ? (rs.outlets_active - (rs.not_searched || 0)) + " of " + rs.outlets_active + " active outlets; " + (rs.found || 0) + " found, " + (rs.none_found || 0) + " none, " + (rs.blocked || 0) + " blocked, " + (rs.unreachable || 0) + " unreachable" : "not recorded"],
      ["Feed polls with nothing seen before", fs && fs.polls ? fs.saturated + " of " + fs.polls + ", an estimated " + fs.missed_estimate + " items missed" : "not yet measured"],
      ["Collector on the owner's machine", rc ? rc.outlets + " outlets; " + (rc.last_run ? "last pass " + rc.last_run + ", " + (rc.incomplete_days || []).length + " incomplete days" : "no heartbeat yet") : "not recorded"],
      ["Human review coverage", pct(m.review_coverage) + " of classified articles (" + m.articles_reviewed + ")"],
      ["Relevance gate version", (m.gate_version || "not recorded") + (m.gate_applies_forward_only ? ", applying to items discovered after each gate change; earlier rejections are not re-examined and are pruned after three days" : "")],
      ["Ruleset version", m.ruleset_version + (m.reclassification_complete === false ? ", reclassification in progress (" + Object.keys(m.ruleset_mix || {}).sort().map(function (v) { return v + ": " + m.ruleset_mix[v]; }).join(", ") + ")" : "")],
      ["Classifier model", m.llm_model + ", " + m.llm_calls_total + " calls to date, daily ceiling " + m.llm_daily_ceiling + (m.llm_ceiling_days && m.llm_ceiling_days.length ? ", draw capped on " + m.llm_ceiling_days.map(function (d) { return d + " (" + ((m.llm_calls_by_day || {})[d] || 0) + " calls made)"; }).join(", ") : "") + ((m.llm_sampling_days || []).length ? "; stratified draws on " + m.llm_sampling_days.length + " days" : "") + (m.llm_budget ? "; estimated spend " + m.llm_budget.month + ": $" + m.llm_budget.estimated_usd + " of a $" + m.llm_budget.budget_usd + " monthly budget" + (m.llm_budget.calls_left_today !== null && m.llm_budget.calls_left_today !== undefined ? ", leaving " + m.llm_budget.calls_left_today + " batched calls for the rest of today" : "") : "")],
      ["Last successful run", m.last_successful_run || "none"],
      ["Data generated", m.generated_at]
    ];
    dl.innerHTML = rows.map(function (r) { return '<dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd>'; }).join("");
    el("citation").textContent = C.citation(new Date().toISOString().slice(0, 10), CITATION_AUTHOR);
    el("footer-run").textContent = "Generated " + m.generated_at + ". Ruleset " + m.ruleset_version + ". Schema " + m.schema_version + ".";
  }

  /* --------------------------------------------------------------- exports */
  function download(name, text) {
    var blob = new Blob([text], {type: "text/csv;charset=utf-8"});
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  function exportView() {
    var agg = currentAgg();
    var cite = C.citation(new Date().toISOString().slice(0, 10), CITATION_AUTHOR);
    var rows = C.rankCountries(agg, state.latest, state.metric, state.mode, state.names, ctxFor).map(function (r) {
      r.metric = state.metric; r.route = routeActive() ? state.route.kind + ":" + state.route.id : ""; r.window = windowLabel(); r.mode = state.mode; r.relay_basis = relayBasis() || "";
      r.warnings = countryWarnings(state.latest.countries[r.iso] || {}, agg).join("; ");
      r.citation = cite;
      return r;
    });
    download("tracker_view_" + state.metric + "_" + (state.endDate || "empty") + ".csv", C.toCSV(rows, ["iso", "name", "metric", "route", "window", "mode", "value", "fill", "note", "state_origin", "state_origin_underlying_items", "unverified_relay", "relay_status", "relay_basis", "official_sourcing_pending", "target", "independent", "china_total", "outlets_active", "population", "items_published_monitored_outlets", "target_in_published_items", "china_in_published_items", "language_support", "warnings", "citation"]));
  }
  function exportDaily() {
    var rows = [];
    var cite = C.citation(new Date().toISOString().slice(0, 10), CITATION_AUTHOR);
    var routeCols = routeList().map(function (r) { return "route_" + r.id; }).concat(arrivalList().map(function (r) { return "arrival_" + r.id; }));
    days().forEach(function (d) {
      var e = C.dayEntry(state.months, d);
      if (!e) return;
      Object.keys(e.countries).forEach(function (iso) {
        var c = e.countries[iso], r = (e.reviewed || {})[iso] || {};
        var relay = relayFor((state.latest.countries || {})[iso]);
        var blank = function (v) { return relay.publishable ? v : ""; };
        var row = {date: d, iso: iso, name: state.names[iso] || iso, state_origin: c.A, state_origin_underlying_items: c.uniqA, unverified_relay: blank(c.B), relay_status: C.relayStatus(relay), independent: c.C, not_relevant: c.N,
                   state_origin_rules: c.Ar, state_origin_model: c.Al, unverified_relay_rules: blank(c.Br), unverified_relay_model: blank(c.Bl),
                   reviewed: c.rev, human_state_origin: r.A || 0, human_unverified_relay: blank(r.B || 0), human_independent: r.C || 0, discovered: c.disc, gate_relevant: c.rel, fetched: c.fetched,
                   paywalled: c.paywalled, failed: c.failed, blocked_robots: c.blocked, awaiting_model: c.pending,
                   items_published_monitored_outlets: c.tdisc, target_in_published_items: c.ttarget, china_in_published_items: c.tchina,
                   feed_polls: c.polls || 0, feed_polls_saturated: c.sat || 0, items_missed_estimate: c.miss || 0,
                   model_draw_eligible: ((e.llm_sampling || {})[iso] || [""])[0], model_draw_sent: ((e.llm_sampling || {})[iso] || ["", ""])[1],
                   llm_ceiling_hit: e.llm_ceiling_hit, relay_collector_incomplete: !!e.relay_incomplete, labels_on_older_ruleset: e.labels_on_older_ruleset || 0, citation: cite};
        routeList().forEach(function (x) { row["route_" + x.id] = ((e.routes || {})[iso] || {})[x.id] || 0; });
        arrivalList().forEach(function (x) { row["arrival_" + x.id] = ((e.arrivals || {})[iso] || {})[x.id] || 0; });
        rows.push(row);
      });
    });
    download("tracker_daily_counts.csv", C.toCSV(rows, ["date", "iso", "name", "state_origin", "state_origin_underlying_items", "unverified_relay", "relay_status", "independent", "not_relevant", "state_origin_rules", "state_origin_model", "unverified_relay_rules", "unverified_relay_model", "reviewed", "human_state_origin", "human_unverified_relay", "human_independent", "discovered", "gate_relevant", "fetched", "paywalled", "failed", "blocked_robots", "awaiting_model", "items_published_monitored_outlets", "target_in_published_items", "china_in_published_items", "feed_polls", "feed_polls_saturated", "items_missed_estimate", "model_draw_eligible", "model_draw_sent"].concat(routeCols).concat(["llm_ceiling_hit", "relay_collector_incomplete", "labels_on_older_ruleset", "citation"])));
  }

  /* -------------------------------------------------------------- controls */
  function setupRouteSelect() {
    var sel = el("route");
    if (!sel) return;
    var groups = [["How it arrived on the page", routeList()], ["Where it was collected", arrivalList()]];
    sel.innerHTML = '<option value="">Every route</option>' + groups.filter(function (g) { return g[1].length; }).map(function (g) {
      return '<optgroup label="' + esc(g[0]) + '">' + g[1].map(function (r) { return '<option value="' + r.kind + ':' + esc(r.id) + '">' + esc(r.label) + '</option>'; }).join("") + '</optgroup>';
    }).join("");
  }

  function bindControls() {
    bindThemes();
    setupRouteSelect();
    /* Measure and Basis toggles form a grid; the same Basis toggle is repeated above the map and above
       the ranked list and every copy stays in step. The select holds the other denominators. A route
       applies to state origin only, so choosing one switches the measure to state origin. */
    function applyMetric() {
      var other = el("metric").value;
      state.metric = other || C.gridMetric(state.measure, state.basis);
      Array.prototype.forEach.call(document.querySelectorAll("[data-basis-group] button"), function (b) { b.classList.toggle("active", !other && b.getAttribute("data-basis") === state.basis); });
      Array.prototype.forEach.call(el("measure").querySelectorAll("button"), function (b) { b.classList.toggle("active", !other && b.getAttribute("data-measure") === state.measure); });
      el("route").value = state.route ? state.route.kind + ":" + state.route.id : "";
      renderMap(); renderSpark(); renderThemes(); if (state.selected) renderPanel();
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-basis-group] button"), function (b) {
      b.addEventListener("click", function () { state.basis = b.getAttribute("data-basis"); el("metric").value = ""; applyMetric(); });
    });
    Array.prototype.forEach.call(el("measure").querySelectorAll("button"), function (b) {
      b.addEventListener("click", function () {
        state.measure = b.getAttribute("data-measure");
        if (state.measure !== "a") state.route = null;
        el("metric").value = ""; applyMetric();
      });
    });
    el("metric").addEventListener("change", function () { if (el("metric").value) state.route = null; applyMetric(); });
    el("route").addEventListener("change", function () {
      var v = el("route").value;
      state.route = v ? {kind: v.slice(0, v.indexOf(":")), id: v.slice(v.indexOf(":") + 1)} : null;
      if (state.route) { state.measure = "a"; el("metric").value = ""; if (state.basis === "share_of_output") state.basis = "count"; }
      applyMetric();
    });
    /* The Window toggle is repeated above the map and above the ranked list, like Basis. */
    function applyWindow(v) {
      state.windowDays = v === "all" ? "all" : Number(v);
      Array.prototype.forEach.call(document.querySelectorAll("[data-window-group] button"), function (b) { b.classList.toggle("active", b.getAttribute("data-window") === v); });
      renderMap(); if (state.selected) renderPanel();
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-window-group] button"), function (b) {
      b.addEventListener("click", function () { applyWindow(b.getAttribute("data-window")); });
    });
    Array.prototype.forEach.call(el("mode").querySelectorAll("button"), function (b) {
      b.addEventListener("click", function () {
        state.mode = b.getAttribute("data-mode");
        Array.prototype.forEach.call(el("mode").querySelectorAll("button"), function (x) { x.classList.toggle("active", x === b); });
        renderMap(); renderThemes(); if (state.selected) renderPanel();
      });
    });
    Array.prototype.forEach.call(el("view").querySelectorAll("button"), function (b) {
      b.addEventListener("click", function () {
        var v = b.getAttribute("data-view");
        document.body.classList.toggle("force-map", v === "map");
        document.body.classList.toggle("force-list", v === "list");
        Array.prototype.forEach.call(el("view").querySelectorAll("button"), function (x) { x.classList.toggle("active", x === b); });
        renderMap();
      });
    });
    el("export-view").addEventListener("click", exportView);
    el("export-daily").addEventListener("click", exportDaily);
  }

  /* ------------------------------------------------------------------ init */
  function init() {
    loadAll().then(function () {
      try { setupMap(); } catch (e) { console.error("map setup failed", e); mapFallback("The map could not be drawn: " + (e && e.message ? e.message : e)); }
      if (!window.d3) mapFallback("The map library did not load.");
      else if (!state.topo) mapFallback("The world outline data did not load.");
      bindControls();
      setupCountryPicks();
      setupTimeline();
      setupThemeTimeline();
      renderNotices();
      renderMap();
      renderThemes();
      renderPanel();
      renderMethod();
    }).catch(function (e) {
      console.error(e);
      el("data-notice").textContent = "Data could not be loaded: " + e.message;
      el("data-notice").classList.remove("hidden");
      try { setupMap(); bindControls(); setupTimeline(); setupThemeTimeline(); renderMap(); renderThemes(); renderPanel(); renderMethod(); } catch (e2) { console.error(e2); }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
