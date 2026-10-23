/* Smoke test for the front end computations on an empty dataset. Run with: node tests/frontend_smoke.js */
var C = require("../docs/compute.js");
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
var agg = C.aggregateWindow({}, null, 30);
assert(Object.keys(agg.countries).length === 0, "empty aggregate");
assert(C.listDays({}).length === 0, "no days");
var mv = C.metricValue(undefined, "count_china", 0, "all", undefined);
assert(mv.value === 0 && mv.pending === 0, "china count on empty");
mv = C.metricValue(undefined, "share_b", 0, "all", undefined);
assert(mv.value === null && mv.chinaTotal === 0, "share undefined on empty");
assert(C.fillClass(undefined, mv) === "nocoverage", "no entry is nocoverage");
assert(C.fillClass({coverage: "monitored", outlets_active: 3}, mv) === "nodata", "monitored without coverage is nodata");
assert(C.fillClass({coverage: "monitored", outlets_active: 3}, {value: 0, chinaTotal: 5}) === "zero", "zero detections");
assert(C.fillClass({coverage: "gap"}, mv) === "gap", "gap");
assert(C.rankCountries(agg, {countries: {}}, "count_a", "all", {}).length === 0, "rank empty");
assert(C.toCSV([], ["a"]) === "a\n", "csv header only");
var months = {"2026-09": {days: {"2026-09-01": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 2, C: 3})}, reviewed: {}, llm_ceiling_hit: false, routes: {ITA: {wire_credit: 2}}},
                                 "2026-09-02": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 1, B: 1, C: 1})}, reviewed: {}, llm_ceiling_hit: true, relay_incomplete: true,
                                                routes: {ITA: {diplomatic_byline: 1}}, arrivals: {ITA: {press_release_section: 1}}}}}};
var a2 = C.aggregateWindow(months, "2026-09-02", 2);
assert(a2.countries.ITA.A === 3 && a2.countries.ITA.B === 1 && a2.ceilingDays.length === 1 && a2.relayIncompleteDays.length === 1, "window sum");
assert(a2.routes.ITA.wire_credit === 2 && a2.routes.ITA.diplomatic_byline === 1 && C.routeCount(a2, "ITA", {kind: "arrival", id: "press_release_section"}) === 1, "routes and arrivals");
var a1 = C.aggregateWindow(months, "2026-09-02", 1);
assert(a1.countries.ITA.A === 1, "single day");
assert(Math.abs(C.metricValue(a2.countries.ITA, "share_a", 5, "all").value - 3 / 8) < 1e-9, "share");
/* Unverified relay is never shown as zero when it has not been measured, and is withheld until publishable. */
var unmeasured = C.metricValue(a2.countries.ITA, "count_b", 5, "all", undefined, {relay: {measured: false, publishable: false}});
assert(unmeasured.value === null && unmeasured.withheld && unmeasured.note === C.RELAY_NOT_MEASURED, "relay not measured");
assert(C.fillClass({coverage: "monitored", outlets_active: 5}, unmeasured) === "withheld", "withheld fill");
var withheld = C.metricValue(a2.countries.ITA, "share_b", 5, "all", undefined, {relay: {measured: true, publishable: false}});
assert(withheld.value === null && withheld.note === C.RELAY_WITHHELD, "relay withheld");
assert(C.metricValue(a2.countries.ITA, "count_a", 5, "all", undefined, {relay: {measured: false, publishable: false}}).value === 3, "state origin unaffected by the relay gate");
var rows = C.rankCountries(a2, {countries: {ITA: {coverage: "monitored", outlets_active: 5}}}, "count_a", "all", {}, function () { return {relay: {measured: false, publishable: false}}; });
assert(rows[0].unverified_relay === "" && rows[0].relay_status === "not measured", "csv leaves unmeasured relay blank");
/* The route filter replaces the state origin count and applies to state origin only. */
assert(C.metricValue(a2.countries.ITA, "count_a", 5, "all", undefined, {routeCount: 1}).value === 1, "route filter");
assert(C.metricValue(a2.countries.ITA, "count_china", 5, "all", undefined, {routeCount: 1}).value === null, "route filter is state origin only");
var withPending = Object.assign(C.emptyCounts(), {A: 1, C: 2, pending: 3});
var tv = C.metricValue(withPending, "count_china", 2, "all");
assert(tv.value === 6 && tv.chinaTotal === 6 && tv.pending === 3 && tv.target === undefined, "pending counts as coverage and in no other measure");
assert(C.metricValue(withPending, "count_a", 2, "all").value === 1 && Math.abs(C.metricValue(withPending, "share_a", 2, "all").value - 1 / 6) < 1e-9, "state origin share");
var tiny = C.metricValue(Object.assign(C.emptyCounts(), {A: 2}), "share_a", 1, "all");
assert(tiny.value === null && tiny.sparse === true, "tiny denominator gives no share");
assert(C.fillClass({coverage: "monitored", outlets_active: 1}, tiny) === "sparse", "sparse fill");
assert(C.metricValue(Object.assign(C.emptyCounts(), {A: 2}), "count_a", 1, "all").value === 2, "count still shown");
var all = Object.assign(C.emptyCounts(), {A: 1, C: 2, pending: 1, tdisc: 200, ta: 2, ttarget: 3, tchina: 4});
assert(Math.abs(C.metricValue(all, "share_of_output_a", 2, "all", undefined, {topOutlets: 8}).value - 0.01) < 1e-9, "share of monitored output");
assert(Math.abs(C.metricValue(all, "share_of_output_china", 2, "all", undefined, {topOutlets: 8}).value - 0.02) < 1e-9, "china share of monitored output");
var floor = C.metricValue(all, "share_of_output_a", 2, "all", undefined, {topOutlets: 4});
assert(floor.value === null && floor.sparse === true && /Fewer than 5 monitored outlets/.test(floor.note), "outlet floor");
var few = Object.assign(C.emptyCounts(), {A: 1, tdisc: 10, ta: 1});
assert(C.metricValue(few, "share_of_output_a", 1, "all").value === null, "small stream gives no share");
assert(C.fillClass({coverage: "monitored", outlets_active: 1}, C.metricValue(Object.assign(C.emptyCounts(), {tdisc: 300}), "share_of_output_a", 1, "all")) === "zero", "items but no China coverage is zero, not nodata");
assert(C.fillClass({coverage: "monitored", outlets_active: 2, language_support: "none"}, C.metricValue(C.emptyCounts(), "count_a", 2, "all")) === "unreadable", "unreadable language");
assert(Math.abs(C.percentile([1, 2, 3, 4, 100], 0.95) - 80.8) < 1e-9 && C.percentile([], 0.95) === null && C.percentile([5], 0.95) === 5, "percentile");
assert(C.gridMetric("a", "share_of_output") === "share_of_output_a" && C.gridMetric("china", "count") === "count_china" && C.gridMetric("nope", "nope") === "count_a", "grid");
/* No per capita basis: a saved or stale per million selection falls back to Number of articles in the same measure. */
assert(C.BASES.indexOf("per_million") === -1 && Object.keys(C.METRICS).every(function (k) { return k.indexOf("per_million") !== 0; }), "no per capita basis or metric");
["a", "b", "china"].forEach(function (m) {
  assert(!C.basisAvailable(m, "per_million") && C.gridMetric(m, "per_million") === C.GRID[m].count, "per million falls back to the count for " + m);
  assert(C.BASIS_NAMES[C.metricParts(C.gridMetric(m, "per_million")).basis] === "Number of articles", "the fallback is named Number of articles for " + m);
});
/* Share of China coverage is a denominator like the others, and is meaningless for All China coverage. */
assert(C.gridMetric("a", "share_of_china") === "share_a" && C.gridMetric("b", "share_of_china") === "share_b", "share of China coverage is a basis");
/* No combined state-linked measure: it mixed judged counts with candidates still waiting for the model.
   A saved or stale selection of it falls back to state origin, never to another sum. */
assert(C.GRID.target === undefined && C.MEASURE_NAMES.target === undefined && C.MEASURE_INDEX.target === undefined &&
       Object.keys(C.METRICS).every(function (k) { return k.indexOf("target") === -1 && C.METRICS[k].measure !== "target"; }), "no state-linked measure");
assert(C.gridMetric("target", "count") === "count_a" && C.gridMetric("target", "share_of_china") === "share_a" && C.basisAvailable("target", "share_of_china"), "a state-linked selection falls back to state origin");
assert(C.metricValue(Object.assign(C.emptyCounts(), {pending: 7}), "count_target", 3, "all").value === null, "a removed metric reads nothing");
assert(C.basisAvailable("a", "share_of_china") && !C.basisAvailable("china", "share_of_china"), "not offered for All China coverage");
assert(C.gridMetric("china", "share_of_china") === "count_china", "an unavailable pair falls back inside its own measure");
assert(C.METRICS.count_ab === undefined && C.METRICS.share_ab === undefined, "the combined metrics are gone");
var allA = Object.assign(C.emptyCounts(), {A: 2, C: 8, tdisc: 400, ttarget: 3, tchina: 10, ta: 2});
assert(Math.abs(C.metricValue(allA, "share_of_output_a", 1, "all").value - 0.005) < 1e-9, "state origin share of monitored output");
assert(C.metricValue(allA, "count_china", 1, "all").value === 10, "china count");
assert(C.metricValue(allA, "per_outlet_china", 2, "all").value === 5, "china per outlet");
/* The scale cap is pooled across dates, so it does not move with the day shown. */
var latest = {countries: {ITA: {coverage: "monitored", outlets_active: 5}, FRA: {coverage: "monitored", outlets_active: 5}}};
var scaleMonths = {"2026-09": {days: {"2026-09-01": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 10, C: 1}), FRA: Object.assign(C.emptyCounts(), {A: 1, C: 1})}},
                                      "2026-09-02": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 2, C: 1}), FRA: Object.assign(C.emptyCounts(), {A: 2, C: 1})}}}}};
var s = C.scaleCap(scaleMonths, ["2026-09-01", "2026-09-02"], latest, "count_a", 1, "all");
assert(s.values === 4 && s.max === 10 && Math.abs(s.cap - C.percentile([10, 1, 2, 2], 0.95)) < 1e-9, "pooled scale cap");
/* The cap is a function of the published data alone: the same files give the same cap, and a data update
   that adds a day recomputes it, so it moves between updates. Every key says so in the same words. */
assert(C.scaleCap(scaleMonths, ["2026-09-01", "2026-09-02"], latest, "count_a", 1, "all").cap === s.cap, "scale cap repeats on the same data");
var grownMonths = JSON.parse(JSON.stringify(scaleMonths));
grownMonths["2026-09"].days["2026-09-03"] = {countries: {ITA: Object.assign(C.emptyCounts(), {A: 40, C: 1}), FRA: Object.assign(C.emptyCounts(), {A: 30, C: 1})}};
var grown = C.scaleCap(grownMonths, ["2026-09-01", "2026-09-02", "2026-09-03"], latest, "count_a", 1, "all");
assert(grown.cap > s.cap && grown.max === 40, "a data update that adds days moves the scale cap");
assert(/each data update/i.test(C.CAP_UPDATE_NOTE) && /different number/.test(C.CAP_UPDATE_NOTE) && !/[–—]/.test(C.CAP_UPDATE_NOTE), "the keys say the cap moves between updates");

var themeMonths = {"2026-09": {days: {"2026-09-01": {countries: {}, reviewed: {}, themes: {ITA: {diplomacy: [2, 1, 1]}}},
                                      "2026-09-02": {countries: {}, reviewed: {}, themes: {ITA: {diplomacy: [1, 1, 0], culture: [3, 0, 0]}}}}}};
