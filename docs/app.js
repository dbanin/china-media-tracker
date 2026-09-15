/* China state media tracker front end. Vanilla JavaScript and D3, no build step.
   Reads static JSON from data/. Every view must render with an empty dataset without throwing. */
(function () {
  "use strict";
  var C = window.TrackerCompute;
  var CITATION_AUTHOR = "Daniel Banin";

  var state = {
    metric: "count_target", measure: "target", basis: "count", windowDays: 30, themeSort: null, themeEnd: null, themePlaying: null, themeWindow: 30, mode: "all", endDate: null, selected: null, playing: null, zoomIso: null, zoomK: 1, lastCountry: null,
    meta: null, latest: null, series: [], months: {}, outlets: [], names: {}, officialNames: {}, numToIso: {}, topo: null,
    articlesCache: {}
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
  function currentAgg() {
    return C.aggregateWindow(state.months, state.endDate, state.windowDays === "all" ? null : Number(state.windowDays));
  }
  function bProvisional() { return !(state.meta && state.meta.b_counts_settled); }
  function metricLabel() { return C.METRICS[state.metric] ? C.METRICS[state.metric].label : state.metric; }
  function windowLabel() { return windowLabelFor(state.endDate, state.windowDays); }
  function windowLabelFor(date, days) {
    if (!date) return "no data";
    if (days === "all") return "total to " + date;
    if (Number(days) === 1) return date;
    return Number(days) + " days ending " + date;
  }

  /* --------------------------------------------------------------- notices */
  function renderNotices() {
    var kn = el("kappa-notice");
    var m = state.meta;
    if (!m) { kn.textContent = "No data has been exported yet. The interface is rendering an empty dataset."; kn.classList.remove("hidden"); }
    else if (!m.b_counts_settled) {
      var k = m.kappa;
      kn.innerHTML = k && k.bc !== null && k.bc !== undefined
        ? "Unverified relay counts are provisional. Cohen's kappa on the unverified relay versus independent journalism distinction is " + k.bc.toFixed(2) + " (n = " + k.n_bc + "), below the " + m.kappa_warning_threshold + " threshold. Metrics that include unverified relay are shown but are not settled."
        : "Unverified relay counts are provisional. No agreement study has been completed yet, so the machine labels have not been checked against hand coding. Metrics that include unverified relay are shown but are not settled.";
      kn.classList.remove("hidden");
    } else kn.classList.add("hidden");
    var dn = el("data-notice");
    var flagged = (m && m.paywall_flagged_countries) || [];
    var gaps = (m && m.countries_in_gaps) || 0;
    var parts = [];
    if (flagged.length) parts.push("Paywalls removed more than " + Math.round((m.paywall_flag_share || 0.33) * 100) + " percent of retrieved articles in " + flagged.map(function (c) { return state.names[c] || c; }).join(", ") + ". Those countries are not comparable to the rest and carry a warning marker.");
    if (m && m.countries_monitored && m.countries_monitored < 30) parts.push("Only " + m.countries_monitored + " countries are monitored so far. The map mostly displays the registry, not the world.");
    if (gaps) parts.push(gaps + " countries are recorded as coverage gaps with a stated reason.");
    if (m && !m.llm_calls_total) parts.push("The verification stage that separates unverified relay from independent journalism has not run yet, so no article has been confirmed as unverified relay. Candidates are shown as pending.");
    if (m && m.official_sourcing_pending) parts.push(m.official_sourcing_pending + " articles in " + m.official_sourcing_pending_countries + " countries carry official Chinese sourcing and are waiting for the verification judgement that separates unverified relay from independent journalism. They are counted as targets and listed in each country panel with the sentence that triggered them.");
    var u = m && m.registry_unevenness;
    if (u && u.max_over_median && u.max_over_median >= 3) parts.push("The registry is uneven: the densest country has " + u.max + " active outlets against a median of " + u.median + ", and " + u.countries_with_one_outlet + " countries have a single outlet. Raw counts mostly display that sampling. Share and per-outlet metrics correct for it; count metrics do not.");
    var ranked = (m && m.countries_with_audience_ranks) || [];
    if (m && !ranked.length) parts.push("The share of all published items metrics use every active outlet in a country as the denominator, because no outlet carries an audience rank yet. Once ranks are recorded in the registry, the denominator becomes the " + (m.top_outlets_per_country || 30) + " largest outlets by audience.");
    if (parts.length) { dn.textContent = parts.join(" "); dn.classList.remove("hidden"); } else dn.classList.add("hidden");
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
    if (cls === "nodata") return "#1a1c1f";
    if (cls === "sparse") return "url(#sparse)";
    if (cls === "zero") return ZERO_COLOR;
    return colorScale(value);
  }

  function renderMap() {
    if (!gCountries) return;
    var agg = currentAgg();
    var vals = [];
    var perIso = {};
    Object.keys(state.latest.countries || {}).forEach(function (iso) {
      var entry = state.latest.countries[iso];
      var mv = C.metricValue(agg.countries[iso], state.metric, entry.outlets_active, state.mode, agg.reviewed[iso], {population: entry.population});
      var cls = C.fillClass(entry, mv);
      perIso[iso] = {entry: entry, mv: mv, cls: cls};
      if (cls === "value") vals.push(mv.value);
    });
    /* The ramp tops out at the 95th percentile so one outlier does not flatten every other country.
       Values above the cap take the darkest color; the legend says the cap is a cap. */
    var trueMax = vals.length ? d3.max(vals) : 1;
    var max = vals.length ? C.percentile(vals, 0.95) : 1;
    if (!max) max = trueMax || 1;
    var capped = vals.some(function (v) { return v > max; });
    var fmt = C.METRICS[state.metric] ? C.METRICS[state.metric].format : "int";
    if (fmt === "pct") max = Math.max(max, 0.05);
    /* White for exactly zero; any positive value takes one of STEPS tinted steps up to deep red at the cap. */
    var stepScale = d3.scaleThreshold().domain(stepEdges(max)).range(STEP_COLORS);
    colorScale = function (v) { return v > 0 ? stepScale(v) : ZERO_COLOR; };
    state._perIso = perIso; state._max = max; state._capped = capped; state._trueMax = trueMax;
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
      if (!p || !(p.entry.warnings || []).length) return;
      var c = path.centroid(f);
      if (isNaN(c[0])) return;
      gMarkers.append("path").attr("class", "warn-marker").attr("d", d3.symbol(d3.symbolTriangle, 40)()).attr("transform", "translate(" + c[0] + "," + c[1] + ") scale(" + (1 / state.zoomK) + ")");
    });
    renderLegend(max, fmt);
    renderBars(agg);
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
    var m = C.METRICS[state.metric] || {};
    var notShown = m.allItems ? "Not enough articles published in this window to give a percent"
      : m.population ? "No population figure, or under " + C.MIN_POPULATION.toLocaleString("en-US") + " residents"
      : m.format === "pct" ? "Fewer than " + C.MIN_SHARE_DENOMINATOR + " China articles, so no share"
      : null;
    el("legend").innerHTML =
      '<div class="lg-group">' +
        '<div class="lg-kicker">Color scale</div>' +
        '<div class="lg-head">' + esc(metricLabel()) + '</div>' +
        '<div class="lg-when">' + esc(windowLabel()) + '</div>' +
        '<ul class="lg-steps">' +
          '<li><span class="sw" style="background:' + ZERO_COLOR + '"></span><span>None found</span></li>' +
          steps.join("") +
        '</ul>' +
        (state.zoomIso ? '<p class="lg-note">Zoomed to ' + esc(countryName(state.zoomIso)) + '. Shades keep the world scale, so they compare directly with every other country.</p>' : '') +
        (state._capped ? '<p class="lg-note">The darkest shade starts at the 95th percentile, so a few extreme countries do not wash out the rest. The highest value is ' + esc(C.formatValue(state._trueMax, fmt)) + '.</p>' : '') +
      '</div>' +
      '<div class="lg-group">' +
        '<div class="lg-kicker">Not on the scale</div>' +
        '<ul class="lg-keys">' +
          '<li><span class="sw" style="background:repeating-linear-gradient(45deg,#141618,#141618 3px,#2b2e33 3px,#2b2e33 4px)"></span><span>Not monitored: no outlets registered</span></li>' +
          '<li><span class="sw" style="background:radial-gradient(#3a3d43 0.9px, #141618 1px) 0 0/6px 6px"></span><span>Coverage gap, or every outlet inactive</span></li>' +
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
      var e = p.entry, mv = p.mv;
      if (p.cls === "gap") html += '<div class="muted">Coverage gap: ' + esc(e.gap_reason) + '</div>';
      else if (p.cls === "inactive") html += '<div class="muted">' + e.outlets_total + ' outlets registered, none active.</div>';
      else {
        html += '<div>' + esc(metricLabel()) + ': <strong>' + C.formatValue(mv.value, C.METRICS[state.metric].format) + '</strong></div>';
        html += '<div class="muted">State origin ' + mv.a + ', unverified relay ' + mv.b + (bProvisional() ? ' (provisional)' : '') + ', official sourcing pending ' + mv.pending + ', independent ' + mv.c + ' in ' + esc(windowLabel()) + '</div>';
        html += '<div class="muted">' + e.outlets_active + ' active outlets, ' + e.feeds_ok + ' of ' + e.feeds_total + ' feeds healthy</div>';
        if (p.cls === "nodata") html += '<div class="muted">No China coverage classified in this window.</div>';
        if (p.cls === "sparse" && mv.note) html += '<div class="muted">' + esc(mv.note) + '</div>';
        if (C.METRICS[state.metric] && C.METRICS[state.metric].allItems) html += '<div class="muted">' + mv.allItems + ' items published by the ' + e.top_outlets + ' largest monitored outlets' + (e.top_outlets_ranked ? '' : ' (no audience ranks recorded, so every active outlet counts)') + ', ' + mv.allItemsTarget + ' targets and ' + mv.allItemsChina + ' China items among them</div>';
        if (C.METRICS[state.metric] && C.METRICS[state.metric].population && e.population) html += '<div class="muted">Population ' + e.population.toLocaleString("en-US") + '</div>';
      }
      (e.warnings || []).forEach(function (w) { html += '<div class="t-warn">Warning: ' + esc(w.text) + '</div>'; });
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
    var rows = C.rankCountries(agg, state.latest, state.metric, state.mode, state.names);
    var fmt = C.METRICS[state.metric] ? C.METRICS[state.metric].format : "int";
    var max = d3.max(rows, function (r) { return r.value || 0; }) || 1;
    el("bars-title").textContent = metricLabel() + ", " + windowLabel();
    el("bars").innerHTML = rows.map(function (r) {
      var w = r.value ? Math.max(2, 100 * r.value / max) : 0;
      var cls = r.fill === "value" ? "" : (r.fill === "sparse" ? "zero" : r.fill);
      return '<div class="bar-row" data-iso="' + r.iso + '"><span>' + esc(r.name) + '</span><span><span class="bar ' + cls + '" style="width:' + (r.fill === "value" ? w : (r.fill === "nocoverage" ? 100 : 6)) + '%"></span></span><span class="num">' + C.formatValue(r.value, fmt) + '</span></div>';
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
  function measureIndex() { var i = C.MEASURE_INDEX[state.measure]; return i === undefined ? 1 : i; }
  function denominator(k, mi) {
    k = k || C.emptyCounts();
    if (mi === 0) return k.A + k.B + k.C + (k.pending || 0);
    if (mi === 1) return k.A + k.B + (k.pending || 0);
    return k.A;
  }
  function pctText(share) { return share >= 0.995 ? "100%" : (share < 0.005 && share > 0 ? "<1%" : Math.round(100 * share) + "%"); }

  function themeModel(agg) {
    var catalog = (state.meta && state.meta.themes) || [];
    var byIso = C.aggregateThemes(state.months, state.themeEnd, state.themeWindow === "all" ? null : Number(state.themeWindow));
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
    el("themes-sub").textContent = noun.charAt(0).toUpperCase() + noun.slice(1) + " by theme" + (state.selected ? " in " + countryName(state.selected) : "") + ", " + themeWindowLabel() +
      ". An article can carry more than one theme, so theme counts can add up to more than the number of articles.";
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
      (sel && rows.indexOf(sel) >= THEME_ROWS ? ', plus the selected country' : '') + '. Click a country to open it, or a theme heading to rank by it.</span>';
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
        var top = C.rankCountries(currentAgg(), state.latest, state.metric, state.mode, state.names).filter(function (r) { return r.value > 0; })[0];
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

  function renderPanel() {
    var body = el("panel-body");
    if (!state.selected) {
      var t = (state.latest.totals && state.latest.totals.all_time) || null;
      body.innerHTML = '<h2>Select a country</h2><p class="muted">Click a country on the map, or a row in the ranked list on small screens, to see its time series, category breakdown, monitored outlets and recent classified articles.</p>' +
        (t ? '<h3>All monitored countries, all time</h3><table><tr><th>State origin</th><td class="num">' + t.A + '</td></tr><tr><th>Unverified relay' + (bProvisional() ? ' <span class="badge">provisional</span>' : '') + '</th><td class="num">' + t.B + '</td></tr><tr><th>Official Chinese sourcing, verification pending</th><td class="num">' + t.pending + '</td></tr><tr><th>Independent journalism</th><td class="num">' + t.C + '</td></tr><tr><th>Not relevant</th><td class="num">' + t.N + '</td></tr><tr><th>Paywalled</th><td class="num">' + t.paywalled + '</td></tr></table>' : '<p class="muted">No totals available.</p>');
      return;
    }
    var iso = state.selected;
    var entry = (state.latest.countries || {})[iso];
    var name = iso.indexOf("name:") === 0 ? iso.slice(5) : (state.names[iso] || iso);
    var agg = currentAgg();
    var html = '<button class="close" id="panel-close">Close</button><h2>' + esc(name) + '</h2>';
    if (!entry) { html += '<p class="muted">No monitored outlets in ' + esc(name) + '. This is absence of data, not absence of content. To add coverage, add an outlet with a working feed to sources/outlets.yaml, or record the reason in sources/gaps.yaml.</p>'; body.innerHTML = html; bindClose(); return; }
    if (entry.coverage === "gap") { html += '<p class="warn">Coverage gap: ' + esc(entry.gap_reason) + '</p>'; body.innerHTML = html; bindClose(); return; }
    (entry.warnings || []).forEach(function (w) { html += '<p class="warn">Warning: ' + esc(w.text) + '</p>'; });
    var mv = C.metricValue(agg.countries[iso], state.metric, entry.outlets_active, state.mode, agg.reviewed[iso], {population: entry.population});
    html += '<p>' + esc(metricLabel()) + ', ' + esc(windowLabel()) + ': <strong>' + C.formatValue(mv.value, C.METRICS[state.metric].format) + '</strong></p>';
    html += '<h3>Time series, all days</h3><svg class="mini" id="mini"></svg>';
    var k = agg.countries[iso] || C.emptyCounts();
    var rv = agg.reviewed[iso] || {A: 0, B: 0, C: 0, N: 0};
    html += '<h3>Breakdown, ' + esc(windowLabel()) + '</h3><table><tr><th></th><th class="num">All</th><th class="num">Rules</th><th class="num">Model</th><th class="num">Human</th></tr>' +
      '<tr><td>State origin</td><td class="num">' + k.A + '</td><td class="num">' + k.Ar + '</td><td class="num">' + k.Al + '</td><td class="num">' + rv.A + '</td></tr>' +
      '<tr><td>Unverified relay' + (bProvisional() ? ' <span class="badge">provisional</span>' : '') + '</td><td class="num">' + k.B + '</td><td class="num">' + k.Br + '</td><td class="num">' + k.Bl + '</td><td class="num">' + rv.B + '</td></tr>' +
      '<tr><td>Official Chinese sourcing, verification pending</td><td class="num">' + k.pending + '</td><td class="num">' + k.pending + '</td><td class="num"></td><td class="num"></td></tr>' +
      '<tr><td>Independent journalism</td><td class="num">' + k.C + '</td><td class="num"></td><td class="num"></td><td class="num">' + rv.C + '</td></tr>' +
      '<tr><td>Not relevant</td><td class="num">' + k.N + '</td><td class="num"></td><td class="num"></td><td class="num">' + rv.N + '</td></tr>' +
      '<tr><td class="muted">Underlying items (state origin plus unverified relay)</td><td class="num">' + k.uniqAB + '</td><td></td><td></td><td></td></tr>' +
      '<tr><td class="muted">Fetched / paywalled / failed / robots</td><td class="num" colspan="4">' + k.fetched + ' / ' + k.paywalled + ' / ' + k.failed + ' / ' + k.blocked + '</td></tr>' +
      '</table>';
    html += '<h3>Monitored outlets</h3><table><tr><th>Outlet</th><th class="num">State origin</th><th class="num">Relay</th><th class="num">Pending</th><th class="num">Independent</th><th class="num">Paywalled</th><th>Feeds</th></tr>';
    state.outlets.filter(function (o) { return o.country === iso; }).sort(function (a, b) { return (b.active - a.active) || a.name.localeCompare(b.name); }).forEach(function (o) {
      var okN = o.feeds.filter(function (f) { return f.ok; }).length;
      var feedCls = !o.active ? "muted" : (okN === o.feeds.length ? "ok" : (okN === 0 ? "fail" : "warn"));
      html += '<tr><td>' + esc(o.name) + (o.active ? '' : ' <span class="badge">inactive</span>') + '</td><td class="num">' + o.counts.A + '</td><td class="num">' + o.counts.B + '</td><td class="num">' + (o.counts.pending || 0) + '</td><td class="num">' + o.counts.C + '</td><td class="num">' + o.counts.paywalled + '</td><td class="' + feedCls + '" title="' + esc(o.inactive_reason || o.feeds.map(function (f) { return f.url + (f.ok ? " ok" : " " + (f.last_error || "failing")); }).join("\n")) + '">' + (o.active ? okN + '/' + o.feeds.length : '') + '</td></tr>';
    });
    html += '</table>';
    html += '<h3>Target articles first, then the rest</h3><p class="muted">State placements, unverified relay, and pieces carrying official Chinese sourcing that still await the verification judgement, each with the sentence that triggered it. Independent coverage follows.</p><div id="panel-articles"><p class="muted">Loading</p></div>';
    body.innerHTML = html;
    bindClose();
    renderMini(iso);
    loadArticles(iso);
  }

  function bindClose() { var b = el("panel-close"); if (b) b.addEventListener("click", function () { selectCountry(null); }); }

  function renderMini(iso) {
    var svgm = d3.select("#mini");
    if (svgm.empty()) return;
    var w = 360, h = 88;
    svgm.attr("viewBox", "0 0 " + w + " " + h);
    var pts = days().map(function (d) {
      var e = C.dayEntry(state.months, d);
      var c = (e && e.countries && e.countries[iso]) || C.emptyCounts();
      return {date: new Date(d + "T00:00:00Z"), A: c.A, B: c.B, P: c.pending || 0};
    });
    if (!pts.length) { svgm.append("text").attr("x", 4).attr("y", 14).text("No daily data"); return; }
    var x = d3.scaleUtc().domain(d3.extent(pts, function (p) { return p.date; })).range([4, w - 4]);
    var y = d3.scaleLinear().domain([0, d3.max(pts, function (p) { return Math.max(p.A, p.B, p.P); }) || 1]).nice().range([h - 16, 6]);
    var lineA = d3.line().x(function (p) { return x(p.date); }).y(function (p) { return y(p.A); });
    var lineB = d3.line().x(function (p) { return x(p.date); }).y(function (p) { return y(p.B); });
    var lineP = d3.line().x(function (p) { return x(p.date); }).y(function (p) { return y(p.P); });
    svgm.append("path").attr("class", "p").attr("d", lineP(pts));
    svgm.append("path").attr("class", "a").attr("d", lineA(pts));
    svgm.append("path").attr("class", "b").attr("d", lineB(pts));
    svgm.append("text").attr("x", 4).attr("y", h - 4).text(pts[0].date.toISOString().slice(0, 10));
    svgm.append("text").attr("x", w - 4).attr("y", h - 4).attr("text-anchor", "end").text(pts[pts.length - 1].date.toISOString().slice(0, 10));
    svgm.append("text").attr("x", w - 4).attr("y", 12).attr("text-anchor", "end").text("solid state origin, dashed unverified relay, dotted pending, max " + y.domain()[1] + " per day");
  }

  function loadArticles(iso) {
    var target = el("panel-articles");
    var render = function (arts) {
      if (!target) return;
      if (!arts || !arts.length) { target.innerHTML = '<p class="muted">No classified China coverage yet.</p>'; return; }
      var outletName = {};
      state.outlets.forEach(function (o) { outletName[o.id] = o.name; });
      target.innerHTML = arts.slice(0, 60).map(function (a) {
        var cat = a.human_category || a.category;
        var prov = a.provenance === "human" ? "human-reviewed" : (a.provenance === "rules" ? "rules" : "model only");
        var srcs = (a.sources && a.sources.length) ? '<div class="a-meta">Chinese sources carried: ' + esc(a.sources.join(", ")) + '</div>' : '';
        return '<div class="article"><a class="a-title" href="' + esc(a.url) + '" target="_blank" rel="noopener">' + esc(a.title || a.url) + '</a>' +
          '<span class="a-meta">' + esc(outletName[a.outlet_id] || a.outlet_id) + ', ' + esc(a.date) + ' <span class="badge cat-' + esc(cat) + '">' + esc(C.nameOf(cat)) + (a.human_category && a.human_category !== a.category ? ' (machine said ' + esc(C.nameOf(a.category)) + ')' : '') + '</span><span class="badge prov-' + esc(a.provenance) + '">' + prov + '</span>' + (a.dup_group ? '<span class="badge" title="One of several placements of the same underlying item">syndicated</span>' : '') + '</span>' +
          srcs + (a.evidence_quote ? '<p class="a-quote">' + esc(a.evidence_quote) + '</p>' : '') +
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
  function renderSpark() {
    var s = d3.select("#spark");
    s.selectAll("*").remove();
    var w = 1000, h = 40;
    s.attr("viewBox", "0 0 " + w + " " + h);
    var pts = tlDays.map(function (d, i) {
      var e = C.dayEntry(state.months, d);
      var tot = 0, ceiling = false;
      if (e) { ceiling = !!e.llm_ceiling_hit; Object.keys(e.countries || {}).forEach(function (c) { tot += (e.countries[c].A || 0) + (e.countries[c].B || 0) + (e.countries[c].pending || 0); }); }
      return {i: i, v: tot, ceiling: ceiling};
    });
    if (!pts.length) return;
    var x = d3.scaleLinear().domain([0, Math.max(1, pts.length - 1)]).range([0, w]);
    var y = d3.scaleLinear().domain([0, d3.max(pts, function (p) { return p.v; }) || 1]).range([h - 1, 2]);
    var area = d3.area().x(function (p) { return x(p.i); }).y0(h - 1).y1(function (p) { return y(p.v); }).curve(d3.curveMonotoneX);
    s.append("path").attr("d", area(pts));
    pts.filter(function (p) { return p.ceiling; }).forEach(function (p) {
      s.append("rect").attr("class", "ceiling").attr("x", x(p.i) - 2).attr("y", 0).attr("width", 4).attr("height", h);
    });
  }

  /* ------------------------------------------------------ theme timeline */
  /* The theme counter has its own day and its own Play, separate from the map's timeline.
     Starting either animation stops the other, so the two never run at the same time. */
  function themeWindowLabel() { return windowLabelFor(state.themeEnd, state.themeWindow); }
  function themeAgg() {
    return C.aggregateWindow(state.months, state.themeEnd, state.themeWindow === "all" ? null : Number(state.themeWindow));
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
    var k = m.kappa;
    var rows = [
      ["Outlets monitored", m.outlets_active + " active of " + m.outlets_total + " registered, across " + m.countries_monitored + " countries"],
      ["Population figures", m.population_source || "not recorded"],
      ["Largest outlets denominator", (m.countries_with_audience_ranks && m.countries_with_audience_ranks.length ? m.countries_with_audience_ranks.length + " countries carry audience ranks; " : "no country carries audience ranks yet; ") + "elsewhere every active outlet counts"],
      ["Countries with zero coverage", (m.countries_in_gaps || 0) + " recorded in the gaps file with a reason; every unhatched country not listed there is simply unregistered"],
      ["Articles", m.articles_discovered + " discovered, " + m.articles_gate_relevant + " passed the relevance gate, " + m.articles_classified + " classified"],
      ["Paywall-blocked proportion", m.paywall_share === null || m.paywall_share === undefined ? "not measured" : pct(m.paywall_share) + " of gated articles" + (m.paywall_flagged_countries && m.paywall_flagged_countries.length ? "; flagged: " + m.paywall_flagged_countries.join(", ") : "")],
      ["Current kappa", k && k.bc !== null && k.bc !== undefined ? "all categories " + (k.all === null ? "n/a" : k.all.toFixed(2)) + ", unverified relay versus independent " + k.bc.toFixed(2) + " (n = " + k.n + ", computed " + (k.computed_at || "").slice(0, 10) + ")" : "not yet measured; unverified relay counts are provisional"],
      ["Human review coverage", pct(m.review_coverage) + " of classified articles (" + m.articles_reviewed + ")"],
      ["Ruleset version", m.ruleset_version],
      ["Classifier model", m.llm_model + ", " + m.llm_calls_total + " calls to date, daily ceiling " + m.llm_daily_ceiling + (m.llm_ceiling_days && m.llm_ceiling_days.length ? ", ceiling hit on " + m.llm_ceiling_days.join(", ") : "")],
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
    var rows = C.rankCountries(agg, state.latest, state.metric, state.mode, state.names).map(function (r) {
      r.metric = state.metric; r.window = windowLabel(); r.mode = state.mode; r.relay_provisional = bProvisional();
      r.citation = C.citation(new Date().toISOString().slice(0, 10), CITATION_AUTHOR);
      return r;
    });
    download("tracker_view_" + state.metric + "_" + (state.endDate || "empty") + ".csv", C.toCSV(rows, ["iso", "name", "metric", "window", "mode", "value", "fill", "state_origin", "unverified_relay", "official_sourcing_pending", "target", "relay_provisional", "independent", "china_total", "outlets_active", "population", "all_items_top_outlets", "target_in_top_outlets", "china_in_top_outlets", "warnings", "citation"]));
  }
  function exportDaily() {
    var rows = [];
    var cite = C.citation(new Date().toISOString().slice(0, 10), CITATION_AUTHOR);
    days().forEach(function (d) {
      var e = C.dayEntry(state.months, d);
      if (!e) return;
      Object.keys(e.countries).forEach(function (iso) {
        var c = e.countries[iso], r = (e.reviewed || {})[iso] || {};
        rows.push({date: d, iso: iso, name: state.names[iso] || iso, state_origin: c.A, unverified_relay: c.B, independent: c.C, not_relevant: c.N, state_origin_rules: c.Ar, state_origin_model: c.Al, unverified_relay_rules: c.Br, unverified_relay_model: c.Bl,
                   reviewed: c.rev, human_state_origin: r.A || 0, human_unverified_relay: r.B || 0, human_independent: r.C || 0, unique_items_origin_relay: c.uniqAB, discovered: c.disc, gate_relevant: c.rel, fetched: c.fetched,
                   paywalled: c.paywalled, failed: c.failed, blocked_robots: c.blocked, awaiting_model: c.pending,
                   all_items_top_outlets: c.tdisc, target_in_top_outlets: c.ttarget, china_in_top_outlets: c.tchina, llm_ceiling_hit: e.llm_ceiling_hit, citation: cite});
      });
    });
    download("tracker_daily_counts.csv", C.toCSV(rows, ["date", "iso", "name", "state_origin", "unverified_relay", "independent", "not_relevant", "state_origin_rules", "state_origin_model", "unverified_relay_rules", "unverified_relay_model", "reviewed", "human_state_origin", "human_unverified_relay", "human_independent", "unique_items_origin_relay", "discovered", "gate_relevant", "fetched", "paywalled", "failed", "blocked_robots", "awaiting_model", "all_items_top_outlets", "target_in_top_outlets", "china_in_top_outlets", "llm_ceiling_hit", "citation"]));
  }

  /* -------------------------------------------------------------- controls */
  function bindControls() {
    bindThemes();
    /* Measure and Basis toggles form a three by three grid; the same Basis toggle is repeated above the
       map and above the ranked list and every copy stays in step. The select holds the other denominators. */
    function applyMetric() {
      var other = el("metric").value;
      state.metric = other || C.gridMetric(state.measure, state.basis);
      Array.prototype.forEach.call(document.querySelectorAll("[data-basis-group] button"), function (b) { b.classList.toggle("active", !other && b.getAttribute("data-basis") === state.basis); });
      Array.prototype.forEach.call(el("measure").querySelectorAll("button"), function (b) { b.classList.toggle("active", !other && b.getAttribute("data-measure") === state.measure); });
      renderMap(); renderThemes(); if (state.selected) renderPanel();
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-basis-group] button"), function (b) {
      b.addEventListener("click", function () { state.basis = b.getAttribute("data-basis"); el("metric").value = ""; applyMetric(); });
    });
    Array.prototype.forEach.call(el("measure").querySelectorAll("button"), function (b) {
      b.addEventListener("click", function () { state.measure = b.getAttribute("data-measure"); el("metric").value = ""; applyMetric(); });
    });
    el("metric").addEventListener("change", applyMetric);
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
