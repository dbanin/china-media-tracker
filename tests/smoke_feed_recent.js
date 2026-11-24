/* Smoke test for the recent share of feed items missed (docs/app.js recentMissed, exposed on
   window.TrackerApp.helpers), loaded like tests/smoke_stakes.js. Run by tests/test_feed_recent.py under node. */
(function () {
  var H = window.TrackerApp && window.TrackerApp.helpers;
  function assert(cond, msg) { if (!cond) throw new Error("FAIL: " + msg); }
  assert(H && H.recentMissed, "the helper is exposed");
  var recent = {days: 7, first_day: "2026-11-11", last_day: "2026-11-17", polls: 264070, sat: 2839, miss: 45197, mpolls: 264070, mnew: 647991, miss_share: 0.0652};
  assert(H.recentMissed({recent: recent}) === "over the 7 days to 2026-11-17 an estimated 6.5 percent of feed items were missed (45,197 against 647,991 found)", H.recentMissed({recent: recent}));
  assert(H.recentMissed({}) === "" && H.recentMissed(null) === "", "nothing without the block, as in an export made before it");
  assert(H.recentMissed({recent: Object.assign({}, recent, {mpolls: 0, miss_share: null})}) === "", "nothing when the estimate does not cover those days");
  assert(H.knownSchema >= 21, "the page knows the schema that carries the block");
  console.log("feed recent smoke ok");
})();