var th = C.aggregateThemes(themeMonths, "2026-09-02", 2);
assert(th.ITA.diplomacy[0] === 3 && th.ITA.diplomacy[1] === 2 && th.ITA.diplomacy[2] === 1 && th.ITA.culture[0] === 3, "theme window sum");
assert(C.aggregateThemes(themeMonths, "2026-09-02", 1).ITA.diplomacy[0] === 1, "theme single day");
assert(Object.keys(C.aggregateThemes({}, null, 30)).length === 0 && C.MEASURE_INDEX.target === undefined && C.MEASURE_INDEX.china === 0, "themes on empty");
/* Unverified relay as a measure of its own, and the provisional state before hand coding has checked it. */
assert(C.gridMetric("b", "count") === "count_b" && C.gridMetric("b", "share_of_output") === "share_of_output_b" && C.MEASURE_INDEX.b === 3, "relay grid");
var relayCounts = Object.assign(C.emptyCounts(), {A: 1, B: 4, C: 5, uniqA: 1, uniqAB: 4, tdisc: 100, tb: 4});
var PROV = {measured: true, publishable: false, provisional: true};
var prov = C.metricValue(relayCounts, "count_b", 5, "all", undefined, {relay: PROV});
assert(prov.value === 4 && prov.provisional === true && !prov.withheld && prov.underlyingB === 3 && prov.note === C.RELAY_PROVISIONAL, "provisional relay is shown and marked");
assert(C.fillClass({coverage: "monitored", outlets_active: 5}, prov) === "value", "provisional relay colours the map");
assert(C.metricValue(relayCounts, "count_china", 5, "all", undefined, {relay: PROV}).value === 10, "all China coverage stays readable while provisional");
var failed = C.metricValue(relayCounts, "count_b", 5, "all", undefined, {relay: {measured: true, publishable: false}});
assert(failed.value === null && failed.withheld && failed.note === C.RELAY_WITHHELD, "measured but neither publishable nor provisional stays withheld");
assert(C.metricValue(relayCounts, "count_b", 5, "all", undefined, {relay: {measured: true, publishable: true}}).provisional === false, "published relay is not provisional");
assert(Math.abs(C.metricValue(relayCounts, "share_b", 5, "all").value - 0.4) < 1e-9, "relay share of China coverage");
assert(Math.abs(C.metricValue(relayCounts, "share_of_output_b", 5, "all", undefined, {topOutlets: 6}).value - 0.04) < 1e-9, "relay share of monitored output");
assert(C.metricValue(relayCounts, "share_of_output_b", 5, "all", undefined, {topOutlets: 6, noRelayOutput: true}).value === null, "no false zero from data that predates tb");
assert(C.metricValue(relayCounts, "count_b", 5, "all", undefined, {routeCount: 1}).value === null, "route filter does not apply to relay");
var provRows = C.rankCountries({countries: {ITA: relayCounts}, reviewed: {}}, {countries: {ITA: {coverage: "monitored", outlets_active: 5}}}, "count_b", "all", {}, function () { return {relay: PROV}; });
assert(provRows[0].relay_status === "provisional" && provRows[0].unverified_relay === 4 && provRows[0].unverified_relay_underlying_items === 3, "csv carries provisional relay and says so");
assert(C.relayStatus({measured: true, publishable: false}) === "withheld" && C.relayStatus({measured: false, publishable: false}) === "not measured", "relay status");
var fourSlot = {"2026-09": {days: {"2026-09-01": {countries: {}, reviewed: {}, themes: {ITA: {diplomacy: [2, 1, 1]}}},
                                   "2026-09-02": {countries: {}, reviewed: {}, themes: {ITA: {diplomacy: [3, 2, 0, 2]}}}}}};
var th4 = C.aggregateThemes(fourSlot, "2026-09-02", 2).ITA.diplomacy;
assert(th4[0] === 5 && th4[3] === 2 && th4.length === 4, "fourth theme slot, and three slot files still read");
/* A value that could not be computed is never an observed zero: every branch that refuses to compute
   one marks it sparse, so fillClass gives it the "not shown" pattern instead of falling through to zero. */
var monitored = {coverage: "monitored", outlets_active: 6};
var stream = Object.assign(C.emptyCounts(), {A: 3, C: 9, tdisc: 400, ta: 3, ttarget: 3, tchina: 12});
var byRouteShare = C.metricValue(stream, "share_of_output_a", 6, "all", undefined, {topOutlets: 8, routeCount: 2});
assert(byRouteShare.value === null && byRouteShare.sparse === true && C.fillClass(monitored, byRouteShare) === "sparse", "share of output by route is not shown, never zero");
var reviewedShare = C.metricValue(stream, "share_of_output_a", 6, "reviewed", {A: 1, B: 0, C: 1, N: 0}, {topOutlets: 8});
assert(reviewedShare.value === null && reviewedShare.sparse === true && C.fillClass(monitored, reviewedShare) === "sparse", "share of output in reviewed mode is not shown, never zero");
var oldExport = C.metricValue(Object.assign(C.emptyCounts(), {A: 1, B: 2, C: 9, tdisc: 400}), "share_of_output_b", 6, "all", undefined, {topOutlets: 8, noRelayOutput: true});
assert(oldExport.value === null && oldExport.sparse === true && C.fillClass(monitored, oldExport) === "sparse", "a share the export cannot carry is not shown, never zero");
var offRouteCount = C.metricValue(stream, "count_china", 6, "all", undefined, {routeCount: 2});
assert(offRouteCount.value === null && offRouteCount.sparse === true, "the route filter leaves other measures not shown, never zero");
/* The sums that contain unchecked state sourcing are never withheld, and carry its provisional marking. */
var insideProv = C.metricValue(relayCounts, "share_of_output_china", 5, "all", undefined, {relay: PROV, topOutlets: 6});
assert(insideProv.provisional === true && !insideProv.withheld && insideProv.note === C.RELAY_PROVISIONAL, "a share of all China coverage is marked provisional while the relay count inside it is");
assert(C.metricValue(relayCounts, "count_china", 5, "all", undefined, {relay: PROV}).provisional === true, "all China coverage is marked provisional too");
assert(C.metricValue(relayCounts, "count_a", 5, "all", undefined, {relay: PROV}).provisional === false, "state origin contains no relay count, so it is not marked");
assert(C.METRICS.count_china.relayInside && !C.METRICS.count_a.relayInside, "which metrics contain the relay count");
/* A withheld count cannot be reconstructed from the columns beside it. */
var WITHHELD = {measured: true, publishable: false, provisional: false};
var wLatest = {countries: {ITA: {coverage: "monitored", outlets_active: 5}}};
var wAgg = {countries: {ITA: relayCounts}, reviewed: {}};
var wa = C.rankCountries(wAgg, wLatest, "count_a", "all", {}, function () { return {relay: WITHHELD}; })[0];
assert(wa.unverified_relay === "" && wa.target === undefined && wa.china_total === "" && wa.target_in_published_items === undefined && wa.china_in_published_items === "", "no sum containing a withheld count is written beside it");
assert(wa.state_origin === 1 && wa.independent === 5, "state origin and independent journalism still travel when the shown value contains no relay count");
var wt = C.rankCountries(wAgg, wLatest, "count_china", "all", {}, function () { return {relay: WITHHELD}; })[0];
assert(wt.value === 10 && wt.state_origin === "" && wt.official_sourcing_pending === "", "the counts the shown sum would be differenced against leave with it");
/* A route narrows state origin and nothing else, so the counts it does not touch are not written beside it. */
var rr = C.rankCountries({countries: {ITA: relayCounts}, reviewed: {}}, wLatest, "count_a", "all", {}, function () { return {relay: {measured: true, publishable: true}, routeCount: 1}; })[0];
assert(rr.value === 1 && rr.state_origin === 1 && rr.china_total === "" && rr.state_origin_underlying_items === "" && rr.items_published_monitored_outlets === "" && /route/.test(rr.note), "a route filtered row carries only what the route filtered");
/* CSV: a lone carriage return breaks a row, and a leading =, +, - or @ runs as a formula. */
assert(C.toCSV([{a: "one\rtwo"}], ["a"]) === 'a\n"one\rtwo"\n', "a carriage return is quoted");
assert(C.toCSV([{a: "=1+1"}, {a: "@x"}, {a: "-lead"}], ["a"]) === 'a\n"\'=1+1"\n"\'@x"\n"\'-lead"\n', "a formula is quoted and marked as text");
assert(C.toCSV([{a: -3.5}, {a: 0.25}], ["a"]) === "a\n-3.5\n0.25\n", "a negative number is still a number");
/* The color key. Its tiers are exactly what the map colors with, and every printed bound is distinct. */
function keyNumbers(label) { return (label.match(/[0-9][0-9,]*(\.[0-9]+)?/g) || []).map(function (s) { return Number(s.replace(/,/g, "")); }); }
[0.4, 1, 1.9, 2, 3, 5, 7, 10, 26.3, 49, 192.4, 1000.5, 25000].forEach(function (cap) {
  var k = C.keyTiers(cap, C.keyUnit("count_a"));
  var labels = k.tiers.map(function (t) { return t.label; });
  assert(k.tiers.length >= 1 && k.tiers.length <= C.KEY_STEPS, "count key has between one and seven tiers at cap " + cap);
  assert(new Set(labels).size === labels.length, "no count tier is printed twice at cap " + cap);
  assert(k.tiers[0].lo === 1 && /or more$/.test(labels[labels.length - 1]), "count tiers start at 1 and end open at cap " + cap);
  k.tiers.forEach(function (t, i) {
    assert(Number.isInteger(t.lo) && (t.hi === null || (Number.isInteger(t.hi) && t.hi >= t.lo)), "whole-number tier " + t.label + " at cap " + cap);
    keyNumbers(t.label).forEach(function (n) { assert(Number.isInteger(n), "no decimal in a count tier: " + t.label); });
    if (i) assert(t.lo === k.tiers[i - 1].hi + 1, "count tiers are contiguous at cap " + cap);
    assert(i === 0 || t.index > k.tiers[i - 1].index, "each count tier has its own shade at cap " + cap);
  });
  /* Every whole number takes the shade of the tier whose label contains it. */
  for (var v = 1; v <= Math.min(3 * cap + 3, 400); v++) {
    var hit = k.tiers.filter(function (t) { return v >= t.lo && (t.hi === null || v <= t.hi); });
    assert(hit.length === 1 && hit[0].index === C.keyIndex(v, k.edges), "value " + v + " is colored as its tier says at cap " + cap);
  }
});
assert(C.keyTiers(1, C.WHOLE_UNIT).tiers.length === 1 && C.keyTiers(1, C.WHOLE_UNIT).tiers[0].label === "1 or more", "a cap of one gives one tier, not seven copies of it");
assert(C.keyTiers(2, C.WHOLE_UNIT).tiers.map(function (t) { return t.label; }).join("|") === "1|2 or more", "a cap of two gives two tiers");
/* Equal share cut points (all China coverage): each shade holds about a seventh of the pooled values,
   pooled over the dates whose window is complete, and every value still takes exactly one tier. */
var spreadMonths = {"2026-09": {days: {}}};
for (var sd = 1; sd <= 9; sd++) {
  var dkey = "2026-09-0" + sd, dc = {};
  ["ITA", "FRA", "DEU", "ESP"].forEach(function (iso, j) { dc[iso] = Object.assign(C.emptyCounts(), {C: Math.pow(3, j) * sd}); });
  spreadMonths["2026-09"].days[dkey] = {countries: dc};
}
var spreadLatest = {countries: {ITA: {coverage: "monitored", outlets_active: 5}, FRA: {coverage: "monitored", outlets_active: 5},
                                DEU: {coverage: "monitored", outlets_active: 5}, ESP: {coverage: "monitored", outlets_active: 5}}};
var spreadDays = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09"];
var sp = C.scaleCap(spreadMonths, spreadDays, spreadLatest, "count_china", 3, "all");
assert(sp.breaks && sp.breaks.length === 6, "six equal share cut points for seven shades");
assert(sp.breaks.every(function (b, i) { return i === 0 || b >= sp.breaks[i - 1]; }), "cut points rise");
/* The two short windows (ending on the 1st and 2nd) are left out of the pool: its smallest value is a full window's. */
var fullOnly = [];
spreadDays.slice(2).forEach(function (d) {
  var agg = C.aggregateWindow(spreadMonths, d, 3);
  Object.keys(agg.countries).forEach(function (iso) { fullOnly.push(C.metricValue(agg.countries[iso], "count_china", 5, "all").value); });
});
assert(Math.abs(sp.breaks[0] - C.percentile(fullOnly, 1 / 7)) < 1e-9, "cut points pooled over complete windows only");
var spk = C.keyTiers(sp.cap, C.keyUnit("count_china"), 7, sp.breaks);
var spCounts = {};
fullOnly.forEach(function (v) {
  var hit = spk.tiers.filter(function (t) { return v >= t.lo && (t.hi === null || v <= t.hi); });
  assert(hit.length === 1 && hit[0].index === C.keyIndex(v, spk.edges), "value " + v + " is colored as its equal share tier says");
  spCounts[hit[0].index] = (spCounts[hit[0].index] || 0) + 1;
});
assert(Object.keys(spCounts).length >= 5 && Math.max.apply(null, Object.keys(spCounts).map(function (k) { return spCounts[k]; })) <= Math.ceil(fullOnly.length / 3),
       "no shade holds most of the values under equal share cut points");
