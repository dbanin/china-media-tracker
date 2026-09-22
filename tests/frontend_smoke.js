/* Smoke test for the front end computations on an empty dataset. Run with: node tests/frontend_smoke.js */
var C = require("../docs/compute.js");
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
var agg = C.aggregateWindow({}, null, 30);
assert(Object.keys(agg.countries).length === 0, "empty aggregate");
assert(C.listDays({}).length === 0, "no days");
var mv = C.metricValue(undefined, "count_target", 0, "all", undefined);
assert(mv.value === 0 && mv.pending === 0, "target count on empty");
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
assert(Math.abs(C.metricValue(a2.countries.ITA, "share_target", 5, "all").value - 4 / 8) < 1e-9, "share");
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
var tv = C.metricValue(withPending, "count_target", 2, "all");
assert(tv.value === 4 && tv.chinaTotal === 6 && tv.target === 4, "pending counts as target and as coverage");
assert(Math.abs(C.metricValue(withPending, "share_target", 2, "all").value - 4 / 6) < 1e-9, "target share");
var tiny = C.metricValue(Object.assign(C.emptyCounts(), {A: 2}), "share_target", 1, "all");
assert(tiny.value === null && tiny.sparse === true, "tiny denominator gives no share");
assert(C.fillClass({coverage: "monitored", outlets_active: 1}, tiny) === "sparse", "sparse fill");
assert(C.metricValue(Object.assign(C.emptyCounts(), {A: 2}), "count_a", 1, "all").value === 2, "count still shown");
var pm = C.metricValue(withPending, "per_million_target", 2, "all", undefined, {population: 2000000});
assert(Math.abs(pm.value - 2) < 1e-9, "per million people");
var nopop = C.metricValue(withPending, "per_million_target", 2, "all", undefined, {});
assert(nopop.value === null && nopop.sparse === true && nopop.note, "no population gives no value");
var all = Object.assign(C.emptyCounts(), {A: 1, C: 2, pending: 1, tdisc: 200, ttarget: 2, tchina: 4});
assert(Math.abs(C.metricValue(all, "share_of_output_target", 2, "all", undefined, {topOutlets: 8}).value - 0.01) < 1e-9, "share of monitored output");
assert(Math.abs(C.metricValue(all, "share_of_output_china", 2, "all", undefined, {topOutlets: 8}).value - 0.02) < 1e-9, "china share of monitored output");
var floor = C.metricValue(all, "share_of_output_target", 2, "all", undefined, {topOutlets: 4});
assert(floor.value === null && floor.sparse === true && /Fewer than 5 monitored outlets/.test(floor.note), "outlet floor");
var few = Object.assign(C.emptyCounts(), {A: 1, tdisc: 10, ttarget: 1});
assert(C.metricValue(few, "share_of_output_target", 1, "all").value === null, "small stream gives no share");
assert(C.fillClass({coverage: "monitored", outlets_active: 1}, C.metricValue(Object.assign(C.emptyCounts(), {tdisc: 300}), "share_of_output_target", 1, "all")) === "zero", "items but no China coverage is zero, not nodata");
assert(C.fillClass({coverage: "monitored", outlets_active: 2, language_support: "none"}, C.metricValue(C.emptyCounts(), "count_a", 2, "all")) === "unreadable", "unreadable language");
var tinyPop = C.metricValue(withPending, "per_million_target", 2, "all", undefined, {population: 3700});
assert(tinyPop.value === null && tinyPop.sparse === true, "tiny population gives no per capita value");
assert(Math.abs(C.percentile([1, 2, 3, 4, 100], 0.95) - 80.8) < 1e-9 && C.percentile([], 0.95) === null && C.percentile([5], 0.95) === 5, "percentile");
assert(C.gridMetric("a", "share_of_output") === "share_of_output_a" && C.gridMetric("china", "count") === "count_china" && C.gridMetric("nope", "nope") === "count_a", "grid");
assert(C.BASES[C.BASES.length - 1] === "per_million", "per capita is offered last");
/* Share of China coverage is a denominator like the others, and is meaningless for All China coverage. */
assert(C.gridMetric("a", "share_of_china") === "share_a" && C.gridMetric("b", "share_of_china") === "share_b" &&
       C.gridMetric("target", "share_of_china") === "share_target", "share of China coverage is a basis");
