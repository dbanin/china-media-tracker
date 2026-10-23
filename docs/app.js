/* China state media tracker front end. Vanilla JavaScript and D3, no build step.
   Reads static JSON from data/. Every view must render with an empty dataset without throwing. */
(function () {
  "use strict";
  var C = window.TrackerCompute;
  var CITATION_AUTHOR = "Daniel Banin";
  /* Matches the index.html query string on style.css, compute.js and app.js. content.json is not
     linked from the page, so it carries its own copy of the same number here. */
  var ASSET_VERSION = "89";

  var state = {
    metric: "count_a", measure: "a", basis: "count", route: null, windowDays: 30, distribution: "both", themeSort: null, themeEnd: null, themePlaying: null, themeWindow: 30, mode: "all", endDate: null, selected: null, playing: null, zoomIso: null, zoomK: 1, lastCountry: null,
    meta: null, latest: null, series: [], months: {}, outlets: [], names: {}, officialNames: {}, numToIso: {}, topo: null,
    content: null, examples: null, planted: null, plantedShowAll: false, measureData: {},
    articlesCache: {}, scaleCache: {}, loadFailures: [], mapped: null,
    monthsAll: [], monthsFailed: {}, monthsLoading: null, seriesByDate: {}, generations: [], barsShowAll: false
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
    }).then(function (j) { noteGeneration(url, j); return j; });
  }
  /* Which export wrote each data file, when the file says so (generated_at, schema_version), so the page
     can tell a reader when the files it loaded come from different exports (renderStaleNotice). */
  function noteGeneration(url, j) {
    if (String(url).indexOf("data/") !== 0 || !j || typeof j !== "object" || Array.isArray(j)) return;
    if (j.generated_at === undefined && j.schema_version === undefined) return;
    state.generations.push({url: String(url).split("?")[0], at: j.generated_at || null, schema: j.schema_version === undefined ? null : j.schema_version});
  }

  /* ------------------------------------------------------------ daily months
     The daily files grow by one a month and every one of them used to load before the first render. Only
     the months the opening view reads load first: the 31 days ending the last complete day, and the partial
     export day after it. The rest load once, on demand (ensureAllMonths), when an earlier day, Total, Play,
     a country's full time series or the daily CSV needs them. Until then every figure that would read the
     missing days says which days it covers, or takes them from global_series.json, which holds every day. */
  function monthKeys(series) {
    var seen = {}, out = [];
    (series || []).forEach(function (d) { var m = d && d.date ? String(d.date).slice(0, 7) : null; if (m && !seen[m]) { seen[m] = 1; out.push(m); } });
    return out.sort();
  }
  function loadMonth(m, failed) {
    return getJSON("data/daily/" + m + ".json").then(function (j) { state.months[m] = j; })
      .catch(function (e) { console.error(e); state.monthsFailed[m] = true; failed.push("data/daily/" + m + ".json"); });
  }
  function monthsComplete() {
    return state.monthsAll.every(function (m) { return state.months[m] || state.monthsFailed[m]; });
  }
  /* The first day every loaded month covers without a gap back to the latest month, or null once every
     month has been asked for. A window starting on or after it reads only loaded days. */
  function loadedFrom() {
    if (monthsComplete()) return null;
    var from = null;
    for (var i = state.monthsAll.length - 1; i >= 0; i--) {
      var m = state.monthsAll[i];
      if (!(state.months[m] || state.monthsFailed[m])) break;
      from = m + "-01";
    }
    return from || "9999-12-31";
  }
  function windowStart(end, w) { return !end ? null : w === "all" ? "" : C.shiftDate(end, 1 - Math.max(1, Number(w) || 1)); }
  function covered(from) { var lf = loadedFrom(); return lf === null || (from !== null && from !== "" && from >= lf); }
  /* Loads every month not yet loaded, once; the promise is shared by every caller while it runs. When the
     months arrive every cache built from the partial set is dropped and the page is drawn again. */
  function ensureAllMonths() {
    if (monthsComplete()) return null;
    if (state.monthsLoading) return state.monthsLoading;
    var failed = [];
    updateDateLabel(true);
    state.monthsLoading = Promise.all(state.monthsAll.filter(function (m) { return !(state.months[m] || state.monthsFailed[m]); })
      .map(function (m) { return loadMonth(m, failed); })).then(function () {
      state.monthsLoading = null;
      state.loadFailures = (state.loadFailures || []).concat(failed);
      state.scaleCache = {}; themeCapCache = {}; state._allTime = null;
      try { renderLoadFailures(); renderStaleNotice(); } catch (e) { console.error(e); }
      try { rerenderAll(); } catch (e) { console.error("render after loading the daily files failed", e); }
    });
    return state.monthsLoading;
  }
  /* True when the window ending end needs months not loaded yet; it then starts loading them, and the page
     is drawn again when they arrive. */
  function demandMonths(end, w) {
    if (covered(windowStart(end, w))) return false;
    return !!ensureAllMonths();
  }
  function rerenderAll() {
    updateDateLabel();
    renderMap(); renderSpark(); renderThemes(); renderLeaning(); renderPanel(); renderFindings();
  }
  /* Every day the export holds: the dates in global_series.json, which lists all of them, and any loaded
     day it lacks. A month that failed to load is left out, as it was when every month loaded up front. */
  function allDays() {
    var seen = {}, out = [];
    state.series.forEach(function (d) { if (d && d.date && !seen[d.date] && !state.monthsFailed[String(d.date).slice(0, 7)]) { seen[d.date] = 1; out.push(d.date); } });
    days().forEach(function (d) { if (!seen[d]) { seen[d] = 1; out.push(d); } });
    return out.sort();
  }

  /* Every fallback here is recorded. A file that fails to arrive renders as zero everywhere it is read,
     and the one thing the reader must not be shown is an empty dataset that looks like a measured one:
     the list of what is missing goes into the notice at the top of the page. */
  function loadAll() {
    var failed = [];
    function soft(url, fallback) {
      return getJSON(url).catch(function (e) { console.error(e); failed.push(url); return fallback; });
    }
    /* content.json and examples.json hold reader-facing prose and rotating examples, never a figure
       on the map or in a count: a missing one hides the sections built from it, and does not belong
       in the "every figure reads as zero" notice that a missing data/*.json file gets. */
    function quiet(url, fallback) {
      return getJSON(url).catch(function (e) { console.error(e); return fallback; });
    }
    return Promise.all([
      soft("data/meta.json", null),
      soft("data/latest.json", {countries: {}, totals: {}}),
      soft("data/global_series.json", []),
      soft("data/outlets.json", {outlets: []}),
      soft("vendor/countries-110m.json", null),
      soft("vendor/iso3166.json", []),
      soft("country-names.json", {names: {}}),
      quiet("content.json?v=" + ASSET_VERSION, null),
      quiet("data/examples.json", null),
      quiet("data/planted.json", null),
      quiet("data/archive_checks.json", null)
    ]).then(function (res) {
      state.meta = res[0]; state.latest = res[1] || {countries: {}, totals: {}};
      state.series = res[2] || []; state.outlets = (res[3] && res[3].outlets) || [];
      state.topo = res[4];
      (res[5] || []).forEach(function (r) { state.names[r["alpha-3"]] = r.name; state.officialNames[r["alpha-3"]] = r.name; state.numToIso[String(parseInt(r["country-code"], 10))] = r["alpha-3"]; });
      /* ISO short names are often formal or inverted ("Korea, Republic of"); show the common English name. */
      var common = (res[6] && res[6].names) || {};
      Object.keys(common).forEach(function (iso) { state.names[iso] = common[iso]; });
      state.content = res[7] || null;
      state.examples = res[8] || null;
      state.planted = res[9] || null;
      state.archiveChecks = res[10] || null;
      state.series.forEach(function (d) { if (d && d.date) state.seriesByDate[d.date] = d; });
      state.monthsAll = monthKeys(state.series);
      /* The opening view: the 31 days ending the last complete day, worked out from the series alone. */
      var openingDay = lastCompleteDay(), firstMonth = openingDay ? C.shiftDate(openingDay, -30).slice(0, 7) : null;
      var monthsWanted = {};
      state.monthsAll.forEach(function (m) { if (!firstMonth || m >= firstMonth) monthsWanted[m] = true; });
      /* The research measures' side files (meta.measures[id].file), loaded quietly: a missing one shows
         that measure's empty state and is not a figure on the map. */
      var measureLoads = measureFiles().map(function (mf) {
        return quiet("data/" + mf.file, null).then(function (j) { state.measureData[mf.id] = j; });
      });
      return Promise.all(Object.keys(monthsWanted).map(function (m) { return loadMonth(m, failed); }).concat(measureLoads));
    }).then(function () { state.loadFailures = failed; });
  }

  /* What did not arrive, said plainly. Without this the site renders a full interface over nothing. */
  function renderLoadFailures() {
    var n = el("data-notice"), failed = state.loadFailures || [];
    if (!n || !failed.length) return;
    n.textContent = plural(failed.length, "data file") + " could not be loaded, so every figure drawn from "
      + (failed.length === 1 ? "it reads" : "them reads") + " as zero and this page is incomplete: " + failed.join(", ")
      + ". Reload the page, or check whether the last export ran.";
    n.classList.remove("hidden");
  }

  /* A page whose data is old, or assembled from different exports, says so above everything else. Built
     from meta.generated_at alone (meta.stale_after_hours, when an export states it, replaces the 24 hours),
     and from the generated_at and schema_version each data file carries, where it carries them. */
  var STALE_HOURS = 24, GENERATION_GAP_HOURS = 2;
  function utcText(iso) { return String(iso || "").slice(0, 16).replace("T", " ") + " UTC"; }
  function staleLines(meta, gens, now) {
    var lines = [], at = meta && meta.generated_at ? Date.parse(meta.generated_at) : NaN;
    var limit = meta && typeof meta.stale_after_hours === "number" && meta.stale_after_hours > 0 ? meta.stale_after_hours : STALE_HOURS;
    if (!isNaN(at)) {
      var hours = (now - at) / 36e5;
      if (hours > limit) lines.push("This data was exported " + (hours < 48 ? Math.floor(hours) + " hours" : Math.floor(hours / 24) + " days") + " ago, at " + utcText(meta.generated_at) +
        ". The export normally runs every day, so the most recent days are missing and every figure may be out of date.");
    }
    var schemas = {}, far = [];
    (gens || []).forEach(function (g) {
      if (g.schema !== null && g.schema !== undefined) (schemas[String(g.schema)] = schemas[String(g.schema)] || []).push(g.url);
      var t = g.at ? Date.parse(g.at) : NaN;
      if (!isNaN(t) && !isNaN(at) && Math.abs(t - at) > GENERATION_GAP_HOURS * 36e5) far.push(g.url.replace(/^data\//, "") + " (" + utcText(g.at) + ")");
    });
    var versions = Object.keys(schemas).sort();
    if (versions.length > 1 || far.length) lines.push("The data files on this page come from different exports" +
      (far.length ? ": " + far.join(", ") + (isNaN(at) ? "" : " against " + utcText(meta.generated_at) + " for the rest") : "") +
      (versions.length > 1 ? (far.length ? "; " : ": ") + "schema versions " + versions.join(" and ") : "") +
      ". Figures that combine them can disagree. Reload the page; if this stays, the last export was published only in part.");
    return lines;
  }
  function renderStaleNotice() {
    var lines = staleLines(state.meta, state.generations, Date.now());
    var n = el("stale-notice"), dn = el("data-notice");
    if (!n && !lines.length) return;
    if (!n) {
      if (!dn || !dn.parentNode) return;
      n = document.createElement("div");
      n.id = "stale-notice"; n.className = "notice"; n.setAttribute("role", "status");
      dn.parentNode.insertBefore(n, dn.nextSibling);
    }
    n.textContent = lines.join(" ");
    n.classList.toggle("hidden", !lines.length);
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

  /* force-list hides #map-wrap, which is where this message lives, so the body also takes map-failed,
     whose rule keeps the wrap visible with the map and legend inside it hidden instead. */
  function mapFallback(text) {
    var wrap = el("map-wrap");
    if (!wrap) return;
    var d = el("map-fallback");
    if (!d) {
      d = document.createElement("div");
      d.id = "map-fallback";
      wrap.insertBefore(d, wrap.firstChild);
    }
    d.textContent = text + " The ranked list below still works, and carries every monitored country.";
    document.body.classList.add("map-failed");
    document.body.classList.add("force-list");
    syncViewButtons("list");
  }
  function syncViewButtons(v) {
    var view = el("view");
    if (!view) return;
    Array.prototype.forEach.call(view.querySelectorAll("button"), function (b) { b.classList.toggle("active", b.getAttribute("data-view") === v); });
    syncPressed();
  }
  /* At 720 pixels or less the ranked list is the default view (style.css), so unless the reader chose a
     view the toggle must say List there, and Map above it. */
  var SMALL_SCREEN = "(max-width: 720px)";
  function smallScreen() { return !!(window.matchMedia && window.matchMedia(SMALL_SCREEN).matches); }
  function syncDefaultView() {
    var b = document.body;
    if (b.classList.contains("force-map") || b.classList.contains("force-list")) return;
    syncViewButtons(smallScreen() ? "list" : "map");
  }

  /* --------------------------------------------------------------- helpers */
  function el(id) { return document.getElementById(id); }
  function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  /* Titles and quotes reach us from feeds with their entities already encoded, so escaping them again
     prints &amp; and &#039; at the reader. Decode exactly once, in a detached textarea whose content is
     text and never runs, and escape what comes out: what reaches the page is still escaped. */
  var decoder = null;
  function decodeEntities(s) {
    var t = String(s === null || s === undefined ? "" : s);
    if (t.indexOf("&") === -1) return t;
    if (!decoder) decoder = document.createElement("textarea");
    decoder.innerHTML = t;
    return decoder.value;
  }
  function escText(s) { return esc(decodeEntities(s)); }
  /* An article link is http or https or it is not a link. Control characters and whitespace are stripped
     before the test, so a javascript: URL cannot hide inside one. */
  function safeUrl(u) {
    var t = decodeEntities(u);
    return /^https?:\/\//i.test(t.replace(/[\u0000-\u0020\u007f-\u00a0]/g, "")) ? t.trim() : "";
  }
  function pct(v) { return v === null || v === undefined ? "n/a" : (v * 100).toFixed(1) + "%"; }
  function days() { return C.listDays(state.months); }
  function windowArg(w) { return w === "all" ? null : Number(w); }
  function currentAgg() { return C.aggregateWindow(state.months, state.endDate, windowArg(state.windowDays), state.distribution); }
  /* The Distribution toggle: both, country (the country outlets alone) or wires (the worldwide wires alone). */
  function wiresOnly() { return state.distribution === "wires"; }
  function countryOnly() { return state.distribution === "country"; }
  /* The ranked rows for the view shown, under the Distribution toggle. */
  function rankedRows(agg) {
    var rows = C.rankCountries(agg || currentAgg(), state.latest, state.metric, state.mode, state.names, ctxFor, state.distribution);
    if (!metricDef().allItems) return rows;
    /* A share of monitored output of exactly zero where the country has some of the measure is not an
       observed zero (outputShareGap): n/a, with its reason, ranked with the other rows that have no value. */
    var changed = false;
    rows.forEach(function (r) {
      var m = metricDef().measure, own = m === "b" ? r.unverified_relay : m === "china" ? r.china_total : r.state_origin;
      var gap = r.value === 0 ? outputShareGap(Number(own) || 0, (state.latest.countries || {})[r.iso]) : null;
      if (gap) { r.value = null; r.fill = "sparse"; r.note = gap + (r.note ? " " + r.note : ""); changed = true; }
    });
    if (changed) rows.sort(function (x, y) {
      var xv = x.value === null ? -1 : x.value, yv = y.value === null ? -1 : y.value;
      return yv - xv || x.name.localeCompare(y.name);
    });
    return rows;
  }
  /* % of all its news divides a measure's articles in a country's counted outlets (its audience ranked
     outlets where the registry ranks any, otherwise every active outlet) by everything those outlets
     published. When they carried none of the country's articles in the measure but its other outlets did,
     the share reads 0 although the country has some: not an observed zero, so it is shown as n/a with this
     reason. own is the country's count in the measure; null when there is no gap. */
  function outputShareGap(own, entry) {
    if (!own || !entry) return null;
    var who = entry.top_outlets_ranked ? (entry.top_outlets ? "The " + entry.top_outlets + " audience ranked outlets" : "The audience ranked outlets") : "The counted outlets";
    return who + " this share divides by carried none of the country's " + plural(own, measureNounFor(metricDef().measure).replace(/s$/, ""), measureNounFor(metricDef().measure)) +
      " in this window, which ran in its other monitored outlets, so the share is not shown.";
  }
  /* metricValue with the share of monitored output gap (outputShareGap) read as a value not shown. */
  function withOutputGap(mv, entry) {
    if (!metricDef().allItems || !mv || mv.value !== 0) return mv;
    var m = metricDef().measure, gap = outputShareGap(m === "b" ? mv.b : m === "china" ? mv.chinaTotal : mv.aAll, entry);
    return gap ? Object.assign({}, mv, {value: null, sparse: true, note: gap}) : mv;
  }
  /* The value as the ranked list, the panel and the legend print it: a share of monitored output per
     1,000 published items, as the key reads it, since almost every such share is a fraction of one percent. */
  function valueText(v, metric) {
    var def = C.METRICS[metric] || {};
    if (def.allItems) return v === null || v === undefined || isNaN(v) ? "n/a" : (v === 0 ? "0" : C.formatKeyNumber(v, C.keyUnit(metric), 2)) + " per 1,000";
    return C.formatValue(v, def.format || "int");
  }
  /* What a share of monitored output divides by, named for the heading of the ranked list and the panel. */
  function outputShareLabel() {
    var noun = measureNounFor(metricDef().measure);
    return noun.charAt(0).toUpperCase() + noun.slice(1) + " per 1,000 items published by each country's counted outlets (its audience ranked outlets where the registry ranks any, otherwise every active outlet)";
  }
  function metricDef() { return C.METRICS[state.metric] || {}; }
  function metricLabel() { return metricDef().label || state.metric; }
  function windowLabel() { return windowLabelFor(state.endDate, state.windowDays); }
  function windowLabelFor(date, days) {
    if (!date) return "no data";
    if (days === "all") return "total to " + date;
    if (Number(days) === 1) return date;
    return Number(days) + " days ending " + date;
  }
  function plural(n, word, words) { return n + " " + (n === 1 ? word : (words || word + "s")); }
  /* The estimated items missed between feed polls, worded with the day the estimate starts. Only polls
     made by the current missed-items estimator carry it (mpolls of polls; meta.feed_saturation says
     since when): the earlier estimates inflated batch publishers and cannot be made again. */
  function missedSince() {
    var fs = (state.meta && state.meta.feed_saturation) || {};
    return fs.estimator ? (fs.missed_estimate_since || fs.estimator) : null;
  }
  function missedPhrase(miss, mpolls, polls) {
    var since = missedSince();
    if (!since) return "an estimated " + (miss || 0) + " were missed";
    if (!mpolls) return "the number missed is estimated only for polls since " + since + ", and none of these are";
    return "an estimated " + (miss || 0) + " were missed" + (mpolls < polls ? " over " + mpolls + " of these polls" : "") + " (estimated since " + since + ")";
  }
  function intComma(n) { return n === null || n === undefined ? "n/a" : Number(n).toLocaleString("en-US"); }

  /* --------------------------------------------------------------- content
     content.json carries every piece of reader-facing prose this page shows: the definitions in
     Start here, the FAQ, the map and panel captions, and the tokens the findings box fills in. Every
     function here reads state.content and degrades to nothing, never to an error, when it is absent
     (a page built before content.json existed) or missing the one key asked for. */
  function caption(id) { return (state.content && state.content.captions && state.content.captions[id]) || null; }
  /* Internal category codes to the ids content.categories uses, so the panel can show the plainer
     label content.json proposes wherever one exists, without renaming the codes used everywhere else
     (the CSV export, the classifier, the codebase). */
  var CODE_TO_CATEGORY_ID = {A: "state_origin", B: "unchecked_state_sourcing", pending: "official_chinese_sourcing_verification_pending",
    C: "independent_journalism", N: "not_relevant", paywalled: "paywalled_unread_and_uncounted"};
  function clearerLabel(code, fallback) {
    var cats = (state.content && state.content.categories) || {};
    var id = CODE_TO_CATEGORY_ID[code];
    var c = id && cats[id];
    return (c && (c.proposed_clearer_label || c.label)) || fallback;
  }
  function contentRouteName(id) { return C.contentRouteName(state.content && state.content.routes, id) || routeLabel({kind: "route", id: id}); }
  /* A URL inside a content.json string (a country note's "Source: ..." tail, or the CTA's GitHub
     link) rendered as a real anchor. Everything else is plain escaped text: this never trusts the
     string enough to treat it as markup beyond the links it finds itself. */
  var URL_IN_TEXT = /https?:\/\/[^\s,)]+/g;
  function linkifyHtml(text) {
    var s = String(text === null || text === undefined ? "" : text);
    var out = "", last = 0, m;
    URL_IN_TEXT.lastIndex = 0;
    while ((m = URL_IN_TEXT.exec(s))) {
      out += escText(s.slice(last, m.index));
      var u = safeUrl(m[0]);
      out += u ? '<a href="' + esc(u) + '" target="_blank" rel="noopener">' + escText(m[0]) + '</a>' : escText(m[0]);
      last = m.index + m[0].length;
    }
    out += escText(s.slice(last));
    return out;
  }
  /* The short form of a country note for the map tooltip: the descriptive sentence(s) without the
     trailing "Source(s): url" tail, and never a link, as the tooltip carries no links anywhere. */
  function shortNoteText(text) { return String(text || "").replace(/\s*Sources?:\s*https?:\/\/.*$/i, "").trim(); }

  /* Unchecked state sourcing is published only when the verification stage has run and the hand coding study has
     settled it, and never for a country whose outlets publish in a language withheld on its own kappa.
     Data files from before those fields existed fall back to the older flags. */
  function relayFor(entry) {
    var m = state.meta || {};
    var measured = m.relay_measured !== undefined ? !!m.relay_measured : !!m.llm_calls_total;
    var publishable = m.relay_publishable !== undefined ? !!m.relay_publishable : !!(m.b_counts_settled && measured);
    var langs = m.relay_withheld_languages || [];
    if (publishable && entry && (entry.languages || []).some(function (l) { return langs.indexOf(l) !== -1; })) publishable = false;
    /* Provisional: the model has labelled articles and no hand coding has checked them yet. The counts are
       shown, marked. A model stability check never removes the marking. It is never the state after a failed
       study; that one is withheld. */
    var provisional = measured && !publishable && m.relay_provisional === true;
    return {measured: measured, publishable: publishable, provisional: provisional, visible: publishable || provisional,
            status: !measured ? "not_measured" : publishable ? "published" : provisional ? "provisional" : "withheld"};
  }
  /* Only hand coding takes the provisional marking away (since 12 October 2026). The export states why the
     counts are provisional; a data file from before it did gets the rule built from the study block. */
  /* A study that failed under the rule of 2026-10 can carry no kappa at all, only the reason it failed
     (failed_reason); its words are then used, never a kappa it did not compute. */
  function studyFailedReason() {
    var hs = (state.meta && state.meta.relay_human_study) || {};
    return hs.state === "failed" && (hs.kappa_bc === null || hs.kappa_bc === undefined) && hs.failed_reason ? String(hs.failed_reason) : null;
  }
  /* The two states of a study whose labels predate the current instructions, as one sentence each. */
  var STUDY_STATE_TEXT = {
    sample_superseded: "the first hand coding sample was set aside because its labels predate the current instructions; a new sample is drawn once labels are re-judged",
    stale: "the last study checked labels made under earlier instructions"
  };
  function humanStudyText() {
    var m = state.meta || {}, hs = m.relay_human_study || {};
    if (m.relay_provisional_reason) return m.relay_provisional_reason;
    var lead = STUDY_STATE_TEXT[hs.state] ? STUDY_STATE_TEXT[hs.state].charAt(0).toUpperCase() + STUDY_STATE_TEXT[hs.state].slice(1) + ". " : "";
    return lead + "The counts stay provisional until a hand coding study of a fixed random sample, about " + (hs.per_language || 200) +
      " articles in each language with at least " + (hs.min_labelled || 300) + " model labelled articles, stratified by language and, within each language, by the model\u2019s current category, puts Cohen\u2019s kappa with the model on unchecked state sourcing versus independent journalism at or above " +
      (m.kappa_warning_threshold || 0.7) + ", overall and in each language.";
  }
  function humanStudyShort() {
    var hs = (state.meta && state.meta.relay_human_study) || {};
    if (hs.state === "passed") return "hand coding passed, kappa " + (hs.kappa_bc === null || hs.kappa_bc === undefined ? "n/a" : hs.kappa_bc.toFixed(2));
    if (studyFailedReason()) return studyFailedReason();
    if (STUDY_STATE_TEXT[hs.state]) return STUDY_STATE_TEXT[hs.state];
    if (hs.state === "failed") return "hand coding put kappa below " + ((state.meta && state.meta.kappa_warning_threshold) || 0.7);
    if (hs.state === "incomplete") return "hand coding under way, " + (hs.coded || 0) + " of " + (hs.sampled || 0) + " sampled articles coded";
    if (hs.state === "inconclusive") return "the hand coding sample could not produce a kappa";
    if (hs.state === "sample_drawn") return "hand coding of a fixed sample is under way";
    return "awaiting hand coding of a fixed sample";
  }
  /* The model's agreement with itself, or with a second model, on a re-judged sample. Stability, not
     correctness: shown, never a basis for publication, and a figure below the threshold withholds. */
  function stabilityUnstable() {
    var rs = state.meta && state.meta.relay_reliability, thr = (state.meta && state.meta.kappa_warning_threshold) || 0.7;
    return !!(rs && rs.kappa_bc !== null && rs.kappa_bc !== undefined && rs.kappa_bc < thr);
  }
  function stabilityText() {
    var m = state.meta || {}, rs = m.relay_reliability;
    if (!rs) return C.stabilityStatusText(state.meta);
    var same = rs.method === "same_model_rerun";
    return "Stability check: " + (same ? "the same model, " + (rs.model_a || "the model") + ", with the same prompt, re-judged " : "a second model, " + (rs.model_b || "another model") + ", re-judged ") +
      (rs.n || 0) + " articles and agreed " + (same ? "with its own earlier labels" : "with " + (rs.model_a || "the first")) + " at kappa " +
      (rs.kappa_bc === null || rs.kappa_bc === undefined ? "n/a" : rs.kappa_bc.toFixed(2)) + " on unchecked sourcing versus independent. That measures whether the judgement is stable, not whether it is correct, so it never takes the provisional marking away; a figure below " +
      (m.kappa_warning_threshold || 0.7) + " withholds the counts.";
  }
  function relayBasis() { return (state.meta && state.meta.relay_basis) || null; }
  /* A published relay count says what it rests on. "model agreement" survives only for data files from before
     12 October 2026, when a model study could publish; it was never a synonym for correct. */
  function relayText(relay, b) {
    if (!relay.measured) return "not yet measured";
    if (relay.provisional) return String(b) + " (provisional)";
    if (!relay.publishable) return "withheld";
    return String(b) + (relayBasis() === "model_vs_model" ? " (model agreement)" : "");
  }

  function routeList() { return C.withRouteFallbacks(state.meta && state.meta.routes).map(function (r) { return {kind: "route", id: r.id, label: r.label}; }); }
  function arrivalList() { return ((state.meta && state.meta.arrivals) || []).map(function (r) { return {kind: "arrival", id: r.id, label: r.label}; }); }
  function routeLabel(f) {
    var hit = routeList().concat(arrivalList()).filter(function (r) { return f && r.kind === f.kind && r.id === f.id; })[0];
    return hit ? hit.label : (f ? (f.kind === "route" && C.ROUTE_FALLBACK_LABELS[f.id]) || f.id : "");
  }
  function routeActive() { return !!(state.route && metricDef().measure === "a"); }
  /* Undetermined items (meta.undetermined, code U in the data files): who wrote or commissioned them could
     not be settled, so, as in the original study, they are excluded from every count and denominator and
     never rejoin state origin. They are shown only as their own row and line, and drawn by the route
     filter's undetermined option as a layer of its own, marked not counted (decision D1 as revised). */
  function undeterminedRouteActive() { return routeActive() && state.route.kind === "route" && state.route.id === "unattributed"; }
  var UNDETERMINED_ROW = "Undetermined: who wrote or commissioned it could not be settled, excluded from the counts";
  /* A label placed mid-sentence: lower case first letter, never on China, Chinese or a country name. */
  function lowerLabel(text) { return C.lowerFirst(text, Object.keys(state.names || {}).map(function (k) { return state.names[k]; })); }

  /* Everything metricValue needs beyond the counts, for one country. */
  function ctxFor(iso, entry, agg) {
    return {topOutlets: entry.top_outlets, relay: relayFor(entry),
            noRelayOutput: ((state.meta && state.meta.schema_version) || 0) < 6,
            routeCount: routeActive() ? C.routeCount(agg, iso, state.route) : null,
            routeUndetermined: undeterminedRouteActive(),
            worldwide: iso === C.WORLDWIDE_ISO};
  }

  /* --------------------------------------------------------------- notices */
  function renderNotices() {
    var m = state.meta;
    var kparts = [], short = [];
    if (!m) { kparts.push("No data has been exported yet. The interface is rendering an empty dataset."); short.push("no data exported yet"); }
    else {
      var relay = relayFor(null), k = m.kappa;
      if (!relay.measured) short.push("unchecked state sourcing not yet measured");
      else if (relay.provisional) short.push("unchecked state sourcing is provisional, " + humanStudyShort());
      else if (!relay.publishable) short.push(studyFailedReason() ? "unchecked state sourcing withheld: " + studyFailedReason() : k && k.bc !== null && k.bc !== undefined ? "unchecked state sourcing withheld, hand coding kappa " + k.bc.toFixed(2) + " below " + m.kappa_warning_threshold
        : stabilityUnstable() ? "unchecked state sourcing withheld, stability check below " + m.kappa_warning_threshold : "unchecked state sourcing withheld pending hand coding");
      if (!relay.measured) kparts.push("Unchecked state sourcing has not been measured: the verification stage that separates it from independent journalism has not run. It is shown as not yet measured, never as zero, and the map defaults to state origin only.");
      else if (relay.provisional) kparts.push("Unchecked state sourcing counts are provisional. They are one model\u2019s judgement of whether an article passes on official Chinese sourcing unchecked, and no hand coding has checked that judgement yet. Every figure that contains them is marked provisional here and in the CSV export. " + humanStudyText() + " If the study falls below that threshold the counts are withheld. " + stabilityText());
      else if (!relay.publishable) kparts.push(studyFailedReason() ? "Unchecked state sourcing counts are withheld: " + studyFailedReason().replace(/\.?$/, ".")
        : k && k.bc !== null && k.bc !== undefined
        ? "Unchecked state sourcing counts are withheld. Cohen's kappa between hand coding and the model on the unchecked sourcing versus independent journalism judgement is " + k.bc.toFixed(2) + " (n = " + k.n_bc + "), below the " + m.kappa_warning_threshold + " threshold, so the counts are not published."
        : stabilityUnstable() ? "Unchecked state sourcing counts are withheld. " + stabilityText()
        : "Unchecked state sourcing counts are withheld until hand coding checks the unchecked sourcing versus independent journalism judgement. " + humanStudyText());
      else if (relayBasis() === "human_coding") {
        var hk = (k && k.bc !== null && k.bc !== undefined) ? k.bc.toFixed(2) : "n/a";
        kparts.push("Unchecked state sourcing counts are published: the owner and two research assistants hand coded a fixed random sample, stratified by language and, within each language, by the model\u2019s category, and Cohen\u2019s kappa between their codes and the model on unchecked sourcing versus independent journalism is " + hk + (k && k.n_bc ? " (n = " + k.n_bc + ")" : "") + ", at or above the " + m.kappa_warning_threshold + " threshold. " + stabilityText());
      }
      var wl = m.relay_withheld_languages || [];
      if (wl.length) { kparts.push("Unchecked state sourcing counts are withheld for outlets publishing in " + wl.join(", ") + ", where agreement in that language is below the threshold."); short.push("unchecked sourcing withheld for " + wl.join(", ")); }
      if (m.official_sourcing_pending) short.push(m.official_sourcing_pending + " articles await the model");
      if (m.official_sourcing_pending) kparts.push(m.official_sourcing_pending + " articles in " + m.official_sourcing_pending_countries + " countries are flagged for the model (official Chinese sourcing, a weak signature, or a near copy of a credited state item) and wait for its judgement. They are not a finding: no measure on the map counts them, and the country panel lists them as their own row.");
    }
    var parts = [];
    if (m) {
      var flagged = m.paywall_flagged_countries || [];
      if (flagged.length) short.push("paywalls limit " + plural(flagged.length, "country", "countries"));
      if (flagged.length) parts.push("Paywalls removed more than " + Math.round((m.paywall_flag_share || 0.33) * 100) + " percent of retrieved articles in " + flagged.map(function (c) { return state.names[c] || c; }).join(", ") + ". Paywalled articles cannot be read, so they are missing from every count. They stay in the share of monitored output denominator, which counts every item the outlets published, so that share is a lower bound in those countries, and their counts are not comparable to the rest.");
      if (m.countries_monitored && m.countries_monitored < 30) short.push("only " + m.countries_monitored + " countries monitored");
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
      /* Relay only outlets and the ones the hosted runner also polls are stated apart: a silent relay
         leaves only the first uncollected (C.relayCollectorNotes). */
      var rcNotes = C.relayCollectorNotes(m.relay_collector, countryName);
      Array.prototype.push.apply(short, rcNotes.short);
      Array.prototype.push.apply(parts, rcNotes.parts);
      /* Ruleset mix, reclassification progress and relevance gate version changes are operational
         detail rather than something that changes how today's map should be read: they live in the
         Technical notes block inside the methodology section (renderTechnicalNotes), not here. */
      var fs = m.feed_saturation;
      if (fs && fs.polls && fs.saturated) parts.push(fs.saturated + " of " + fs.polls + " feed polls returned a full window with nothing seen before, so items were lost between polls: " + missedPhrase(fs.missed_estimate, fs.estimator ? fs.estimated_polls : fs.polls, fs.polls) + ". Countries where this passes " + Math.round((fs.warning_share || 0.25) * 100) + " percent of polls carry a warning.");
      if ((m.llm_sampling_days || []).length) parts.push("On " + plural(m.llm_sampling_days.length, "day") + " the model call ceiling bound, and the articles sent were a random draw with the same fraction in every country.");
      /* One line per provisional or withheld research measure, in the full notes only, never the short strip. */
      if (MS) Array.prototype.push.apply(parts, MS.noticeLines(m.measures));
    }
    /* Every note in full under the methodology heading; one muted line up here with only the notes that
       change how today's map should be read, and a link down to the rest. */
    var full = kparts.concat(parts);
    var list = el("method-notes");
    list.innerHTML = "";
    full.forEach(function (t) { var li = document.createElement("li"); li.textContent = t; list.appendChild(li); });
    el("method-notes-wrap").hidden = !full.length;
    var strip = el("notes-strip");
    strip.innerHTML = "";
    strip.title = caption("notes_strip") || "";
    if (short.length) {
      var nk = document.createElement("span"); nk.className = "nk"; nk.textContent = "Notes"; strip.appendChild(nk);
      strip.appendChild(document.createTextNode(short.join("; ") + ". "));
      var link = document.createElement("a"); link.href = "#method-notes-title";
      link.textContent = full.length > short.length ? "All " + full.length + " notes above" : "Read in full above";
      strip.appendChild(link);
    }
    strip.hidden = !short.length;
    /* The human-reviewed switch is hidden until a review has actually been recorded; an empty
       reviewed mode would only blank the map. */
    var reviewed = !!(m && m.articles_reviewed);
    el("mode-control").classList.toggle("hidden", !reviewed);
    el("review-coverage-control").classList.toggle("hidden", !reviewed);
    el("review-coverage").textContent = m ? pct(m.review_coverage) + " of classified articles" + (typeof m.articles_reviewed === "number" && typeof m.articles_classified === "number" ? " (" + intComma(m.articles_reviewed) + " of " + intComma(m.articles_classified) + ")" : "") : "n/a";
  }

  /* -------------------------------------------------------- reader content
     The sections built entirely from content.json and data/examples.json: the "What we found" box,
     the collapsible Start here explainer with its route cards, the rotating worked examples, and the
     FAQ. Every one of these hides itself, rather than rendering empty, when its data has not arrived. */
  function ownershipLabels() {
    var out = {};
    (LEAN_GROUPS.ownership || []).forEach(function (g) { out[g[0]] = g[1]; });
    return out;
  }
  function renderFindings() {
    try { renderTop10(); } catch (e) { console.error("top 10 countries failed", e); }
    var box = el("findings-box");
    if (!box) return;
    var content = state.content, f = state.meta && state.meta.findings;
    if (!content || !f || !content.findings_template) { box.hidden = true; return; }
    /* The top country sentence counts the 30 complete days the map opens on, like the card, and takes its
       share of the world's total, wires included, whatever the toggle says, as its words do. */
    var tc = windowTopCountry(f, "both");
    var sentences = C.fillFindingsTemplate(content.findings_template, Object.assign({}, f, {all_time: allTimeFindings(f).at, since: f.since ? allTimeSpan(f) : f.since, top_country: tc}), state.names, ownershipLabels());
    if (!sentences) { box.hidden = true; return; }
    /* "most" only above half of every placement, signed or not, and a tie as a tie (C.findingsRouteSentence). */
    var routeSentence = tc ? C.findingsRouteSentence(tc.top_route_detail, countryName(tc.iso), function (id) { return lowerLabel(contentRouteName(id)); }, tc.state_origin) : "";
    var at = findingsForMode(f);
    var html = '<div class="fb-kicker">What we found</div><p>' + sentences.map(esc).join(" ") +
      (routeSentence ? " " + esc(routeSentence) : "") + '</p>' +
      '<p class="fb-meta">Pilot data since ' + esc(f.since ? allTimeSpan(f) : "n/a") + '. ' + intComma(at.state_origin) + ' state origin' +
      (at.unchecked !== null && at.unchecked !== undefined ? ', ' + intComma(at.unchecked) + ' unchecked state sourcing' + (relayFor(null).provisional ? ' (provisional)' : '') : '') +
      ' and ' + intComma(at.independent) + ' independent journalism articles, out of ' + intComma(at.china_total) + ' China-related articles counted so far' +
      (wiresOnly() ? ', on the worldwide wires alone' : countryOnly() ? ', in the country outlets alone' : '') + '.</p>';
    var udLine = undeterminedFindingsLine(f, at);
    if (udLine) html += '<p class="fb-meta" id="fb-undetermined">' + esc(udLine) + '</p>';
    if (MS) html += MS.findingsHtml(measureBlocks(), state.measureData, measureApi);
    box.innerHTML = html;
    box.hidden = false;
    /* The sentences count both distributions whatever the toggle says, so under Country outlets or
       Worldwide wires they disagree with the cards above them; the kicker says which they count. */
    var fbKick = box.querySelector(".fb-kicker");
    if (fbKick && state.distribution !== "both") { fbKick.textContent += " (both distributions)"; fbKick.title = "These sentences count the country outlets and the worldwide wires together, whatever the Distribution toggle shows; the cards above follow the toggle."; }
    renderKpis(f);
    if (tc && tc.iso) {
      var see = document.createElement("a");
      see.href = "#"; see.className = "fb-see";
      see.textContent = "See " + countryName(tc.iso);
      /* Exactly the click handler the map itself uses, so the link opens the country view the same way. */
      see.addEventListener("click", function (ev) {
        ev.preventDefault(); selectCountry(tc.iso);
        if (window.matchMedia && window.matchMedia("(max-width: 1100px)").matches) { revealPanel(true); return; }
        var card = el("map-card"); if (card && card.scrollIntoView) card.scrollIntoView({behavior: "smooth", block: "start"});
      });
      box.appendChild(see);
    }
  }

  /* The one line of What we found on undetermined items: their own figure, never in the sentences, totals
     and shares above it. at is findingsForMode(f), under the Distribution toggle; the 30 day figure counts
     the same complete days as the cards. */
  function undeterminedFindingsLine(f, at) {
    var n = at && typeof at.undetermined === "number" ? at.undetermined : (f && f.undetermined ? f.undetermined.all_time : null);
    if (n === null || n === undefined) return "";
    var lastDay = lastCompleteDay(), n30 = null;
    if (lastDay) {
      var agg = C.aggregateWindow(state.months, lastDay, 30, state.distribution);
      n30 = Object.keys(agg.countries).reduce(function (t, iso) { return t + ((agg.countries[iso] && agg.countries[iso].U) || 0); }, 0);
    }
    var tail = n30 !== null ? " (" + intComma(n30) + " in the 30 complete days ending " + lastDay + ")" : "";
    return "A further " + intComma(n) + " placements" + tail + " are undetermined: the model called them state origin, but who wrote or commissioned them could not be settled, with no credit line, stamp, byline or copy match behind them, or a sponsored disclosure the rules did not find on the article. As in the original study, they are excluded from every count here, China coverage included.";
  }

  /* The ten countries with the most state origin placements in the 30 complete days ending lastCompleteDay,
     under What we found (C.topCountriesInWindow). It is fixed: no control of the map moves it, neither the
     window, the day, the measure, the route, the label mode nor the Distribution toggle. It sums the same
     days the map and the ranked list open on, under both distributions, so its counts are theirs. Those
     days are the latest months, which load before the first render; if they have not arrived it says so,
     asks for them and shows no number. The worldwide wires are not a country and get one line of their
     own. A row opens the country as the See link of the findings does. Unchecked state sourcing is not in it. */
  var TOP10_DAYS = 30, TOP10_ROWS = 10, top10Cache = null;
  function top10Text(t, key, vars) {
    return String((t && t[key]) || "").replace(/\{(\w+)\}/g, function (m, k) { return vars && vars[k] !== undefined && vars[k] !== null ? String(vars[k]) : m; });
  }
  function top10Names(names) {
    return names.length < 2 ? names.join("") : names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
  }
  function openCountryFromFindings(iso) {
    selectCountry(iso);
    if (window.matchMedia && window.matchMedia("(max-width: 1100px)").matches) { revealPanel(true); return; }
    var card = el("map-card"); if (card && card.scrollIntoView) card.scrollIntoView({behavior: "smooth", block: "start"});
  }
  function renderTop10() {
    var box = el("top10");
    if (!box) return;
    var t = state.content && state.content.top10, lastDay = lastCompleteDay();
    var title = el("top10-title"), sub = el("top10-sub"), head = el("top10-head"), list = el("top10-list"), notes = el("top10-notes");
    if (!t || !lastDay || !title || !sub || !list || !notes) { box.hidden = true; return; }
    var start = windowStart(lastDay, TOP10_DAYS);
    var vars = {days: TOP10_DAYS, start: start, end: lastDay, min: C.MIN_SHARE_DENOMINATOR};
    title.innerHTML = esc(top10Text(t, "title", vars)) + (t.title_window ? ', <em>' + esc(top10Text(t, "title_window", vars)) + '</em>' : '');
    box.hidden = false;
    if (!covered(start)) {
      sub.textContent = top10Text(t, "loading", vars);
      list.innerHTML = ""; notes.innerHTML = ""; if (head) head.hidden = true;
      demandMonths(lastDay, TOP10_DAYS);
      return;
    }
    var key = lastDay + "|" + (loadedFrom() || "") + "|" + ((state.meta && state.meta.generated_at) || "");
    if (!top10Cache || top10Cache.key !== key) {
      top10Cache = {key: key, value: C.topCountriesInWindow(C.aggregateWindow(state.months, lastDay, TOP10_DAYS, "both"), TOP10_ROWS, state.names, lastDay, TOP10_DAYS)};
    }
    var top = top10Cache.value, rows = top.rows;
    sub.textContent = top10Text(t, "lede", vars);
    if (head) {
      head.innerHTML = '<span></span><span>' + esc(t.head_country || "") + '</span><span></span><span>' + esc(t.head_count || "") + '</span><span>' + esc(t.head_share || "") + '</span>';
      head.hidden = !rows.length;
    }
    list.innerHTML = rows.map(function (r) {
      var w = top.max ? Math.max(2, 100 * r.count / top.max) : 0;
      var items = intComma(r.underlying) + (r.underlying === 1 ? " underlying item" : " underlying items");
      var why = "fewer than " + C.MIN_SHARE_DENOMINATOR + " China articles in these days";
      var label = "Rank " + r.rank + (r.tied ? ", tied" : "") + ", " + r.name + ": " + intComma(r.count) + " state origin placements, " + items + ", " +
        (r.share === null ? "share of its China coverage n/a, " + why : pct(r.share) + " of its China coverage") + ". Opens the country view.";
      return '<li><button type="button" class="t10-row" data-iso="' + esc(r.iso) + '" aria-label="' + esc(label) + '">' +
        '<span class="t10-rank">' + r.rank + '</span>' +
        '<span class="t10-name">' + esc(r.name) + (r.tied ? ' <span class="t10-tied">tied</span>' : '') + '</span>' +
        '<span class="t10-bar"><span style="width:' + w.toFixed(1) + '%"></span></span>' +
        '<span class="t10-count"><span class="t10-n">' + intComma(r.count) + '</span><span class="t10-items">' + esc(items) + '</span></span>' +
        '<span class="t10-share"' + (r.share === null ? ' title="' + esc("Not shown: " + why) + '"' : '') + '>' +
          (r.share === null ? '<span class="t10-share-pre">share </span>n/a' : esc(pct(r.share)) + '<span class="t10-share-post"> of its China coverage</span>') + '</span>' +
        '</button></li>';
    }).join("");
    var lines = [];
    if (!rows.length) lines.push(top10Text(t, "empty", vars));
    if (rows.some(function (r) { return r.tied; })) lines.push(top10Text(t, "tie_note", vars));
    if (top.tiedBeyond.length) {
      var lastRow = rows[rows.length - 1], tv = {rank: lastRow.rank, count: intComma(lastRow.count) + (lastRow.count === 1 ? " placement" : " placements"), k: top.tiedBeyond.length};
      tv.names = top10Names(top.tiedBeyond.map(function (r) { return r.name; }));
      lines.push(top10Text(t, top.tiedBeyond.length <= 5 ? "tied_beyond" : "tied_beyond_many", tv));
    }
    if (rows.length) lines.push(top10Text(t, "share_note", vars));
    if (top.worldwide) lines.push(top10Text(t, "worldwide", {n: intComma(top.worldwide.count)}));
    if (top.underlyingSummed) lines.push(C.UNDERLYING_SUMMED);
    var missing = state.monthsAll.filter(function (m) { return state.monthsFailed[m] && m >= start.slice(0, 7) && m <= lastDay.slice(0, 7); });
    if (missing.length) lines.push(top10Text(t, "missing_month", {months: top10Names(missing)}));
    notes.innerHTML = lines.filter(Boolean).map(function (s) { return '<p>' + esc(s) + '</p>'; }).join("");
    Array.prototype.forEach.call(list.querySelectorAll(".t10-row"), function (b) {
      b.addEventListener("click", function () { openCountryFromFindings(b.getAttribute("data-iso")); });
    });
  }

  /* Twelve headline numbers above the findings sentences
, read as a funnel in three rows of four: the
     screening (how much was read and how much of it concerned China), the judgements, and the reach.
     Built from meta, meta.findings, the daily files and the outlet registry; hidden when findings are missing. */
  /* findings.all_time through the map's last complete day, summed from the daily files (C.allTimeInWindow),
     so every all time card and the summary end on the day the map's All time view ends, and the day it ends
     on. The export's own totals also count the partial day it ran, so they are only the fallback when the
     daily files are absent, and then through is null. Cached, since it reads every day's item ids. */
  function allTimeFindings(f) {
    var lastDay = lastCompleteDay();
    var key = lastDay + "|" + ((state.meta && state.meta.generated_at) || "") + "|" + (loadedFrom() || "");
    if (state._allTime && state._allTime.key === key && state._allTime.f === f) return state._allTime.value;
    var agg = lastDay ? (loadedFrom() === null ? C.aggregateWindow(state.months, lastDay, null, "both") : exportAllTimeAgg(lastDay)) : null;
    var value = {at: C.allTimeInWindow(agg, (f && f.all_time) || {}), through: agg ? lastDay : null, totals: null, wld: null};
    /* The same days summed per count, for the side panel's all time table: every country and the stratum, and the stratum alone. */
    if (agg) {
      value.totals = C.emptyCounts();
      Object.keys(agg.countries).forEach(function (iso) { C.addInto(value.totals, agg.countries[iso]); });
      value.wld = agg.countries[C.WORLDWIDE_ISO] || C.emptyCounts();
    }
    state._allTime = {key: key, f: f, value: value};
    return value;
  }
  /* Before every month has loaded, the same all time sums come from the export's own totals, latest.json's
     all_time, which the daily files add up to exactly, less the loaded days after lastDay (the partial
     export day, always in the latest month, which loads first). Shaped like an aggregateWindow result,
     the stratum apart. null when latest.json carries no totals; the export's findings are then the fallback. */
  function exportAllTimeAgg(lastDay) {
    var lt = state.latest && state.latest.totals && state.latest.totals.all_time;
    if (!lt) return null;
    var wEntry = state.latest.countries && state.latest.countries[C.WORLDWIDE_ISO];
    var rest = C.addInto(C.emptyCounts(), lt), wld = C.addInto(C.emptyCounts(), (wEntry && wEntry.all_time) || {});
    days().forEach(function (d) {
      if (d <= lastDay) return;
      var e = C.dayEntry(state.months, d);
      Object.keys((e && e.countries) || {}).forEach(function (iso) {
        var k = e.countries[iso] || {};
        Object.keys(rest).forEach(function (c) { rest[c] -= k[c] || 0; if (iso === C.WORLDWIDE_ISO) wld[c] -= k[c] || 0; });
      });
    });
    Object.keys(rest).forEach(function (c) { rest[c] -= wld[c]; });
    var countries = {_rest: rest};
    countries[C.WORLDWIDE_ISO] = wld;
    return {countries: countries};
  }
  /* "since <first day> through <last complete day>", the span every all time card and the summary count. */
  function allTimeSpan(f) {
    var through = allTimeFindings(f).through;
    return ((f && f.since) || "the start") + (through ? " through " + through : "");
  }
  /* findings.all_time through the last complete day (allTimeFindings), with its worldwide wires parts filled
     in from latest.json when an export without daily files predates them, then read under the Distribution toggle. */
  function findingsForMode(f) {
    var at = Object.assign({}, allTimeFindings(f).at);
    var wld = (state.latest && state.latest.countries && state.latest.countries[C.WORLDWIDE_ISO]) || null;
    if (at.state_origin_wires === undefined && wld && wld.all_time) {
      at.state_origin_wires = wld.all_time.A || 0; at.independent_wires = wld.all_time.C || 0;
      at.china_total_wires = wld.all_time.china_total || 0;
      at.unchecked_wires = at.unchecked === null || at.unchecked === undefined ? null : (wld.all_time.B || 0);
    }
    return C.distributionFindings(at, last30ForFindings(f), f && f.top_country, state.distribution);
  }
  /* The top country over the 30 complete days ending lastCompleteDay, the window the map and the ranked list
     open on, with its share of the state origin the given Distribution mode keeps in those days. The export's
     findings.top_country is the fallback when the daily files are absent; exports made before 2026-10-12's
     fix also counted the partial export day in it. When the window names another country than the export
     did, its outlet and route come from latest.json's per country top carrier. */
  function windowTopCountry(f, distribution, agg) {
    var lastDay = lastCompleteDay();
    if (agg === undefined) agg = lastDay ? C.aggregateWindow(state.months, lastDay, 30, distribution) : null;
    var t = C.topCountryInWindow(agg, distribution, f && f.top_country, last30ForFindings(f));
    var lc = t && state.latest && state.latest.countries && state.latest.countries[t.iso];
    if (t && !t.top_outlet && lc && lc.top_carrier) t.top_outlet = lc.top_carrier;
    if (t && !t.top_route && lc && lc.top_route) { t.top_route = lc.top_route.id; t.top_route_detail = lc.top_route; }
    if (t) t.window_end = agg ? lastDay : ((f && f.last_30d && f.last_30d.window_end) || null);
    return t;
  }
  /* findings.last_30d, or the same two numbers from latest.json when the export predates it. */
  function last30ForFindings(f) {
    var wld = (state.latest && state.latest.countries && state.latest.countries[C.WORLDWIDE_ISO]) || null;
    var last30 = (f && f.last_30d) || null;
    if (!last30 && wld && wld.last_30d && state.latest.totals && state.latest.totals.last_30d) {
      last30 = {state_origin: state.latest.totals.last_30d.A || 0, state_origin_wires: wld.last_30d.A || 0};
    }
    return last30;
  }
  /* % of all its news divides by the published items, which before meta.rediscovery.fixed_from carry
     items counted again after a prune (C.rediscoveryShareNote), so a window reaching back before then
     says so in the key. */
  function rediscoveryLegendNote() {
    if (!state.endDate) return "";
    var w = state.windowDays;
    return C.rediscoveryShareNote(state.meta, w === "all" ? (allDays()[0] || "") : C.shiftDate(state.endDate, 1 - Number(w)));
  }
  function renderKpis(f) {
    var wrap = el("kpis");
    if (!wrap) return;
    if (!f || !f.all_time) { wrap.hidden = true; return; }
    var m = state.meta || {};
    var at = findingsForMode(f);
    /* Outlets monitored counts newsrooms, so the wires are never in it, whatever the toggle says. */
    var outs = (state.outlets || []).filter(function (o) { return o.active && o.tier !== "distribution_wire"; });
    var share = at.state_share_of_china === null || at.state_share_of_china === undefined ? "n/a" : (at.state_share_of_china * 100).toFixed(1) + "%";
    var wldA = at.wires_all_time || 0;
    var since = allTimeSpan(f);
    /* Articles awaiting the model over the same days, under the toggle: the map's All China coverage counts them. */
    var atAll = allTimeFindings(f).at;
    var pendingAll = typeof atAll.pending !== "number" ? 0 : wiresOnly() ? atAll.pending_wires : countryOnly() ? atAll.pending - atAll.pending_wires : atAll.pending;
    var exportAt = m.generated_at ? "the export at " + m.generated_at.slice(0, 16).replace("T", " ") + " UTC" : "the last export";
    var has = function (v) { return v !== null && v !== undefined; };
    /* The words a toggled card adds so the reader knows which part of the dataset it counts. */
    var modeTail = wiresOnly() ? ", on the worldwide wires alone" : countryOnly() ? ", in the country outlets alone" : "";
    var firstCard = wiresOnly()
      ? {label: "State-origin releases on the wires", value: intComma(at.state_origin), note: "Text written or commissioned by the Chinese state and pushed through the worldwide distribution wires, since " + since + "."}
      : countryOnly()
      ? {label: "State-origin articles", value: intComma(at.state_origin), note: "Text written or commissioned by the Chinese state and run without substantive change by the country outlets, since " + since + (wldA > 0 ? "; " + intComma(wldA) + " more arrived through the worldwide wires" : "") + "."}
      : {label: "State-origin articles", value: intComma(at.state_origin), note: "Text written or commissioned by the Chinese state and run without substantive change, since " + since + (wldA > 0 ? ", including " + intComma(wldA) + " through worldwide distribution wires" : "") + "."};
    /* The 30 day state-origin counts come from the daily files over the same complete days the map opens on,
       so the cards, the ranked list and the legend line under the map give one number. The export's last_30d
       also counts the day the export ran, which is partial, so it is only the fallback when the daily files
       are absent. */
    var lastDay = lastCompleteDay();
    var last30 = last30ForFindings(f);
    var aggMode = lastDay ? C.aggregateWindow(state.months, lastDay, 30, state.distribution) : null;
    var origin30 = C.stateOriginInWindow(aggMode, last30, state.distribution);
    /* The top country card counts the same days, and its share is of the same total, origin30. */
    var tc = wiresOnly() ? null : windowTopCountry(f, state.distribution, aggMode);
    var wires30 = at.wires_last_30d;
    if (wiresOnly() && lastDay) {
      var aggW = C.aggregateWindow(state.months, lastDay, 30, "wires");
      if (aggW.countries[C.WORLDWIDE_ISO]) wires30 = aggW.countries[C.WORLDWIDE_ISO].A || 0;
    }
    var topCard = wiresOnly()
      ? (wires30 !== null && wires30 !== undefined ? {label: "Worldwide wires, last 30 days", value: intComma(wires30), note: "State-origin releases on the wires. They are not tied to any one country, so no country is ranked in this mode."} : null)
      : tc ? {label: "Top country, last 30 days", value: esc(countryName(tc.iso)) + (tc.share_of_world ? " <em>" + Math.round(tc.share_of_world * 100) + "%</em>" : ""),
              note: intComma(tc.state_origin) + " placements" + (typeof tc.underlying === "number" ? " (" + plural(tc.underlying, "underlying item") + ")" : "") + (tc.window_end ? " in the 30 complete days ending " + tc.window_end : "") + esc(C.findingsCarrierClause(tc.top_outlet, tc.state_origin)) + (typeof tc.world_total === "number" && tc.world_total > 0 && tc.share_of_world ? "; " + Math.round(tc.share_of_world * 100) + " percent of the " + intComma(tc.world_total) + " placements " + (countryOnly() ? "in the country outlets" : "in the monitored outlets and on the worldwide wires") + " in those days" : countryOnly() ? ", share of the country outlets' total" : "") + ".", html: true} : null;
    /* Unchecked state sourcing is shown only when the relay helpers say it may be: withheld after a failed
       study, not yet measured before the model has run, marked when provisional. Its note always says who
       made the judgement and what checked it. */
    var relay = relayFor(null);
    var basis = relayBasis();
    var checkedBy = basis === "human_coding" ? "Judged by the model and checked against hand coding of a fixed random sample, stratified by language and by the model\u2019s category, at kappa " + (m.kappa_warning_threshold || 0.7) + " or above."
      : "Judged by the model; not checked against hand coding.";
    var uncheckedCard;
    if (!relay.measured) {
      uncheckedCard = {label: "Unchecked state sourcing", value: "not yet measured", note: "Local reporting that repeats an official Chinese factual claim without an independent check. The model has not judged these articles yet.", small: true};
    } else if (!relay.visible) {
      uncheckedCard = {label: "Unchecked state sourcing", value: "withheld", note: "Local reporting that repeats an official Chinese factual claim without an independent check. Withheld: " + (stabilityUnstable() && !(m.relay_human_study && m.relay_human_study.state === "failed") ? "the model\u2019s stability check fell below " + (m.kappa_warning_threshold || 0.7) : humanStudyShort()) + ".", small: true};
    } else {
      uncheckedCard = {label: "Unchecked state sourcing", value: has(at.unchecked) ? intComma(at.unchecked) : "n/a",
        note: "Local reporting that repeats an official Chinese factual claim without an independent check, since " + since + modeTail + ". " +
              (relay.provisional ? "Provisional: judged by the model, not yet checked against hand coding." : checkedBy)};
    }
    var cards = [
      /* Row 1, the screening: how much was read, how much concerned China, how much of that the state placed. */
      has(m.articles_discovered) ? {label: "Articles screened, running total", value: intComma(m.articles_discovered),
        note: "Every item the tracker saw in the monitored feeds and wires since " + (f.since || "the start") + ", each checked against the China keyword list, up to " + exportAt + ", the day the export ran included." +
              (C.rediscoveryNote(m) ? " " + C.rediscoveryNote(m) : "")} : null,
      has(at.china_total) ? {label: "China-related articles", value: intComma(at.china_total),
        note: "Articles that substantively concern China after the full text was read, since " + since + modeTail + "." +
              (pendingAll > 0 ? " The map's All China coverage also counts the " + intComma(pendingAll) + " still awaiting the model." : "")} : null,
      firstCard,
      {label: "Share of China coverage", value: share, note: (has(at.state_origin) && has(at.china_total)
        ? intComma(at.state_origin) + (wiresOnly() ? " state origin releases of the " + intComma(at.china_total) + " China-related releases the wires carried"
          : " state origin articles of the " + intComma(at.china_total) + " China-related articles the " + (countryOnly() ? "country outlets" : "monitored outlets") + " published")
        : (wiresOnly() ? "Of every China-related release the wires carried" : "Of every China-related article the " + (countryOnly() ? "country outlets" : "monitored outlets") + " published")) + ", since " + since + ".", accent: true},
      /* Row 2, the judgements. */
      has(m.articles_classified) ? {label: "Articles classified", value: intComma(m.articles_classified),
        note: "China-relevant items from the feeds and wires whose full text was read and labelled by the rules or the model, including those found not to concern China. A running total up to " + exportAt + "."} : null,
      has(origin30) ? {label: "State origin, last 30 days", value: intComma(origin30),
        note: (aggMode ? "Placed by the Chinese state, in the 30 complete days ending " + lastDay
              : last30 && last30.window_end ? "Placed by the Chinese state, in the 30 complete days ending " + last30.window_end : "Placed by the Chinese state, in the 30 days to the last export") +
              (wiresOnly() ? ", on the worldwide wires alone." : countryOnly() ? ", in the country outlets alone." : ", in the monitored outlets and on the worldwide wires.")} : null,
      uncheckedCard,
      has(at.independent) ? {label: "Independent journalism", value: intComma(at.independent),
        note: "China coverage that is neither state origin nor unchecked state sourcing: the outlets' own reporting, wire agency copy such as Reuters or AP, and quotes from Chinese officials that are confirmed, given context or challenged, since " + since + modeTail + "."} : null,
      /* Row 3, the reach. */
      topCard,
      {label: "Outlets monitored", value: intComma(outs.length), note: "Read every day. Newsrooms only; the wires are not counted here."},
      has(m.countries_monitored) ? {label: "Countries monitored", value: intComma(m.countries_monitored),
        note: "Countries and territories with at least one active newsroom."} : null,
      has(m.countries_in_gaps) ? {label: "Countries with coverage gaps", value: intComma(m.countries_in_gaps),
        note: "Each recorded with a stated reason, such as no working feed or no news outlet at all."} : null
    ].filter(Boolean);
    wrap.innerHTML = cards.map(function (c) {
      return '<div class="kpi' + (c.accent ? ' accent' : '') + '"><div class="k-label">' + esc(c.label) + '</div><div class="k-value' + (c.small ? ' k-word' : '') + '">' +
        (c.html ? c.value : esc(c.value)) + '</div><div class="k-note">' + esc(c.note) + '</div></div>';
    }).join("");
    wrap.hidden = false;
    renderIssuerLine();
  }

  /* Who issued a release, from meta.issuer_type_totals (exports since the issuer rule of 2026-10-17): state
     origin from state-owned enterprises and from Hong Kong or Macao SAR government bodies stated apart,
     so a reader can leave them out, and private releases, which are not state origin, as their own count.
     All time and both distributions, as the export counts them. An export without the field shows nothing. */
  var ISSUER_LABELS = {state_media: "State media", government: "Government", party: "Party", diplomatic: "Diplomatic",
    state_owned_enterprise: "State-owned enterprise", sar_government: "SAR government", private_release: "Private release"};
  function issuerBadge(type) {
    var label = type && ISSUER_LABELS[type];
    return label ? '<span class="badge issuer-' + esc(type) + '" title="Issuer of the release">' + esc(label) + '</span>' : '';
  }
  function issuerLineText(it) {
    if (!it || typeof it !== "object" || !it.by_type) return "";
    var by = it.by_type, n = function (t, k) { return (by[t] && by[t][k]) || 0; };
    var all = function (t) { var o = by[t] || {}; return Object.keys(o).reduce(function (s, k) { return s + (Number(o[k]) || 0); }, 0); };
    var parts = [];
    var soe = n("state_owned_enterprise", "A"), sar = n("sar_government", "A"), priv = all("private_release");
    if (soe || sar) parts.push("Of the state origin articles, " + intComma(soe) + " are releases of state-owned enterprises and " + intComma(sar) + " come from Hong Kong or Macao SAR government bodies.");
    if (priv) parts.push(intComma(priv) + " releases from private issuers are counted as independent journalism or as not about China, never as state origin.");
    if (it.unrecorded) parts.push(intComma(it.unrecorded) + " labels were made before the issuer rule and have not been judged again under it.");
    return parts.length ? parts.join(" ") + " All time, both distributions." : "";
  }
  function renderIssuerLine() {
    var wrap = el("kpis");
    if (!wrap || !wrap.parentNode) return;
    var text = issuerLineText(state.meta && state.meta.issuer_type_totals), line = el("issuer-line");
    if (!text) { if (line) line.hidden = true; return; }
    if (!line) {
      line = document.createElement("p");
      line.id = "issuer-line"; line.className = "issuer-line";
      wrap.parentNode.insertBefore(line, wrap.nextSibling);
    }
    line.textContent = text;
    line.hidden = false;
  }

  var START_HERE_LS_KEY = "tracker_start_here_closed";
  function setupStartHere() {
    var det = el("start-here");
    if (!det) return;
    var closed = false;
    try { closed = localStorage.getItem(START_HERE_LS_KEY) === "1"; } catch (e) { closed = false; }
    det.open = !closed;
    det.addEventListener("toggle", function () {
      try { localStorage.setItem(START_HERE_LS_KEY, det.open ? "0" : "1"); } catch (e) { /* private mode or blocked storage: nothing persists, page still works */ }
    });
  }
  var ROUTE_CARD_ORDER = ["wire_credit", "distribution_stamp", "sponsored_disclosure", "diplomatic_byline", "uncredited_copy", "unattributed"];
  function renderRouteCards() {
    var wrap = el("sh-routes");
    if (!wrap) return;
    var routes = (state.content && state.content.routes) || {};
    wrap.innerHTML = ROUTE_CARD_ORDER.filter(function (id) { return routes[id]; }).map(function (id) {
      var r = routes[id];
      return '<button type="button" class="sh-route-card" data-route-id="' + esc(id) + '">' +
        '<span class="sh-route-name">' + esc(r.plain_name) + '</span>' +
        '<span class="sh-route-how">' + esc(r.how_it_arrives) + '</span>' +
        '<span class="sh-route-line"><span class="sh-badge">Illustrative</span> ' + esc(r.illustrative_line) + '</span></button>';
    }).join("");
    Array.prototype.forEach.call(wrap.querySelectorAll(".sh-route-card"), function (btn) {
      btn.addEventListener("click", function () { selectRouteCard(btn.getAttribute("data-route-id")); });
    });
  }
  /* Selects the route in the existing Route of state origin select, exactly as choosing it there
     would, then scrolls the map into view. */
  function selectRouteCard(id) {
    var sel = el("route");
    if (!sel) return;
    var value = "route:" + id;
    var found = Array.prototype.some.call(sel.options, function (o) { return o.value === value; });
    if (!found) return;
    /* Under another measure the select is disabled; choosing a route card switches to state origin, as choosing the route does. */
    sel.disabled = false;
    sel.value = value;
    sel.dispatchEvent(new Event("change"));
    var wrap = el("map-wrap");
    if (wrap && wrap.scrollIntoView) wrap.scrollIntoView({behavior: "smooth", block: "start"});
  }
  function renderStartHere() {
    var det = el("start-here");
    if (!det) return;
    var content = state.content;
    if (!content || !content.start_here) { det.hidden = true; return; }
    det.hidden = false;
    var introEl = el("sh-intro");
    introEl.innerHTML = "";
    String(content.start_here).split(/\n{2,}/).forEach(function (para) {
      var p = document.createElement("p");
      p.textContent = para;
      introEl.appendChild(p);
    });
    var cats = content.categories || {};
    var order = ["state_origin", "unchecked_state_sourcing", "official_chinese_sourcing_verification_pending", "independent_journalism", "not_relevant", "paywalled_unread_and_uncounted"];
    el("sh-categories").innerHTML = order.filter(function (id) { return cats[id]; }).map(function (id) {
      var c = cats[id];
      return '<div class="sh-cat"><dt>' + esc(c.proposed_clearer_label || c.label) + '</dt><dd>' + esc(c.definition) + '</dd></div>';
    }).join("");
    renderRouteCards();
  }

  /* Picks the same two or three examples all day, by hashing the calendar date into the pool: stable
     within a day, different the next. A pool smaller than three shows what there is. */
  function dailyRotationIndices(n, dateStr, count) {
    if (!n) return [];
    var h = 0, s = String(dateStr || "");
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    var c = Math.max(0, Math.min(n, count || 0));
    var start = h % n, out = [];
    for (var j = 0; j < c; j++) out.push((start + j) % n);
    return out;
  }
  /* The matched phrase inside a snippet, built from text nodes: the snippet and its offsets come
     straight from the data file, so nothing here is ever parsed as markup. */
  function buildSnippetFragment(snippet, start, end) {
    var frag = document.createDocumentFragment();
    var s = String(snippet || "");
    var st = Math.max(0, Math.min(s.length, start || 0));
    var en = Math.max(st, Math.min(s.length, end || 0));
    if (st > 0) frag.appendChild(document.createTextNode(s.slice(0, st)));
    if (en > st) {
      var mark = document.createElement("mark");
      mark.className = "ex-hl";
      mark.appendChild(document.createTextNode(s.slice(st, en)));
      frag.appendChild(mark);
    }
    if (en < s.length) frag.appendChild(document.createTextNode(s.slice(en)));
    return frag;
  }
  function outletLang(id) {
    var o = (state.outlets || []).find(function (x) { return x.id === id; });
    return o ? o.language : null;
  }
  function translateHtml(lang, text, url) {
    var t = C.translateLinks(lang, text, url);
    if (!t) return "";
    return '<span class="tr-links">' +
      (t.text ? '<a href="' + esc(t.text) + '" target="_blank" rel="noopener" title="Opens Google Translate with this headline and quote">Translate</a>' : '') +
      (t.page ? '<a href="' + esc(t.page) + '" target="_blank" rel="noopener" title="Opens the article through Google Translate">Read the article in English</a>' : '') + '</span>';
  }
  function renderExamples() {
    var sec = el("examples"), list = el("examples-list");
    if (!sec || !list) return;
    var ex = state.examples;
    var poolA = ((ex && ex.A) || []).map(function (e) { return {item: e, cat: "A"}; });
    var poolB = ((ex && ex.B) || []).map(function (e) { return {item: e, cat: "B"}; });
    var pool = poolA.concat(poolB).filter(function (w) { return w.item && w.item.tell && w.item.tell.snippet; });
    if (!pool.length) { sec.hidden = true; return; }
    sec.hidden = false;
    var today = new Date().toISOString().slice(0, 10);
    var count = pool.length >= 3 ? 3 : pool.length;
    var idxs = dailyRotationIndices(pool.length, today, count);
    list.innerHTML = "";
    idxs.map(function (i) { return pool[i]; }).forEach(function (w) {
      var item = w.item, tell = item.tell;
      var div = document.createElement("div");
      div.className = "ex-item";
      var href = safeUrl(item.url);
      var a = document.createElement(href ? "a" : "span");
      a.className = "ex-headline";
      if (href) { a.href = href; a.target = "_blank"; a.rel = "noopener"; }
      a.textContent = decodeEntities(item.headline || item.url || "Untitled article");
      div.appendChild(a);
      var meta = document.createElement("div");
      meta.className = "ex-meta";
      var badge = document.createElement("span");
      badge.className = "badge cat-" + w.cat;
      badge.textContent = clearerLabel(w.cat, w.cat === "A" ? "State origin" : "Unchecked state sourcing");
      meta.appendChild(badge);
      meta.appendChild(document.createTextNode(" " + (item.outlet_name || item.outlet_id || "an unnamed outlet") + ", " +
        countryName(item.country) + ", " + (item.date || "date unknown") + (w.cat === "A" && item.route ? ", " + lowerLabel(contentRouteName(item.route)) + " route" : "")));
      div.appendChild(meta);
      var trEx = translateHtml(outletLang(item.outlet_id), (item.headline || "") + "\n\n" + (tell.snippet || ""), safeUrl(item.url));
      if (trEx) { var trWrap = document.createElement("div"); trWrap.innerHTML = trEx; div.appendChild(trWrap.firstChild); }
      var q = document.createElement("p");
      q.className = "ex-snippet";
      q.appendChild(document.createTextNode("“"));
      var ww = C.wholeWordSnippet(tell.snippet, tell.start, tell.end);
      q.appendChild(buildSnippetFragment(ww.snippet, ww.start, ww.end));
      q.appendChild(document.createTextNode("”"));
      div.appendChild(q);
      list.appendChild(div);
    });
  }

  /* ------------------------------------------------------ research measures
     The measures in meta.measures (pipeline/measures.py, docs/measures.js): each section hidden while all
     of its measures are off, every registered renderer run on its own, and their method paragraphs. A
     renderer that throws is named in the notice like any other step, and the rest still render. */
  var MS = window.TrackerMeasures || null;
  function measureBlocks() { return (state.meta && state.meta.measures) || {}; }
  /* The side files to load: every measure that is not off and has one. */
  function measureFiles() {
    var blocks = measureBlocks();
    return Object.keys(blocks).filter(function (id) {
      var b = blocks[id];
      return b && b.file && (MS ? !MS.isOff(b) : b.status && b.status !== "off");
    }).map(function (id) { return {id: id, file: blocks[id].file}; });
  }
  function measureApi(id) {
    var sec = MS ? MS.SECTIONS.filter(function (x) { return x.measures.indexOf(id) !== -1; })[0] : null;
    return {id: id, slot: MS && MS.SLOTS[id] ? el(MS.SLOTS[id]) : null, section: sec ? el(sec.id) : null,
            meta: state.meta, names: state.names, esc: esc, countryName: countryName, M: MS, C: C};
  }
  function renderMeasures() {
    if (!MS) return;
    var blocks = measureBlocks();
    MS.SECTIONS.forEach(function (x) { var sec = el(x.id); if (sec) sec.hidden = !MS.sectionVisible(x, blocks); });
    var failed = MS.renderAll(blocks, state.measureData, measureApi);
    var wrap = el("measure-method"), body = el("measure-method-body");
    if (wrap && body) {
      var paras = MS.methodParagraphs(blocks);
      body.innerHTML = paras.join("");
      wrap.hidden = !paras.length;
    }
    if (failed.length) throw new Error(failed.join("; "));
  }

  /* Planted bylines, from data/planted.json. A flag says only that a byline or a cited organisation
     matches a persona or front named in a published investigation; the paragraph says what it does
     not mean. The file is written by the export. When it has not loaded the section says so and
     nothing else, since a missing file is not a finding; when it has loaded and holds no flag, the
     section shows the empty state. The section is never hidden: a reader should see that the check
     runs and has found nothing. */
  var PLANTED_ROWS = 20;
  function plantedDate(f) { return String((f && (f.published_at || f.discovered_at)) || "").slice(0, 10); }
  function plantedSortKey(f) { return String((f && (f.published_at || f.discovered_at)) || ""); }
  function renderPlanted() {
    var sec = el("planted"), intro = el("planted-intro"), body = el("planted-body");
    if (!sec || !intro || !body) return;
    var loaded = !!(state.planted && typeof state.planted === "object");
    var j = loaded ? state.planted : {};
    var sum = C.planted.summarise(j);
    var byId = function (list) { var m = {}; (list || []).forEach(function (x) { if (x && x.id) m[x.id] = x; }); return m; };
    var ops = byId(j.operations), personas = byId(j.personas), fronts = byId(j.fronts);
    intro.textContent = "A flag here means that an article's byline, or an organisation it cites, matches a persona or false front named in a published investigation, with a link to that investigation beside each match. " +
      "It does not mean the byline is fictitious: the tracker cannot prove that, and the absence of a match proves nothing. " +
      "Flagged articles are listed here only and are never counted as state origin, unchecked state sourcing or independent journalism." +
      (loaded ? " The list holds " + plural(sum.personas, "persona") + " and " + plural(sum.fronts, "front") + " from " + plural(sum.operations, "investigation") +
        (sum.list_version ? ", list version " + sum.list_version : "") + "." : "");
    if (!loaded) {
      body.innerHTML = '<p class="pl-empty">The planted bylines file did not load. Reload the page, or check whether the last export ran.</p>';
      return;
    }

    var flags = (Array.isArray(j.flags) ? j.flags : []).filter(function (f) { return f && typeof f === "object"; });
    flags = flags.slice().sort(function (a, b) { var ka = plantedSortKey(a), kb = plantedSortKey(b); return ka < kb ? 1 : ka > kb ? -1 : 0; });
    if (!flags.length) {
      body.innerHTML = '<p class="pl-empty">' + esc(C.planted.emptyState(j)) + '</p>';
      return;
    }
    var shown = state.plantedShowAll ? flags : flags.slice(0, PLANTED_ROWS);
    var rows = shown.map(function (f) {
      var who = f.persona_id && personas[f.persona_id] ? personas[f.persona_id].name : (f.front_id && fronts[f.front_id] ? fronts[f.front_id].name : (f.evidence || f.persona_id || f.front_id || ""));
      var op = f.operation_id && ops[f.operation_id] ? ops[f.operation_id] : null;
      var opName = op ? op.name : (f.operation_id || "");
      var opUrl = op ? safeUrl(op.report_url) : "";
      var href = safeUrl(f.url);
      var title = escText(f.title || "");
      return '<tr><td class="pl-date">' + esc(plantedDate(f)) + '</td>' +
        '<td class="pl-outlet">' + escText(f.outlet || f.outlet_id || "") + (title ? '<span class="pl-title">' + (href ? '<a href="' + esc(href) + '" target="_blank" rel="noopener">' + title + '</a>' : title) + '</span>' : '') + '</td>' +
        '<td>' + esc(countryName(f.country || "")) + '</td>' +
        '<td>' + escText(who) + '</td>' +
        '<td class="pl-detector">' + esc(C.planted.detectorLabel(f.detector)) + '</td>' +
        '<td>' + (opUrl ? '<a href="' + esc(opUrl) + '" target="_blank" rel="noopener">' + esc(opName) + '</a>' : esc(opName)) + '</td></tr>';
    }).join("");
    var line = sum.by_country.map(function (r) { return esc(countryName(r.iso)) + " " + r.n; }).join(", ");
    body.innerHTML = '<p class="pl-line">' + plural(sum.flags, "flagged article") + ' by country: ' + line + '.</p>' +
      '<div class="pl-table-wrap"><table class="pl-table"><thead><tr><th scope="col">Date</th><th scope="col">Outlet</th><th scope="col">Country</th><th scope="col">Byline or front</th><th scope="col">Detector</th><th scope="col">Investigation</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
      (flags.length > PLANTED_ROWS ? '<p class="pl-toggle"><button type="button" id="planted-toggle">' + (state.plantedShowAll ? 'Show the newest ' + PLANTED_ROWS : 'Show all ' + flags.length) + '</button>' +
        (state.plantedShowAll ? '' : ' <span class="muted">Newest ' + Math.min(PLANTED_ROWS, flags.length) + ' of ' + flags.length + '.</span>') + '</p>' : '');
    var toggle = el("planted-toggle");
    if (toggle) toggle.addEventListener("click", function () {
      state.plantedShowAll = !state.plantedShowAll;
      renderPlanted();
      var again = el("planted-toggle");
      if (again) again.focus();
      if (!state.plantedShowAll && sec.scrollIntoView) sec.scrollIntoView({block: "start"});
    });
  }

  function renderFaq() {
    var sec = el("faq"), list = el("faq-list");
    if (!sec || !list) return;
    var items = state.content && state.content.faq;
    if (!items || !items.length) { sec.hidden = true; return; }
    sec.hidden = false;
    list.innerHTML = "";
    items.forEach(function (item) {
      var det = document.createElement("details");
      det.className = "faq-item";
      var sum = document.createElement("summary");
      sum.textContent = item.question;
      det.appendChild(sum);
      var p = document.createElement("p");
      p.textContent = item.answer;
      det.appendChild(p);
      var srcs = item.sources || [];
      if (srcs.length) {
        var ul = document.createElement("ul");
        ul.className = "faq-sources";
        srcs.forEach(function (u) {
          var li = document.createElement("li");
          var safe = safeUrl(u);
          if (safe) {
            var a = document.createElement("a");
            a.href = safe; a.target = "_blank"; a.rel = "noopener";
            a.textContent = safe;
            li.appendChild(a);
          } else li.textContent = u;
          ul.appendChild(li);
        });
        det.appendChild(ul);
      }
      list.appendChild(det);
    });
  }
  function renderCta() {
    var line = el("cta-line");
    if (!line) return;
    var t = state.content && state.content.cta;
    if (!t) { line.hidden = true; return; }
    line.innerHTML = linkifyHtml(t);
    line.hidden = false;
  }

  /* ------------------------------------------------------------------- map */
  var svg, gCountries, gMarkers, path, projection, colorScale;
  var ZERO_COLOR = "#f6f1e8", LOW_COLOR = "#f7d3cb", HIGH_COLOR = "#3d0306";
  /* Seven distinct steps, interpolated in Lab so lightness falls steadily: the darker the shade, the more articles. Bin edges follow a square law, so the low end gets most of the
     resolution: a country at a fifth of the cap is already three steps away from white. */
  var STEPS = 7;
  var RAMP = d3.piecewise(d3.interpolateLab, [LOW_COLOR, "#ef8a73", "#d23a2e", "#9e0f17", HIGH_COLOR]);
  var STEP_COLORS = d3.range(STEPS).map(function (i) { return RAMP(STEPS === 1 ? 1 : i / (STEPS - 1)); });
  /* The key for a cap: edges snapped to what the key prints, so the map and its key never disagree. */
  function keyFor(cap, unit, breaks) { return C.keyTiers(cap, unit, STEPS, breaks); }
  /* All China coverage runs from a handful of articles to thousands per country, so steps up to the cap
     left most of the map in the darkest two shades. Its shades split the pooled values of every date into
     equal shares instead; the other measures keep steps up to the cap. */
  function spreadScale(sc) { return metricDef().measure === "china" && !!(sc && sc.cap && sc.breaks); }
  function shadeFor(key) { return function (v) { return v > 0 ? STEP_COLORS[C.keyIndex(v, key.edges)] : ZERO_COLOR; }; }
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
    /* Withheld: unchecked state sourcing not measured or not publishable. A cross hatch in dark amber. */
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
    /* The 110m outline has no polygon at all for small states and territories, so a monitored country can
       be missing from the map rather than hatched on it. Remember what was drawn, so the legend can count
       what was not and point the reader at the list that does carry it. */
    state.mapped = {};
    features.forEach(function (f) { if (f.iso) state.mapped[f.iso] = true; });
    gCountries.selectAll("path").data(features).enter().append("path")
      .attr("class", "country").attr("d", path)
      .on("mousemove", function (ev, f) { showTip(ev, f); })
      .on("mouseleave", hideTip)
      .on("click", function (ev, f) { selectCountry(f.iso || ("name:" + ((f.properties && f.properties.name) || "Unknown"))); });
  }

  /* Monitored countries with active outlets that the basemap has no outline for. They are absent from the
     map, which no pattern in the legend covers, so the legend says how many and where to read them. */
  function offMapCountries() {
    var drawn = state.mapped;
    if (!drawn || !Object.keys(drawn).length) return [];
    var countries = (state.latest && state.latest.countries) || {};
    return Object.keys(countries).filter(function (iso) {
      /* The worldwide distribution stratum has its own line under the legend. */
      return iso !== C.WORLDWIDE_ISO && (countries[iso].outlets_active || 0) > 0 && !drawn[iso];
    }).sort(function (x, y) {
      var px = (state._perIso || {})[x], py = (state._perIso || {})[y];
      return ((py && py.mv.value) || 0) - ((px && px.mv.value) || 0) || countryName(x).localeCompare(countryName(y));
    });
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
    var key = [state.metric, state.windowDays, state.mode, state.route ? state.route.kind + ":" + state.route.id : "", (state.meta && state.meta.generated_at) || "", loadedFrom() || ""].join("|");
    if (!state.scaleCache[key]) state.scaleCache[key] = C.scaleCap(state.months, scaleDays(state.windowDays), state.latest, state.metric, windowArg(state.windowDays), state.mode, ctxFor);
    return state.scaleCache[key];
  }
  /* The end dates a scale is pooled over: every day of the timeline once every month has loaded, and
     before that only the days whose window lies inside the loaded months, so no pooled value is a window
     cut short by a month that has not arrived. */
  function scaleDays(w) {
    if (loadedFrom() === null) return tlDays;
    return tlDays.filter(function (d) { return covered(windowStart(d, w)); });
  }
  /* The words the keys add while only the opening months are loaded. */
  function partialScaleNote() {
    var lf = loadedFrom();
    if (lf === null) return "";
    var first = tlDays.filter(function (d) { return d >= lf; })[0] || lf;
    return " Only the days from " + first + " have loaded so far, so the scale is pooled over the dates whose window they cover; an earlier day, Total or Play loads the whole timeline, and the scale is then pooled over every date.";
  }

  function renderMap() {
    if (!gCountries) return;
    var agg = currentAgg();
    var vals = [];
    var perIso = {};
    C.distributionIsos(state.latest.countries || {}, state.distribution).forEach(function (iso) {
      var entry = state.latest.countries[iso];
      var mv = withOutputGap(C.metricValue(agg.countries[iso], state.metric, entry.outlets_active, state.mode, agg.reviewed[iso], ctxFor(iso, entry, agg)), entry);
      var cls = C.fillClass(entry, mv);
      perIso[iso] = {entry: entry, mv: mv, cls: cls};
      if (cls === "value") vals.push(mv.value);
    });
    var sc = scaleFor();
    /* The cap is the 95th percentile of every date, and nothing else. A floor under it would make the
       legend's own sentence false: the scale is relative, and a small cap is the honest one. */
    var max = sc.cap || (vals.length ? d3.max(vals) : 1) || 1;
    var trueMax = sc.max !== null && sc.max !== undefined ? sc.max : (vals.length ? d3.max(vals) : max);
    var capped = trueMax > max || vals.some(function (v) { return v > max; });
    /* White for exactly zero; any positive value takes one of STEPS tinted steps up to deep red at the cap. */
    var spread = spreadScale(sc);
    var key = keyFor(max, C.keyUnit(state.metric), spread ? sc.breaks : null);
    colorScale = shadeFor(key);
    state._perIso = perIso; state._agg = agg; state._max = max; state._capped = capped; state._trueMax = trueMax;
    state._spread = spread;
    // Fills are set directly. A D3 transition would interpolate strings between pattern
    // URLs and colors and leave an invalid fill behind if a re-render interrupted it;
    // the CSS transition on path.country smooths color to color changes instead.
    gCountries.selectAll("path.country")
      .classed("selected", function (f) { return f.iso && f.iso === state.selected; })
      .attr("fill", function (f) {
        var p = f.iso && perIso[f.iso];
        /* Under worldwide wires nothing is drawn by country: every outline takes the "cannot be computed
           here" pattern, never a shade and never an observed zero. */
        if (wiresOnly()) return fillFor("sparse");
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
    renderLegend(key);
    renderMapCaption();
    renderBars(agg);
  }
  /* One line under the map when the toggle shows the worldwide wires alone, which have no country to draw. */
  function renderMapCaption() {
    var cap = el("map-caption");
    if (!cap) return;
    cap.textContent = wiresOnly() ? "Worldwide wires are not drawn by country: a release on the wires is not tied to any one country, so the map is left unfilled and the figure is the line under the legend and the one row in the ranked list." : "";
    cap.hidden = !wiresOnly();
  }

  /* The warnings that travel with every figure for a country: the export's standing warnings plus
     the ones that depend on the window shown. */
  function countryWarnings(entry, agg) {
    var out = (entry.warnings || []).map(function (w) { return w.text; });
    var gaps = (agg && agg.relayIncompleteDays) || [];
    if (entry.relay_outlets && gaps.length) out.push("the collector on the owner's machine, which alone collects " + plural(entry.relay_outlets, "outlet") + " here, ran in too few hours on " + plural(gaps.length, "day") + " of this window");
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
  /* What one tier of the key counts, in the words of the Measure and Basis buttons. */
  function keyUnitText(metric) {
    var parts = C.metricParts(metric), noun = measureNounFor(parts.measure);
    var Noun = noun.charAt(0).toUpperCase() + noun.slice(1);
    if (routeActive()) Noun += ", " + lowerLabel(routeLabel(state.route)) + ",";
    return {
      count: "Number of " + (routeActive() ? lowerLabel(Noun) : noun) + " in the window",
      per_outlet: Noun + " per active monitored outlet",
      share_of_china: Noun + " as a percent of the country’s China coverage",
      share_of_output: Noun + " per 1,000 items the country's counted outlets published (its audience ranked outlets where the registry ranks any, otherwise every active outlet); 1 per 1,000 is 0.1%"
    }[parts.basis] || metricLabel();
  }
  /* The heading names the three things that set the scale: measure, basis and window. */
  function keyHeading() {
    var parts = C.metricParts(state.metric);
    return [C.MEASURE_NAMES[parts.measure] + (routeActive() ? ", " + lowerLabel(routeLabel(state.route)) : ""),
            C.BASIS_NAMES[parts.basis] || "", windowLabel()]
      .concat(state.mode === "reviewed" ? ["human-reviewed labels only"] : []).filter(Boolean);
  }
  /* A 30 day window over a shorter timeline covers the same days as Total, so the two share one key.
     Say so, or flipping between them looks like the key failed to update. */
  function sameWindowNote() {
    var all = allDays(), w = state.windowDays;
    if (!all.length || !tlDays.length) return "";
    var span = Math.round((Date.parse(tlDays[tlDays.length - 1]) - Date.parse(all[0])) / 864e5) + 1;
    var twin = w === "all" ? [7, 30].filter(function (n) { return n >= span; })[0] : (Number(w) > 1 && Number(w) >= span ? "all" : null);
    if (!twin) return "";
    return " The timeline holds " + plural(span, "day") + ", so " + (w === "all" ? "Total and the " + twin + " day window" : "the " + Number(w) + " day window and Total") + " cover the same days and share this key.";
  }
  function renderLegend(key) {
    var steps = key.tiers.map(function (t) {
      return '<li><span class="sw" style="background:' + STEP_COLORS[t.index] + '"></span><span>' + esc(t.label) + '</span></li>';
    });
    var m = metricDef();
    /* The key names the reason the reader is actually looking at: under a route filter, or in reviewed
       mode, every share of monitored output is not shown for that reason and not for a small denominator. */
    var notShown = m.allItems && routeActive() ? "Not shown by route: the items the outlets published are not split by route"
      : m.allItems && state.mode === "reviewed" ? "Not shown for human-reviewed labels only: the published items are not reviewed"
      : m.allItems ? "Fewer than " + C.MIN_OUTLETS_FOR_OUTPUT_SHARE + " outlets, or too few items published in this window, for a share"
      : m.format === "pct" ? "Fewer than " + C.MIN_SHARE_DENOMINATOR + " China articles, so no share"
      : null;
    var relay = relayFor(null);
    var offMap = offMapCountries();
    /* The stratum's line follows the toggle: absent under country outlets, alone with the key under wires. */
    var wld = state._perIso && state._perIso[C.WORLDWIDE_ISO];
    var worldwideLine = wld ? C.worldwideLegendLine(wld.mv, state.metric) : null;
    if (wiresOnly() && !worldwideLine) worldwideLine = "Worldwide distribution via press release wires: nothing in this window under this measure.";
    el("legend").innerHTML =
      '<div class="lg-group">' +
        '<div class="lg-kicker">Color scale</div>' +
        '<div class="lg-head" title="' + esc(metricLabel()) + '">' + keyHeading().map(esc).join('<span class="lg-sep"> \u00b7 </span>') + '</div>' +
        (undeterminedRouteActive() ? '<div class="lg-prov">' + esc(C.UNDETERMINED_LAYER) + '</div>' : '') +
        ((m.relay || m.relayInside) && relay.provisional ? '<div class="lg-prov">Provisional: ' + (m.relayInside ? 'contains unchecked state sourcing, which is one model\u2019s judgement, not yet checked against hand coding' : 'one model\u2019s judgement, not yet checked against hand coding') + '</div>' : '') +
        '<div class="lg-unit">Each tier: ' + esc(lowerLabel(keyUnitText(state.metric))) + '</div>' +
        '<ul class="lg-steps">' +
          '<li><span class="sw" style="background:' + ZERO_COLOR + '"></span><span>None found</span></li>' +
          steps.join("") +
        '</ul>' +
        '<p class="lg-note">Until the next data update, the scale is fixed for this measure, basis and window across every date on the timeline, so a shade means the same number on every day. ' + esc(C.CAP_UPDATE_NOTE) + esc(partialScaleNote()) +
          (state._spread ? ' This measure runs from a handful of China articles to thousands, so each shade holds about a seventh of the values on every date whose window is complete, rather than a step up to a cap; the highest value on any date is ' + esc(C.formatKeyNumber(state._trueMax, key.unit, 3)) + (key.unit.per1000 ? ' per 1,000 items' : '') + '.' : '') +
          (state._capped && !state._spread ? ' The darkest shade is an overflow step for values above the 95th percentile of all dates; the highest value on any date is ' + esc(C.formatKeyNumber(state._trueMax, key.unit, 3)) + (key.unit.per1000 ? ' per 1,000 items' : '') + '.' : '') +
          (key.tiers.length < STEPS && !state._spread ? ' The cap is small enough that ' + plural(STEPS - key.tiers.length, "shade holds", "shades hold") + ' no whole number, so ' + (STEPS - key.tiers.length === 1 ? 'it is' : 'they are') + ' left out.' : '') +
          esc(sameWindowNote()) + '</p>' +
        (m.allItems && rediscoveryLegendNote() ? '<p class="lg-note" id="legend-rediscovery">' + esc(rediscoveryLegendNote()) + '</p>' : '') +
        (state.zoomIso ? '<p class="lg-note">Zoomed to ' + esc(countryName(state.zoomIso)) + '. Shades keep the world scale, so they compare directly with every other country.</p>' : '') +
      '</div>' +
      '<div class="lg-group">' +
        '<div class="lg-kicker">Not on the scale</div>' +
        '<ul class="lg-keys">' +
          '<li><span class="sw" style="background:repeating-linear-gradient(45deg,#141618,#141618 3px,#2b2e33 3px,#2b2e33 4px)"></span><span>Not monitored: no outlets registered</span></li>' +
          '<li><span class="sw" style="background:radial-gradient(#3a3d43 0.9px, #141618 1px) 0 0/6px 6px"></span><span>Coverage gap, or every outlet inactive</span></li>' +
          (m.relay && !relay.visible ? '<li><span class="sw" style="background:repeating-linear-gradient(45deg,transparent,transparent 3px,#5a4520 3px,#5a4520 4px),repeating-linear-gradient(-45deg,#141618,#141618 3px,#5a4520 3px,#5a4520 4px)"></span><span>' + esc(relay.measured ? "Unchecked state sourcing withheld until agreement is settled" : "Unchecked state sourcing not yet measured") + '</span></li>' : '') +
          '<li><span class="sw" style="background:repeating-linear-gradient(0deg,#141618,#141618 3px,#3a4450 3px,#3a4450 4px)"></span><span>Monitored, but no keyword list for the outlets\' language, and nothing found</span></li>' +
          '<li><span class="sw" style="background:#1a1c1f"></span><span>Monitored, but no China coverage in this window</span></li>' +
          (notShown ? '<li><span class="sw" style="background:radial-gradient(#8a7443 0.7px, #23262a 0.8px) 0 0/5px 5px"></span><span>' + esc(notShown) + '</span></li>' : '') +
          '<li><svg class="sw-tri" viewBox="0 0 16 13" aria-hidden="true"><path d="M8,1.5 L14.5,12 L1.5,12 Z" fill="#0c0d0f" stroke="#d7b46a" stroke-width="1.2"/></svg><span>Data warning: hover the country to read it</span></li>' +
        '</ul>' +
        (wiresOnly() ? '<li><span class="sw" style="background:radial-gradient(#8a7443 0.7px, #23262a 0.8px) 0 0/5px 5px"></span><span>Not drawn by country: the Distribution toggle shows the worldwide wires alone</span></li>' : '') +
        (offMap.length && !wiresOnly() ? '<p class="lg-note">' + plural(offMap.length, "monitored country", "monitored countries") + ' with active outlets have no outline at this map\u2019s scale and are missing from it altogether, not hatched: ' +
          esc(offMap.slice(0, 4).map(countryName).join(", ")) + (offMap.length > 4 ? ' and ' + plural(offMap.length - 4, "other") : '') +
          '. Their figures are in the ranked list, the Country search and both CSV exports.</p>' : '') +
        (worldwideLine ? '<p class="lg-note" id="legend-worldwide">' + esc(worldwideLine) + '</p>' : '') +
        (countryOnly() ? '<p class="lg-note" id="legend-distribution">Country outlets only: the worldwide wires are left out of every figure on this page while the Distribution toggle says so.</p>' : '') +
      '</div>';
  }

  function showTip(ev, f) {
    var tip = el("tooltip");
    var iso = f.iso;
    var name = (iso && state.names[iso]) || (f.properties && f.properties.name) || "Unknown";
    var p = iso && state._perIso && state._perIso[iso];
    var html = '<div class="t-name">' + esc(name) + '</div>';
    if (wiresOnly()) html += '<div class="muted">Not drawn by country: the Distribution toggle shows the worldwide wires alone. Choose Both or Country outlets to read this country.</div>';
    else if (!p) html += '<div class="muted">No monitored outlets. Absence of data, not absence of content.</div>';
    else {
      var e = p.entry, mv = p.mv, agg = state._agg || currentAgg();
      var k = agg.countries[iso] || C.emptyCounts();
      if (p.cls === "gap") html += '<div class="muted">Coverage gap: ' + esc(e.gap_reason) + '</div>';
      else if (p.cls === "inactive") html += '<div class="muted">' + e.outlets_total + ' outlets registered, none active.</div>';
      else {
        html += '<div>' + esc(metricLabel()) + (routeActive() ? ', ' + esc(lowerLabel(routeLabel(state.route))) : '') + ': <strong>' + (p.cls === "withheld" ? esc(relayText(relayFor(e), 0)) : C.formatValue(mv.value, metricDef().format) + (mv.provisional ? " (provisional)" : "")) + '</strong></div>';
        if (mv.note && mv.value === null) html += '<div class="muted">' + esc(mv.note) + '</div>';
        html += '<div class="muted">State origin ' + plural(mv.aAll, "placement") + ' of ' + plural(mv.underlying, "underlying item") + (agg.underlyingSummed ? ' (summed day by day)' : '') + '; unchecked state sourcing ' + esc(relayText(relayFor(e), mv.b)) + '; flagged for the model, not yet judged ' + mv.pending + '; independent ' + mv.c + (mv.undetermined ? '; undetermined, excluded from the counts, ' + mv.undetermined : '') + '; ' + esc(windowLabel()) + '</div>';
        html += '<div class="muted">' + e.outlets_active + ' active outlets, ' + e.feeds_ok + ' of ' + e.feeds_total + ' feeds healthy</div>';
        if (k.paywalled) html += '<div class="muted">' + plural(k.paywalled, "paywalled article") + ' unread: missing from every count, still counted in the monitored output denominator.</div>';
        if (k.polls && k.sat) html += '<div class="muted">' + k.sat + ' of ' + k.polls + ' feed polls came back full with nothing seen before; ' + esc(missedPhrase(k.miss, k.mpolls, k.polls)) + '.</div>';
        if (e.language_support === "none") html += '<div class="muted">No keyword list for ' + esc((e.languages || []).join(", ")) + ': only international and English terms are matched.</div>';
        if (p.cls === "nodata") html += '<div class="muted">No China coverage classified in this window.</div>';
        if (metricDef().allItems && mv.value !== null) html += '<div class="muted">Per 1,000 published items: ' + C.formatValue(mv.value * 1000, "dec") + '</div>';
        if (metricDef().allItems) html += '<div class="muted">' + mv.allItems + ' items published by ' + (e.top_outlets_ranked ? 'the ' + e.top_outlets + ' largest monitored outlets' : 'all ' + e.top_outlets + ' monitored outlets (no audience ranks are recorded)') + ', ' + mv.allItemsChina + ' China items among them</div>';
        /* latest.json counts the carrier and route over the 30 complete days before the export, the window the map opens on. */
        var carrierStart = state.latest && state.latest.window_start_30d;
        /* "mostly" only above half of the window's placements, and a tie as a tie (C.carrierTooltipLine, C.routeTooltipLine). */
        var carrierLine = C.carrierTooltipLine(e.top_carrier, carrierStart ? ' in the 30 complete days ending ' + C.shiftDate(carrierStart, 29) : '');
        var routeLine = C.routeTooltipLine(e.top_route, function (id) { return lowerLabel(contentRouteName(id)); });
        if (carrierLine) html += '<div class="muted">' + esc(carrierLine) + '</div>';
        if (routeLine) html += '<div class="muted">' + esc(routeLine) + '</div>';
        var noteText = iso && state.content && state.content.country_notes && state.content.country_notes[iso];
        if (noteText) html += '<div class="muted">' + esc(shortNoteText(noteText)) + '</div>';
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
  var BARS_SMALL = 25;
  function renderBars(agg) {
    var rows = rankedRows(agg);
    var fmt = metricDef().format || "int";
    var max = d3.max(rows, function (r) { return r.value || 0; }) || 1;
    var itemsMeasure = metricDef().measure;
    var showItems = (itemsMeasure === "a" || itemsMeasure === "b") && state.basis === "count" && !routeActive();
    el("bars-title").textContent = (metricDef().allItems ? outputShareLabel() : metricLabel()) + (routeActive() ? ", " + lowerLabel(routeLabel(state.route)) : "") + ", " + windowLabel() +
      (wiresOnly() ? ", worldwide wires only" : countryOnly() ? ", country outlets only" : "");
    var capEl = el("bars-caption");
    if (capEl) { var capText = caption("ranked_list"); capEl.textContent = capText || ""; capEl.hidden = !capText; }
    /* On a small screen the list is the default view and runs to every monitored country, so it opens on
       the top rows, the selected country kept, with a button for the rest. */
    var allRows = rows, cut = smallScreen() && !state.barsShowAll && rows.length > BARS_SMALL;
    if (cut) {
      rows = rows.slice(0, BARS_SMALL);
      var selRow = allRows.filter(function (r) { return r.iso === state.selected; })[0];
      if (selRow && rows.indexOf(selRow) === -1) rows.push(selRow);
    }
    el("bars").classList.toggle("per1000", !!metricDef().allItems);
    el("bars").innerHTML = rows.map(function (r) {
      var w = r.value ? Math.max(2, 100 * r.value / max) : 0;
      /* sparse keeps its own class: a value that could not be computed is never drawn as an observed zero. */
      var cls = r.fill === "value" ? "" : r.fill;
      var entry = state.latest.countries[r.iso] || {};
      var warns = countryWarnings(entry, agg);
      var num = r.fill === "withheld" ? relayText(relayFor(entry), 0) : fmt === "pct" ? valueText(r.value, state.metric) : C.formatValue(r.value, fmt);
      /* Article count and underlying-item count used to run together with only a CSS gap between
         them; spelled out as "290 articles, 211 underlying items" they cannot be misread as one number. */
      var itemsCount = itemsMeasure === "b" ? r.unverified_relay_underlying_items : r.state_origin_underlying_items;
      var numHtml = (showItems && r.value)
        ? esc(num) + ' articles<span class="items" title="' + esc(agg && agg.underlyingSummed ? C.UNDERLYING_SUMMED : 'Underlying items: syndicated placements of one item, and one item placed on several days of the window, count once') + '">, ' + esc(itemsCount) + ' underlying items</span>'
        : esc(num);
      return '<div class="bar-row" role="button" tabindex="0" aria-label="' + esc(r.name + ", " + num + (warns.length ? ", data warning" : "")) + '" data-iso="' + esc(r.iso) + '"><span>' + esc(r.name) + '</span><span><span class="bar ' + cls + '" style="width:' + (r.fill === "value" ? w : (r.fill === "nocoverage" ? 100 : 6)) + '%"></span></span>' +
        '<span class="num">' + numHtml + '</span>' +
        '<span class="bar-warn"' + (warns.length ? ' title="' + esc(warns.join("; ")) + '">!' : '>') + '</span></div>';
    }).join("") || '<p class="muted">' + (wiresOnly() ? 'The data holds no worldwide distribution stratum yet; the next export adds it.' : 'No countries in the dataset.') + '</p>';
    if (smallScreen() && allRows.length > BARS_SMALL) {
      el("bars").insertAdjacentHTML("beforeend", '<p class="bars-more"><button type="button" id="bars-more">' +
        (cut ? 'Show all ' + allRows.length + ' countries' : 'Show the top ' + BARS_SMALL + ' only') + '</button>' +
        (cut ? ' <span class="muted">Top ' + BARS_SMALL + ' of ' + allRows.length + ' shown.</span>' : '') + '</p>');
      el("bars-more").addEventListener("click", function () {
        state.barsShowAll = !state.barsShowAll;
        renderBars(state._agg || currentAgg());
        var again = el("bars-more");
        if (again) again.focus();
      });
    }
    Array.prototype.forEach.call(el("bars").querySelectorAll(".bar-row"), function (row) {
      function open() { selectCountry(row.getAttribute("data-iso")); }
      row.addEventListener("click", open);
      row.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") { ev.preventDefault(); open(); }
      });
    });
  }

  /* ------------------------------------------------------ who publishes it */
  /* Share of a category's articles by the political leaning or the ownership of the outlet that published
     it. All time, active outlets only, and without the global press release distribution services, which
     are not national newsrooms. Leaning is judged against each country's own political spectrum from a
     cited source; not assessed means no source was found, never a guess. Groups keep a fixed order and a
     fixed colour, so a filter never repaints a survivor. */
  var LEAN_GROUPS = {
    leaning: [["left", "Left", "var(--lean-left)"], ["centre_left", "Centre left", "var(--lean-cleft)"],
              ["centre", "Centre", "var(--lean-centre)"], ["centre_right", "Centre right", "var(--lean-cright)"],
              ["right", "Right", "var(--lean-right)"], ["state_controlled", "State controlled", "var(--lean-state)"],
              ["unassessed", "Not assessed", null]],
    ownership: [["private", "Private", "var(--own-private)"], ["public_service", "Public service", "var(--own-public)"],
                ["state_funded_international", "State funded international", "var(--own-intl)"],
                ["state", "State", "var(--own-state)"], ["party", "Party", "var(--own-party)"], ["unassessed", "Not assessed", null]],
    stance: [["state_controlled", "State controlled", "var(--lean-state)"], ["pro_government", "Pro-government", "var(--stance-pro)"],
             ["independent", "Independent", "var(--stance-ind)"], ["opposition", "Opposition", "var(--stance-opp)"],
             ["unassessed", "Not assessed", null]]
  };
  var LEAN_AXIS = {
    leaning: {key: "political_leaning", what: "political leaning", title: "Outlets by political leaning", noun: "leaning", head: "Leaning"},
    ownership: {key: "ownership", what: "ownership", title: "Outlets by ownership", noun: "ownership type", head: "Ownership"},
    stance: {key: "government_stance", what: "stance toward its own government", title: "Outlets by stance toward their government",
             noun: "stance toward government", head: "Stance"}
  };
  state.leanAxis = "leaning";
  function leanValue(o) {
    var c = o.counts || {}, m = state.measure;
    if (m === "a") return c.A || 0;
    if (m === "b") return c.B || 0;
    return (c.A || 0) + (c.B || 0) + (c.C || 0) + (c.pending || 0);
  }
  function leanModel() {
    var axis = state.leanAxis, key = (LEAN_AXIS[axis] || LEAN_AXIS.leaning).key;
    var iso = state.selected && String(state.selected).indexOf("name:") !== 0 ? state.selected : null;
    var outs = (state.outlets || []).filter(function (o) { return o.active && o.tier !== "distribution_wire" && (!iso || o.country === iso); });
    var rows = LEAN_GROUPS[axis].map(function (g) { return {id: g[0], label: g[1], color: g[2], articles: 0, outlets: 0, weak: 0}; });
    var byId = {}; rows.forEach(function (r) { byId[r.id] = r; });
    outs.forEach(function (o) {
      var r = byId[o[key]] || byId.unassessed;
      r.articles += leanValue(o); r.outlets += 1;
      /* A leaning outside the United States resting on a US based rater alone is counted, with a caution. */
      if (axis === "leaning" && r.id !== "unassessed" && C.leaningSourceWeak(o)) r.weak += 1;
    });
    var total = rows.reduce(function (t, r) { return t + r.articles; }, 0);
    rows.forEach(function (r) { r.share = total ? r.articles / total : 0; r.perOutlet = r.outlets ? r.articles / r.outlets : null; });
    return {axis: axis, iso: iso, rows: rows, total: total, outlets: outs.length,
            assessed: outs.filter(function (o) { return byId[o[key]] && o[key] !== "unassessed"; }).length,
            sources: axis === "leaning" ? C.leaningSources(outs) : null};
  }
  function renderLeaning() {
    if (!el("leaning")) return;
    var md = leanModel(), relay = relayFor(null), noun = measureNoun();
    var marked = relay.provisional && (state.measure === "b" || state.measure === "china");
    var ax = LEAN_AXIS[md.axis] || LEAN_AXIS.leaning, what = ax.what;
    el("leaning-title").textContent = ax.title;
    el("leaning-sub").textContent = noun.charAt(0).toUpperCase() + noun.slice(1) + (marked ? " (provisional)" : "") +
      " by the " + what + " of the outlet that published them, " + (md.iso ? "in " + countryName(md.iso) : "in every monitored country") +
      ", all time. " + md.assessed + " of " + md.outlets + " active outlets have a sourced " + ax.noun + "." +
      (wiresOnly() ? " The worldwide wires are left out here in every Distribution mode: a release has no publishing newsroom, so it has no " + ax.noun + "." : "");
    var leanCap = caption(md.axis === "stance" ? "stance_chart_note" : "leaning_chart_note");
    el("lean-note").textContent = leanCap ? leanCap + " A group with more outlets publishes more, so the per outlet column is the fairer comparison." +
      (md.axis === "stance" ? " A stance source older than a change of government is not used." : "") :
      "A share of articles partly counts outlets: a group with more monitored outlets publishes more, so the per outlet column is the fairer comparison. " +
      (md.axis === "leaning" ? "Leaning is judged against each country's own political spectrum. State controlled means a state owned outlet without editorial independence, whose line is the government's rather than a left or right one. " : "") +
      (md.axis === "stance" ? "Stance is toward the government of the outlet's own country, from a dated source such as Reporters Without Borders or Freedom House; a source older than a change of government is not used. " : "") +
      "Not assessed means no reliable source was found, never a guess.";
    /* Name the leaning sources plainly, and say what the international ownership group holds. */
    var leanExtra = md.axis === "leaning" ? C.leaningSourcesSentence(md.sources) :
      md.axis === "ownership" ? "State funded international means a broadcaster a government funds to reach audiences abroad, such as Radio Free Asia, RFE/RL, Deutsche Welle, RFI, France 24 or the BBC World Service language services, kept apart from domestic public service broadcasters such as the BBC at home, ABC or CBC. Outlets a state runs without editorial independence, at home or abroad, such as RT or Sputnik, stay under State." : "";
    if (leanExtra) el("lean-note").textContent += " " + leanExtra.trim();
    var svg = d3.select("#lean-pie"); svg.selectAll("*").remove();
    var defs = svg.append("defs");
    var pat = defs.append("pattern").attr("id", "lean-hatch").attr("patternUnits", "userSpaceOnUse").attr("width", 6).attr("height", 6).attr("patternTransform", "rotate(45)");
    pat.append("rect").attr("width", 6).attr("height", 6).attr("fill", "#1f2226");
    pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 6).attr("stroke", "#5d5a53").attr("stroke-width", 1.5);
    var table = el("lean-table");
    if (relayHidden()) {
      table.innerHTML = '<tbody><tr><td class="muted">' + esc(relayText(relay, 0).charAt(0).toUpperCase() + relayText(relay, 0).slice(1)) +
        ': unchecked state sourcing is not shown until hand coding clears it.</td></tr></tbody>';
      return;
    }
    var g = svg.append("g").attr("transform", "translate(120,120)");
    if (!md.assessed) {
      /* Before any outlet has a sourced leaning the chart would be one hatched ring, which reads as a
         finding. Say what is missing instead. */
      g.append("circle").attr("r", 87).attr("fill", "none").attr("stroke", "var(--rule-strong)").attr("stroke-width", 42).attr("stroke-dasharray", "2 5");
      g.append("text").attr("class", "lean-total-sub").attr("dy", "0.35em").text("Not assessed yet");
      table.innerHTML = '<tbody><tr><td class="muted">No outlet has a sourced ' + ax.what +
        ' yet. Each outlet is being researched against a cited source, and the chart fills in as that lands.</td></tr></tbody>';
      return;
    }
    if (!md.total) {
      g.append("circle").attr("r", 104).attr("fill", "none").attr("stroke", "var(--rule-strong)").attr("stroke-width", 1);
      g.append("text").attr("class", "lean-total-sub").attr("dy", "0.35em").text("None yet");
    } else {
      var arcs = d3.pie().sort(null).value(function (r) { return r.articles; }).padAngle(0.012)(md.rows);
      var arc = d3.arc().innerRadius(66).outerRadius(108).cornerRadius(2);
      g.selectAll("path").data(arcs.filter(function (a) { return a.data.articles > 0; })).enter().append("path")
        .attr("class", "slice").attr("d", arc).attr("data-id", function (a) { return a.data.id; })
        .style("fill", function (a) { return a.data.color || "url(#lean-hatch)"; })
        .attr("stroke", "var(--ink)").attr("stroke-width", 2)
        .on("mousemove", function (ev, a) { showLeanTip(ev, a.data, md); highlightLean(a.data.id); })
        .on("mouseleave", function () { el("lean-tip").style.display = "none"; highlightLean(null); });
      g.append("text").attr("class", "lean-total").attr("dy", "0.1em").text(md.total.toLocaleString("en-US"));
      g.append("text").attr("class", "lean-total-sub").attr("dy", "2.1em").text(state.measure === "china" ? "China articles" : "articles");
    }
    var fmtPer = function (v) { return v === null ? "n/a" : v.toFixed(v < 10 ? 2 : 1); };
    table.innerHTML = '<thead><tr><th></th><th>' + ax.head + '</th><th class="num">Articles</th><th class="num">Share</th><th class="num">Outlets</th><th class="num">Per outlet</th></tr></thead><tbody>' +
      md.rows.map(function (r) {
        var sw = r.color ? "background:" + r.color : "background:var(--lean-hatch)";
        return '<tr data-id="' + esc(r.id) + '" class="' + (r.articles ? "" : "zero") + '" tabindex="0">' +
          '<td><span class="lean-sw" style="' + sw + '"></span></td><td>' + esc(r.label) +
          (r.weak ? '<span class="lean-caveat">' + esc(C.leaningWeakCaveat(r.weak, r.outlets)) + '</span>' : '') + '</td>' +
          '<td class="num">' + r.articles.toLocaleString("en-US") + '</td><td class="num">' + (md.total ? pctText(r.share) : "n/a") + '</td>' +
          '<td class="num">' + r.outlets + '</td><td class="num">' + fmtPer(r.perOutlet) + '</td></tr>';
      }).join("") + '</tbody>';
    Array.prototype.forEach.call(table.querySelectorAll("tbody tr"), function (tr) {
      var id = tr.getAttribute("data-id");
      tr.addEventListener("mouseenter", function () { highlightLean(id); });
      tr.addEventListener("mouseleave", function () { highlightLean(null); });
      tr.addEventListener("focus", function () { highlightLean(id); });
      tr.addEventListener("blur", function () { highlightLean(null); });
    });
  }
  function highlightLean(id) {
    d3.selectAll("#lean-pie .slice").classed("dim", function () { return id !== null && this.getAttribute("data-id") !== id; });
  }
  function showLeanTip(ev, r, md) {
    var tip = el("lean-tip"), sec = el("leaning").getBoundingClientRect();
    tip.innerHTML = '<div><strong>' + esc(r.label) + '</strong></div>' +
      '<div>' + r.articles.toLocaleString("en-US") + ' ' + esc(measureNoun()) + ', ' + pctText(r.share) + ' of the total</div>' +
      '<div class="muted">' + plural(r.outlets, "active outlet") + (r.perOutlet === null ? "" : ', ' + r.perOutlet.toFixed(2) + ' per outlet') + '</div>' +
      (r.weak ? '<div class="muted">' + esc(C.leaningWeakCaveat(r.weak, r.outlets)) + '</div>' : '');
    tip.style.display = "block";
    var x = ev.clientX - sec.left + 14, y = ev.clientY - sec.top + 14;
    if (x + 280 > sec.width) x = ev.clientX - sec.left - 290;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }

  /* ---------------------------------------------------------------- themes */
  /* Cells are shaded by the number of articles in them, on the map's own steps and swatches, so the two
     charts read the same way and the key says how many articles each shade needs for the measure shown.
     The share of the row each cell holds is in its tooltip. Like the map, the tiers are pooled over every
     day of the theme timeline, so moving the counter's day never changes what a shade means. */
  /* Dark ink from Lab lightness 55 up: between 55 and 62 the light ink fell under 4.5:1 on the mid reds. */
  function inkOn(color) { return d3.lab(color).l > 55 ? "#0c0d0f" : "#f3ede2"; }
  var THEME_ROWS = 15;
  var themeCapCache = {};
  function themeKey(iso) {
    var mi = measureIndex(), ids = ((state.meta && state.meta.themes) || []).map(function (c) { return c.id; });
    var w = iso ? 1 : windowArg(state.themeWindow);
    var k = [mi, iso ? "day:" + iso : state.themeWindow, (state.meta && state.meta.generated_at) || ""].join("|");
    k += "|" + (loadedFrom() || "");
    if (!themeCapCache[k]) themeCapCache[k] = C.themeScaleCap(state.months, scaleDays(w === null ? "all" : w), w, mi, ids, iso || null);
    var sc = themeCapCache[k];
    var key = keyFor(sc.cap || sc.max || 1, C.WHOLE_UNIT);
    key.max = sc.max;
    return key;
  }
  function themeKeyItems(key) {
    return key.tiers.map(function (t) { return '<span class="tl-i"><span class="sw" style="background:' + STEP_COLORS[t.index] + '"></span>' + esc(t.label) + '</span>'; }).join("");
  }
  function measureNoun() { return measureNounFor(state.measure); }
  function measureNounFor(m) { return {a: "state origin articles", b: "unchecked state sourcing articles", china: "China articles"}[m] || "articles"; }
  /* The theme grid, the theme focus panel and the theme curve all read the fourth theme slot, which is the
     same unchecked state sourcing count the map hatches. One test governs all of them. */
  function relayHidden() { return state.measure === "b" && !relayFor(null).visible; }
  function measureIndex() { var i = C.MEASURE_INDEX[state.measure]; return i === undefined ? 2 : i; }
  function denominator(k, mi) {
    k = k || C.emptyCounts();
    if (mi === 0) return k.A + k.B + k.C + (k.pending || 0);
    if (mi === 3) return k.B;
    return k.A;
  }
  /* One day's world total from global_series.json, for a day whose month has not loaded: the measure slot
     as denominator() reads it, under a Distribution mode (the series carries the wires as wld). null when
     the series lacks the day. */
  function seriesTotal(d, mi, distribution) {
    var s = state.seriesByDate[d];
    if (!s) return null;
    var pick = function (k) { k = k || {}; return mi === 0 ? (k.A || 0) + (k.B || 0) + (k.C || 0) + (k.pending || 0) : mi === 3 ? (k.B || 0) : (k.A || 0); };
    var all = pick(s), wld = pick(s.wld);
    return distribution === "wires" ? wld : distribution === "country" ? all - wld : all;
  }
  function pctText(share) { return share >= 0.995 ? "100%" : (share < 0.005 && share > 0 ? "<1%" : Math.round(100 * share) + "%"); }

  function themeModel(agg) {
    var catalog = (state.meta && state.meta.themes) || [];
    var byIso = C.aggregateThemes(state.months, state.themeEnd, windowArg(state.themeWindow), state.distribution);
    var mi = measureIndex();
    var rows = [];
    var world = {name: wiresOnly() ? "Worldwide wires" : countryOnly() ? "All monitored countries, country outlets only" : "All monitored countries", n: 0, t: {}};
    Object.keys(byIso).forEach(function (iso) {
      var t = {}, most = 0;
      catalog.forEach(function (c) {
        var v = (byIso[iso][c.id] || [0, 0, 0, 0])[mi];
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
    var themeCap = caption("theme_counter_intro");
    /* The plain caption explains how tagging works; with it, only the live line (what is shown, for
       which window) follows, so the reader does not get the same explanation twice. */
    var themeLive = "Showing " + noun + (state.measure !== "a" && relayFor(null).provisional ? " (provisional)" : "") + " by theme" + (state.selected ? " in " + countryName(state.selected) : "") + ", " + themeWindowLabel() + "." +
      (routeActive() ? " Themes are not split by route, so the route filter does not apply here." : "") +
      (wiresOnly() ? " Distribution: the worldwide wires alone." : countryOnly() ? " Distribution: the country outlets alone." : "");
    el("themes-sub").textContent = themeCap ? themeCap + " " + themeLive :
      noun.charAt(0).toUpperCase() + noun.slice(1) + (state.measure !== "a" && relayFor(null).provisional ? " (provisional)" : "") + " by theme" + (state.selected ? " in " + countryName(state.selected) : "") + ", " + themeWindowLabel() +
      ". Tags read the headline, the feed summary and the whole body. An article can carry more than one theme, so theme counts do not add up to the number of articles." +
      (langs.length ? " Theme terms exist in " + langs.length + " languages; articles in other languages are matched on English terms only." : "") +
      (routeActive() ? " Themes are not split by route, so the route filter does not apply here." : "");
    if (relayHidden()) {
      el("theme-focus").innerHTML = '<p class="muted">' + esc(relayFor(null).measured ? C.RELAY_WITHHELD : C.RELAY_NOT_MEASURED) + '</p>';
      el("theme-grid").innerHTML = "";
      el("theme-legend").innerHTML = "";
      el("theme-grid").classList.remove("days");
      return;
    }
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
      (selName && !sel ? '<p class="muted">No ' + esc(noun) + ' for this country in ' + esc(themeWindowLabel()) + '. The grid beside this shows its own days, so anything it has outside this window appears there. Switch to Whole world to compare countries.</p>' :
      '<ul class="tf-list">' + list.map(function (x) {
        var share = focus.n ? x.v / focus.n : 0;
        return '<li data-theme="' + esc(x.c.id) + '"' + (state.themeSort === x.c.id ? ' class="active"' : '') + ' tabindex="0" role="button" aria-pressed="' + (state.themeSort === x.c.id) + '" title="Rank the countries by ' + esc(lowerLabel(x.c.label)) + '">' +
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
    var tkey = themeKey(null), heat = shadeFor(tkey);
    el("theme-grid").innerHTML =
      '<thead><tr><th class="c-country" scope="col">Country</th><th class="c-n" scope="col">' + esc(noun.charAt(0).toUpperCase() + noun.slice(1)) + '</th>' +
      catalog.map(function (c) {
        return '<th scope="col" class="c-theme' + (sortKey === c.id ? ' sorted' : '') + '" data-theme="' + esc(c.id) + '" tabindex="0"' + (sortKey === c.id ? ' aria-sort="descending"' : '') + ' title="' + esc(c.label) + ': rank the countries by it">' + esc(c.short) + '</th>';
      }).join("") + '</tr></thead><tbody>' +
      shown.map(function (r) {
        return '<tr data-iso="' + esc(r.iso) + '"' + (r.iso === state.selected ? ' class="selected"' : '') + '>' +
          '<th scope="row" class="c-country" tabindex="0" title="Open ' + esc(r.name) + '">' + esc(r.name) + '</th><td class="c-n">' + r.n + '</td>' +
          catalog.map(function (c) {
            var v = r.t[c.id] || 0, share = Math.min(1, v / Math.max(r.n, 1)), bg = v > 0 ? heat(v) : null;
            return '<td class="cell" data-theme="' + esc(c.id) + '" data-v="' + v + '" data-share="' + share.toFixed(4) + '"' + (bg ? ' style="background:' + bg + ';color:' + inkOn(bg) + '"' : '') + '>' + (v || "") + '</td>';
          }).join("") + '</tr>';
      }).join("") + '</tbody>';
    updateGridScroll();
    el("theme-legend").innerHTML =
      '<span class="tl-title">Cell shade: number of ' + esc(noun) + ' in the theme, ' + esc(themeWindowLabel()) + '</span>' +
      themeKeyItems(tkey) +
      '<span class="tl-foot">Same steps and colors as the map. Until the next data update, the tiers are fixed for this measure and window across every day of the theme timeline' +
      (tkey.max > tkey.cap ? '; the darkest shade holds everything above the 95th percentile, up to ' + tkey.max.toLocaleString("en-US") : '') +
      '. ' + esc(C.CAP_UPDATE_NOTE) + esc(partialScaleNote()) + ' Hover a cell for its share of the country’s ' + esc(noun) + '. The ' + Math.min(THEME_ROWS, rows.length) + ' countries with the most ' + esc(noun) +
      (sortKey ? ' in ' + esc(lowerLabel(label[sortKey].label)) : '') + ' in this window' +
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
      catalog.forEach(function (c) { var v = (t[c.id] || [0, 0, 0, 0])[mi]; vals[c.id] = v; most = Math.max(most, v); });
      return {d: d, n: Math.max(denominator((e.countries || {})[iso], mi), most), t: vals};
    });
    var total = function (id) { return (sel && sel.t[id]) || 0; };
    var order = catalog.filter(function (c) { return c.id !== "other"; }).sort(function (a, b) { return total(b.id) - total(a.id); });
    if (label.other) order.push(label.other);
    /* Shaded by the number of articles, on the map's steps: darker always means more. A share of a
       day's articles would paint a day with one article as dark as the busiest day. The tiers come from
       this country's single days across the whole timeline, so they hold still while the day moves. */
    var dkey = themeKey(iso), shade = shadeFor(dkey);
    var grid = el("theme-grid");
    grid.classList.add("days");
    grid.innerHTML =
      '<thead><tr><th class="c-country" scope="col">Theme</th><th class="c-n" scope="col">Window</th>' +
      perDay.map(function (p) {
        return '<th scope="col" class="c-day' + (p.d === state.themeEnd ? ' sorted' : '') + '" title="' + p.d + ': ' + p.n + ' ' + esc(noun) + '">' + p.d.slice(8) + '</th>';
      }).join("") + '</tr></thead><tbody>' +
      order.map(function (c) {
        return '<tr data-theme-row="' + esc(c.id) + '"' + (state.themeSort === c.id ? ' class="selected"' : '') + '>' +
          '<th scope="row" class="c-country" tabindex="0" title="Mark this theme">' + esc(c.label) + '</th><td class="c-n">' + total(c.id) + '</td>' +
          perDay.map(function (p) {
            var v = p.t[c.id] || 0, share = Math.min(1, v / Math.max(p.n, 1)), bg = v > 0 ? shade(v) : null;
            return '<td class="cell" data-theme="' + esc(c.id) + '" data-day="' + esc(p.d) + '" data-n="' + p.n + '" data-v="' + v + '" data-share="' + share.toFixed(4) + '"' + (bg ? ' style="background:' + bg + ';color:' + inkOn(bg) + '"' : '') + '>' + (v || "") + '</td>';
          }).join("") + '</tr>';
      }).join("") + '</tbody>';
    updateGridScroll();
    el("theme-legend").innerHTML =
      '<span class="tl-title">Cell shade: number of ' + esc(noun) + ' in the theme that day, ' + esc(name) + '</span>' +
      themeKeyItems(dkey) +
      '<span class="tl-foot">Same steps and colors as the map. Until the next data update, the tiers are fixed for this measure and country across every day of the timeline' +
      (dkey.max > dkey.cap ? '; the darkest shade holds everything above the 95th percentile, up to ' + dkey.max.toLocaleString("en-US") : '') +
      '. ' + esc(C.CAP_UPDATE_NOTE) + esc(partialScaleNote()) + ' The ' + perDay.length + ' days ending ' + esc(state.themeEnd || "") + ' (day of the month across the top). The Window column counts ' + esc(themeWindowLabel()) + '. Switch to Whole world to compare countries.</span>';
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
        syncPressed();
        if (demandThemeMonths()) return;
        renderThemes(); renderLeaning();
      });
    });
    el("theme-grid-wrap").addEventListener("scroll", updateGridScroll);
    window.addEventListener("resize", updateGridScroll);
    /* Every theme, heading and row that answers a click is focusable and answers Enter and Space too;
       the redraw replaces them, so the focus is put back on the same one. */
    function toggleSort(id, refocus) {
      state.themeSort = state.themeSort === id ? null : id; renderThemes(); renderLeaning();
      if (refocus) { var again = document.querySelector(refocus); if (again) again.focus(); }
    }
    function cssId(v) { return String(v).replace(/[^A-Za-z0-9_:-]/g, ""); }
    function focusAction(ev, kb) {
      var li = ev.target.closest("li[data-theme]");
      if (li) { toggleSort(li.getAttribute("data-theme"), kb ? '#theme-focus li[data-theme="' + cssId(li.getAttribute("data-theme")) + '"]' : null); return true; }
      return false;
    }
    function gridAction(ev, kb) {
      var th = ev.target.closest("th[data-theme]");
      if (th) { toggleSort(th.getAttribute("data-theme"), kb ? '#theme-grid th[data-theme="' + cssId(th.getAttribute("data-theme")) + '"]' : null); return true; }
      var row = ev.target.closest("tr[data-theme-row]");
      if (row) { toggleSort(row.getAttribute("data-theme-row"), kb ? '#theme-grid tr[data-theme-row="' + cssId(row.getAttribute("data-theme-row")) + '"] th' : null); return true; }
      var tr = ev.target.closest("tr[data-iso]");
      if (tr) { selectCountry(tr.getAttribute("data-iso")); return true; }
      return false;
    }
    function onKey(action) {
      return function (ev) {
        if (ev.key !== "Enter" && ev.key !== " " && ev.key !== "Spacebar") return;
        if (!ev.target.matches || !ev.target.matches("li[data-theme], th[data-theme], th.c-country")) return;
        if (action(ev, true)) ev.preventDefault();
      };
    }
    el("theme-focus").addEventListener("click", function (ev) { focusAction(ev, false); });
    el("theme-focus").addEventListener("keydown", onKey(focusAction));
    el("theme-grid").addEventListener("click", function (ev) { gridAction(ev, false); });
    el("theme-grid").addEventListener("keydown", onKey(gridAction));
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
    /* A row the Distribution toggle hides is never opened under it: the toggle goes back to Both first.
       The quiet switch leaves the renders below to this function, which then also redraws the two views
       it does not otherwise touch, the sparkline and the findings with their KPI cards. */
    var toBoth = !!state.selected && !C.distributionKeeps(state.distribution, state.selected);
    if (toBoth) setDistribution("both", true);
    syncCountryPicks();
    renderMap();
    if (toBoth) renderSpark();
    renderThemes(); renderLeaning();
    renderPanel();
    if (toBoth) renderFindings();
    state._revealed = state.selected ? revealPanel(false) : false;
  }
  /* On a small screen the panel sits below the map and every row of the ranked list, thousands of pixels
     down, so a country opened from above it would change nothing in view. When the panel's top is below
     the fold, or force says so, it is scrolled into view and its heading takes the focus. Returns whether
     it did. */
  function revealPanel(force) {
    var panel = el("panel");
    if (!panel || !panel.getBoundingClientRect) return false;
    var top = panel.getBoundingClientRect().top, vh = window.innerHeight || document.documentElement.clientHeight || 0;
    if (!force && !(top > vh - 48)) return false;
    var smooth = !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    if (panel.scrollIntoView) panel.scrollIntoView({behavior: smooth ? "smooth" : "auto", block: "start"});
    var h = panel.querySelector("h2");
    if (h) { h.setAttribute("tabindex", "-1"); try { h.focus({preventScroll: true}); } catch (e) { h.focus(); } }
    return true;
  }
  function countryName(iso) { return String(iso).indexOf("name:") === 0 ? String(iso).slice(5) : (state.names[iso] || iso); }
  /* The day the relay on the owner's machine was stopped (meta.relay_collector.retired_on), or null. */
  function relayRetiredOn() { return ((state.meta || {}).relay_collector || {}).retired_on || null; }
  /* The Country search boxes above the map, the ranked list and the theme counter open the same single
     country view. Typing filters the monitored countries, ignoring accents and full stops, ranked: an exact
     ISO code, name or common alias first ("us" is the United States, "uk" the United Kingdom), then names
     that start with the query, then names or aliases with a word that starts with it, then any that
     contain every typed word; alphabetical within each rank. */
  var EXACT_ALIASES = {us: "USA", usa: "USA", america: "USA", "united states": "USA", "united states of america": "USA",
    uk: "GBR", britain: "GBR", "great britain": "GBR", "united kingdom": "GBR", england: "GBR",
    holland: "NLD", "the netherlands": "NLD", prc: "CHN", "peoples republic of china": "CHN", "mainland china": "CHN",
    uae: "ARE", drc: "COD", "south korea": "KOR", "north korea": "PRK", burma: "MMR", "ivory coast": "CIV", czechia: "CZE", turkiye: "TUR"};
  var COUNTRY_ALIASES = {USA: "usa us u.s. u.s.a. america united states", GBR: "uk u.k. britain great britain england", NLD: "holland netherlands", CHN: "prc p.r.c. people's republic mainland china",
    KOR: "south korea", PRK: "north korea",
    RUS: "russia", VNM: "vietnam", IRN: "iran", TWN: "taiwan", CZE: "czech republic czechia", TUR: "turkey turkiye", BOL: "bolivia",
    VEN: "venezuela", TZA: "tanzania", SYR: "syria", LAO: "laos", MDA: "moldova", COD: "drc congo kinshasa", COG: "congo brazzaville",
    CIV: "ivory coast", ARE: "uae emirates", HKG: "hong kong", MAC: "macau macao", PSE: "palestine", MMR: "burma myanmar", SWZ: "swaziland eswatini",
    WLD: "worldwide distribution press release wires pr newswire globenewswire world"};
  function foldText(t) { return String(t).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }
  function searchEntry(iso, name, official) {
    return {iso: iso, name: name, fold: foldText(name), key: foldText(name + " " + iso + " " + (official || "") + " " + (COUNTRY_ALIASES[iso] || ""))};
  }
  /* The panel's notes on a country's languages. A language a project rule excludes (latest.json
     excluded_languages, a list or a {language: outlets} map) is not read at all, so it gets its own
     sentence, never the keyword list one. The export caps support at partial for an excluded language
     alone, so with one present the keyword sentence is kept only for support of none, which no cap makes. */
  function languageName(code) {
    try { if (typeof Intl !== "undefined" && Intl.DisplayNames) { var n = new Intl.DisplayNames(["en"], {type: "language"}).of(String(code)); if (n && n !== String(code)) return n; } } catch (e) { /* an unknown code reads as itself */ }
    return String(code);
  }
  function languageNotes(entry) {
    var out = [], ex = entry && entry.excluded_languages;
    var excluded = Array.isArray(ex) ? ex.filter(Boolean) : ex && typeof ex === "object" ? Object.keys(ex) : [];
    if (excluded.length) out.push("Outlets in " + excluded.map(languageName).join(", ") + " are registered but not monitored (project rule), so their coverage is not counted.");
    var support = entry && entry.language_support;
    if (support && support !== "full" && (!excluded.length || support === "none"))
      out.push("Keyword lists cover " + (support === "none" ? "none" : "only some") + " of this country's outlet languages (" + ((entry.languages || []).join(", ")) + "); the rest are matched on international and English terms only.");
    return out;
  }
  function rankCountryMatches(entries, q) {
    var bare = q.replace(/[.'’]/g, "").replace(/\s+/g, " ").trim();
    var words = q.replace(/[,()]/g, " ").split(/\s+/).filter(Boolean);
    var exact = EXACT_ALIASES[bare] || null;
    var ranked = [];
    entries.forEach(function (e) {
      var r;
      if (!q) r = 3;
      else if (e.iso.toLowerCase() === bare || e.iso === exact || e.fold === q || e.fold.replace(/[.'’]/g, "") === bare) r = 0;
      else if (e.fold.indexOf(q) === 0) r = 1;
      else if ((" " + e.key.replace(/[,()]/g, " ")).indexOf(" " + q) !== -1) r = 2;
      /* Every typed word must appear somewhere, in any order, so "republic of korea" finds "Korea, Republic of". */
      else if (words.every(function (w) { return e.key.indexOf(w) !== -1; })) r = 3;
      else return;
      ranked.push({e: e, r: r});
    });
    ranked.sort(function (x, y) { return x.r - y.r || x.e.name.localeCompare(y.e.name); });
    return ranked.map(function (x) { return x.e; });
  }
  function setupCountryPicks() {
    var entries = Object.keys((state.latest && state.latest.countries) || {}).map(function (iso) {
      return searchEntry(iso, String(countryName(iso)), state.officialNames[iso]);
    }).sort(function (x, y) { return x.name.localeCompare(y.name); });
    Array.prototype.forEach.call(document.querySelectorAll("[data-country-pick]"), function (box) {
      var input = box.querySelector("input"), list = box.querySelector("ul"), matches = [], active = -1;
      function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); active = -1; }
      function draw() {
        var q = foldText(input.value.trim());
        matches = rankCountryMatches(entries, q);
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
      /* The focus stays in the box, so a keyboard reader can search again; only a panel that had to be
         scrolled into view takes it (revealPanel). */
      function choose(iso) { close(); selectCountry(iso); restore(); if (!state._revealed && document.activeElement === input) input.select(); }
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
        var top = rankedRows().filter(function (r) { return r.value > 0; })[0];
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
    syncPressed();
  }

  /* The rows that, with the label rows above them, account for every article that passed the relevance
     gate in these counts (meta.gate_accounting is the same by status, to the export): what could not be
     read, what was read and waits for the rules stage, and what is not yet fetched. fetched counts every
     article read, classified and waiting included, so the read articles still waiting are fetched less
     the classified and the pending ones. */
  function gateRows(t) {
    var waiting = (t.fetched || 0) - (t.cls || 0) - (t.pending || 0);
    var unfetched = (t.rel || 0) - (t.fetched || 0) - (t.paywalled || 0) - (t.failed || 0) - (t.blocked || 0);
    var rows = [["Fetch failed after retries or page unreadable, unread and uncounted", t.failed || 0],
                ["Blocked by the site's robots.txt, unread and uncounted", t.blocked || 0],
                ["Fetched, waiting for the rules stage, not yet counted", waiting]];
    if (unfetched) rows.push(["Not yet fetched, unread and uncounted", unfetched]);
    return rows;
  }
  /* The sentence under a table that says its rows add up. While unchecked state sourcing is withheld the
     total is not printed, since the total less the other rows would give the withheld count back. */
  function gateSumText(t, relay, where, lead) {
    var text = !relay.visible
      ? "With the withheld unchecked state sourcing count, these rows add up to every article that passed the relevance gate" + where + "; the total is not printed while that count is withheld, since it would give the count back."
      : "These rows add up to the " + intComma(t.rel || 0) + " articles that passed the relevance gate" + where + ", each placement counted once.";
    return lead ? lead + text.charAt(0).toLowerCase() + text.slice(1) : text;
  }
  /* The world table's second sentence: the export's own count of stored rows, which counts every copy. */
  function gateStoredText() {
    var ga = state.meta && state.meta.gate_accounting;
    if (!ga || !ga.copies_counted_once) return "";
    return " To the last export " + intComma(ga.total) + " stored rows passed the gate; " + intComma(ga.copies_counted_once) +
      " of them are further copies of one placement (a shared widget page, the same page stored twice, a numbered series) and are counted with it.";
  }

  var RELEASE_LABELS = {found: "found", none_found: "none", blocked: "blocked", unreachable: "unreachable", not_searched: "not searched"};

  function renderPanel() {
    var body = el("panel-body");
    var relayAll = relayFor(null);
    if (!state.selected) {
      var wldEntry = (state.latest.countries || {})[C.WORLDWIDE_ISO];
      /* Summed from the daily files through the map's last complete day, as the cards are; latest.json's
         all time totals, which also count the partial export day, are the fallback. */
      var atp = allTimeFindings(state.meta && state.meta.findings);
      var t = atp.totals ? C.distributionTotals(atp.totals, atp.wld, state.distribution)
        : C.distributionTotals((state.latest.totals && state.latest.totals.all_time) || null, wldEntry && wldEntry.all_time, state.distribution);
      var totalsSpan = atp.totals ? ", through " + atp.through : "";
      var totalsHead = wiresOnly() ? "Worldwide wires, all time" + totalsSpan : countryOnly() ? "All monitored countries, all time" + totalsSpan + ", country outlets only" : "All monitored countries, all time" + totalsSpan;
      var introText = caption("side_panel_labels") || "Click a country on the map, type one into the Country search, or open a row in the ranked list, which is shown on small screens and can be reached with the keyboard. The country view carries its time series, category breakdown, routes, monitored outlets and recent classified articles.";
      var top3 = rankedRows().filter(function (r) { return r.value; }).slice(0, 3);
      body.innerHTML = '<h2>Select a country</h2><p class="muted">' + esc(introText) + '</p>' +
        (top3.length ? '<div class="panel-shortcuts" id="panel-shortcuts"><span class="ps-label">Jump to the top ' + top3.length + ':</span>' +
          top3.map(function (r) { return '<button type="button" class="ps-btn" data-iso="' + esc(r.iso) + '">' + esc(r.name) + '</button>'; }).join("") + '</div>' : '') +
        (t ? '<h3>' + esc(totalsHead) + '</h3><table><tr><th>' + esc(clearerLabel("A", "State origin")) + '</th><td class="num">' + t.A + '</td></tr>' +
          '<tr class="undetermined-row"><th>' + esc(UNDETERMINED_ROW) + '</th><td class="num">' + (t.U || 0) + '</td></tr>' +
          '<tr><th>' + esc(clearerLabel("B", "Unchecked state sourcing")) + '</th><td class="num' + (relayAll.publishable ? '' : ' relay-state') + '">' + esc(relayText(relayAll, t.B)) + '</td></tr><tr><th>' + esc(clearerLabel("pending", "Official Chinese sourcing, verification pending")) + '</th><td class="num">' + t.pending + '</td></tr><tr><th>' + esc(clearerLabel("C", "Independent journalism")) + '</th><td class="num">' + t.C + '</td></tr><tr><th>' + esc(clearerLabel("N", "Not relevant")) + '</th><td class="num">' + t.N + '</td></tr><tr><th>' + esc(clearerLabel("paywalled", "Paywalled, unread and uncounted")) + '</th><td class="num">' + t.paywalled + '</td></tr>' +
          gateRows(t).map(function (r) { return '<tr><th>' + esc(r[0]) + '</th><td class="num">' + r[1] + '</td></tr>'; }).join("") +
          (relayAll.visible ? '<tr><th>Passed the relevance gate</th><td class="num"><strong>' + (t.rel || 0) + '</strong></td></tr>' : '') + '</table>' +
          '<p class="panel-note">' + esc(gateSumText(t, relayAll, totalsSpan)) + esc(gateStoredText()) + '</p>' : '<p class="muted">No totals available.</p>');
      Array.prototype.forEach.call(body.querySelectorAll(".ps-btn"), function (btn) {
        btn.addEventListener("click", function () { selectCountry(btn.getAttribute("data-iso")); });
      });
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
    var countryNote = state.content && state.content.country_notes && state.content.country_notes[iso];
    if (countryNote) html += '<div class="panel-note-box"><h3>Why this number</h3><p>' + linkifyHtml(countryNote) + '</p></div>';
    var relay = relayFor(entry);
    var mv = withOutputGap(C.metricValue(agg.countries[iso], state.metric, entry.outlets_active, state.mode, agg.reviewed[iso], ctxFor(iso, entry, agg)), entry);
    /* A share of monitored output names what it divides by in this country, and reads per 1,000 items. */
    var shareHead = metricDef().allItems ? measureNounFor(metricDef().measure).replace(/^./, function (c) { return c.toUpperCase(); }) + ' per 1,000 items published by ' +
      (entry.top_outlets_ranked ? 'its ' + (entry.top_outlets || '') + ' audience ranked outlets' : 'all ' + (entry.top_outlets || entry.outlets_active || '') + ' active monitored outlets') : null;
    html += '<p>' + esc(shareHead || metricLabel()) + (routeActive() ? ', ' + esc(lowerLabel(routeLabel(state.route))) : '') + ', ' + esc(windowLabel()) + ': <strong>' + (mv.withheld ? esc(relayText(relay, 0)) : (metricDef().allItems ? valueText(mv.value, state.metric).replace(/ per 1,000$/, '') : C.formatValue(mv.value, metricDef().format)) + (mv.provisional ? " (provisional)" : "")) + '</strong></p>';
    if (mv.note && mv.value === null) html += '<p class="panel-note">' + esc(mv.note) + '</p>';
    /* The time series reads the loaded months; before the rest have loaded it says where it starts. */
    var miniFrom = loadedFrom();
    html += miniFrom === null ? '<h3>Time series, all days</h3><svg class="mini" id="mini"></svg>'
      : '<h3>Time series, from ' + esc(tlDays.filter(function (d) { return d >= miniFrom; })[0] || miniFrom) + '</h3><svg class="mini" id="mini"></svg>' +
        '<p class="panel-note">Earlier days have not loaded yet. <button type="button" class="mini-load" id="mini-load">Load every day</button></p>';
    var k = agg.countries[iso] || C.emptyCounts();
    var rv = agg.reviewed[iso] || {A: 0, B: 0, C: 0, N: 0};
    /* relay.visible governs every relay figure in this panel: the breakdown, the outlet table below it and
       the mini chart. They used to disagree, one printing counts while the next printed "withheld". */
    var relayNote = relay.provisional ? ' (provisional)' : '';
    var relayCells = relay.visible
      ? '<td class="num">' + k.B + '</td><td class="num">' + k.Br + '</td><td class="num">' + k.Bl + '</td><td class="num">' + rv.B + '</td>'
      : '<td class="num relay-state" colspan="4">' + esc(relayText(relay, 0)) + '</td>';
    html += '<h3>Breakdown, ' + esc(windowLabel()) + '</h3><table><tr><th></th><th class="num">All</th><th class="num">Rules</th><th class="num">Model</th><th class="num">Human</th></tr>' +
      '<tr><td>' + esc(clearerLabel("A", "State origin")) + '</td><td class="num">' + k.A + '</td><td class="num">' + k.Ar + '</td><td class="num">' + k.Al + '</td><td class="num">' + rv.A + '</td></tr>' +
      '<tr><td class="muted"' + (agg.underlyingSummed ? ' title="' + esc(C.UNDERLYING_SUMMED) + '"' : '') + '>Underlying items among them' + (agg.underlyingSummed ? ' (summed day by day)' : '') + '</td><td class="num">' + k.uniqA + '</td><td></td><td></td><td></td></tr>' +
      '<tr class="undetermined-row"><td>' + esc(UNDETERMINED_ROW) + '</td><td class="num">' + (k.U || 0) + '</td><td class="num"></td><td class="num">' + (k.U || 0) + '</td><td class="num"></td></tr>' +
      '<tr><td>' + esc(clearerLabel("B", "Unchecked state sourcing")) + relayNote + '</td>' + relayCells + '</tr>' +
      '<tr><td>' + esc(clearerLabel("pending", "Official Chinese sourcing, verification pending")) + '</td><td class="num">' + k.pending + '</td><td class="num">' + k.pending + '</td><td class="num"></td><td class="num"></td></tr>' +
      '<tr><td>' + esc(clearerLabel("C", "Independent journalism")) + '</td><td class="num">' + k.C + '</td><td class="num"></td><td class="num"></td><td class="num">' + rv.C + '</td></tr>' +
      '<tr><td>' + esc(clearerLabel("N", "Not relevant")) + '</td><td class="num">' + k.N + '</td><td class="num"></td><td class="num"></td><td class="num">' + rv.N + '</td></tr>' +
      '<tr><td>Paywalled, unread and uncounted</td><td class="num">' + k.paywalled + '</td><td colspan="3"></td></tr>' +
      gateRows(k).map(function (r) { return '<tr><td>' + esc(r[0]) + '</td><td class="num">' + r[1] + '</td><td colspan="3"></td></tr>'; }).join("") +
      (relay.visible ? '<tr><td><strong>Passed the relevance gate</strong></td><td class="num"><strong>' + k.rel + '</strong></td><td colspan="3"></td></tr>' : '') +
      '<tr><td class="muted">Feed polls full with nothing seen before / estimated items missed' + (missedSince() ? ' (estimated since ' + esc(missedSince()) + ')' : '') + '</td><td class="num" colspan="4">' + k.sat + ' of ' + k.polls + ' / ' + (!missedSince() ? k.miss : !k.mpolls ? 'not estimated' : k.miss + (k.mpolls < k.polls ? ' over ' + k.mpolls + ' polls' : '')) + '</td></tr>' +
      '</table>';
    html += '<p class="panel-note">' + esc(gateSumText(k, relay, " (" + windowLabel() + ")", "Leaving out the underlying items row, which is part of state origin, and the feed polls row, ")) + '</p>';
    if (k.paywalled) html += '<p class="panel-note">Paywalled articles are never classified, so they are missing from every count above and from the share of China coverage. They remain in the share of monitored output denominator, which counts every item the monitored outlets published, so that share is a lower bound here.</p>';
    html += archiveCheckPanelLine(iso, name);
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
    languageNotes(entry).forEach(function (t) { html += '<p class="panel-note">' + esc(t) + '</p>'; });
    /* The outlet counts come from outlets.json, which counts every day to the export, not the window above. */
    html += '<h3>Monitored outlets, all time to the last export</h3><table><tr><th>Outlet</th><th class="num">State origin</th><th class="num">Unchecked' + relayNote + '</th><th class="num">Pending</th><th class="num">Independent</th><th class="num">Paywalled</th><th>Feeds</th><th>Release section</th></tr>';
    state.outlets.filter(function (o) { return o.country === iso; }).sort(function (a, b) { return (b.active - a.active) || a.name.localeCompare(b.name); }).forEach(function (o) {
      /* Health counts editorial feeds; release section feeds are listed in the title but never make an outlet look failing. */
      var editorial = o.feeds.filter(function (f) { return !f.kind || f.kind === "editorial"; });
      var okN = editorial.filter(function (f) { return f.ok; }).length;
      var feedCls = !o.active ? "muted" : (okN === editorial.length ? "ok" : (okN === 0 ? "fail" : "warn"));
      html += '<tr><td>' + esc(o.name) + (o.active ? '' : ' <span class="badge">inactive</span>') + (C.collectorBadge(o.collector, relayRetiredOn()) ? ' <span class="badge" title="' + esc(C.collectorBadge(o.collector, relayRetiredOn()).title) + '">' + esc(C.collectorBadge(o.collector, relayRetiredOn()).label) + '</span>' : '') + '</td><td class="num">' + o.counts.A + '</td><td class="num">' + (relay.visible ? o.counts.B : '<span class="relay-state">' + esc(relay.measured ? "withheld" : "n/m") + '</span>') + '</td><td class="num">' + (o.counts.pending || 0) + '</td><td class="num">' + o.counts.C + '</td><td class="num">' + o.counts.paywalled + '</td><td class="' + feedCls + '" title="' + esc(o.inactive_reason || o.feeds.map(function (f) { return f.url + (f.kind && f.kind !== "editorial" ? " (" + f.kind + ")" : "") + (f.ok ? " ok" : " " + (f.last_error || "failing")); }).join("\n")) + '">' + (o.active ? okN + '/' + o.feeds.length : '') + '</td><td class="muted">' + esc(RELEASE_LABELS[o.release_sections || "not_searched"] || o.release_sections) + '</td></tr>';
    });
    html += '</table>';
    html += '<h3>State origin, unchecked state sourcing and unjudged candidates first, then the rest</h3><p class="muted">State placements, unchecked state sourcing, and pieces flagged for the model that still await its judgement (official Chinese sourcing, a weak signature, or a near copy of a credited state item), each with the sentence or match that flagged it. The unjudged candidates are listed for inspection only and are not a finding. Independent coverage follows.</p><div id="panel-articles"><p class="muted">Loading</p></div>';
    body.innerHTML = html;
    bindClose();
    var loadAllBtn = el("mini-load");
    if (loadAllBtn) loadAllBtn.addEventListener("click", function () { loadAllBtn.disabled = true; loadAllBtn.textContent = "Loading"; ensureAllMonths(); });
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
      return {date: new Date(d + "T00:00:00Z"), A: c.A, B: relay.visible ? c.B : 0, P: c.pending || 0};
    });
    if (!pts.length) { svgm.append("text").attr("x", 4).attr("y", 14).text("No daily data"); return; }
    var x = d3.scaleUtc().domain(d3.extent(pts, function (p) { return p.date; })).range([4, w - 4]);
    /* .nice() rounds the axis outward, so the domain's top is a drawing decision, not an observation.
       The caption prints the busiest day that actually happened. */
    var busiest = d3.max(pts, function (p) { return Math.max(p.A, p.B, p.P); }) || 0;
    var y = d3.scaleLinear().domain([0, busiest || 1]).nice().range([h - 16, 6]);
    var lineA = d3.line().x(function (p) { return x(p.date); }).y(function (p) { return y(p.A); });
    var lineB = d3.line().x(function (p) { return x(p.date); }).y(function (p) { return y(p.B); });
    var lineP = d3.line().x(function (p) { return x(p.date); }).y(function (p) { return y(p.P); });
    svgm.append("path").attr("class", "p").attr("d", lineP(pts));
    svgm.append("path").attr("class", "a").attr("d", lineA(pts));
    if (relay.visible) svgm.append("path").attr("class", "b").attr("d", lineB(pts));
    svgm.append("text").attr("x", 4).attr("y", h - 4).text(pts[0].date.toISOString().slice(0, 10));
    svgm.append("text").attr("x", w - 4).attr("y", h - 4).attr("text-anchor", "end").text(pts[pts.length - 1].date.toISOString().slice(0, 10));
    svgm.append("text").attr("x", w - 4).attr("y", 12).attr("text-anchor", "end").text("solid state origin, " + (relay.visible ? "dashed unchecked state sourcing" + (relay.provisional ? " (provisional), " : ", ") : "") + "dotted pending, max " + busiest + " per day");
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
        var srcs = (a.sources && a.sources.length) ? '<div class="a-meta">Chinese sources carried: ' + escText(a.sources.join(", ")) + '</div>' : '';
        var catLabel = cat === "B" && relay.provisional ? "Unchecked state sourcing (provisional)" : cat === "B" && !relay.publishable ? "Sourcing judgement, " + (relay.measured ? "withheld" : "not settled") : cat === "U" ? "Undetermined, not counted" : C.nameOf(cat);
        var route = cat === "A" && a.route ? '<div class="a-meta">Route: ' + esc(routeLabel({kind: "route", id: a.route})) + (a.arrival && a.arrival !== "editorial_feed" ? '; collected from ' + esc(lowerLabel(routeLabel({kind: "arrival", id: a.arrival}))) : '') + '</div>' : '';
        var href = safeUrl(a.url);
        var title = escText(a.title || a.url);
        return '<div class="article">' + (href ? '<a class="a-title" href="' + esc(href) + '" target="_blank" rel="noopener">' + title + '</a>' : '<span class="a-title">' + title + '</span>') +
          '<span class="a-meta">' + esc(outletName[a.outlet_id] || a.outlet_id) + ', ' + esc(a.date) + ' <span class="badge cat-' + esc(cat) + '">' + esc(catLabel) + (a.human_category && a.human_category !== a.category ? ' (machine said ' + esc(C.nameOf(a.category)) + ')' : '') + '</span><span class="badge prov-' + esc(a.provenance) + '">' + prov + '</span>' + (a.dup_group ? '<span class="badge" title="One of several placements of the same underlying item">syndicated</span>' : '') + issuerBadge(a.issuer_type) + '</span>' +
          translateHtml(outletLang(a.outlet_id), decodeEntities(a.title || "") + (a.evidence_quote ? "\n\n" + decodeEntities(a.evidence_quote) : ""), href) +
          srcs + route + (a.evidence_quote ? '<p class="a-quote">' + escText(a.evidence_quote) + '</p>' : '') +
          (a.signatures && a.signatures.length ? '<div class="a-meta">Signatures: ' + escText(a.signatures.join(", ")) + '</div>' : '') + '</div>';
      }).join("");
    };
    /* Cache the request, not just the result, so re-renders while it is in flight (the timeline
       scrubber re-renders the panel on every step) reuse it instead of fetching again. */
    if (!state.articlesCache[iso]) {
      state.articlesCache[iso] = getJSON("data/articles/" + iso + ".json").catch(function (e) {
        console.error(e); delete state.articlesCache[iso]; return null;
      });
    }
    state.articlesCache[iso].then(function (arts) {
      if (el("panel-articles") !== target) return;
      /* null is a request that failed, which is not the same statement as an empty list. */
      if (arts === null) { target.innerHTML = '<p class="warn">The article list for this country could not be loaded.</p>'; return; }
      render(arts);
    });
  }

  /* -------------------------------------------------------------- timeline */
  var tlDays = [];
  /* The last day the export saw in full: the export day itself is partial. The timeline ends here and the
     KPI cards that count a 30 day window end here too. */
  function lastCompleteDay() {
    var all = allDays(), first = state.meta && state.meta.first_discovered;
    all = all.filter(function (d) { return !first || d >= first; });
    if (!all.length) return null;
    var gen = state.meta && state.meta.generated_at ? state.meta.generated_at.slice(0, 10) : null;
    var i = all.length - 1;
    while (i > 0 && gen && all[i] >= gen) i -= 1;
    return all[i];
  }
  /* The partial day the export ran, when the timeline leaves it out, for the line under the timeline. */
  function partialExportDay() {
    var all = allDays(), last = lastCompleteDay();
    return last && all.length && all[all.length - 1] > last ? all[all.length - 1] : null;
  }
  function setupTimeline() {
    var first = state.meta && state.meta.first_discovered;
    /* The day the export ran is always partial, so the timeline ends on the last complete day: neither
       scrubber, Play nor a day sent by another script can reach a day that was still being collected. */
    var lastDay = lastCompleteDay();
    tlDays = allDays().filter(function (d) { return (!first || d >= first) && (!lastDay || d <= lastDay); });
    var scrub = el("scrub");
    scrub.max = Math.max(0, tlDays.length - 1);
    scrub.value = Math.max(0, tlDays.length - 1);
    state.endDate = tlDays.length ? tlDays[scrub.value] : null;
    scrub.addEventListener("input", function () { stopPlay(); setDay(Number(scrub.value)); });
    el("step-back").addEventListener("click", function () { stopPlay(); setDay(Number(scrub.value) - 1); });
    el("step-fwd").addEventListener("click", function () { stopPlay(); setDay(Number(scrub.value) + 1); });
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
    /* A day whose window reaches before the loaded months is drawn when they arrive. */
    if (demandMonths(state.endDate, state.windowDays)) return;
    renderMap();
    if (state.selected) renderPanel();
  }
  function updateDateLabel(loading) {
    var busy = loading || !!state.monthsLoading;
    el("tl-date").textContent = (state.endDate || "no days") + (busy ? ", loading earlier days" : "");
    var th = el("th-date");
    if (th && state.themeEnd) th.textContent = state.themeEnd + (busy ? ", loading earlier days" : "");
  }
  /* Dragging the scrubber against a running interval is a fight the interval wins, so moving the day
     by hand stops playback first. */
  function stopPlay() {
    if (!state.playing) return;
    clearInterval(state.playing);
    state.playing = null;
    el("play").textContent = "Play";
  }
  function togglePlay() {
    if (state.playing) { stopPlay(); return; }
    if (!tlDays.length) return;
    /* Play runs over every day, so it waits for the months not loaded yet. */
    var wait = ensureAllMonths();
    if (wait) { el("play").textContent = "Loading"; wait.then(function () { el("play").textContent = "Play"; togglePlay(); }); return; }
    stopThemePlay();
    if (Number(el("scrub").value) >= tlDays.length - 1) setDay(0);
    el("play").textContent = "Pause";
    state.playing = setInterval(function () {
      var i = Number(el("scrub").value);
      if (i >= tlDays.length - 1) { stopPlay(); return; }
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
      /* A day whose month has not loaded yet reads its world total from global_series.json; a route
         total is not in it, so that day is left undrawn until the month arrives. */
      if (!e && !state.months[d.slice(0, 7)]) tot = byRoute ? null : state.measure === "b" && !relayFor(null).visible ? 0 : seriesTotal(d, state.measure === "a" ? 2 : state.measure === "b" ? 3 : 0, state.distribution);
      else if (e) {
        if (byRoute) {
          var src = state.route.kind === "arrival" ? e.arrivals : e.routes;
          C.distributionIsos(src || {}, state.distribution).forEach(function (c) { tot += (src[c] || {})[state.route.id] || 0; });
        } else C.distributionIsos(e.countries || {}, state.distribution).forEach(function (c) {
          var k = e.countries[c];
          k = k || {};
          tot += state.measure === "a" ? (k.A || 0) : state.measure === "b" ? (relayFor(null).visible ? (k.B || 0) : 0) : (k.A || 0) + (k.B || 0) + (k.C || 0) + (k.pending || 0);
        });
      }
      return {i: i, d: d, v: tot, ceiling: !!(e && e.llm_ceiling_hit), relayGap: !!(e && e.relay_incomplete), older: !!(e && (e.labels_on_older_ruleset || e.labels_on_older_prompt))};
    });
    var noun = byRoute ? "state origin articles, " + lowerLabel(routeLabel(state.route)) : measureNoun();
    var mapCap = caption("map_timeline_caption");
    el("tl-hint").textContent = "The shaded curve is the global daily number of " + noun + (wiresOnly() ? " on the worldwide wires alone" : countryOnly() ? " in the country outlets alone" : "") + ". " + (mapCap ||
      "Moves only the map; the theme counter keeps its own day and window. Amber bars are days on which more articles were waiting than the daily model call cap allowed, so those days are truncated rather than quiet; the cap binds the draw and a run can stop at its time budget before spending it. Red ticks along the bottom are days, up to 22 October 2026, on which the relay on the owner's machine ran too few hours, so counts for the outlets only it collected are incomplete; it was stopped that day and its outlets are inactive since. Dashed vertical lines are ruleset changes, and grey ticks along the top are days whose labels still carry an older ruleset or an older model prompt, waiting to be judged again. Gold dotted lines are relevance gate changes, which apply only to items discovered after them.");
    var partial = partialExportDay();
    if (partial && tlDays.length) el("tl-hint").textContent += " The timeline ends on " + tlDays[tlDays.length - 1] + ", the last complete day: " + partial + ", the day the export ran, was still being collected and is left out.";
    if (!pts.length) return;
    var x = d3.scaleLinear().domain([0, Math.max(1, pts.length - 1)]).range([0, w]);
    var y = d3.scaleLinear().domain([0, d3.max(pts, function (p) { return p.v; }) || 1]).range([h - 1, 2]);
    var area = d3.area().defined(function (p) { return p.v !== null; }).x(function (p) { return x(p.i); }).y0(h - 1).y1(function (p) { return y(p.v); }).curve(d3.curveMonotoneX);
    s.append("path").attr("d", area(pts));
    if (byRoute && pts.some(function (p) { return p.v === null; })) el("tl-hint").textContent += " Days before " + (loadedFrom() || "") + " are drawn by route once they load: choose an earlier day, Total or Play.";
    pts.forEach(function (p) {
      if (p.ceiling) s.append("rect").attr("class", "ceiling").attr("x", x(p.i) - 2).attr("y", 0).attr("width", 4).attr("height", h);
      if (p.relayGap) s.append("rect").attr("class", "relay-gap").attr("x", x(p.i) - 3).attr("y", h - 3).attr("width", 6).attr("height", 3);
      if (p.older) s.append("rect").attr("class", "older-ruleset").attr("x", x(p.i) - 3).attr("y", 0).attr("width", 6).attr("height", 2);
    });
    /* Marks the research measures add (unusual days); each may add a sentence to the hint. */
    if (MS) {
      var hints = MS.sparkAll(measureBlocks(), state.measureData, measureApi,
        {svg: s, x: function (day) { var k = tlDays.indexOf(day); return k === -1 ? null : x(k); }, days: tlDays, w: w, h: h});
      if (hints.length) el("tl-hint").textContent += " " + hints.join(" ");
    }
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
    return C.aggregateWindow(state.months, state.themeEnd, windowArg(state.themeWindow), state.distribution);
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
    if (demandThemeMonths()) return;
    renderThemes(); renderLeaning();
  }
  /* The theme counter reads its window, and in the single country view up to 31 days ending its day. */
  function demandThemeMonths() {
    return demandMonths(state.themeEnd, state.themeWindow === "all" ? "all" : Math.max(31, Number(state.themeWindow) || 1));
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
    var wait = ensureAllMonths();
    if (wait) { el("th-play").textContent = "Loading"; wait.then(function () { el("th-play").textContent = "Play"; toggleThemePlay(); }); return; }
    stopPlay();
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
      /* An unloaded month: the world total comes from global_series.json, one country is left undrawn. */
      if (!e && !state.months[d.slice(0, 7)] && !relayHidden()) v = state.selected ? null : seriesTotal(d, mi, "both");
      else if (e && !relayHidden()) Object.keys(e.countries || {}).forEach(function (c) { if (!state.selected || state.selected === c) v += denominator(e.countries[c], mi); });
      return {i: i, v: v};
    });
    var who = state.selected ? (state.names[state.selected] || state.selected) : "all monitored countries";
    var marks = C.themesChangeMarks(tlDays, state.meta && state.meta.themes_changes);
    el("th-hint").textContent = "Moves only the theme counter; the map keeps its own day and window. The curve is the daily number of " + noun + " in " + who + "." +
      (marks.length ? " Green dash-dot lines are themes version changes, the days the theme terms or the part of each article read for them changed; hover one for what changed. Every change re-tags every stored article, so the counts on every day follow the current version, but figures taken before a change were made under the earlier one." : "");
    if (!pts.length) return;
    var x = d3.scaleLinear().domain([0, Math.max(1, pts.length - 1)]).range([0, w]);
    var y = d3.scaleLinear().domain([0, d3.max(pts, function (p) { return p.v; }) || 1]).range([h - 1, 2]);
    s.append("path").attr("d", d3.area().defined(function (p) { return p.v !== null; }).x(function (p) { return x(p.i); }).y0(h - 1).y1(function (p) { return y(p.v); }).curve(d3.curveMonotoneX)(pts));
    if (pts.some(function (p) { return p.v === null; })) el("th-hint").textContent += " Days before " + (loadedFrom() || "") + " are drawn for one country once they load: choose an earlier day, Total or Play.";
    /* Themes version changes, marked the way ruleset and gate changes are on the map timeline. A wide
       transparent line under each mark gives the tooltip something to hover. */
    marks.forEach(function (m) {
      var g = s.append("g").attr("class", "themes-change");
      g.append("title").text(m.title);
      g.append("line").attr("class", "themes-hit").attr("x1", x(m.index)).attr("x2", x(m.index)).attr("y1", 0).attr("y2", h);
      g.append("line").attr("class", "themes").attr("x1", x(m.index)).attr("x2", x(m.index)).attr("y1", 0).attr("y2", h);
    });
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
      ["Population figures", (m.population_source || "not recorded") + ". No figure is divided by population; it is given only in the population column of the CSV of the current view"],
      ["% of all its news", (m.countries_with_audience_ranks && m.countries_with_audience_ranks.length ? m.countries_with_audience_ranks.length + " countries carry audience ranks and use their largest outlets; " : "no country carries audience ranks yet; ") + "elsewhere every active outlet counts, and no rate is shown below " + (m.min_outlets_for_output_share || C.MIN_OUTLETS_FOR_OUTPUT_SHARE) + " outlets"],
      ["Countries with zero coverage", (m.countries_in_gaps || 0) + " recorded in the gaps file with a reason; every unhatched country not listed there is simply unregistered"],
      ["Articles", m.articles_discovered + " discovered, " + m.articles_gate_relevant + " passed the relevance gate, " + m.articles_classified + " classified" +
        (C.rediscoveryOf(m) ? "; before " + C.rediscoveryOf(m).fixed_from + " the discovered count includes items a feed kept listing, counted again after a prune, an estimated " + C.rediscoveryPercent(m) + " percent of it" : "")],
      ["Paywall-blocked proportion", m.paywall_share === null || m.paywall_share === undefined ? "not measured" : pct(m.paywall_share) + " of gated articles" + (function (lt) { return lt && typeof lt.paywalled === "number" ? " (" + intComma(lt.paywalled) + " paywalled of the " + intComma(lt.fetched + lt.paywalled + lt.failed + lt.blocked) + " whose fetch was attempted, to the export)" : ""; })(state.latest && state.latest.totals && state.latest.totals.all_time) + ": missing from every count, but still counted in the share of monitored output denominator, so that share is a lower bound" + (m.paywall_flagged_countries && m.paywall_flagged_countries.length ? "; flagged: " + m.paywall_flagged_countries.join(", ") : "")],
      ["Unchecked state sourcing", !relay.measured ? "not yet measured: the verification stage has not run"
        : (relay.publishable ? "published, " + (m.relay_qualifier || "basis not recorded") : relay.provisional ? "provisional: shown and marked, " + humanStudyShort() : "withheld until hand coding settles it")
          + ((m.relay_withheld_languages || []).length ? " Withheld for " + m.relay_withheld_languages.join(", ") + "." : "")],
      ["Labels that cannot be reclassified", (m.labels_unreclassifiable || 0) + " of " + (m.articles_classified || 0) + ", because the article can no longer be retrieved"],
      ["Stability check (never publishes)", m.relay_reliability ? (m.relay_reliability.method === "model_vs_model" ? "second model " + m.relay_reliability.model_b + " against " + m.relay_reliability.model_a : m.relay_reliability.method === "same_model_rerun" ? "same model rerun, same prompt, " + m.relay_reliability.model_a : m.relay_reliability.method) + ", " + m.relay_reliability.n + " articles, kappa " + (m.relay_reliability.kappa_bc === null || m.relay_reliability.kappa_bc === undefined ? "n/a" : m.relay_reliability.kappa_bc.toFixed(2)) + " on unchecked sourcing versus independent: stability, not correctness" : C.stabilityStatusText(m) || "none run"],
      ["Hand coding", humanStudyShort() + "; publication needs kappa " + (m.kappa_warning_threshold || 0.7) + " or above on a fixed sample of about " + ((m.relay_human_study && m.relay_human_study.per_language) || 200) + " articles per language, stratified by language and by the model\u2019s category"],
      ["Current kappa", k && k.bc !== null && k.bc !== undefined ? "all categories " + (k.all === null ? "n/a" : k.all.toFixed(2)) + ", unchecked state sourcing versus independent " + k.bc.toFixed(2) + " (n = " + k.n + ", computed " + (k.computed_at || "").slice(0, 10) + ")" : "not yet measured"],
      ["Routes of state origin", (m.routes || []).map(function (r) { return lowerLabel(r.label) + " " + (routeTotals[r.id] || 0); }).join("; ") || "not recorded"],
      ["Undetermined, excluded from the counts", undeterminedMethodText(m)],
      ["Release sections searched", rs && rs.outlets_active ? (rs.outlets_active - (rs.not_searched || 0)) + " of " + rs.outlets_active + " active outlets; " + (rs.found || 0) + " found, " + (rs.none_found || 0) + " none, " + (rs.blocked || 0) + " blocked, " + (rs.unreachable || 0) + " unreachable" : "not recorded"],
      ["Feed polls with nothing seen before", fs && fs.polls ? fs.saturated + " of " + fs.polls + "; " + missedPhrase(fs.missed_estimate, fs.estimator ? fs.estimated_polls : fs.polls, fs.polls) : "not yet measured"],
      ["Relay on the owner's machine", rc ? C.relayOutletsSummary(rc) + "; " + (rc.last_run ? "last pass " + rc.last_run + ", " + (rc.incomplete_days || []).length + " incomplete days" : "no heartbeat yet") : "not recorded"],
      ["Human review coverage", pct(m.review_coverage) + " of classified articles (" + m.articles_reviewed + ")"],
      ["Relevance gate version", (m.gate_version || "not recorded") + (m.gate_applies_forward_only ? ", applying to items discovered after each gate change; earlier rejections are not re-examined and are pruned after three days, except a random 2 percent kept permanently for the gate audit" : "")],
      ["Ruleset version", m.ruleset_version + (m.reclassification_complete === false ? ", reclassification in progress (" + Object.keys(m.ruleset_mix || {}).sort().map(function (v) { return v + ": " + m.ruleset_mix[v]; }).join(", ") + ")" : "")],
      ["Classifier model", m.llm_model + ", " + m.llm_calls_total + " calls to date, daily ceiling " + m.llm_daily_ceiling + (m.llm_ceiling_days && m.llm_ceiling_days.length ? ", draw capped on " + m.llm_ceiling_days.map(function (d) { return d + " (" + ((m.llm_calls_by_day || {})[d] || 0) + " calls made)"; }).join(", ") : "") + ((m.llm_sampling_days || []).length ? "; stratified draws on " + m.llm_sampling_days.length + " days" : "") + (m.llm_budget ? "; estimated spend " + m.llm_budget.month + ": $" + m.llm_budget.estimated_usd + " of a $" + m.llm_budget.budget_usd + " monthly budget" + (m.llm_budget.calls_left_today !== null && m.llm_budget.calls_left_today !== undefined ? ", leaving " + m.llm_budget.calls_left_today + " batched calls for the rest of today" : "") : "")],
      ["Last successful run", m.last_successful_run || "none"],
      ["Data generated", m.generated_at]
    ];
    dl.innerHTML = rows.map(function (r) { return '<dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd>'; }).join("");
    el("citation").textContent = C.citation(new Date().toISOString().slice(0, 10), CITATION_AUTHOR);
    el("footer-run").textContent = "Generated " + m.generated_at + ". Ruleset " + m.ruleset_version + ". Schema " + m.schema_version + ".";
  }

  /* The methodology line for undetermined items, from meta.undetermined. */
  function undeterminedMethodText(m) {
    var ud = m && m.undetermined;
    var n = ud && typeof ud === "object" ? ud.count : (m.route_totals || {}).unattributed;
    var br = (ud && ud.by_route) || {}, chk = (ud && ud.check) || {};
    var split = typeof br.unattributed === "number" ? " (" + intComma(br.unattributed) + " with no checkable marker, " + intComma(br.sponsored_disclosure || 0) + " sponsored disclosures only the model found" + (br.uncredited_copy ? ", " + intComma(br.uncredited_copy) + " uncredited copies of an item that is not settled state origin" : "") + ")" : "";
    var check = chk.coded ? "; hand check, for information: " + intComma(chk.coded) + " coded, " + intComma(chk.confirmed || 0) + " settled as state origin" + (typeof chk.confirmed_share === "number" ? " (" + Math.round(chk.confirmed_share * 100) + "%)" : "") + ", " + intComma(chk.undetermined || 0) + " undetermined for the coders too" : "; no hand check coded yet";
    return intComma(n || 0) + " placements" + split + " the model called state origin whose authorship or commissioning the evidence does not settle. As in the original study, they are excluded from every count and denominator, China coverage included, and never rejoin state origin" + check +
      ". The model never sees outlet, country or address, so these calls carry no marker a reader can check.";
  }

  /* The operational detail a reader does not need to interpret today's map, moved out of the notes
     strip and the "Current notes" list: model call caps, ruleset and gate versions, reclassification
     progress, the reliability study, feed saturation, the owner-run collector, and release section
     search coverage. Static prose from content.json, written at the last export; hidden entirely when
     content.json has not loaded or carries none of these keys. */
  var TECHNICAL_NOTE_KEYS = [
    ["model_call_caps", "Model call caps"], ["ruleset_and_gate_versions", "Ruleset and gate versions"],
    ["reclassification_status", "Reclassification status"], ["reliability_study", "Reliability study"],
    ["feed_saturation", "Feed saturation"], ["relay_collector", "Owner-run collector"],
    ["release_section_search_coverage", "Release section search coverage"]
  ];
  function renderTechnicalNotes() {
    var wrap = el("technical-notes-wrap"), dl = el("technical-notes");
    if (!wrap || !dl) return;
    var extra = state.content && state.content.methodology_extra;
    if (!extra) { wrap.hidden = true; return; }
    var rows = TECHNICAL_NOTE_KEYS.filter(function (kv) { return extra[kv[0]]; });
    dl.innerHTML = rows.map(function (kv) { return '<dt>' + esc(kv[1]) + '</dt><dd>' + esc(extra[kv[0]]) + '</dd>'; }).join("");
    wrap.hidden = !rows.length;
  }

  /* The licensed archive check (data/archive_checks.json; pipeline/archive_checks.py), a separate source
     and method: its own block beside the paywall disclosure in the methodology, and one line in the panel
     of each country it covers. It feeds no count, map, share, top 10 or finding. The wording is in
     content.json (archive_check); every figure comes from the side file, the per country ones summed
     from its title rows (C.archiveCheck). Hidden when either has not loaded. */
  function archiveCheckText() { return (state.content && state.content.archive_check) || null; }
  function renderArchiveCheck() {
    var box = el("archive-check"), tx = archiveCheckText(), AC = C.archiveCheck;
    var checks = AC.list(state.archiveChecks);
    if (!box) return;
    if (!tx || !checks.length) { box.hidden = true; return; }
    var html = '<h3 id="archive-check-title">' + esc(tx.title) + '</h3>';
    checks.forEach(function (c) {
      var tok = AC.tokens(c), t = c.totals;
      var cell = function (v) { return '<td class="num">' + esc(AC.int(v)) + '</td>'; };
      html += '<p class="ac-summary">' + esc(AC.fill(tx.summary, tok)) + '</p>' +
        '<p>' + esc(AC.fill(tx.intro, tok)) + '</p>' +
        '<div class="ac-table-wrap"><table class="ac-table"><thead><tr><th>' + esc(tx.head_title) + '</th><th class="num">' + esc(tx.head_china) + '</th><th class="num">' +
        esc(tx.head_awaiting) + '</th><th class="num">' + esc(tx.head_no_signature) + '</th><th class="num">' + esc(tx.head_not_china) + '</th></tr></thead><tbody>' +
        c.titles.map(function (r) {
          return '<tr><td>' + esc(r.name) + '<span class="ac-country">' + esc(state.names[r.country] || r.country) + '</span></td>' +
            cell(r.china_relevant) + cell(r.awaiting_model) + cell(r.no_state_signature) + cell(r.not_about_china) + '</tr>';
        }).join("") +
        '<tr class="ac-total"><td>' + esc(tx.total) + '</td>' + cell(t.china_relevant) + cell(t.awaiting_model) + cell(t.no_state_signature) + cell(t.not_about_china) + '</tr>' +
        '</tbody></table></div>' +
        '<p>' + esc(AC.fill(tx.no_signature_note, tok)) + '</p>' +
        (Number(t.awaiting_model) ? '<p>' + esc(AC.fill(tx.awaiting_note, tok)) + '</p>' : '') +
        '<p>' + esc(AC.fill(tx.records_note, tok)) + '</p>' +
        (c.not_searchable || []).map(function (n) { return '<p>' + esc(AC.fill(tx.not_searchable, {name: n.name, reason: n.reason})) + '</p>'; }).join("") +
        '<p class="ac-method">' + esc(AC.fill(tx.method_line, tok)) + (c.ruleset_version ? ' ' + esc(AC.fill(tx.ruleset_line, tok)) : '') + '</p>';
    });
    box.innerHTML = html;
    box.hidden = false;
  }
  function archiveCheckPanelLine(iso, name) {
    var tx = archiveCheckText();
    if (!tx || !state.archiveChecks) return "";
    var line = C.archiveCheck.countryLine(state.archiveChecks, iso, name, tx);
    return line ? '<p class="panel-note ac-panel-line">' + esc(line) + ' <a href="#archive-check">' + esc(tx.link_text) + '</a>.</p>' : "";
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
    var rows = rankedRows(agg).map(function (r) {
      r.metric = state.metric; r.route = routeActive() ? state.route.kind + ":" + state.route.id : ""; r.window = windowLabel(); r.mode = state.mode; r.distribution = state.distribution; r.relay_basis = relayBasis() || "";
      r.warnings = countryWarnings(state.latest.countries[r.iso] || {}, agg).join("; ");
      r.citation = cite;
      return r;
    });
    download("tracker_view_" + state.metric + "_" + (state.endDate || "empty") + ".csv", C.toCSV(rows, ["iso", "name", "metric", "route", "window", "mode", "distribution", "value", "fill", "note", "state_origin", "state_origin_underlying_items", "undetermined", "unverified_relay", "unverified_relay_underlying_items", "relay_status", "relay_basis", "official_sourcing_pending", "independent", "china_total", "outlets_active", "population", "items_published_monitored_outlets", "china_in_published_items", "language_support", "warnings", "citation"]));
  }
  function exportDaily() {
    /* The daily table is every day, so it waits for the months not loaded yet. */
    var wait = ensureAllMonths();
    if (wait) { el("export-daily").disabled = true; wait.then(function () { el("export-daily").disabled = false; exportDaily(); }); return; }
    var rows = [];
    var cite = C.citation(new Date().toISOString().slice(0, 10), CITATION_AUTHOR);
    var routeCols = routeList().map(function (r) { return "route_" + r.id; }).concat(arrivalList().map(function (r) { return "arrival_" + r.id; }));
    days().forEach(function (d) {
      var e = C.dayEntry(state.months, d);
      if (!e) return;
      C.distributionIsos(e.countries, state.distribution).forEach(function (iso) {
        var c = e.countries[iso], r = (e.reviewed || {})[iso] || {};
        var relay = relayFor((state.latest.countries || {})[iso]);
        var blank = function (v) { return relay.visible ? v : ""; };
        var row = {date: d, iso: iso, name: state.names[iso] || iso, distribution: state.distribution, state_origin: c.A, state_origin_underlying_items: c.uniqA, unverified_relay: blank(c.B), unverified_relay_underlying_items: blank(Math.max(0, (c.uniqAB || 0) - (c.uniqA || 0))), relay_status: C.relayStatus(relay), independent: c.C, not_relevant: c.N,
                   undetermined: c.U || 0, state_origin_rules: c.Ar, state_origin_model: c.Al, unverified_relay_rules: blank(c.Br), unverified_relay_model: blank(c.Bl),
                   reviewed: c.rev, human_state_origin: r.A || 0, human_unverified_relay: blank(r.B || 0), human_independent: r.C || 0, discovered: c.disc, gate_relevant: c.rel, fetched: c.fetched,
                   paywalled: c.paywalled, failed: c.failed, blocked_robots: c.blocked, awaiting_model: c.pending,
                   items_published_monitored_outlets: c.tdisc, china_in_published_items: c.tchina,
                   feed_polls: c.polls || 0, feed_polls_saturated: c.sat || 0, items_missed_estimate: missedSince() && !c.mpolls ? "" : (c.miss || 0),
                   feed_polls_estimated: missedSince() ? (c.mpolls || 0) : (c.polls || 0),
                   model_draw_eligible: ((e.llm_sampling || {})[iso] || [""])[0], model_draw_sent: ((e.llm_sampling || {})[iso] || ["", ""])[1],
                   llm_ceiling_hit: e.llm_ceiling_hit, relay_collector_incomplete: !!e.relay_incomplete, labels_on_older_ruleset: e.labels_on_older_ruleset || 0, citation: cite};
        routeList().forEach(function (x) { row["route_" + x.id] = ((e.routes || {})[iso] || {})[x.id] || 0; });
        arrivalList().forEach(function (x) { row["arrival_" + x.id] = ((e.arrivals || {})[iso] || {})[x.id] || 0; });
        rows.push(row);
      });
    });
    download("tracker_daily_counts.csv", C.toCSV(rows, ["date", "iso", "name", "distribution", "state_origin", "state_origin_underlying_items", "undetermined", "unverified_relay", "unverified_relay_underlying_items", "relay_status", "independent", "not_relevant", "state_origin_rules", "state_origin_model", "unverified_relay_rules", "unverified_relay_model", "reviewed", "human_state_origin", "human_unverified_relay", "human_independent", "discovered", "gate_relevant", "fetched", "paywalled", "failed", "blocked_robots", "awaiting_model", "items_published_monitored_outlets", "china_in_published_items", "feed_polls", "feed_polls_saturated", "items_missed_estimate", "feed_polls_estimated", "model_draw_eligible", "model_draw_sent"].concat(routeCols).concat(["llm_ceiling_hit", "relay_collector_incomplete", "labels_on_older_ruleset", "citation"])));
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
    /* Every figure on the map is one category read against one denominator, so the interface is those
       two rows and nothing else. Both rows are repeated above the map, the ranked list and the theme
       chart, and every copy stays in step. A route applies to state origin only, so choosing one
       switches the category to state origin. */
    function applyMetric() {
      syncMetric();
      renderMap(); renderSpark(); renderThemes(); renderLeaning(); if (state.selected) renderPanel();
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-basis-group] button"), function (b) {
      b.setAttribute("data-title", b.title || "");
      b.addEventListener("click", function () { state.basis = b.getAttribute("data-basis"); applyMetric(); });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-measure-group] button"), function (b) {
      b.addEventListener("click", function () {
        state.measure = b.getAttribute("data-measure");
        if (state.measure !== "a") state.route = null;
        applyMetric();
      });
    });
    Array.prototype.forEach.call(el("lean-axis").querySelectorAll("button"), function (b) {
      b.addEventListener("click", function () {
        state.leanAxis = b.getAttribute("data-axis");
        Array.prototype.forEach.call(el("lean-axis").querySelectorAll("button"), function (x) { x.classList.toggle("active", x === b); });
        syncPressed();
        renderLeaning();
      });
    });
    el("route").addEventListener("change", function () {
      var v = el("route").value;
      state.route = v ? {kind: v.slice(0, v.indexOf(":")), id: v.slice(v.indexOf(":") + 1)} : null;
      if (state.route) { state.measure = "a"; if (state.basis === "share_of_output") state.basis = "count"; }
      applyMetric();
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-distribution-group] button"), function (b) {
      b.addEventListener("click", function () { setDistribution(b.getAttribute("data-distribution")); });
    });
    /* The Window toggle is repeated above the map and above the ranked list, like Basis. */
    function applyWindow(v) {
      syncWindow(v);
      if (demandMonths(state.endDate, state.windowDays)) return;
      renderMap(); if (state.selected) renderPanel();
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-window-group] button"), function (b) {
      b.addEventListener("click", function () { applyWindow(b.getAttribute("data-window")); });
    });
    Array.prototype.forEach.call(el("mode").querySelectorAll("button"), function (b) {
      b.addEventListener("click", function () {
        state.mode = b.getAttribute("data-mode");
        Array.prototype.forEach.call(el("mode").querySelectorAll("button"), function (x) { x.classList.toggle("active", x === b); });
        syncPressed();
        renderMap(); renderThemes(); renderLeaning(); if (state.selected) renderPanel();
      });
    });
    Array.prototype.forEach.call(el("view").querySelectorAll("button"), function (b) {
      b.addEventListener("click", function () {
        var v = b.getAttribute("data-view");
        document.body.classList.toggle("force-map", v === "map");
        document.body.classList.toggle("force-list", v === "list");
        Array.prototype.forEach.call(el("view").querySelectorAll("button"), function (x) { x.classList.toggle("active", x === b); });
        syncPressed();
        renderMap();
      });
    });
    el("export-view").addEventListener("click", exportView);
    el("export-daily").addEventListener("click", exportDaily);
    /* The View toggle names the view on screen, which a resize across 720 pixels can change. */
    syncDefaultView();
    if (window.matchMedia) {
      var mq = window.matchMedia(SMALL_SCREEN), onMq = function () { syncDefaultView(); renderBars(state._agg || currentAgg()); };
      if (mq.addEventListener) mq.addEventListener("change", onMq); else if (mq.addListener) mq.addListener(onMq);
    }
    syncMetric();
  }

  /* The Measure and Basis rows and the route select, set from state without drawing anything. */
  function syncMetric() {
    /* A measure that no longer exists, such as the removed state-linked measure, falls back to state origin. */
    if (!C.GRID[state.measure]) state.measure = "a";
    if (state.measure !== "a") state.route = null;
    if (!C.basisAvailable(state.measure, state.basis)) state.basis = "count";
    state.metric = C.gridMetric(state.measure, state.basis);
    Array.prototype.forEach.call(document.querySelectorAll("[data-basis-group] button"), function (b) {
      var basis = b.getAttribute("data-basis"), ok = C.basisAvailable(state.measure, basis);
      b.classList.toggle("active", basis === state.basis);
      /* All China coverage is its own denominator, so its share of itself is always 100 percent. */
      b.disabled = !ok;
      b.title = ok ? (b.getAttribute("data-title") || b.title)
                   : "Every article that concerns China is all of a country's China coverage, so this share is always 100 percent.";
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-measure-group] button"), function (b) {
      b.classList.toggle("active", b.getAttribute("data-measure") === state.measure);
    });
    /* Routes split state origin only, so under another measure the select is reset, disabled and says why. */
    var sel = el("route");
    if (sel) {
      sel.value = state.route ? state.route.kind + ":" + state.route.id : "";
      sel.disabled = state.measure !== "a";
      sel.title = sel.disabled ? "Routes split state origin only. Choose State origin to filter by route." : "";
    }
    var rn = el("route-note");
    if (rn) { rn.textContent = state.measure !== "a" ? "State origin only" : ""; rn.hidden = state.measure === "a"; }
    syncPressed();
  }
  /* The map Window row, set from state without drawing anything. */
  function syncWindow(v) {
    state.windowDays = v === "all" || v === "total" ? "all" : (Number(v) || 30);
    Array.prototype.forEach.call(document.querySelectorAll("[data-window-group] button"), function (b) { b.classList.toggle("active", b.getAttribute("data-window") === String(state.windowDays)); });
    syncPressed();
  }
  /* Every segmented control of the page says which of its buttons is on to assistive technology, as
     Distribution does: aria-pressed follows the active class. Only the groups index.html marks as groups;
     a measure's own segments (docs/m_*.js) keep their own state. */
  function syncPressed() {
    Array.prototype.forEach.call(document.querySelectorAll('.segment[role="group"] button'), function (b) {
      b.setAttribute("aria-pressed", b.classList.contains("active") ? "true" : "false");
    });
  }

  /* The Distribution toggle drives every figure that sums across countries: the map, the ranked list and
     the legend, the sparkline, the KPI cards, the theme counter's totals, the panel's world table and
     both CSV exports. quiet skips the renders, for the caller that is about to render anyway. */
  function setDistribution(v, quiet) {
    state.distribution = C.DISTRIBUTIONS.indexOf(v) === -1 ? "both" : v;
    Array.prototype.forEach.call(document.querySelectorAll("[data-distribution-group] button"), function (b) {
      b.classList.toggle("active", b.getAttribute("data-distribution") === state.distribution);
      b.setAttribute("aria-pressed", b.getAttribute("data-distribution") === state.distribution ? "true" : "false");
    });
    if (quiet) return;
    renderMap(); renderSpark(); renderThemes(); renderLeaning(); renderPanel(); renderFindings();
  }

  /* The public hook other scripts open a country and a day with (docs/m_spikes.js does, for an unusual
     day): window.TrackerApp, and a "tracker:select" event on document with detail {iso, day, measure,
     window, distribution}. Each field is optional. measure, window and distribution set those controls
     first (an unusual day opens as state origin, That day, Both), then the day, then the country. A country
     opens through selectCountry, so one the Distribution toggle hides opens under Both with every figure
     redrawn for it. A day off the timeline opens the last day before it, or the first day; the timeline
     itself caps it at the last complete day. */
  var MEASURE_ALIASES = {a: "a", A: "a", state_origin: "a", "state origin": "a", b: "b", B: "b", unchecked: "b", unchecked_state_sourcing: "b", unverified_relay: "b",
    china: "china", all: "china", all_china: "china", all_china_coverage: "china"};
  var WINDOW_ALIASES = {"1": 1, day: 1, that_day: 1, "that day": 1, "7": 7, "30": 30, all: "all", total: "all"};
  function viewFields(d) {
    d = d || {};
    var m = d.measure === undefined || d.measure === null ? null : MEASURE_ALIASES[String(d.measure)] || null;
    var w = d.window === undefined || d.window === null ? null : WINDOW_ALIASES[String(d.window).toLowerCase()];
    var dist = d.distribution && C.DISTRIBUTIONS.indexOf(String(d.distribution)) !== -1 ? String(d.distribution) : null;
    return {measure: m, window: w === undefined ? null : w, distribution: dist};
  }
  function openView(d) {
    d = d || {};
    var v = viewFields(d);
    var end = d.day !== undefined && d.day !== null ? dayIndex(d.day) : null;
    var endDate = end === null ? state.endDate : tlDays[end];
    var win = v.window === null ? state.windowDays : v.window;
    /* The window asked for may reach before the loaded months: open the view once they have arrived. */
    if (!covered(windowStart(endDate, win)) && ensureAllMonths()) { state.monthsLoading.then(function () { openView(d); }); return state.endDate; }
    stopPlay();
    if (v.measure) state.measure = v.measure;
    if (v.measure || v.window !== null || v.distribution) {
      syncMetric();
      if (v.window !== null) syncWindow(v.window);
      if (v.distribution) setDistribution(v.distribution, true);
    }
    if (end !== null) setDay(end);
    if (d.iso) selectCountry(String(d.iso));
    else if (end === null) renderMap();
    renderSpark(); renderThemes(); renderLeaning(); renderPanel(); renderFindings();
    return state.endDate;
  }
  function dayIndex(day) {
    var i = typeof day === "number" ? day : -1;
    if (i === -1 && /^\d{4}-\d{2}-\d{2}$/.test(String(day))) {
      i = 0;
      tlDays.forEach(function (x, k) { if (x <= day) i = k; });
    }
    return i === -1 || isNaN(i) ? null : i;
  }
  function openDay(day) {
    var i = typeof day === "number" ? day : -1;
    if (i === -1 && /^\d{4}-\d{2}-\d{2}$/.test(String(day))) {
      i = 0;
      tlDays.forEach(function (d, k) { if (d <= day) i = k; });
    }
    if (i === -1 || isNaN(i)) return state.endDate;
    stopPlay();
    setDay(i);
    return state.endDate;
  }
  window.TrackerApp = {selectCountry: selectCountry, setDay: openDay, open: openView,
    /* Pure helpers, for tests/app_smoke.js. */
    helpers: {searchEntry: searchEntry, rankCountryMatches: rankCountryMatches, staleLines: staleLines, viewFields: viewFields,
              monthKeys: monthKeys, valueText: valueText, inkOn: inkOn, foldText: foldText, issuerLineText: issuerLineText, issuerBadge: issuerBadge, languageNotes: languageNotes}};
  document.addEventListener("tracker:select", function (ev) {
    var d = (ev && ev.detail) || {};
    openView(d);
    /* In one column the panel sits below the map and the list, so the country is brought into view there. */
    if (d.iso && window.matchMedia && window.matchMedia("(max-width: 1100px)").matches) { revealPanel(true); return; }
    var card = el("map-card");
    if (card && card.scrollIntoView) card.scrollIntoView({behavior: "smooth", block: "start"});
  });

  /* The preliminary figures banner under the title. index.html carries its default text, so it shows
     without JavaScript; here the sentences are rebuilt from content.json and meta (C.preliminaryBanner),
     so a hand check that has passed is never described as still to come. Without the content block the
     static text stays as it is. */
  function renderPreliminary() {
    var b = C.preliminaryBanner(state.content && state.content.preliminary_banner, state.meta);
    var title = el("prelim-title"), text = el("prelim-text");
    if (!b || !text) return;
    if (title && b.heading) {
      var at = b.headingEm ? b.heading.indexOf(b.headingEm) : -1;
      title.innerHTML = at === -1 ? esc(b.heading)
        : esc(b.heading.slice(0, at)) + "<em>" + esc(b.headingEm) + "</em>" + esc(b.heading.slice(at + b.headingEm.length));
    }
    var href = /^#[A-Za-z][\w-]*$/.test(b.linkHref) ? b.linkHref : "#method";
    text.innerHTML = esc(b.text) + (b.linkText ? ' <a href="' + href + '" id="prelim-link">' + esc(b.linkText) + "</a>" : "");
  }

  /* ------------------------------------------------------------------ init */
  function init() {
    loadAll().then(function () {
      try { setupMap(); } catch (e) { console.error("map setup failed", e); mapFallback("The map could not be drawn: " + (e && e.message ? e.message : e)); }
      if (!window.d3) mapFallback("The map library did not load.");
      else if (!state.topo) mapFallback("The world outline data did not load.");
      /* Each step runs on its own so that one failure leaves the rest of the page standing, and the
         notice names the step rather than the bare error type. */
      var steps = [
        ["renderPreliminary", renderPreliminary], ["bindControls", bindControls], ["setupCountryPicks", setupCountryPicks], ["setupTimeline", setupTimeline],
        ["setupThemeTimeline", setupThemeTimeline], ["setupStartHere", setupStartHere], ["renderNotices", renderNotices],
        ["renderMap", renderMap], ["renderThemes", renderThemes], ["renderLeaning", renderLeaning], ["renderPanel", renderPanel],
        ["renderMethod", renderMethod], ["renderTechnicalNotes", renderTechnicalNotes], ["renderArchiveCheck", renderArchiveCheck], ["renderFindings", renderFindings],
        ["renderStartHere", renderStartHere], ["renderExamples", renderExamples], ["renderPlanted", renderPlanted], ["measures", renderMeasures],
        ["renderFaq", renderFaq], ["renderCta", renderCta], ["renderLoadFailures", renderLoadFailures], ["renderStaleNotice", renderStaleNotice]
      ];
      var failed = [];
      steps.forEach(function (step) {
        try { step[1](); } catch (e) { console.error(step[0] + " failed", e); failed.push(step[0] + (e && e.message ? " (" + e.message + ")" : "")); }
      });
      if (failed.length) {
        var n = el("data-notice");
        if (n) {
          n.textContent = "Part of the page could not be drawn. " + (failed.length === 1 ? "This step failed: " : "These steps failed: ") + failed.join("; ") + ". Reload the page; if it happens again, the last export may be incomplete.";
          n.classList.remove("hidden");
        }
      }
    }).catch(function (e) {
      console.error("data load failed", e);
      el("data-notice").textContent = "Data could not be loaded: " + (e && e.message ? e.message : e);
      el("data-notice").classList.remove("hidden");
      /* Draw what can be drawn from empty state. Nothing here binds a listener: the controls are
         bound only in the success path, so a failure can never attach them twice. */
      try { setupMap(); renderMap(); renderThemes(); renderLeaning(); renderPanel(); renderMethod(); } catch (e2) { console.error("fallback render failed", e2); }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