assert(C.keyTiers(sp.cap, C.keyUnit("count_china"), 7).edges.join() !== spk.edges.join(), "without cut points the key keeps its steps up to the cap");
/* Everything that is not an amount: distinct, increasing bounds at any scale, and never an "under 0". */
["per_outlet_a", "share_a", "share_of_output_a", "share_of_output_b", "per_outlet_china"].forEach(function (metric) {
  [0.00003, 0.0004, 0.0021, 0.008, 0.05, 0.32, 1, 2.94, 17.8, 250, 12345].forEach(function (cap) {
    if (C.METRICS[metric].format === "pct" && cap > 1) return;
    var k = C.keyTiers(cap, C.keyUnit(metric));
    assert(k.tiers.length === C.KEY_STEPS, metric + " keeps seven tiers at cap " + cap);
    var bounds = [];
    k.tiers.forEach(function (t, i) {
      assert(i > 0 || t.label.indexOf("Above 0, under ") === 0, metric + " lowest tier opens above zero");
      var ns = keyNumbers(t.label.replace(/^Above 0, /, ""));
      assert(!/under 0(\.0*)?(%|$)/.test(t.label) && ns.every(function (n) { return n > 0; }), metric + " prints no zero bound: " + t.label);
      if (i === 0) bounds.push(ns[ns.length - 1]); else if (t.hi === null) assert(ns[0] === bounds[bounds.length - 1], metric + " top tier opens at the last bound");
      else { assert(ns[0] === bounds[bounds.length - 1], metric + " tiers meet: " + t.label); bounds.push(ns[1]); }
    });
    for (var j = 1; j < bounds.length; j++) assert(bounds[j] > bounds[j - 1], metric + " prints distinct increasing bounds at cap " + cap + ": " + bounds.join(", "));
    /* The printed bounds are the edges the map uses, in the key's unit. */
    k.edges.forEach(function (e, i) { assert(Math.abs(e * k.unit.factor - bounds[i]) < 1e-9 * Math.max(1, bounds[i]), metric + " edge " + i + " is what the key prints"); });
  });
});
/* Tiny shares read per 1,000 published items, never as 0.0%. */
var tinyShare = C.keyTiers(0.008, C.keyUnit("share_of_output_a")).tiers.map(function (t) { return t.label; });
assert(C.keyUnit("share_of_output_a").per1000 && C.keyUnit("share_of_output_a").factor === 1000, "share of monitored output is keyed per 1,000 items");
assert(tinyShare[0] === "Above 0, under 0.16" && tinyShare[6] === "5.9 or more" && tinyShare.join("|").indexOf("%") === -1, "a share under one percent reads per 1,000: " + tinyShare.join(" | "));
assert(C.keyTiers(0.45, C.keyUnit("share_a")).tiers[6].label === "33% or more" && C.keyTiers(0.012, C.keyUnit("share_a")).tiers[0].label === "Above 0, under 0.024%", "share of China coverage keeps percent, with precision to suit the cap");
assert(C.formatKeyNumber(0.000114, C.keyUnit("share_of_output_a"), 3) === "0.114" && C.formatKeyNumber(255, C.WHOLE_UNIT) === "255" && C.formatKeyNumber(12500, C.WHOLE_UNIT) === "12,500", "key numbers");
var parts = C.metricParts("share_of_output_b");
assert(parts.measure === "b" && parts.basis === "share_of_output" && C.BASIS_NAMES[parts.basis] === "% of all its news" && C.MEASURE_NAMES.b === "Unchecked state sourcing", "the key names measure and basis as the buttons do");
/* The theme counter's cap is pooled over dates and reads one measure slot. */
var tsc = C.themeScaleCap(themeMonths, ["2026-09-01", "2026-09-02"], 1, 0, ["diplomacy", "culture"]);
assert(tsc.values === 3 && tsc.max === 3 && Math.abs(tsc.cap - C.percentile([2, 1, 3], 0.95)) < 1e-9, "theme scale cap pools days");
assert(C.themeScaleCap(themeMonths, ["2026-09-01", "2026-09-02"], 2, 2, null, "ITA").max === 1 && C.themeScaleCap({}, [], 1, 0).cap === null, "theme scale cap by slot and country");
var grownThemes = JSON.parse(JSON.stringify(themeMonths));
grownThemes["2026-09"].days["2026-09-03"] = {countries: {}, reviewed: {}, themes: {ITA: {diplomacy: [9, 0, 0], culture: [7, 0, 0]}}};
assert(C.themeScaleCap(grownThemes, ["2026-09-01", "2026-09-02", "2026-09-03"], 1, 0, ["diplomacy", "culture"]).cap > tsc.cap, "a data update that adds days moves the theme cap too");

/* The "What we found" box: fills the four findings_template sentences from meta.findings. */
var findingsTemplates = ["Share {state_share_of_china} since {since}.", "{top_country} led, carried by {top_carrier}.",
  "State-controlled outlets published at roughly {rate_ratio} the rate.", "{top_ownership_group} outlets led."];
var findings = {
  since: "2026-09-03",
  all_time: {state_origin: 100, unchecked: 20, independent: 300, china_total: 420, state_share_of_china: 0.2381},
  top_country: {iso: "ITA", state_origin: 50, share_of_world: 0.5, top_outlet: {id: "italpress", name: "Italpress"}, top_route: "wire_credit"},
  by_ownership: {groups: [{id: "state", articles: 40, outlets: 10, per_outlet_rate: 4}, {id: "private", articles: 10, outlets: 20, per_outlet_rate: 0.5},
                           {id: "party", articles: 0, outlets: 2, per_outlet_rate: 0}], top: {id: "state", per_outlet_rate: 4}}
};
var filled = C.fillFindingsTemplate(findingsTemplates, findings, {ITA: "Italy"}, {state: "State", private: "Private", party: "Party"});
assert(filled[0] === "Share 23.8% since 2026-09-03.", "findings box: percentage to one decimal");
assert(filled[1] === "Italy led, carried by Italpress.", "findings box: country name from the lookup and top carrier");
assert(filled[2] === "State-controlled outlets published at roughly 8.0 times the rate.", "findings box: ownership rate ratio to one decimal, in words");
var filled2 = C.fillFindingsTemplate(["{top_country_count} ({top_country_share}); {next_ownership_group}; {largest_group} {largest_group_share}"], findings, {ITA: "Italy"}, {state: "State", private: "Private", party: "Party"});
assert(filled2[0] === "50 (50%); private; state 80%", "findings box: country count and share, next group, and the largest group by articles, unassessed left out");
assert(filled[3] === "State outlets led.", "findings box: top ownership group label");
assert(C.fillFindingsTemplate(findingsTemplates, null, {}, {}) === null, "findings box hides cleanly with no meta.findings");
assert(C.fillFindingsTemplate(findingsTemplates, {all_time: {state_share_of_china: null}}, {}, {})[0] === "Share n/a since the start of data collection.",
  "findings box fills missing pieces without throwing");
/* The generated findings phrases (DECISIONS 6): "most" only above half, a tie said as a tie, unassessed
   owners never the subject, underlying items beside placements. On 2026-10-17 the box said Australia's
   940 came "most of them through The Courier (Ballarat)", which carried 94 tied with nine sister papers,
   that most arrived by a route that carried 200 of 940, and that not assessed outlets led the per outlet rates. */
var fT = ["{top_country} carried more than any other country in the last 30 days: {top_country_count} state-origin placements{top_country_items}, {top_country_share} of the world's total{top_carrier_clause}.",
  "Among outlets with a known owner, {ownership_rate_comparison}, counted in placements per outlet.",
  "In absolute numbers, {largest_group_comparison}."];
var ownLabels = {private: "Private", public_service: "Public service", state: "State", party: "Party", unassessed: "Not assessed"};
var fAus = {all_time: {}, top_country: {iso: "AUS", state_origin: 940, share_of_world: 0.3922, underlying: 94,
  top_outlet: {id: "au_thecourier", name: "The Courier (Ballarat)", count: 94, total: 940, share: 0.1, tied: 10, network: "ACM", tied_names: ["A", "B", "C", "D", "E"]},
  top_route: "wire_credit", top_route_detail: {id: "wire_credit", count: 200, total: 940, share: 0.2128, tied: 1, tied_ids: ["wire_credit"], unattributed: 700}},
  by_ownership: {groups: [{id: "private", articles: 1768, outlets: 1146, per_outlet_rate: 1.543}, {id: "public_service", articles: 3, outlets: 56, per_outlet_rate: 0.054},
    {id: "state", articles: 336, outlets: 168, per_outlet_rate: 2.0}, {id: "party", articles: 3, outlets: 11, per_outlet_rate: 0.273},
    {id: "unassessed", articles: 864, outlets: 300, per_outlet_rate: 2.88}], top: {id: "unassessed", per_outlet_rate: 2.88}}};
