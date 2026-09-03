/* Smoke test for the front end computations on an empty dataset. Run with: node tests/frontend_smoke.js */
var C = require("../docs/compute.js");
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
var agg = C.aggregateWindow({}, null, 30);
assert(Object.keys(agg.countries).length === 0, "empty aggregate");
assert(C.listDays({}).length === 0, "no days");
var mv = C.metricValue(undefined, "share_ab", 0, "all", undefined);
assert(mv.value === null && mv.chinaTotal === 0, "share undefined on empty");
assert(C.fillClass(undefined, mv) === "nocoverage", "no entry is nocoverage");
assert(C.fillClass({coverage: "monitored", outlets_active: 3}, mv) === "nodata", "monitored without coverage is nodata");
assert(C.fillClass({coverage: "monitored", outlets_active: 3}, {value: 0, chinaTotal: 5}) === "zero", "zero detections");
assert(C.fillClass({coverage: "gap"}, mv) === "gap", "gap");
assert(C.rankCountries(agg, {countries: {}}, "count_a", "all", {}).length === 0, "rank empty");
assert(C.toCSV([], ["a"]) === "a\n", "csv header only");
var months = {"2026-09": {days: {"2026-09-01": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 2, C: 3})}, reviewed: {}, llm_ceiling_hit: false},
                                 "2026-09-02": {countries: {ITA: Object.assign(C.emptyCounts(), {A: 1, B: 1, C: 1})}, reviewed: {}, llm_ceiling_hit: true}}}};
var a2 = C.aggregateWindow(months, "2026-09-02", 2);
assert(a2.countries.ITA.A === 3 && a2.countries.ITA.B === 1 && a2.ceilingDays.length === 1, "window sum");
var a1 = C.aggregateWindow(months, "2026-09-02", 1);
assert(a1.countries.ITA.A === 1, "single day");
assert(Math.abs(C.metricValue(a2.countries.ITA, "share_ab", 5, "all").value - 4 / 8) < 1e-9, "share");
console.log("frontend smoke ok");