assert(C.basisAvailable("a", "share_of_china") && !C.basisAvailable("china", "share_of_china"), "not offered for All China coverage");
assert(C.gridMetric("china", "share_of_china") === "count_china", "an unavailable pair falls back inside its own measure");
assert(C.METRICS.count_ab === undefined && C.METRICS.share_ab === undefined, "the combined metrics are gone");
var allA = Object.assign(C.emptyCounts(), {A: 2, C: 8, tdisc: 400, ttarget: 3, tchina: 10, ta: 2});
assert(Math.abs(C.metricValue(allA, "share_of_output_a", 1, "all").value - 0.005) < 1e-9, "state origin share of monitored output");
assert(C.metricValue(allA, "count_china", 1, "all").value === 10, "china count");
assert(Math.abs(C.metricValue(allA, "per_million_china", 1, "all", undefined, {population: 5000000}).value - 2) < 1e-9, "china per million");
assert(C.metricValue(allA, "per_outlet_china", 2, "all").value === 5, "china per outlet");
/* The scale cap is pooled across dates, so it does not move with the day shown. */
var latest = {countries: {ITA: {coverage: "monitored", outlets_active: 5}, FRA: {coverage: "monitored", outlets_active: 5}}};
var scaleMonths = {"2026-09": {days: {"2026-09-01": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 10, C: 1}), FRA: Object.assign(C.emptyCounts(), {A: 1, C: 1})}},
                                      "2026-09-02": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 2, C: 1}), FRA: Object.assign(C.emptyCounts(), {A: 2, C: 1})}}}}};
var s = C.scaleCap(scaleMonths, ["2026-09-01", "2026-09-02"], latest, "count_a", 1, "all");
assert(s.values === 4 && s.max === 10 && Math.abs(s.cap - C.percentile([10, 1, 2, 2], 0.95)) < 1e-9, "pooled scale cap");
var themeMonths = {"2026-09": {days: {"2026-09-01": {countries: {}, reviewed: {}, themes: {ITA: {diplomacy: [2, 1, 1]}}},
                                      "2026-09-02": {countries: {}, reviewed: {}, themes: {ITA: {diplomacy: [1, 1, 0], culture: [3, 0, 0]}}}}}};