var fOut = C.fillFindingsTemplate(fT, fAus, {AUS: "Australia"}, ownLabels);
assert(fOut[0] === "Australia carried more than any other country in the last 30 days: 940 state-origin placements (94 underlying items), 39% of the world's total, spread evenly across 10 ACM outlets (94 each).", "a tie is a tie: " + fOut[0]);
assert(fOut[1] === "Among outlets with a known owner, state outlets publish state-origin articles at about 1.3 times the rate of private outlets (2.00 against 1.54 per outlet), counted in placements per outlet.", "unassessed never leads the rate comparison: " + fOut[1]);
assert(fOut[2] === "In absolute numbers, most state-origin placements in outlets with a known owner (84%) are in private outlets, which make up 83% of those outlets.", "most only above half, outlet share instead of a cause: " + fOut[2]);
assert(!/not assessed|simply because/i.test(fOut.join(" ")), "no finding about unassessed owners and no unsupported cause");
var fRoute = C.findingsRouteSentence(fAus.top_country.top_route_detail, "Australia", function (id) { return id.replace("_", " "); });
assert(fRoute === "In Australia, the wire credit route was the most common signed route, with 200 of 940 placements; 700 carried no route signature.", "route under half: " + fRoute);
var oc = function (count, total, tied, extra) { return Object.assign({id: "x", name: "X Daily", count: count, total: total, share: count / total, tied: tied, tied_names: ["X Daily"]}, extra || {}); };
assert(C.findingsCarrierClause(oc(612, 940, 1)) === ", most of them (65%) through X Daily", "over half is most");
assert(C.findingsCarrierClause(oc(470, 940, 1)) === ", with X Daily carrying the most (470, 50%)", "exactly half is not most");
assert(C.findingsCarrierClause(oc(300, 940, 2, {tied_names: ["X Daily", "Y Daily"]})) === ", led jointly by X Daily and Y Daily at 300 each (64% together)", "a partial tie names both");
assert(C.findingsCarrierClause(oc(94, 940, 10, {tied_names: ["a", "b", "c", "d", "e"]})) === ", spread evenly across 10 outlets (94 each)", "a tie with no common network");
assert(C.findingsCarrierClause({id: "x", name: "X Daily"}) === "" && C.findingsCarrierClause(null) === "", "an older export's carrier has no share, so nothing is claimed");
assert(C.findingsCarrierClause(oc(612, 940, 1), 940) !== "" && C.findingsCarrierClause(oc(612, 940, 1), 94) === "", "a carrier counted over other placements than the sentence's is left out");
var fOther = JSON.parse(JSON.stringify(fAus)); fOther.top_country.state_origin = 94;
assert(/39% of the world's total\.$/.test(C.fillFindingsTemplate(fT, fOther, {AUS: "Australia"}, ownLabels)[0]), "no carrier clause beside a count it does not add up to");
var rd = function (count, total, tied, ids, un) { return {id: ids[0], count: count, total: total, tied: tied, tied_ids: ids, unattributed: un}; };
var lab = function (id) { return id.replace(/_/g, " "); };
assert(C.findingsRouteSentence(rd(600, 940, 1, ["wire_credit"], 100), "Australia", lab) === "In Australia, most of it (600 of 940 placements) arrived by the wire credit route.", "route over half");
assert(C.findingsRouteSentence(rd(470, 940, 1, ["wire_credit"], 470), "Australia", lab).indexOf("most of it") < 0, "half of every placement is not most, signed or not");
assert(C.findingsRouteSentence(rd(300, 940, 2, ["wire_credit", "embassy_byline"], 340), "Australia", lab) === "In Australia, no single signed route led: wire credit and embassy byline carried 300 placements each of 940, and 340 carried no route signature.", "tied routes");
assert(C.findingsRouteSentence(rd(600, 940, 1, ["wire_credit"], 100), "Australia", lab, 94) === "", "a route counted over other placements is left out");
assert(C.findingsRouteSentence(null, "Australia", lab) === "" && C.findingsRouteSentence({id: "wire_credit", count: 3}, "Australia", lab) === "", "an older export's route has no total, so no sentence");
var win = " in the 30 complete days ending 2026-10-14";
assert(C.carrierTooltipLine(oc(94, 940, 10, {network: "ACM"}), win) === "Spread evenly across 10 ACM outlets (94 placements each in the 30 complete days ending 2026-10-14)", "tooltip tie");
assert(C.carrierTooltipLine(oc(612, 940, 1), win) === "Carried mostly by X Daily (612 of 940 placements in the 30 complete days ending 2026-10-14)", "tooltip majority");
assert(C.carrierTooltipLine(oc(400, 940, 1), "") === "Largest carrier: X Daily (400 of 940 placements)", "tooltip plurality");
assert(C.carrierTooltipLine(oc(300, 940, 2, {tied_names: ["X Daily", "Y Daily"]}), "") === "Led jointly by X Daily and Y Daily (300 placements each, of 940)", "tooltip partial tie");
assert(C.carrierTooltipLine({id: "x", name: "X", count: 3}, "") === null && C.routeTooltipLine({id: "wire_credit", count: 3}, lab) === null, "older entries are not described");
assert(C.routeTooltipLine(rd(200, 940, 1, ["wire_credit"], 700), lab) === "Most common signed route: wire credit (200 of 940 placements; 700 carried no route signature)", "tooltip route under half");
assert(C.routeTooltipLine(rd(600, 940, 1, ["wire_credit"], 0), lab) === "Mostly arrives as wire credit (600 of 940 placements)", "tooltip route over half");
assert(C.routeTooltipLine(rd(2, 4, 2, ["wire_credit", "embassy_byline"], 0), lab) === "Most common signed routes, tied: wire credit and embassy byline (2 of 4 placements each)", "tooltip tied routes");
/* Ties and edge cases in the ownership sentences, and a sentence with no phrase to make is left out. */
var fTie = {all_time: {}, by_ownership: {groups: [{id: "private", articles: 10, outlets: 5, per_outlet_rate: 2}, {id: "state", articles: 10, outlets: 5, per_outlet_rate: 2},
  {id: "party", articles: 0, outlets: 1, per_outlet_rate: 0}]}};
var fTieOut = C.fillFindingsTemplate(fT, fTie, {}, ownLabels);
assert(fTieOut.length === 2, "no top country: its sentence is left out, not filled with filler");
assert(fTieOut[0] === "Among outlets with a known owner, private and state outlets publish state-origin articles at the same rate, 2.00 per outlet, counted in placements per outlet.", "rate tie: " + fTieOut[0]);
assert(fTieOut[1] === "In absolute numbers, state-origin placements in outlets with a known owner are split evenly between private and state outlets (50% each).", "largest tie: " + fTieOut[1]);
var fFew = C.fillFindingsTemplate(fT, {all_time: {}, by_ownership: {groups: [{id: "state", articles: 4, outlets: 2, per_outlet_rate: 2}, {id: "private", articles: 6, outlets: 6, per_outlet_rate: 1},
  {id: "party", articles: 5, outlets: 1, per_outlet_rate: 5}]}}, {}, ownLabels);
assert(fFew[1] === "In absolute numbers, the largest share of state-origin placements in outlets with a known owner (40%) is in private outlets, which make up 67% of those outlets.", "largest under half: " + fFew[1]);
assert(C.fillFindingsTemplate(fT, {all_time: {}, by_ownership: {groups: [{id: "unassessed", articles: 9, outlets: 3, per_outlet_rate: 3}]}}, {}, ownLabels).length === 0, "unassessed alone gives no ownership sentence");
/* Example snippets are trimmed to whole words with the highlight kept on the same phrase. */
var ww = C.wholeWordSnippet(", presidente del Zoo.\nFoto: Agencia NA (Xinhua).\nEstamos encantados de dar la bienv", 39, 47);
assert(ww.snippet === "\u2026 presidente del Zoo. Foto: Agencia NA (Xinhua). Estamos encantados de dar la \u2026", "snippet trimmed to whole words with ellipses: " + ww.snippet);
assert(ww.snippet.slice(ww.start, ww.end) === "(Xinhua)", "highlight offsets still point at the matched phrase");
/* An old export with a lowercase mid-word cut at both ends is still trimmed, highlight intact. */
var wwOld = C.wholeWordSnippet("ancia en la provincia de Buenos Aires, según informó la agencia Xinhua en un comunicado del pa", 56, 70);
assert(wwOld.snippet === "… en la provincia de Buenos Aires, según informó la agencia Xinhua en un comunicado del …", "old-style snippet still trimmed: " + wwOld.snippet);
assert(wwOld.snippet.slice(wwOld.start, wwOld.end) === "agencia Xinhua", "old-style highlight intact");
/* The export now trims on word boundaries and marks each cut with an ellipsis (spaced, or attached in
   scripts without spaces). Such a snippet passes through unchanged, both edges, offsets the same:
   a whole lowercase Spanish word or an Arabic word at the uncut edge is never cut again. */
[
  ["… en la provincia de Buenos Aires, según informó la agencia Xinhua en un comunicado oficial publicado este lunes por la tarde en …", 52, 66, "agencia Xinhua"],
  ["provincia de Buenos Aires, según informó la agencia Xinhua en un comunicado oficial publicado este lunes por la tarde en …", 44, 58, "agencia Xinhua"],
  ["… provincia de Buenos Aires, según informó la agencia Xinhua en un comunicado oficial publicado este lunes", 46, 60, "agencia Xinhua"],
  ["الصينية شينخوا أن الرئيس الصيني شي جين بينغ التقى اليوم في بكين برئيس الوزراء …", 8, 14, "شينخوا"],
  ["… الصينية شينخوا أن الرئيس الصيني شي جين بينغ التقى اليوم في بكين برئيس …", 10, 16, "شينخوا"],
  ["…ข่าวจาก สำนักข่าวซินหัวรายงาน ว่าประธานาธิบดี…", 18, 24, "ซินหัว"]
].forEach(function (c, i) {
  var w = C.wholeWordSnippet(c[0], c[1], c[2]);
  assert(c[0].slice(c[1], c[2]) === c[3], "exported case " + i + " fixture offsets");
  assert(w.snippet === c[0], "exported snippet " + i + " passes through unchanged: " + w.snippet);
  assert(w.start === c[1] && w.end === c[2] && w.snippet.slice(w.start, w.end) === c[3], "exported snippet " + i + " highlight intact");
});
/* An exported snippet with a line break is folded to a space and its highlight still lines up. */
var wwFold = C.wholeWordSnippet("provincia de Buenos Aires.\nSegún la agencia Xinhua en un …", 36, 50);
assert(wwFold.snippet === "provincia de Buenos Aires. Según la agencia Xinhua en un …" && wwFold.snippet.slice(wwFold.start, wwFold.end) === "agencia Xinhua", "exported snippet folded, not cut: " + wwFold.snippet);
/* Translate links: only for outlets that do not publish in English, and the text is URL-encoded. */
assert(C.translateLinks("en", "Hello", "https://a.example/x") === null, "no translate links for English outlets");
assert(C.translateLinks(null, "Hola", "https://a.example/x") === null, "no translate links when the language is unknown");
var tl = C.translateLinks("es", "Dos pandas & más", "https://a.example/x?y=1");
assert(tl.text.indexOf("text=Dos%20pandas%20%26%20m%C3%A1s") > 0, "headline text is encoded into the translate link");
assert(tl.page.indexOf("u=https%3A%2F%2Fa.example%2Fx%3Fy%3D1") > 0, "article URL is encoded into the page translate link");
assert(C.translateLinks("es", "Hola", "javascript:alert(1)").page === undefined, "only http(s) article URLs get a page link");
/* Route id to plain name, used by the findings box and the map tooltip. */
var contentRoutes = {wire_credit: {plain_name: "Wire credit"}, distribution_stamp: {plain_name: "Press-release stamp"}};
assert(C.contentRouteName(contentRoutes, "wire_credit") === "Wire credit", "route name lookup");
assert(C.contentRouteName(contentRoutes, "unattributed") === "unattributed", "route name lookup falls back to the id when not named");
assert(C.contentRouteName(null, "wire_credit") === "wire_credit", "route name lookup survives a missing routes object");
/* uncredited_copy: named by content.routes, by a fallback label, and added to an older meta.routes before unattributed. */
assert(C.contentRouteName({uncredited_copy: {plain_name: "Uncredited copy"}}, "uncredited_copy") === "Uncredited copy", "uncredited copy route name lookup");
assert(C.ROUTE_FALLBACK_LABELS.uncredited_copy === "Uncredited copy of a credited state item", "uncredited copy fallback label");
var oldRoutes = [{id: "wire_credit", label: "Wire credit line or dateline"}, {id: "unattributed", label: "No signature; model judgement"}];
var fullRoutes = C.withRouteFallbacks(oldRoutes);
assert(fullRoutes.map(function (r) { return r.id; }).join(",") === "wire_credit,uncredited_copy,unattributed", "uncredited copy is added before unattributed");
assert(oldRoutes.length === 2 && C.withRouteFallbacks(fullRoutes).length === 3, "the fallback never mutates its input or adds a route twice");
assert(C.withRouteFallbacks(null).length === 0 && C.withRouteFallbacks([]).length === 0, "no routes exported, none invented");
/* Planted bylines: the helpers behind the section built from data/planted.json. */
assert(C.planted.detectorLabel("author_field") === "byline in the author field", "planted: author_field in plain words");
assert(C.planted.detectorLabel("byline_head") === "byline at the top of the text", "planted: byline_head in plain words");
assert(C.planted.detectorLabel("front_cited") === "names a front organisation", "planted: front_cited in plain words");
assert(C.planted.detectorLabel("some_new_detector") === "some new detector", "planted: an unknown detector still reads as words");
var pEmpty = C.planted.summarise({});
assert(pEmpty.flags === 0 && pEmpty.personas === 0 && pEmpty.fronts === 0 && pEmpty.operations === 0 && pEmpty.by_country.length === 0 && pEmpty.list_version === null, "planted: empty json summarises to zeros");
assert(C.planted.summarise(null).flags === 0 && C.planted.summarise(undefined).by_country.length === 0, "planted: a missing file (null) summarises without throwing");
assert(C.planted.emptyState({}) === "No byline in the monitored outlets has matched a listed persona since monitoring began.", "planted: empty state is one sentence, the intro carries the counts");
var pOne = {list_version: "2026.10.1",
  operations: [{id: "openai_bogus_bylines_2026", name: "Bogus Bylines", flags: 1}],
  personas: [{id: "ervin_b_hoskins", name: "Ervin B. Hoskins", operation: "openai_bogus_bylines_2026", flags: 1}],
  fronts: [{id: "social_research_center", name: "Social Research Center", operation: "openai_dark_clark_2026", flags: 0}],
  flags: [{article_id: 1, country: "USA", detector: "author_field", persona_id: "ervin_b_hoskins", operation_id: "openai_bogus_bylines_2026", published_at: "2026-10-01T08:00:00Z"}]};
var pOneSum = C.planted.summarise(pOne);
assert(pOneSum.flags === 1 && pOneSum.personas === 1 && pOneSum.fronts === 1 && pOneSum.operations === 1 && pOneSum.list_version === "2026.10.1", "planted: one flag counted");
assert(pOneSum.by_country.length === 1 && pOneSum.by_country[0].iso === "USA" && pOneSum.by_country[0].n === 1, "planted: by_country derived from the flags when the file has no tally");
assert(C.planted.emptyState(pOne) === "No byline in the monitored outlets has matched a listed persona since monitoring began.", "planted: empty state unchanged by the list size");
var pMany = {list_version: "2026.10.2",
  operations: [{id: "op_a", name: "A"}, {id: "op_b", name: "B"}],
  personas: [{id: "p1"}, {id: "p2"}, {id: "p3"}, {id: "p4"}, {id: "p5"}, {id: "p6"}, {id: "p7"}],
  fronts: [{id: "f1"}, {id: "f2"}],
  flags: [{country: "ITA", detector: "front_cited", front_id: "f1", operation_id: "op_b"}, {country: "USA", detector: "author_field", persona_id: "p1", operation_id: "op_a"},
          {country: "USA", detector: "byline_head", persona_id: "p2", operation_id: "op_a"}, {country: "BRA", detector: "author_field", persona_id: "p3", operation_id: "op_a"}],
  by_country: {USA: 2, ITA: 1, BRA: 1}};
var pManySum = C.planted.summarise(pMany);
assert(pManySum.flags === 4 && pManySum.personas === 7 && pManySum.fronts === 2 && pManySum.operations === 2, "planted: several flags counted");
assert(pManySum.by_country.map(function (r) { return r.iso + ":" + r.n; }).join(",") === "USA:2,BRA:1,ITA:1", "planted: by_country sorted by count, then by code: " + JSON.stringify(pManySum.by_country));
var pCapped = C.planted.summarise({flags: [{country: "USA"}], by_country: {USA: 3, ITA: 2}});
assert(pCapped.flags === 5 && pCapped.by_country[0].iso === "USA" && pCapped.by_country[0].n === 3, "planted: the file's own tally wins over a capped flags list");
assert(C.planted.emptyState(pMany) === "No byline in the monitored outlets has matched a listed persona since monitoring began.", "planted: empty state unchanged by several of each");
var pZeroTally = C.planted.summarise({flags: [{country: "USA"}, {country: "ITA"}], by_country: {}, flags_total: 2});
assert(pZeroTally.flags === 2 && pZeroTally.by_country.map(function (r) { return r.iso + ":" + r.n; }).join(",") === "ITA:1,USA:1", "planted: an empty by_country tally falls back to the flags");
assert(C.planted.summarise({flags: [{country: "USA"}], by_country: {USA: 0}, flags_total: 7}).flags === 7, "planted: flags_total from the file is the headline count");
assert(C.planted.summarise({flags: [{country: "USA"}], some_future_key: {rows: [{name: "x"}]}}).flags === 1, "planted: an unknown top-level key is ignored");
/* A null country or theme entry in a daily file must not stop the window sum. */
var nullTheme = {"2026-09": {days: {"2026-09-01": {countries: {ITA: null}, reviewed: {}, themes: {ITA: null, USA: {diplomacy: null, culture: [1, 0, 0, 0]}}}}}};
var thNull = C.aggregateThemes(nullTheme, "2026-09-01", 1);
assert(thNull.USA.culture[0] === 1 && thNull.USA.diplomacy[0] === 0 && Object.keys(thNull.ITA).length === 0, "aggregateThemes tolerates a null country or theme entry");
/* The worldwide distribution stratum: listed beside the countries under WLD, named, never on the map. */
assert(C.WORLDWIDE_ISO === "WLD" && C.WORLDWIDE_NAME === "Worldwide distribution", "worldwide stratum code and name");
var wMonths = {"2026-09": {days: {"2026-09-01": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 2, C: 3, tdisc: 100, ta: 2}),
                                                            WLD: Object.assign(C.emptyCounts(), {A: 5, C: 1, tdisc: 900, ta: 5, tchina: 6, ttarget: 5})},
                                                reviewed: {}, llm_ceiling_hit: false, routes: {ITA: {wire_credit: 2}, WLD: {distribution_stamp: 5}}}}}};
var wLatest = {countries: {ITA: {coverage: "monitored", outlets_active: 10, population: 59000000, top_outlets: 10, language_support: "full"},
                           WLD: {coverage: "monitored", outlets_active: 2, population: null, top_outlets: 2, language_support: "full"}}};
