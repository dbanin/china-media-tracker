/* China state media tracker front end. Vanilla JavaScript and D3, no build step.
   Reads static JSON from data/. Every view must render with an empty dataset without throwing. */
(function () {
  "use strict";
  var C = window.TrackerCompute;
  var CITATION_AUTHOR = "China State Media Tracker project"; /* Edit to the citation author you want. */

  var state = {
    metric: "count_target", windowDays: 30, mode: "all", endDate: null, selected: null, playing: null,
    meta: null, latest: null, series: [], months: {}, outlets: [], names: {}, numToIso: {}, topo: null,
    articlesCache: {}
  };

  /* ------------------------------------------------------------------ data */
  function getJSON(url) {
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
      getJSON("vendor/iso3166.json").catch(function () { return []; })
    ]).then(function (res) {
      state.meta = res[0]; state.latest = res[1] || {countries: {}, totals: {}};
      state.series = res[2] || []; state.outlets = (res[3] && res[3].outlets) || [];
      state.topo = res[4];
      (res[5] || []).forEach(function (r) { state.names[r["alpha-3"]] = r.name; state.numToIso[String(parseInt(r["country-code"], 10))] = r["alpha-3"]; });
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
  function windowLabel() {
    if (!state.endDate) return "no data";
    if (state.windowDays === "all") return "all time to " + state.endDate;
    if (Number(state.windowDays) === 1) return state.endDate;
    return Number(state.windowDays) + " days ending " + state.endDate;
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
    if (m && m.official_sourcing_pending) parts.push(m.official_sourcing_pending + " articles in " + m.official_sourcing_pending_countries + " countries carry official Chinese sourcing and are waiting for the verification judgement that separates unverified relay from independent journalism. They are counted as targets and listed in each country panel with the sentence that triggered them.");
    var u = m && m.registry_unevenness;
    if (u && u.max_over_median && u.max_over_median >= 3) parts.push("The registry is uneven: the densest country has " + u.max + " active outlets against a median of " + u.median + ", and " + u.countries_with_one_outlet + " countries have a single outlet. Raw counts mostly display that sampling. Share and per-outlet metrics correct for it; count metrics do not.");
    if (parts.length) { dn.textContent = parts.join(" "); dn.classList.remove("hidden"); } else dn.classList.add("hidden");
    el("review-coverage").textContent = m ? pct(m.review_coverage) + " of classified articles" : "n/a";
  }

  /* ------------------------------------------------------------------- map */
  var svg, gCountries, gMarkers, path, projection, colorScale;
  function setupMap() {
    svg = d3.select("#map");
    svg.selectAll("*").remove();
    var defs = svg.append("defs");
    var pat = defs.append("pattern").attr("id", "hatch").attr("patternUnits", "userSpaceOnUse").attr("width", 6).attr("height", 6).attr("patternTransform", "rotate(45)");
    pat.append("rect").attr("width", 6).attr("height", 6).attr("fill", "#f3f1ec");
    pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 6).attr("stroke", "#c9c5bb").attr("stroke-width", 1.4);
    var pat2 = defs.append("pattern").attr("id", "stipple").attr("patternUnits", "userSpaceOnUse").attr("width", 6).attr("height", 6);
    pat2.append("rect").attr("width", 6).attr("height", 6).attr("fill", "#f3f1ec");
    pat2.append("circle").attr("cx", 3).attr("cy", 3).attr("r", 0.9).attr("fill", "#b9b4a8");
    var pat3 = defs.append("pattern").attr("id", "sparse").attr("patternUnits", "userSpaceOnUse").attr("width", 5).attr("height", 5);
    pat3.append("rect").attr("width", 5).attr("height", 5).attr("fill", "#e5e2da");
    pat3.append("rect").attr("x", 2).attr("y", 2).attr("width", 1.2).attr("height", 1.2).attr("fill", "#a9c4dd");
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
    if (cls === "nodata") return "#efede7";
    if (cls === "sparse") return "url(#sparse)";
    if (cls === "zero") return "#e5e2da";
    return colorScale(value);
  }

  function renderMap() {
    if (!gCountries) return;
    var agg = currentAgg();
    var vals = [];
    var perIso = {};
    Object.keys(state.latest.countries || {}).forEach(function (iso) {
      var entry = state.latest.countries[iso];
      var mv = C.metricValue(agg.countries[iso], state.metric, entry.outlets_active, state.mode, agg.reviewed[iso]);
      var cls = C.fillClass(entry, mv);
      perIso[iso] = {entry: entry, mv: mv, cls: cls};
      if (cls === "value") vals.push(mv.value);
    });
    var max = vals.length ? d3.max(vals) : 1;
    var fmt = C.METRICS[state.metric] ? C.METRICS[state.metric].format : "int";
    if (fmt === "pct") max = Math.max(max, 0.05);
    colorScale = d3.scaleSequential(d3.interpolateBlues).domain([0, max]);
    state._perIso = perIso; state._max = max;
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
    gMarkers.selectAll("*").remove();
    gCountries.selectAll("path.country").each(function (f) {
      var p = f.iso && perIso[f.iso];
      if (!p || !(p.entry.warnings || []).length) return;
      var c = path.centroid(f);
      if (isNaN(c[0])) return;
      gMarkers.append("path").attr("class", "warn-marker").attr("d", d3.symbol(d3.symbolTriangle, 40)()).attr("transform", "translate(" + c[0] + "," + c[1] + ")");
    });
    renderLegend(max, fmt);
    renderBars(agg);
  }

  function renderLegend(max, fmt) {
    var ramp = [0.1, 0.3, 0.5, 0.7, 0.9].map(function (t) { return '<span style="background:' + colorScale(t * max) + '"></span>'; }).join("");
    el("legend").innerHTML =
      '<span><span class="swatch" style="background:url(#hatch);background:repeating-linear-gradient(45deg,#f3f1ec,#f3f1ec 3px,#c9c5bb 3px,#c9c5bb 4px)"></span>No monitored outlets</span>' +
      '<span><span class="swatch" style="background:radial-gradient(#b9b4a8 0.9px, #f3f1ec 1px) 0 0/6px 6px"></span>Coverage gap recorded, or all outlets inactive</span>' +
      '<span><span class="swatch" style="background:#efede7"></span>Monitored, no China coverage in window</span>' +
      '<span><span class="swatch" style="background:#e5e2da"></span>Monitored, zero detections</span>' +
      (C.METRICS[state.metric] && C.METRICS[state.metric].format === "pct" ? '<span><span class="swatch" style="background:radial-gradient(#a9c4dd 0.7px, #e5e2da 0.8px) 0 0/5px 5px"></span>Monitored, fewer than ' + C.MIN_SHARE_DENOMINATOR + ' China items, share not shown</span>' : '') +
      '<span>0 <span class="ramp">' + ramp + '</span> ' + C.formatValue(max, fmt) + ' ' + esc(metricLabel().toLowerCase()) + '</span>' +
      '<span><svg width="14" height="12"><path d="M7,1 L13,11 L1,11 Z" fill="#fbfaf7" stroke="#9a5b1b" stroke-width="1.2"/></svg> Warning, see tooltip</span>' +
      '<span class="faint">' + esc(windowLabel()) + (state.mode === "reviewed" ? ", human-reviewed labels only" : "") + '</span>';
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
        if (p.cls === "sparse") html += '<div class="muted">Fewer than ' + C.MIN_SHARE_DENOMINATOR + ' China items in this window, so a share is not shown. Switch to a count metric to see them.</div>';
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

  /* ---------------------------------------------------------------- panel */
  function selectCountry(iso) {
    state.selected = iso;
    renderMap();
    renderPanel();
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
    var mv = C.metricValue(agg.countries[iso], state.metric, entry.outlets_active, state.mode, agg.reviewed[iso]);
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
    html += '<h3>Monitored outlets</h3><table><tr><th>Outlet</th><th class="num">State origin</th><th class="num">Relay</th><th class="num">Independent</th><th class="num">Paywalled</th><th>Feeds</th></tr>';
    state.outlets.filter(function (o) { return o.country === iso; }).sort(function (a, b) { return (b.active - a.active) || a.name.localeCompare(b.name); }).forEach(function (o) {
      var okN = o.feeds.filter(function (f) { return f.ok; }).length;
      var feedCls = !o.active ? "muted" : (okN === o.feeds.length ? "ok" : (okN === 0 ? "fail" : "warn"));
      html += '<tr><td>' + esc(o.name) + (o.active ? '' : ' <span class="badge">inactive</span>') + '</td><td class="num">' + o.counts.A + '</td><td class="num">' + o.counts.B + '</td><td class="num">' + o.counts.C + '</td><td class="num">' + o.counts.paywalled + '</td><td class="' + feedCls + '" title="' + esc(o.inactive_reason || o.feeds.map(function (f) { return f.url + (f.ok ? " ok" : " " + (f.last_error || "failing")); }).join("\n")) + '">' + (o.active ? okN + '/' + o.feeds.length : '') + '</td></tr>';
    });
    html += '</table>';
    html += '<h3>Target articles first, then the rest</h3><p class="muted">State placements, unverified relay, and pieces carrying official Chinese sourcing that still await the verification judgement, each with the sentence that triggered it. Independent coverage follows.</p><div id="panel-articles"><p class="muted">Loading</p></div>';
    body.innerHTML = html;
    bindClose();
    renderMini(iso);
    loadArticles(iso);
  }

  function bindClose() { var b = el("panel-close"); if (b) b.addEventListener("click", function () { state.selected = null; renderMap(); renderPanel(); }); }

  function renderMini(iso) {
    var svgm = d3.select("#mini");
    if (svgm.empty()) return;
    var w = 360, h = 88;
    svgm.attr("viewBox", "0 0 " + w + " " + h);
    var pts = days().map(function (d) {
      var e = C.dayEntry(state.months, d);
      var c = (e && e.countries && e.countries[iso]) || C.emptyCounts();
      return {date: new Date(d + "T00:00:00Z"), A: c.A, B: c.B};
    });
    if (!pts.length) { svgm.append("text").attr("x", 4).attr("y", 14).text("No daily data"); return; }
    var x = d3.scaleUtc().domain(d3.extent(pts, function (p) { return p.date; })).range([4, w - 4]);
    var y = d3.scaleLinear().domain([0, d3.max(pts, function (p) { return Math.max(p.A, p.B); }) || 1]).nice().range([h - 16, 6]);
    var lineA = d3.line().x(function (p) { return x(p.date); }).y(function (p) { return y(p.A); });
    var lineB = d3.line().x(function (p) { return x(p.date); }).y(function (p) { return y(p.B); });
    svgm.append("path").attr("class", "a").attr("d", lineA(pts));
    svgm.append("path").attr("class", "b").attr("d", lineB(pts));
    svgm.append("text").attr("x", 4).attr("y", h - 4).text(pts[0].date.toISOString().slice(0, 10));
    svgm.append("text").attr("x", w - 4).attr("y", h - 4).attr("text-anchor", "end").text(pts[pts.length - 1].date.toISOString().slice(0, 10));
    svgm.append("text").attr("x", w - 4).attr("y", 12).attr("text-anchor", "end").text("solid state origin, dashed unverified relay, max " + y.domain()[1] + " per day");
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
    if (state.articlesCache[iso]) { render(state.articlesCache[iso]); return; }
    getJSON("data/articles/" + iso + ".json").then(function (arts) { state.articlesCache[iso] = arts; render(arts); }).catch(function () { render([]); });
  }

  /* -------------------------------------------------------------- timeline */
  var tlDays = [];
  function setupTimeline() {
    var first = state.meta && state.meta.first_discovered;
    tlDays = days().filter(function (d) { return !first || d >= first; });
    var scrub = el("scrub");
    scrub.max = Math.max(0, tlDays.length - 1);
    scrub.value = scrub.max;
    state.endDate = tlDays.length ? tlDays[tlDays.length - 1] : null;
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

  /* ----------------------------------------------------------- methodology */
  function renderMethod() {
    var m = state.meta;
    var dl = el("method-facts");
    if (!m) { dl.innerHTML = '<dt>Status</dt><dd>No export has run yet.</dd>'; el("citation").textContent = C.citation(new Date().toISOString().slice(0, 10), CITATION_AUTHOR); return; }
    var k = m.kappa;
    var rows = [
      ["Outlets monitored", m.outlets_active + " active of " + m.outlets_total + " registered, across " + m.countries_monitored + " countries"],
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
    download("tracker_view_" + state.metric + "_" + (state.endDate || "empty") + ".csv", C.toCSV(rows, ["iso", "name", "metric", "window", "mode", "value", "fill", "state_origin", "unverified_relay", "official_sourcing_pending", "target", "relay_provisional", "independent", "china_total", "outlets_active", "warnings", "citation"]));
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
                   paywalled: c.paywalled, failed: c.failed, blocked_robots: c.blocked, awaiting_model: c.pending, llm_ceiling_hit: e.llm_ceiling_hit, citation: cite});
      });
    });
    download("tracker_daily_counts.csv", C.toCSV(rows, ["date", "iso", "name", "state_origin", "unverified_relay", "independent", "not_relevant", "state_origin_rules", "state_origin_model", "unverified_relay_rules", "unverified_relay_model", "reviewed", "human_state_origin", "human_unverified_relay", "human_independent", "unique_items_origin_relay", "discovered", "gate_relevant", "fetched", "paywalled", "failed", "blocked_robots", "awaiting_model", "llm_ceiling_hit", "citation"]));
  }

  /* -------------------------------------------------------------- controls */
  function bindControls() {
    el("metric").addEventListener("change", function (e) { state.metric = e.target.value; renderMap(); if (state.selected) renderPanel(); });
    el("window").addEventListener("change", function (e) { state.windowDays = e.target.value === "all" ? "all" : Number(e.target.value); renderMap(); if (state.selected) renderPanel(); });
    Array.prototype.forEach.call(el("mode").querySelectorAll("button"), function (b) {
      b.addEventListener("click", function () {
        state.mode = b.getAttribute("data-mode");
        Array.prototype.forEach.call(el("mode").querySelectorAll("button"), function (x) { x.classList.toggle("active", x === b); });
        renderMap(); if (state.selected) renderPanel();
      });
    });
    el("export-view").addEventListener("click", exportView);
    el("export-daily").addEventListener("click", exportDaily);
  }

  /* ------------------------------------------------------------------ init */
  function init() {
    loadAll().then(function () {
      try { setupMap(); } catch (e) { console.error("map setup failed", e); }
      bindControls();
      setupTimeline();
      renderNotices();
      renderMap();
      renderPanel();
      renderMethod();
    }).catch(function (e) {
      console.error(e);
      el("data-notice").textContent = "Data could not be loaded: " + e.message;
      el("data-notice").classList.remove("hidden");
      try { setupMap(); bindControls(); setupTimeline(); renderMap(); renderPanel(); renderMethod(); } catch (e2) { console.error(e2); }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