var th = C.aggregateThemes(themeMonths, "2026-09-02", 2);
assert(th.ITA.diplomacy[0] === 3 && th.ITA.diplomacy[1] === 2 && th.ITA.diplomacy[2] === 1 && th.ITA.culture[0] === 3, "theme window sum");
assert(C.aggregateThemes(themeMonths, "2026-09-02", 1).ITA.diplomacy[0] === 1, "theme single day");
assert(Object.keys(C.aggregateThemes({}, null, 30)).length === 0 && C.MEASURE_INDEX.target === 1, "themes on empty");
/* Unverified relay as a measure of its own, and the provisional state before any reliability study. */
assert(C.gridMetric("b", "count") === "count_b" && C.gridMetric("b", "per_million") === "per_million_b" && C.MEASURE_INDEX.b === 3, "relay grid");
var relayCounts = Object.assign(C.emptyCounts(), {A: 1, B: 4, C: 5, uniqA: 1, uniqAB: 4, tdisc: 100, tb: 4});
var PROV = {measured: true, publishable: false, provisional: true};
var prov = C.metricValue(relayCounts, "count_b", 5, "all", undefined, {relay: PROV});
assert(prov.value === 4 && prov.provisional === true && !prov.withheld && prov.underlyingB === 3 && prov.note === C.RELAY_PROVISIONAL, "provisional relay is shown and marked");
assert(C.fillClass({coverage: "monitored", outlets_active: 5}, prov) === "value", "provisional relay colours the map");
assert(C.metricValue(relayCounts, "count_target", 5, "all", undefined, {relay: PROV}).value === 5, "state-linked stays readable while provisional");
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
/* A country that published items but classified no China coverage still has no per capita value. */
var noPop = C.metricValue(Object.assign(C.emptyCounts(), {tdisc: 400}), "per_million_a", 6, "all", undefined, {});
assert(noPop.value === null && noPop.sparse === true && C.fillClass(monitored, noPop) === "sparse", "no population is not shown, never zero");
/* The sums that contain unchecked state sourcing are never withheld, and carry its provisional marking. */
var insideProv = C.metricValue(relayCounts, "count_target", 5, "all", undefined, {relay: PROV});
assert(insideProv.value === 5 && insideProv.provisional === true && !insideProv.withheld && insideProv.note === C.RELAY_PROVISIONAL, "state-linked articles are marked provisional while the relay count inside them is");
assert(C.metricValue(relayCounts, "count_china", 5, "all", undefined, {relay: PROV}).provisional === true, "all China coverage is marked provisional too");
assert(C.metricValue(relayCounts, "count_a", 5, "all", undefined, {relay: PROV}).provisional === false, "state origin contains no relay count, so it is not marked");
assert(C.METRICS.count_target.relayInside && C.METRICS.count_china.relayInside && !C.METRICS.count_a.relayInside, "which metrics contain the relay count");
/* A withheld count cannot be reconstructed from the columns beside it. */
var WITHHELD = {measured: true, publishable: false, provisional: false};
var wLatest = {countries: {ITA: {coverage: "monitored", outlets_active: 5}}};
var wAgg = {countries: {ITA: relayCounts}, reviewed: {}};
var wa = C.rankCountries(wAgg, wLatest, "count_a", "all", {}, function () { return {relay: WITHHELD}; })[0];
assert(wa.unverified_relay === "" && wa.target === "" && wa.china_total === "" && wa.target_in_published_items === "" && wa.china_in_published_items === "", "no sum containing a withheld count is written beside it");
assert(wa.state_origin === 1 && wa.independent === 5, "state origin and independent journalism still travel when the shown value contains no relay count");
var wt = C.rankCountries(wAgg, wLatest, "count_target", "all", {}, function () { return {relay: WITHHELD}; })[0];
assert(wt.value === 5 && wt.state_origin === "" && wt.official_sourcing_pending === "", "the counts the shown sum would be differenced against leave with it");
/* A route narrows state origin and nothing else, so the counts it does not touch are not written beside it. */
var rr = C.rankCountries({countries: {ITA: relayCounts}, reviewed: {}}, wLatest, "count_a", "all", {}, function () { return {relay: {measured: true, publishable: true}, routeCount: 1}; })[0];
assert(rr.value === 1 && rr.state_origin === 1 && rr.target === "" && rr.china_total === "" && rr.state_origin_underlying_items === "" && rr.items_published_monitored_outlets === "" && /route/.test(rr.note), "a route filtered row carries only what the route filtered");
/* CSV: a lone carriage return breaks a row, and a leading =, +, - or @ runs as a formula. */
assert(C.toCSV([{a: "one\rtwo"}], ["a"]) === 'a\n"one\rtwo"\n', "a carriage return is quoted");
assert(C.toCSV([{a: "=1+1"}, {a: "@x"}, {a: "-lead"}], ["a"]) === 'a\n"\'=1+1"\n"\'@x"\n"\'-lead"\n', "a formula is quoted and marked as text");
assert(C.toCSV([{a: -3.5}, {a: 0.25}], ["a"]) === "a\n-3.5\n0.25\n", "a negative number is still a number");
console.log("frontend smoke ok");