var wNames = {ITA: "Italy", WLD: "Worldwide distribution"};
var wAgg = C.aggregateWindow(wMonths, "2026-09-01", 30);
var wCtx = function (iso, entry, agg) { return {population: entry.population, topOutlets: entry.top_outlets, worldwide: iso === C.WORLDWIDE_ISO}; };
var wRows = C.rankCountries(wAgg, wLatest, "count_a", "all", wNames, wCtx);
assert(wRows.length === 2 && wRows[0].iso === "WLD" && wRows[0].name === "Worldwide distribution" && wRows[0].value === 5 && wRows[0].population === "", "rankCountries lists the stratum by name with its own count");
assert(wRows[1].iso === "ITA" && wRows[1].value === 2, "and the country keeps its own count, with nothing from the wires added");
var wShare = C.metricValue(wAgg.countries.WLD, "share_of_output_a", 2, "all", undefined, wCtx("WLD", wLatest.countries.WLD, wAgg));
assert(Math.abs(wShare.value - 5 / 900) < 1e-9, "the stratum's share of output divides by what the wires published, two outlets notwithstanding");
var wShareCountry = C.metricValue(wAgg.countries.WLD, "share_of_output_a", 2, "all", undefined, {population: null, topOutlets: 2});
assert(wShareCountry.value === null && wShareCountry.sparse, "a real country with two outlets still gets no share of output");
var wCap = C.scaleCap(wMonths, ["2026-09-01"], wLatest, "count_a", 30, "all", wCtx);
assert(wCap.max === 2 && wCap.values === 1, "the map's color scale ignores the stratum, which it cannot draw");
var wCount = C.metricValue(wAgg.countries.WLD, "count_a", 2, "all", undefined, wCtx("WLD", wLatest.countries.WLD, wAgg));
var wLine = C.worldwideLegendLine(wCount, "count_a");
assert(wLine === "Worldwide distribution via press release wires: 5 state-origin articles in this window, not tied to any one country and drawn in none.", "legend line for a count: " + wLine);
assert(/0\.6% of every release the wires published/.test(C.worldwideLegendLine(wShare, "share_of_output_a")), "legend line follows the share of output basis: " + C.worldwideLegendLine(wShare, "share_of_output_a"));
assert(/2\.50 state-origin articles per wire monitored/.test(C.worldwideLegendLine(C.metricValue(wAgg.countries.WLD, "per_outlet_a", 2, "all", undefined, wCtx("WLD", wLatest.countries.WLD, wAgg)), "per_outlet_a")), "legend line follows the per outlet basis");
assert(/China-related articles/.test(C.worldwideLegendLine(C.metricValue(wAgg.countries.WLD, "count_china", 2, "all", undefined, wCtx("WLD", wLatest.countries.WLD, wAgg)), "count_china")), "legend line follows the measure");
assert(C.worldwideLegendLine(C.metricValue(C.emptyCounts(), "count_a", 2, "all", undefined, {worldwide: true}), "count_a") === null, "legend line hidden when the stratum holds nothing");
assert(C.worldwideLegendLine(null, "count_a") === null, "legend line hidden when the stratum is absent");
var wB = C.metricValue(wAgg.countries.WLD, "count_b", 2, "all", undefined, {worldwide: true, relay: {measured: true, publishable: false, provisional: false}});
assert(C.worldwideLegendLine(wB, "count_b") === null, "legend line hidden when the measure is withheld");
var wCsv = C.toCSV(wRows, ["iso", "name", "value", "population"]);
assert(wCsv.indexOf("WLD,Worldwide distribution,5,") !== -1, "the view CSV carries the stratum with its name");
/* The Distribution toggle: both keeps the stratum, country drops it, wires keeps nothing else. */
assert(C.DISTRIBUTIONS.join(",") === "both,country,wires" && C.DISTRIBUTION_NAMES.wires === "Worldwide wires", "distribution modes and names");
assert(C.distributionKeeps("both", "WLD") && C.distributionKeeps("both", "ITA"), "both keeps every row");
assert(!C.distributionKeeps("country", "WLD") && C.distributionKeeps("country", "ITA"), "country outlets drops the stratum");
assert(C.distributionKeeps("wires", "WLD") && !C.distributionKeeps("wires", "ITA"), "worldwide wires keeps only the stratum");
assert(C.distributionKeeps(undefined, "WLD"), "an absent mode is both");
assert(C.distributionIsos({ITA: 1, WLD: 1, FRA: 1}, "country").join(",") === "ITA,FRA" && C.distributionIsos(["WLD", "ITA"], "wires").join(",") === "WLD", "distributionIsos filters keys and lists");
var dBoth = C.aggregateWindow(wMonths, "2026-09-01", 30, "both"), dCountry = C.aggregateWindow(wMonths, "2026-09-01", 30, "country"), dWires = C.aggregateWindow(wMonths, "2026-09-01", 30, "wires");
assert(dBoth.countries.WLD.A === 5 && dBoth.countries.ITA.A === 2 && dBoth.routes.WLD.distribution_stamp === 5, "both: the stratum and the country are both summed");
assert(dBoth.countries.ITA.A === wAgg.countries.ITA.A && Object.keys(dBoth.countries).length === Object.keys(wAgg.countries).length, "both equals the unfiltered aggregate");
assert(!dCountry.countries.WLD && dCountry.countries.ITA.A === 2 && !dCountry.routes.WLD && dCountry.routes.ITA.wire_credit === 2, "country outlets: the stratum is gone from the counts and the routes");
assert(dWires.countries.WLD.A === 5 && !dWires.countries.ITA && !dWires.routes.ITA && dWires.routes.WLD.distribution_stamp === 5, "worldwide wires: only the stratum remains");
var dRows = C.rankCountries(dCountry, wLatest, "count_a", "all", wNames, wCtx, "country");
assert(dRows.length === 1 && dRows[0].iso === "ITA", "rankCountries under country outlets has no stratum row");
dRows = C.rankCountries(dWires, wLatest, "count_a", "all", wNames, wCtx, "wires");
assert(dRows.length === 1 && dRows[0].iso === "WLD" && dRows[0].value === 5, "rankCountries under worldwide wires is the stratum row alone");
assert(C.rankCountries(dBoth, wLatest, "count_a", "all", wNames, wCtx, "both").length === 2 && C.rankCountries(dBoth, wLatest, "count_a", "all", wNames, wCtx).length === 2, "rankCountries under both, or with no mode, lists every row");
/* The legend line exists exactly when the mode keeps the stratum and it holds something. */
function legendLineFor(agg, mode) {
  var isos = C.distributionIsos(wLatest.countries, mode);
  if (isos.indexOf(C.WORLDWIDE_ISO) === -1) return null;
  return C.worldwideLegendLine(C.metricValue(agg.countries.WLD, "count_a", 2, "all", undefined, wCtx("WLD", wLatest.countries.WLD, agg)), "count_a");
}
assert(/5 state-origin articles/.test(legendLineFor(dBoth, "both")), "legend line present under both");
assert(legendLineFor(dCountry, "country") === null, "legend line absent under country outlets");
assert(/5 state-origin articles/.test(legendLineFor(dWires, "wires")), "legend line present under worldwide wires");
/* The theme counter's window follows the toggle too. */
var tMonths = {"2026-09": {days: {"2026-09-01": {countries: {}, reviewed: {}, themes: {ITA: {trade: [2, 1, 1, 0]}, WLD: {trade: [5, 5, 5, 0]}}}}}};
assert(C.aggregateThemes(tMonths, "2026-09-01", 1, "country").ITA.trade[2] === 1 && !C.aggregateThemes(tMonths, "2026-09-01", 1, "country").WLD, "aggregateThemes drops the stratum under country outlets");
assert(C.aggregateThemes(tMonths, "2026-09-01", 1, "wires").WLD.trade[2] === 5 && !C.aggregateThemes(tMonths, "2026-09-01", 1, "wires").ITA, "aggregateThemes keeps only the stratum under worldwide wires");
/* The panel's world table: totals minus the stratum, or the stratum alone, key by key. */
var dTot = {A: 100, B: 20, C: 300, N: 50, pending: 4, paywalled: 7, share_a: 0.2, note: "x"};
var dWld = {A: 30, B: 0, C: 10, N: 5, pending: 1, paywalled: 0, share_a: 0.7};
var dc = C.distributionTotals(dTot, dWld, "country");
assert(dc.A === 70 && dc.C === 290 && dc.N === 45 && dc.pending === 3 && dc.paywalled === 7 && dc.note === "x", "distributionTotals subtracts the stratum under country outlets: " + JSON.stringify(dc));
var dw = C.distributionTotals(dTot, dWld, "wires");
assert(dw.A === 30 && dw.C === 10 && dw.B === 0 && dw.paywalled === 0, "distributionTotals keeps the stratum alone under worldwide wires");
assert(C.distributionTotals(dTot, dWld, "both") === dTot && C.distributionTotals(dTot, null, "country").A === 100, "both returns the totals; a missing stratum means zero");
/* The KPI arithmetic: country outlets is total minus wires, worldwide wires is the wires' part. */
var dAt = {state_origin: 1591, unchecked: 1697, independent: 35700, china_total: 38988, state_share_of_china: 0.0408,
           state_origin_wires: 230, unchecked_wires: 0, independent_wires: 90, china_total_wires: 320};
var d30 = {state_origin: 1200, state_origin_wires: 200};
var dTop = {iso: "ITA", state_origin: 400, share_of_world: 400 / 1200};
var kBoth = C.distributionFindings(dAt, d30, dTop, "both"), kCountry = C.distributionFindings(dAt, d30, dTop, "country"), kWires = C.distributionFindings(dAt, d30, dTop, "wires");
assert(kBoth.state_origin === 1591 && kBoth.china_total === 38988 && Math.abs(kBoth.top_share_of_world - 400 / 1200) < 1e-9, "findings under both are the export's totals");
assert(kCountry.state_origin === 1361 && kCountry.independent === 35610 && kCountry.china_total === 38668 && Math.abs(kCountry.state_share_of_china - 1361 / 38668) < 1e-9, "findings under country outlets subtract the wires: " + JSON.stringify(kCountry));
assert(Math.abs(kCountry.top_share_of_world - 400 / 1000) < 1e-9, "the top country's share of the world is retaken over the country outlets' total");
assert(kWires.state_origin === 230 && kWires.independent === 90 && kWires.china_total === 320 && Math.abs(kWires.state_share_of_china - 230 / 320) < 1e-9, "findings under worldwide wires are the wires' part alone");
assert(kWires.top_share_of_world === null && kWires.wires_last_30d === 200 && kWires.wires_all_time === 230, "no top country under worldwide wires; the wires' own 30 day count is given");
assert(kBoth.state_origin + 0 === kCountry.state_origin + kWires.state_origin, "both is the sum of the two modes");
var kOld = C.distributionFindings({state_origin: 1361, unchecked: null, independent: 35608, china_total: 38666}, null, dTop, "country");
assert(kOld.state_origin === 1361 && kOld.unchecked === null && Math.abs(kOld.top_share_of_world - 400 / 1200) < 1e-9 && kOld.wires_last_30d === null, "an export without the wires fields reads as zero wires, with the share of the world kept");
assert(C.distributionFindings({state_origin: 1361, china_total: 0}, null, null, "both").state_share_of_china === null, "no share over an empty denominator");
/* The "State origin, last 30 days" card: A summed over the window under each mode, equal to the ranked
   list's sum, with findings.last_30d as the fallback when the daily files are absent. */
var sMonths = {"2026-08": {days: {"2026-08-31": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 9}), WLD: Object.assign(C.emptyCounts(), {A: 50})}, reviewed: {}}}},
  "2026-09": {days: {
  "2026-09-10": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 4, C: 2}), FRA: Object.assign(C.emptyCounts(), {A: 3}), WLD: Object.assign(C.emptyCounts(), {A: 5})}, reviewed: {}},
  "2026-09-30": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 1}), WLD: Object.assign(C.emptyCounts(), {A: 2})}, reviewed: {}}}}};
var sLatest = {countries: {ITA: {coverage: "monitored", outlets_active: 5}, FRA: {coverage: "monitored", outlets_active: 5}, WLD: {coverage: "monitored", outlets_active: 2}}};
var sCtx = function () { return {relay: {measured: true, publishable: true}}; };
["both", "country", "wires"].forEach(function (mode) {
  var sAgg = C.aggregateWindow(sMonths, "2026-09-30", 30, mode);
  var expect = {both: 15, country: 8, wires: 7}[mode];
  var got = C.stateOriginInWindow(sAgg, {state_origin: 999, state_origin_wires: 99}, mode);
  assert(got === expect, "stateOriginInWindow under " + mode + " sums the window, not the fallback: " + got);
  var rankedSum = C.rankCountries(sAgg, sLatest, "count_a", "all", {}, sCtx, mode).reduce(function (t, r) { return t + (r.value || 0); }, 0);
  assert(got === rankedSum, "stateOriginInWindow under " + mode + " equals the ranked list's sum: " + rankedSum);
});
var sBothAgg = C.aggregateWindow(sMonths, "2026-09-30", 30);
assert(C.stateOriginInWindow(sBothAgg, null, "country") === 8 && C.stateOriginInWindow(sBothAgg, null, "wires") === 7, "stateOriginInWindow filters an unfiltered aggregate by the mode itself");
assert(C.stateOriginInWindow(C.aggregateWindow({}, "2026-09-30", 30, "both"), null, "both") === 0, "an empty window is zero, not the fallback");
var s30 = {state_origin: 1328, state_origin_wires: 177};
assert(C.stateOriginInWindow(null, s30, "both") === 1328, "fallback under both is findings.last_30d");
assert(C.stateOriginInWindow(null, s30, "country") === 1151, "fallback under country outlets subtracts the wires");
assert(C.stateOriginInWindow(null, s30, "wires") === 177, "fallback under worldwide wires is the wires alone");
assert(C.stateOriginInWindow(null, {state_origin: 1328}, "country") === 1328 && C.stateOriginInWindow(null, {state_origin: 1328}, "wires") === 0, "a last_30d without the wires field reads as zero wires");
assert(C.stateOriginInWindow(null, null, "both") === null && C.stateOriginInWindow(null, {}, "wires") === null, "no source gives null");
/* The top country card and the findings sentence count the 30 complete days the map opens on, not the
   export's window, which also held the partial export day (Italy 436 on the card, 423 on the map, on
   2026-10-12). Its share is of stateOriginInWindow for the same days and mode. */
