/* Smoke test for the map warning triangles switch (2026-10-23): off for now, at the owner's request.
   Loaded after d3, compute.js and app.js with a document that never finishes loading, as tests/app_smoke.js is;
   tests/test_map_triangles_off_2026_10_23.py runs it under jsc (or node). */
(function () {
  var H = window.TrackerApp && window.TrackerApp.helpers;
  function assert(cond, msg) { if (!cond) throw new Error("FAIL: " + msg); }
  assert(H, "TrackerApp.helpers is exposed");
  assert(H.showWarningMarkers === false, "the map draws no warning triangles while the switch is off");
  /* The warnings themselves still reach every text that shows them: the export's standing warnings, in order. */
  var entry = {warnings: [{text: "a large share of its articles were behind a paywall and never read"}, {text: "feed checks came back empty"}]};
  var w = H.countryWarnings(entry, {});
  assert(w.length === 2 && /paywall/.test(w[0]) && /feed checks/.test(w[1]), "countryWarnings keeps the warnings: " + w);
  assert(H.countryWarnings({}, {}).length === 0, "no warnings, none shown");
  var r = H.countryWarnings({warnings: [], relay_outlets: 2}, {relayIncompleteDays: ["2026-10-01"]});
  assert(r.length === 1 && /2 outlets/.test(r[0]) && /1 day/.test(r[0]), "the window dependent warning is kept: " + r);
  console.log("map markers smoke ok");
})();
