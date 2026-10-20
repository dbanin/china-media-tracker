/* Smoke test for docs/m_speed.js. Run with: node tests/smoke_m_speed.js, or under jsc with
   scratchpad/measures/run_jsc_measures.sh. */
(function () {
  var M = require("../docs/measures.js");
  var S = require("../docs/m_speed.js");
  function assert(cond, msg) { if (!cond) { console.error("FAIL m_speed: " + msg); process.exit(1); } }
  function buckets(o) {
    var b = {same_hour: 0, same_day: 0, days_1_3: 0, later: 0, same_date: 0, date_1_3: 0, date_later: 0,
             copy_precedes_original: 0, no_credible_time: 0};
    Object.keys(o || {}).forEach(function (k) { b[k] = o[k]; });
    return b;
  }
  function stat(n_timed, n_date, med, b) {
    return {n: n_timed + n_date, n_timed: n_timed, n_date: n_date, outside: (b.copy_precedes_original || 0) + (b.no_credible_time || 0),
            median_hours: med, p25_hours: med === null ? null : med / 2, p75_hours: med === null ? null : med * 2, buckets: buckets(b)};
  }
  function emptyKind() { return {overall: stat(0, 0, null, {}), by_route: [], by_country: [], by_source: [], by_outlet: []}; }
  var prov = {status: "provisional", label: "Republication speed", check: {state: "none", n: 0, coded: 0}};
  var api = function (slot) { return {id: "speed", slot: slot, esc: M.esc, countryName: function (i) { return i === "ITA" ? "Italy" : i; }, M: M}; };

  assert(M.get("speed") && S.ID === "speed", "registered");
  assert(S.fmtHours(0.5) === "30 min" && S.fmtHours(6) === "6 h" && S.fmtHours(72) === "3 days" && S.fmtHours(null) === "n/a", "fmtHours");
  assert(S.fmtHours(-0.2) === "under 1 min", "a median a little ahead reads as under a minute");

  /* Off: hidden and cleared. */
  var slot = {hidden: false, innerHTML: "stale"};
  S.render({status: "off"}, null, api(slot));
  assert(slot.hidden === true && slot.innerHTML === "", "off hides");

  /* Provisional, file did not load; withheld. */
  S.render(prov, null, api(slot));
  assert(slot.hidden === false && slot.innerHTML.indexOf("did not load") !== -1, "no data");
  S.render({status: "withheld", label: "Republication speed", check: {state: "failed", n: 60, coded: 60, statistic: "bucket agreement", value: 0.7, threshold: 0.85}},
           {absolute: emptyKind(), relative: emptyKind()}, api(slot));
  assert(slot.innerHTML.indexOf("withheld") !== -1 && slot.innerHTML.indexOf("sp-bar") === -1, "withheld shows no figure");

  /* Provisional with no pair inside the distributions: the empty state, plus what is outside. */
  var empty = {absolute: emptyKind(), relative: emptyKind(), coverage: {copies: 5, reference: 1, monitored: 0, none: 4}, diagnostics: []};
  empty.absolute.overall = stat(0, 0, null, {no_credible_time: 1});
  S.render(prov, empty, api(slot));
  assert(slot.innerHTML.indexOf("No paired copy has two credible") !== -1 && slot.innerHTML.indexOf("sp-bar") === -1, "empty state");
  assert(slot.innerHTML.indexOf("1 pair had no credible time") !== -1 && slot.innerHTML.indexOf("Of 5 state origin copies") !== -1, "outside counts and coverage");
  assert(slot.innerHTML.indexOf("m-chip-provisional") !== -1, "status chip");

  /* Populated: bars per route for each kind, never pooled, a ranked outlet table, flagged sources. */
  var data = {
    min_outlet_pairs: 10, min_group_median: 5,
    absolute: {
      overall: stat(30, 2, 3.5, {same_hour: 10, same_day: 15, days_1_3: 3, later: 2, same_date: 2, copy_precedes_original: 1}),
      by_route: [{key: "wire_credit", label: "Wire credit line or dateline", n: 32, n_timed: 30, median_hours: 3.5, buckets: buckets({same_hour: 10, same_day: 15, days_1_3: 3, later: 2, same_date: 2})},
                 {key: "uncredited_copy", label: "<Uncredited>", n: 0, n_timed: 0, median_hours: null, buckets: buckets({})}],
      by_country: [], by_source: [],
      by_outlet: [{key: "o_it", name: "Italpress", country: "ITA", n: 12, n_timed: 12, median_hours: 0.5, p25_hours: 0.2, p75_hours: 2, buckets: buckets({same_hour: 8, same_day: 4})},
                  {key: "o_slow", name: "Slow Daily", country: "WLD", n: 11, n_timed: 11, median_hours: 30, p25_hours: 20, p75_hours: 50, buckets: buckets({same_day: 3, days_1_3: 8})}]
    },
    relative: {overall: stat(6, 0, 20, {same_day: 4, days_1_3: 2}), by_route: [{key: "wire_credit", label: "Wire credit line or dateline", n: 6, n_timed: 6, median_hours: 20, buckets: buckets({same_day: 4, days_1_3: 2})}], by_country: [], by_source: [], by_outlet: []},
    coverage: {copies: 100, reference: 33, monitored: 6, none: 61},
    diagnostics: [{source: "ref_ecns_en", flags: ["published after discovery"], share_negative: 0.8, share_date_only: 0},
                  {source: "ref_cgtn_en", flags: [], share_negative: 0, share_date_only: 0}]
  };
  data.absolute.overall.originals = 9;
  data.absolute.overall.originals_timed = 8;
  data.absolute.by_outlet[0].originals_timed = 11;
  S.render(prov, data, api(slot));
  var h = slot.innerHTML;
  assert(h.indexOf("Behind the state original") !== -1 && h.indexOf("Behind the first monitored copy") !== -1, "two kinds apart");
  assert((h.match(/class="sp-row"/g) || []).length === 4, "all routes plus one route for each kind; empty routes skipped");
  assert(h.indexOf("&lt;Uncredited&gt;") === -1 && h.indexOf("<Uncredited>") === -1, "routes without pairs are skipped");
  assert(h.indexOf("Half of the 30 copies") !== -1 && h.indexOf("3.5 h") !== -1 && h.indexOf("83%") !== -1, "lead sentence");
  assert(h.indexOf("with a timed state original (8 distinct originals) ran within") !== -1, "distinct originals in the lead");
  assert(h.indexOf("32 pairs, 9 originals") !== -1 && h.indexOf(">11<") !== -1, "distinct originals beside the pairs and in the table");
  assert(h.indexOf("Italpress") < h.indexOf("Slow Daily") && h.indexOf("Italy") !== -1 && h.indexOf("30 min") !== -1, "ranked outlet table");
  assert(h.indexOf("1 copy ran more than an hour ahead") !== -1, "copies ahead are counted outside");
  assert(h.indexOf("ECNS need care") !== -1 && h.indexOf("80%") !== -1 && h.indexOf("CGTN English need care") === -1, "flagged sources only");
  assert(h.indexOf("sp-same_date") !== -1, "date only buckets drawn apart");
  assert(h.indexOf("\u2014") === -1 && h.indexOf("\u2013") === -1, "no dashes");

  /* Only relative pairs: no outlet table, the lead says so. */
  S.render(prov, {absolute: emptyKind(), relative: data.relative, coverage: {}, diagnostics: []}, api(slot));
  assert(slot.innerHTML.indexOf("No state original has timed pairs of enough distinct originals yet") !== -1 && slot.innerHTML.indexOf("Fastest outlets") === -1, "relative only");
  assert(slot.innerHTML.indexOf("outside the copy&#39;s own media group") !== -1 || slot.innerHTML.indexOf("outside the copy's own media group") !== -1, "relative lag is behind another group's copy");

  /* New flags, and the reference sources' fetch status. */
  var flagged = JSON.parse(JSON.stringify(data));
  flagged.diagnostics = [{source: "ref_cgtn_en", flags: ["earlier than expected"], time_check: {share_earlier: 0.9}},
                         {source: "ref_global_times_en", flags: ["date only after day one"], share_date_only_after_day_one: 0.42}];
  flagged.reference_sources = [{source: "ref_cgtn_en", items: 992, statuses: {fetched: 100, expired: 800, failed: 3, paywalled: 0}}];
  S.render(prov, flagged, api(slot));
  var f = slot.innerHTML;
  assert(f.indexOf("CGTN English need care: 90% of the items it lists twice read more than an hour earlier") !== -1, "earlier than expected flag");
  assert(f.indexOf("Global Times need care: 42% still give only a date after their first day") !== -1, "date only after day one flag");
  assert(f.indexOf("CGTN English 100 of 992 fetched, 800 expired unread, 3 failed") !== -1, "fetch status per source");

  /* Outlets below the minimum: no ranking. */
  var few = JSON.parse(JSON.stringify(data));
  few.absolute.by_outlet = [];
  S.render(prov, few, api(slot));
  assert(slot.innerHTML.indexOf("No outlet has timed pairs of 10 distinct originals yet") !== -1, "no outlet ranked");

  /* No slot is fine; an api without helpers works. */
  S.render(prov, data, {id: "speed", slot: null});
  var bare = {hidden: true, innerHTML: ""};
  S.render(prov, data, {slot: bare});
  assert(bare.hidden === false && bare.innerHTML.indexOf("Italpress") !== -1, "bare api");

  console.log("m_speed smoke ok");
}());