var pMonths = JSON.parse(JSON.stringify(sMonths));
pMonths["2026-10"] = {days: {"2026-10-01": {countries: {FRA: Object.assign(C.emptyCounts(), {A: 10})}, reviewed: {}}}};
var pTopExport = {iso: "FRA", state_origin: 13, share_of_world: 0.5, top_outlet: {id: "fr_x", name: "Le X"}, top_route: "wire_credit"};
["both", "country"].forEach(function (mode) {
  var pAgg = C.aggregateWindow(pMonths, "2026-09-30", 30, mode);   // 2026-10-01 is the partial export day
  var t = C.topCountryInWindow(pAgg, mode, pTopExport, {state_origin: 26, state_origin_wires: 7});
  var ranked = C.rankCountries(pAgg, sLatest, "count_a", "all", {}, sCtx, mode).filter(function (r) { return r.iso !== C.WORLDWIDE_ISO; });
  ranked.sort(function (x, y) { return (y.value || 0) - (x.value || 0); });
  assert(t.iso === "ITA" && t.state_origin === 5 && t.iso === ranked[0].iso && t.state_origin === ranked[0].value,
         "top country under " + mode + " is the ranked list's top row over the complete days: " + JSON.stringify(t));
  assert(t.share_of_world === 5 / C.stateOriginInWindow(pAgg, null, mode), "top country share under " + mode + " is over the complete day total");
  assert(t.top_outlet === null && t.top_route === null, "the export's outlet is not lent to another country");
});
assert(C.topCountryInWindow(C.aggregateWindow(pMonths, "2026-09-30", 30, "both"), "both", null, null).share_of_world === 5 / 15, "both keeps the wires in the denominator");
assert(C.topCountryInWindow(C.aggregateWindow(pMonths, "2026-09-30", 30, "country"), "country", null, null).share_of_world === 5 / 8, "country outlets leaves them out");
assert(C.topCountryInWindow(C.aggregateWindow(pMonths, "2026-09-30", 30, "wires"), "wires", pTopExport, null) === null, "no country is ranked under worldwide wires");
assert(C.topCountryInWindow(C.aggregateWindow(pMonths, "2026-10-01", 30, "both"), "both", pTopExport, null).iso === "FRA", "a window that ends on the partial day would have named France");
var pSame = C.topCountryInWindow(C.aggregateWindow(pMonths, "2026-09-30", 30, "both"), "both", {iso: "ITA", state_origin: 6, share_of_world: 0.4, top_outlet: {id: "italpress", name: "Italpress"}, top_route: "wire_credit"}, null);
assert(pSame.state_origin === 5 && pSame.top_outlet.name === "Italpress" && pSame.top_route === "wire_credit", "the window's count wins, the export's outlet is kept for the same country");
var pFb = C.topCountryInWindow(null, "country", pTopExport, {state_origin: 26, state_origin_wires: 6});
assert(pFb.iso === "FRA" && pFb.state_origin === 13 && pFb.share_of_world === 13 / 20 && pFb.top_outlet.name === "Le X", "fallback without daily files: " + JSON.stringify(pFb));
assert(C.topCountryInWindow(null, "both", null, null) === null && C.topCountryInWindow(C.aggregateWindow({}, "2026-09-30", 30, "both"), "both", pTopExport, null) === null, "nothing to rank gives null");
var pFilled = C.fillFindingsTemplate(["{top_country} {top_country_count} ({top_country_share})"], {all_time: {}, top_country: pSame}, {ITA: "Italy"}, {});
assert(pFilled[0] === "Italy 5 (33%)", "the findings sentence reads the window's top country: " + pFilled[0]);
assert(typeof pSame.underlying === "number" && pSame.underlying === C.aggregateWindow(pMonths, "2026-09-30", 30, "both").countries.ITA.uniqA, "the window's top country carries its underlying items: " + JSON.stringify(pSame));
assert(pFb.underlying === null && C.topCountryInWindow(null, "both", Object.assign({}, pTopExport, {underlying: 4, top_route_detail: {id: "wire_credit", count: 2, total: 3}}), null).underlying === 4, "the export's underlying items are the fallback");
/* The all time cards and the summary end on the map's last complete day: allTimeInWindow sums the daily files
   up to it, where the export's findings.all_time also counted the partial export day (2026-10-01 here). Under
   every Distribution mode each card equals the map's All time sum, the ranked list over the same days, and
   country outlets plus worldwide wires equals Both. */
var aMonths = JSON.parse(JSON.stringify(pMonths));
aMonths["2026-09"].days["2026-09-10"].countries.ITA.B = 3;
aMonths["2026-09"].days["2026-09-10"].countries.WLD.C = 4;
aMonths["2026-09"].days["2026-09-10"].countries.FRA.pending = 2;
aMonths["2026-10"].days["2026-10-01"].countries.WLD = Object.assign(C.emptyCounts(), {A: 52, B: 1, C: 2});
var aExport = {state_origin: 999, unchecked: 99, independent: 99, china_total: 1197, state_share_of_china: 0.8,
               state_origin_wires: 109, unchecked_wires: 1, independent_wires: 6, china_total_wires: 116};
var aAll = C.allTimeInWindow(C.aggregateWindow(aMonths, "2026-09-30", null, "both"), aExport);
assert(aAll.state_origin === 74 && aAll.unchecked === 3 && aAll.independent === 6 && aAll.china_total === 83 &&
       aAll.state_origin_wires === 57 && aAll.unchecked_wires === 0 && aAll.independent_wires === 4 && aAll.china_total_wires === 61,
       "allTimeInWindow sums every day to the last complete day, not the partial one: " + JSON.stringify(aAll));
assert(Math.abs(aAll.state_share_of_china - 74 / 83) < 1e-12, "the share is over the same days");
var aModes = {};
["both", "country", "wires"].forEach(function (mode) {
  var got = C.distributionFindings(aAll, null, null, mode);
  aModes[mode] = got;
  var mapAgg = C.aggregateWindow(aMonths, "2026-09-30", null, mode);   // the map's All time view under the mode
  var ranked = C.rankCountries(mapAgg, sLatest, "count_a", "all", {}, sCtx, mode).reduce(function (t, r) { return t + (r.value || 0); }, 0);
  var sumOf = function (k) { return Object.keys(mapAgg.countries).reduce(function (t, iso) { return t + (mapAgg.countries[iso][k] || 0); }, 0); };
  assert(got.state_origin === ranked && got.state_origin === sumOf("A"), "the state origin card under " + mode + " equals the map's All time sum: " + got.state_origin + " vs " + ranked);
  assert(got.unchecked === sumOf("B") && got.independent === sumOf("C") && got.china_total === sumOf("A") + sumOf("B") + sumOf("C"),
         "the other all time cards under " + mode + " equal the map's All time sums: " + JSON.stringify(got));
});
["state_origin", "unchecked", "independent", "china_total"].forEach(function (k) {
  assert(aModes.country[k] + aModes.wires[k] === aModes.both[k], "country outlets plus wires is Both for " + k);
});
assert(aAll.pending === 2 && aAll.pending_wires === 0, "articles awaiting the model are counted over the same days");
var aChina = C.rankCountries(C.aggregateWindow(aMonths, "2026-09-30", null, "both"), sLatest, "count_china", "all", {}, sCtx, "both").reduce(function (t, r) { return t + (r.value || 0); }, 0);
assert(aChina === aAll.china_total + aAll.pending, "the map's All China coverage is the China-related card plus the articles awaiting the model: " + aChina);
var aWithheld = C.allTimeInWindow(C.aggregateWindow(aMonths, "2026-09-30", null, "both"), Object.assign({}, aExport, {unchecked: null, unchecked_wires: null}));
assert(aWithheld.unchecked === null && aWithheld.unchecked_wires === null && aWithheld.china_total === 83, "a withheld unchecked count stays withheld");
assert(C.allTimeInWindow(null, aExport) === aExport, "without daily files the export's totals are the fallback");
assert(C.fillFindingsTemplate(["{state_share_of_china} since {since}"], {all_time: aAll, since: "2026-08-31 through 2026-09-30"}, {}, {})[0] === "89.2% since 2026-08-31 through 2026-09-30",
       "the summary sentence reads the complete day share and its span");
/* Themes version changes on the theme timeline: on the change's own day, else the first day after it;
   a change after the last day, or without a date, is left off. */
var tDays = ["2026-09-13", "2026-09-14", "2026-09-16", "2026-09-17"];
var tMarks = C.themesChangeMarks(tDays, [{version: "2026.09.3", date: "2026-09-14", change: "introduced"},
                                         {version: "2026.09.4", date: "2026-09-15", change: "whole body instead of the first 800 characters"},
                                         {version: "2026.10.1", date: "2026-10-01", change: "later"}, {version: "x"}]);
assert(tMarks.length === 2 && tMarks[0].index === 1 && tMarks[1].index === 2, "themes marks land on their day or the next one: " + JSON.stringify(tMarks));
assert(tMarks[1].title === "Themes version 2026.09.4: whole body instead of the first 800 characters (from 2026-09-15)", "themes mark tooltip: " + tMarks[1].title);
assert(C.themesChangeMarks(tDays, undefined).length === 0 && C.themesChangeMarks([], [{version: "a", date: "2026-09-01"}]).length === 0, "no changes or no days, no marks");
/* Leaning sources: Media Bias/Fact Check alone outside the US is weak; the export's flag wins when present. */
var mb = "https://mediabiasfactcheck.com/x/";
assert(C.leaningSourceWeak({country: "FRA", political_leaning: "centre", leaning_source: mb}), "non-US MBFC leaning is weak");
assert(!C.leaningSourceWeak({country: "USA", political_leaning: "centre", leaning_source: mb}), "US MBFC leaning is not weak");
assert(!C.leaningSourceWeak({country: "FRA", political_leaning: "state_controlled", leaning_source: mb}), "state controlled is not a rating");
assert(!C.leaningSourceWeak({country: "FRA", political_leaning: "centre", leaning_source: "https://www.mediabiasfactcheck.com/x/", leaning_second_source: "https://en.wikipedia.org/wiki/X"}), "a second source lifts it");
assert(!C.leaningSourceWeak({country: "FRA", political_leaning: "centre", leaning_source: mb, leaning_source_weak: false}), "export flag wins");
assert(C.leaningSourceKind({political_leaning: "left", leaning_source: "https://fr.wikipedia.org/wiki/X"}) === "wikipedia" &&
       C.leaningSourceKind({political_leaning: "state_controlled", leaning_source: mb}) === "ownership" &&
       C.leaningSourceKind({political_leaning: "unassessed"}) === null, "leaning source kinds");
var ls = C.leaningSources([{country: "FRA", political_leaning: "centre", leaning_source: mb}, {country: "USA", political_leaning: "right", leaning_source: mb},
                           {country: "ITA", political_leaning: "left", leaning_source: "https://en.wikipedia.org/wiki/X"}, {country: "RUS", political_leaning: "state_controlled", leaning_source: "https://en.wikipedia.org/wiki/RT"},
                           {country: "DEU", political_leaning: "centre", leaning_source: "https://www.eurotopics.net/x"}, {country: "DEU", political_leaning: "unassessed"}]);
assert(ls.assessed === 5 && ls.us_rater === 2 && ls.us_rater_weak === 1 && ls.wikipedia === 1 && ls.ownership === 1 && ls.other === 1, "leaning sources tally " + JSON.stringify(ls));
var lsText = C.leaningSourcesSentence(ls);
assert(/Wikipedia media lists for 1 outlet, Media Bias\/Fact Check, a US based rater, for 2 outlets \(1 of them outside the United States/.test(lsText) &&
       /the ownership source for the 1 state controlled outlet\./.test(lsText) && /treat it with caution/.test(lsText), "leaning sources sentence: " + lsText);
assert(C.leaningSourcesSentence({assessed: 0}) === "", "no sentence without assessed outlets");
assert(C.leaningWeakCaveat(3, 10) === "3 of 10 from a US rater alone; treat with caution" &&
       C.leaningWeakCaveat(1, 1) === "Leaning from a US rater alone; treat with caution" && C.leaningWeakCaveat(0, 4) === "", "weak caveat text");
/* Labels set mid-sentence lose only an ordinary first capital: China, Chinese, places, country names and
   acronyms keep theirs, in the legend unit, the tooltip, the ranked list title and the theme captions. */
assert(C.lowerFirst("China articles per active monitored outlet") === "China articles per active monitored outlet", "legend unit keeps China");
assert(C.lowerFirst("Number of China articles in the window") === "number of China articles in the window", "ordinary first word goes lower case");
assert(C.lowerFirst("State origin articles per active monitored outlet") === "state origin articles per active monitored outlet", "state origin unit");
assert(C.lowerFirst("Chinese domestic politics") === "Chinese domestic politics" && C.lowerFirst("Taiwan and Hong Kong") === "Taiwan and Hong Kong" &&
       C.lowerFirst("Belt and Road, investment and infrastructure") === "Belt and Road, investment and infrastructure", "theme labels keep proper nouns");
assert(C.lowerFirst("Signed by a Chinese diplomat") === "signed by a Chinese diplomat", "route label keeps Chinese mid-sentence");
assert(C.lowerFirst("Italy outlets", ["Italy"]) === "Italy outlets" && C.lowerFirst("United States outlets", ["United States"]) === "United States outlets" &&
       C.lowerFirst("Unitedness", ["United States"]) === "unitedness" && C.lowerFirst("Chinatown") === "chinatown", "country names by whole word");
assert(C.lowerFirst("EU members") === "EU members" && C.lowerFirst("") === "" && C.lowerFirst(null) === "", "acronyms and empty");
["a", "b", "china"].forEach(function (m) {
  C.BASES.forEach(function (b) {
    var unit = C.lowerFirst(C.MEASURE_NAMES[m] + ", " + C.BASIS_NAMES[b]), basis = C.lowerFirst(C.BASIS_NAMES[b]);
    assert(!/\bchina\b|\bchinese\b/.test(unit + " " + basis), "no lower case China in " + m + " " + b + ": " + unit + " / " + basis);
    var noun = {a: "state origin articles", b: "unchecked state sourcing articles", china: "China articles"}[m];
    var Noun = noun.charAt(0).toUpperCase() + noun.slice(1);
    [Noun + " per active monitored outlet", Noun + " as a percent of the country\u2019s China coverage", "Number of " + noun + " in the window"].forEach(function (u) {
      assert(!/\bchina\b/.test(C.lowerFirst(u)), "legend unit keeps China for " + m + " " + b + ": " + C.lowerFirst(u));
    });
  });
});
var wwNull = C.worldwideLegendLine({a: 3, chinaTotal: 9, value: null}, "share_a");
assert(wwNull && wwNull.indexOf("China coverage") > 0 && !/\bchina\b/.test(wwNull), "worldwide line keeps China: " + wwNull);
/* Underlying items count each item once per window and scope: story 7 placed in Italy on two days, in
   Germany and on a wire is one item in each figure, never one per day or one per country. */
var uc = function (a, ab, extra) { return Object.assign(C.emptyCounts(), {A: a, uniqA: a, uniqAB: ab}, extra || {}); };
var um = {"2026-09": {days: {
  "2026-09-01": {countries: {ITA: uc(2, 3, {B: 1}), DEU: uc(1, 1)}, reviewed: {}, underlying_items: {ITA: {A: [7, 8], B: [7]}, DEU: {A: [7]}}},
  "2026-09-02": {countries: {ITA: uc(1, 2, {B: 1}), WLD: uc(1, 1)}, reviewed: {}, underlying_items: {ITA: {A: [7], B: [9]}, WLD: {A: [7]}}}}}};
var u2 = C.aggregateWindow(um, "2026-09-02", 30);
assert(u2.countries.ITA.A === 3 && u2.countries.ITA.uniqA === 2 && u2.countries.ITA.uniqAB === 4 && !u2.underlyingSummed, "a story on two days is one item in the window");
assert(u2.countries.DEU.uniqA === 1 && u2.countries.WLD.uniqA === 1, "each country counts its own placements");
assert(u2.world.uniqA === 2 && u2.world.uniqAB === 4, "the world counts a story carried in several countries once");
var uMv = C.metricValue(u2.countries.ITA, "count_a", 5, "all");
assert(uMv.underlying === 2 && uMv.underlyingB === 2, "the metric reads the distinct counts");
assert(C.aggregateWindow(um, "2026-09-02", 1).countries.ITA.uniqA === 1, "a single day keeps its own figure");
assert(C.aggregateWindow(um, "2026-09-02", 30, "country").world.uniqA === 2 && C.aggregateWindow(um, "2026-09-02", 30, "wires").world.uniqA === 1, "the world figure follows the Distribution toggle");
/* An export before schema 12 carries no ids: the per day figures are summed and the view says so. */
var uOld = {"2026-09": {days: {"2026-09-01": {countries: {ITA: uc(2, 2)}, reviewed: {}}, "2026-09-02": {countries: {ITA: uc(1, 1)}, reviewed: {}}}}};
var uo = C.aggregateWindow(uOld, "2026-09-02", 30);
assert(uo.countries.ITA.uniqA === 3 && uo.underlyingSummed && uo.world.uniqA === 3, "older data falls back to the per day sum, flagged");
var uoRows = C.rankCountries(uo, {countries: {ITA: {coverage: "monitored", outlets_active: 5}}}, "count_a", "all", {});
assert(uoRows[0].note.indexOf(C.UNDERLYING_SUMMED) >= 0, "the csv row says its underlying items are summed by day");
assert(C.rankCountries(u2, {countries: {ITA: {coverage: "monitored", outlets_active: 5}}}, "count_a", "all", {})[0].note.indexOf(C.UNDERLYING_SUMMED) < 0, "and only then");
/* Outlets the owner's machine collects alone and outlets it polls beside the hosted runner are badged
   and counted apart, and a silent relay calls only the first kind incomplete. */
assert(C.collectorBadge("self_hosted").label === "relayed" && C.collectorBadge("both").label === "also relayed" && C.collectorBadge("hosted") === null, "collector badges");
assert(C.collectorBadge("both").title.indexOf("Also polled from the owner's machine") === 0, "the shared badge says the owner's machine also polls it");
var rcStale = {outlets: 2, countries: ["AUS", "ITA"], shared_outlets: 3, shared_countries: ["DEU"], last_run: "2026-10-12T00:00:00+00:00", hours_since_last_run: 9.4, stale: true, incomplete_days: ["2026-10-10"]};
var rn = C.relayCollectorNotes(rcStale);
assert(rn.short.length === 1 && rn.short[0] === "the owner's collector has been silent 9 hours", "stale short clause");
assert(rn.parts[0].indexOf("recent counts for the 2 outlets only it collects, in 2 countries, are incomplete.") >= 0, "stale names the relay only outlets as incomplete");
assert(rn.parts[0].indexOf("The 3 outlets it also polls, in 1 country, fall back to the hosted runner's hourly polls alone") >= 0 && rn.parts[0].indexOf("not marked incomplete") >= 0, "shared outlets fall back to the runner");
assert(rn.parts[1].indexOf("1 day") >= 0 && rn.parts[1].indexOf("outlets only it collects are incomplete on those days, while the hosted runner still polled") >= 0, "incomplete days apply to relay only outlets");
var rnShared = C.relayCollectorNotes({outlets: 0, shared_outlets: 3, shared_countries: ["DEU", "ITA"], last_run: "x", hours_since_last_run: 7, stale: true, incomplete_days: []});
assert(rnShared.parts.length === 1 && rnShared.parts[0].indexOf("are incomplete") < 0 && rnShared.parts[0].indexOf("not collected for 7 hours. The 3 outlets it also polls, in 2 countries") >= 0, "a relay with only shared outlets calls nothing incomplete");
assert(C.relayCollectorNotes({outlets: 2, last_run: "x", stale: false, incomplete_days: []}).parts.length === 0 && C.relayCollectorNotes({outlets: 0, shared_outlets: 0, stale: true}).parts.length === 0, "nothing to say when fresh or empty");
assert(C.relayCollectorNotes({outlets: 4, stale: null}).parts[0].indexOf("gaps in the 4 outlets only it collects") >= 0, "no heartbeat yet");
assert(C.relayOutletsSummary(rcStale) === "2 outlets collected only there, 3 also polled by the hosted runner" && C.relayOutletsSummary({outlets: 1}) === "1 outlet collected only there, 0 also polled by the hosted runner", "technical notes summary, older data without shared counts");
/* The relay was stopped on 2026-10-22 (meta.relay_collector.retired_on): its outlets are badged as stopped,
   shared outlets lose their badge, and the notes say what it left inactive and keep the red days before then. */
assert(C.collectorBadge("self_hosted", "2026-10-22").label === "relay stopped" && C.collectorBadge("self_hosted", "2026-10-22").title.indexOf("until that collection stopped on 2026-10-22") >= 0, "a retired relay outlet is badged as stopped");
assert(C.collectorBadge("both", "2026-10-22") === null && C.collectorBadge("hosted", "2026-10-22") === null, "no shared badge once the relay is retired");
var rcRetired = {outlets: 0, countries: [], shared_outlets: 0, shared_countries: [], last_run: "2026-10-22T07:00:00+00:00", hours_since_last_run: null, stale: false,
                 incomplete_days: ["2026-10-10", "2026-10-11"], retired_on: "2026-10-22", dropped_outlets: 123, dropped_by_country: {COK: 1, KIR: 1, ITA: 2},
                 countries_all_inactive: ["COK", "KIR"], countries_partial: ["ITA"]};
var rr = C.relayCollectorNotes(rcRetired, function (c) { return {COK: "Cook Islands", KIR: "Kiribati", ITA: "Italy"}[c]; });
assert(rr.short.length === 1 && rr.short[0] === "123 outlets that refuse the crawler inactive since 2026-10-22", "retired short clause");
assert(rr.parts[0].indexOf("every monitored outlet in Cook Islands and Kiribati, which are no longer monitored, and some outlets in 1 country more") >= 0, "retired names the countries left unmonitored");
assert(rr.parts[0].indexOf("silent") < 0 && rr.parts[0].indexOf("has not collected") < 0, "a retired relay is never called silent");
assert(rr.parts[1].indexOf("2 days, still marked in red") >= 0, "the red days before the stop are kept");
assert(C.relayOutletsSummary(rcRetired) === "stopped on 2026-10-22; 123 outlets it alone collected inactive since, in 3 countries", "retired technical notes summary");
/* The stability check state, matching pipeline/methodology.py: a current check is described on its own,
   an older-prompt check is named as history, and no check says when one runs. */
var stHist = {method: "same_model_rerun", n: 225, kappa_bc: 0.6229, computed_at: "2026-10-09T02:29:14+00:00", prompt_version: null, stale: true};
var stWait = {auto: true, state: "waiting_for_labels", pool: 37, min_pool: 200};
assert(C.stabilityStatusText({relay_reliability: {n: 250, kappa_bc: 0.7}, relay_reliability_history: stHist, relay_study: stWait}) === "", "a current check is described elsewhere");
assert(C.stabilityStatusText(null) === "" && C.stabilityStatusText(undefined) === "", "no meta, nothing to say");
var sNone = C.stabilityStatusText({relay_reliability: null, relay_reliability_history: null, relay_study: stWait});
assert(sNone === "No stability check has been run under the current instructions yet; one runs once 200 articles are labelled under the current instructions (37 so far).", "no check yet: " + sNone);
var sOld = C.stabilityStatusText({relay_reliability: null, relay_reliability_history: stHist, relay_study: stWait});
assert(sOld === "The last stability check (2026-10-09, 225 articles, kappa 0.62 on unchecked sourcing versus independent) was run under earlier instructions and is kept as history: it neither supports nor withholds any count, and a new one runs once 200 articles are labelled under the current instructions (37 so far).", "older-prompt check: " + sOld);
assert(C.stabilityStatusText({relay_reliability_history: {n: 225, kappa_bc: 0.62, computed_at: "2026-10-09", prompt_version: "2026-10-11", stale: true}, relay_study: {state: "submitted"}}).indexOf("under earlier instructions, dated 2026-10-11 and is kept as history: it neither supports nor withholds any count, and a new one has been submitted and is reported when it returns.") >= 0, "prompt version and a submitted check");
assert(C.stabilityStatusText({relay_study: {state: "waiting_for_budget", earliest: "2026-10-20", auto: false}}) === "No stability check has been run under the current instructions yet; enough articles are now labelled and one can be run by hand when the daily model allowance permits, on 2026-10-20 at the earliest.", "waiting for the allowance, run by hand");
/* Older data: no history, no study, a history without kappa, date or stale flag. */
assert(C.stabilityStatusText({}) === "No stability check has been run under the current instructions yet.", "older data with neither field");
assert(C.stabilityStatusText({relay_reliability_history: {}, relay_study: {state: "waiting_for_labels"}}) === "The last stability check (date unknown, 0 articles, kappa n/a on unchecked sourcing versus independent) was run under earlier instructions and is kept as history: it neither supports nor withholds any count.", "history without fields, study without a pool size");
assert(C.stabilityStatusText({relay_reliability_history: stHist, relay_study: {state: "done"}}).indexOf(", and none is needed, because the hand coding study decides publication.") >= 0, "no new check needed");
assert(C.stabilityStatusText({relay_reliability_history: stHist, relay_study: {state: "waiting_for_labels", min_pool: 200}}).slice(-62) === "once 200 articles are labelled under the current instructions.", "no pool count, no so far");
/* Items counted again after a prune before the fix: a note under the running total and in the % of all
   its news key when the export carries meta.rediscovery, nothing for older data without it. */
var rdMeta = {rediscovery: {fixed_from: "2026-10-13", estimated_share: 0.08, measured_floor: 123682, measured_of: 3008671, window_share: 0.082}};
assert(C.rediscoveryNote(rdMeta) === "Before 2026-10-13, items a feed kept listing were counted again after a prune, an estimated 8 percent of this total; see the methodology.", "rediscovery note with the field");
assert(C.rediscoveryNote({}) === "" && C.rediscoveryNote(null) === "" && C.rediscoveryNote({rediscovery: null}) === "" && C.rediscoveryOf({}) === null, "no rediscovery note without the field");
/* The repeats stopped at fixed_from, so their share of a running total that has grown since is computed, not fixed. */
var rdGrown = {articles_discovered: 3352040, rediscovery: rdMeta.rediscovery};
assert(C.rediscoveryPercent(rdGrown) === 7 && C.rediscoveryNote(rdGrown).indexOf("an estimated 7 percent of this total") >= 0, "rediscovery share of a grown total: " + C.rediscoveryNote(rdGrown));
assert(C.rediscoveryPercent({articles_discovered: 100, rediscovery: rdMeta.rediscovery}) === 8 && C.rediscoveryPercent(rdMeta) === 8 && C.rediscoveryPercent({}) === null, "rediscovery share falls back to the measured share");
assert(/^Before 2026-10-13, .*estimated 8 percent overall and unevenly by country/.test(C.rediscoveryShareNote(rdMeta, "2026-09-14")) && C.rediscoveryShareNote(rdMeta, "") !== "", "share note for a window reaching back before the fix");
assert(C.rediscoveryShareNote(rdMeta, "2026-10-13") === "" && C.rediscoveryShareNote({}, "2026-09-14") === "", "no share note after the fix or without the field");
/* Top 10 countries over the 30 complete days ending the map's last day (topCountriesInWindow): ordered by
   state origin placements as the map counts them, the worldwide stratum never ranked but counted beside,
   equal counts sharing a rank, a cut through a tie stated, and only the window's days summed. */
var tk = function (a, b, c) { return Object.assign(C.emptyCounts(), {A: a, B: b || 0, C: c || 0, uniqA: a}); };
var tDays = {
  "2026-08-31": {countries: {ESP: tk(50)}},
  "2026-09-01": {countries: {ITA: tk(4, 1, 10), FRA: tk(3, 0, 2), WLD: tk(9), DEU: tk(3, 0, 0), BRA: tk(0, 0, 4)}},
  "2026-09-30": {countries: {ITA: tk(2), GBR: tk(5, 0, 5), WLD: tk(2), POL: tk(1)}},
  "2026-10-01": {countries: {POL: tk(40)}}
};
var tMonths = {"2026-08": {days: {"2026-08-31": tDays["2026-08-31"]}}, "2026-09": {days: {"2026-09-01": tDays["2026-09-01"], "2026-09-30": tDays["2026-09-30"]}}, "2026-10": {days: {"2026-10-01": tDays["2026-10-01"]}}};
var tAgg = C.aggregateWindow(tMonths, "2026-09-30", 30, "both");
var tNames = {ITA: "Italy", FRA: "France", DEU: "Germany", GBR: "United Kingdom", POL: "Poland", ESP: "Spain", BRA: "Brazil", WLD: "Worldwide distribution"};
var t10 = C.topCountriesInWindow(tAgg, 10, tNames, "2026-09-30", 30);
assert(t10.window.start === "2026-09-01" && t10.window.end === "2026-09-30", "top 10 window is the 30 days ending the last day: " + JSON.stringify(t10.window));
assert(t10.rows.map(function (r) { return r.iso; }).join() === "ITA,GBR,FRA,DEU,POL", "top 10 order, ties by name, nothing outside the window, no zero rows: " + t10.rows.map(function (r) { return r.iso; }).join());
assert(t10.rows.every(function (r) { return r.iso !== C.WORLDWIDE_ISO; }) && t10.worldwide.count === 11, "the worldwide stratum is counted beside the list, never ranked");
assert(t10.rows.map(function (r) { return r.rank; }).join() === "1,2,3,3,5", "competition ranks: " + t10.rows.map(function (r) { return r.rank; }).join());
assert(!t10.rows[0].tied && t10.rows[2].tied && t10.rows[3].tied && !t10.rows[4].tied && t10.tiedBeyond.length === 0, "ties are marked");
assert(t10.max === 6 && t10.rows[0].count === 6 && t10.rows[0].underlying === 6, "the bar scale is the top count");
/* Each count is the map's count_a and each share its share_a for the same aggregate, the minimum denominator kept. */
t10.rows.forEach(function (r) {
  assert(r.count === C.metricValue(tAgg.countries[r.iso], "count_a", 5, "all").value, "count matches the map: " + r.iso);
  assert(r.share === C.metricValue(tAgg.countries[r.iso], "share_a", 5, "all").value, "share matches the map: " + r.iso);
});
assert(Math.abs(t10.rows[0].share - 6 / 17) < 1e-9 && t10.rows[1].share === 0.5, "shares of China coverage");
assert(t10.rows[2].share === 0.6 && t10.rows[3].share === null && t10.rows[3].shareSparse && t10.rows[4].share === null, "a share below the minimum denominator is n/a");
var tRank = C.rankCountries(tAgg, {countries: {ITA: {}, GBR: {}, FRA: {}, DEU: {}, POL: {}, BRA: {}, WLD: {}}}, "count_a", "all", tNames, null, "country");
assert(tRank.filter(function (r) { return r.value; }).slice(0, 5).map(function (r) { return r.iso + r.value; }).join() === t10.rows.map(function (r) { return r.iso + r.count; }).join(), "the same order and counts as the ranked list under country outlets");
/* A cut through a tie: the rows stop at n and the countries tied with the last row are returned. */
var t2 = C.topCountriesInWindow(tAgg, 3, tNames, "2026-09-30", 30);
assert(t2.rows.length === 3 && t2.rows[2].iso === "FRA" && t2.tiedBeyond.length === 1 && t2.tiedBeyond[0].iso === "DEU" && t2.tiedBeyond[0].rank === 3, "a tie past the last row is stated");
assert(C.topCountriesInWindow(tAgg, 4, tNames, "2026-09-30", 30).tiedBeyond.length === 0, "no tie past the last row");
/* Empty and absent data. */
var tEmpty = C.topCountriesInWindow(C.aggregateWindow({}, "2026-09-30", 30, "both"), 10, {}, "2026-09-30", 30);
assert(tEmpty.rows.length === 0 && tEmpty.worldwide === null && tEmpty.max === 0, "nothing to rank");
assert(C.topCountriesInWindow(null, 10).rows.length === 0 && C.topCountriesInWindow(null, 10).window === null, "no aggregate");
assert(C.topCountriesInWindow(tAgg, 10, null, "2026-09-30", 30).rows[0].name === "ITA", "codes stand in for missing names");
/* Every share states what it divides (2026-10-22): the state share sentence names its numerator and
   denominator, and the top country's share names the window's total it is a share of. */
var fBasis = C.fillFindingsTemplate(["{state_share_of_china} ({state_origin_count} of {china_total_count})", "{top_country_share} of {top_country_world}."],
  {all_time: {state_origin: 2279, china_total: 47799, state_share_of_china: 2279 / 47799},
   top_country: {iso: "ITA", state_origin: 304, share_of_world: 304 / 1420, world_total: 1420}}, {ITA: "Italy"}, {});
assert(fBasis[0] === "4.8% (2,279 of 47,799)", "state share with its basis: " + fBasis[0]);
assert(fBasis[1] === "21% of the world's 1,420 placements in those days.", "top country share with its total: " + fBasis[1]);
var fNoTotal = C.fillFindingsTemplate(["{top_country_share} of {top_country_world}."], {all_time: {}, top_country: {iso: "ITA", state_origin: 304, share_of_world: 0.2141}}, {}, {});
assert(fNoTotal[0] === "21% of the world's total.", "an older export without the total keeps the old words");
var tcWin = C.topCountryInWindow({countries: {ITA: {A: 304, uniqA: 292}, AUS: {A: 200}, WLD: {A: 916}}}, "both", null, null);
assert(tcWin.world_total === 1420 && Math.abs(tcWin.share_of_world - 304 / 1420) < 1e-9, "the top country's share is of the window's total, wires included");
/* Undetermined items (U, decision D1 as revised): in no count, share or denominator, China coverage included. */
var uk = C.emptyCounts(); uk.A = 10; uk.B = 5; uk.C = 85; uk.U = 400; uk.tdisc = 1000;
var umv = C.metricValue(uk, "share_a", 20, "all", null, {});
assert(umv.chinaTotal === 100 && Math.abs(umv.value - 0.1) < 1e-9 && umv.undetermined === 400, "U is in no China denominator: " + umv.chinaTotal);
assert(C.metricValue(uk, "count_china", 20, "all", null, {}).value === 100, "all China coverage leaves U out");
assert(C.metricValue(uk, "count_a", 20, "all", null, {}).value === 10, "state origin leaves U out");
var ulayer = C.metricValue(uk, "count_a", 20, "all", null, {routeCount: 400, routeUndetermined: true});
assert(ulayer.value === 400 && ulayer.undeterminedLayer && ulayer.note === C.UNDETERMINED_LAYER && !ulayer.provisional && !ulayer.withheld, "the route filter's undetermined layer is marked not counted");
var ushare = C.metricValue(uk, "share_a", 20, "all", null, {routeCount: 400, routeUndetermined: true});
assert(ushare.value === null && ushare.note === C.UNDETERMINED_NO_SHARE, "undetermined items are in no share");
var uat = C.allTimeInWindow({countries: {ITA: {A: 7, B: 1, C: 2, U: 5, pending: 3}, WLD: {A: 1, B: 0, C: 1, U: 2}}}, {unchecked: 1});
assert(uat.china_total === 12 && uat.undetermined === 7 && uat.undetermined_wires === 2 && uat.china_total_wires === 2, "all time China total is A + B + C");
assert(Math.abs(uat.state_share_of_china - 8 / 12) < 1e-9, "the share of China coverage is on the A + B + C basis");
var udf = C.distributionFindings({state_origin: 8, china_total: 12, undetermined: 7, undetermined_wires: 2}, {}, null, "country");
assert(udf.undetermined === 5, "undetermined follows the Distribution toggle");
var urows = C.rankCountries({countries: {ITA: uk}, reviewed: {}}, {countries: {ITA: {outlets_active: 20}}}, "count_a", "all", {ITA: "Italy"}, null, "both");
assert(urows[0].undetermined === 400 && urows[0].china_total === 100 && !("model_only_state_origin" in urows[0]), "the CSV carries an undetermined column beside A + B + C");
assert(C.nameOf("U") === "Undetermined: who wrote or commissioned it could not be settled" && !C.NAMES.M, "U is named, M is gone");
assert(!C.modelOnlyStatus && !C.MODEL_ONLY_PROVISIONAL, "nothing reads a rejoin status");
/* The licensed archive check: per country sums from the title rows, the panel line, singular and plural. */
var acData = {kept_apart: true, checks: [{id: "t", short_name: "ProQuest TDM", period: "September to October 2026", state_origin_signatures: 0,
  titles: [{name: "A Paper", country: "CAN", china_relevant: 10, awaiting_model: 3, no_state_signature: 7, not_about_china: 20},
           {name: "B Paper", country: "CAN", china_relevant: 5, awaiting_model: 1, no_state_signature: 4, not_about_china: 2},
           {name: "C Paper", country: "ARG", china_relevant: 2, awaiting_model: 1, no_state_signature: 1, not_about_china: 1000}],
  totals: {china_relevant: 17, awaiting_model: 5, no_state_signature: 12, not_about_china: 1022, records: 1039}, records_pulled: 1040, records_deduplicated: 1039,
  triggers: [{label: "cites Xinhua", count: 3}, {label: "CCTV", count: 2}]}]};
var acTpl = {country_line: "{titles_phrase} from {country} ({names}): {china}; {awaiting_clause}.", country_line_signatures: "covers {titles_phrase}",
  awaiting_none: "none", awaiting_one: "{awaiting} cites", awaiting_many: "{awaiting} cite"};
var acSums = C.archiveCheck.countrySums(acData.checks[0]);
assert(acSums.CAN.china_relevant === 15 && acSums.CAN.awaiting_model === 4 && acSums.CAN.titles.length === 2 && acSums.ARG.not_about_china === 1000, "archive sums by country");
assert(C.archiveCheck.countryLine(acData, "CAN", "Canada", acTpl) === "two titles from Canada (A Paper and B Paper): 15; 4 cite.", "archive line, plural: " + C.archiveCheck.countryLine(acData, "CAN", "Canada", acTpl));
assert(C.archiveCheck.countryLine(acData, "ARG", "Argentina", acTpl) === "one title from Argentina (C Paper): 2; 1 cites.", "archive line, singular");
assert(C.archiveCheck.countryLine(acData, "ITA", "Italy", acTpl) === "" && C.archiveCheck.countryLine(null, "CAN", "Canada", acTpl) === "", "no archive line elsewhere");
var acTok = C.archiveCheck.tokens(acData.checks[0]);
assert(acTok.not_china === "1,022" && acTok.records === "1,039" && acTok.titles === "3" && acTok.triggers === "cites Xinhua 3, CCTV 2", "archive tokens");
assert(C.archiveCheck.fill("{short_name} check, {period}: {signatures} in {china}; {nope}", acTok) === "ProQuest TDM check, September to October 2026: 0 in 17; {nope}", "archive fill");
assert(C.archiveCheck.countryLine({checks: [Object.assign({}, acData.checks[0], {state_origin_signatures: 2})]}, "CAN", "Canada", acTpl) === "covers two titles", "signatures found: no zero claim");
assert(C.archiveCheck.list({checks: [{id: "x", titles: []}]}).length === 0, "an empty check is skipped");
console.log("frontend smoke ok");
