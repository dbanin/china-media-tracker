/* Smoke test for the deploy self heal helpers docs/app.js exposes on window.TrackerApp.helpers: which app.js
   version an index.html links, whether to reload (once per version per tab, never without storage), and which
   data files come from another export. Run by tests/test_deploy_self_heal_2026_10_23.py under jsc or node,
   after d3, compute.js and app.js, with a document that never finishes loading. */
(function () {
  var H = window.TrackerApp && window.TrackerApp.helpers;
  function assert(cond, msg) { if (!cond) throw new Error("FAIL: " + msg); }
  assert(H && H.healDecision && H.pageAssetVersion && H.stragglers, "self heal helpers are exposed");

  /* The ?v= of app.js, from the real index.html and from small cases. */
  if (typeof __INDEX_HTML === "string") assert(H.pageAssetVersion(__INDEX_HTML) === H.assetVersion, "index.html links app.js?v=" + H.assetVersion + ": " + H.pageAssetVersion(__INDEX_HTML));
  assert(H.pageAssetVersion('<script src="compute.js?v=7"></script><script src="app.js?v=92"></script>') === "92", "app.js, not compute.js");
  assert(H.pageAssetVersion("<script src='./app.js?v=93'></script>") === "93", "single quotes and ./");
  assert(H.pageAssetVersion('<script src="measures.js?v=91"></script>') === null, "no app.js");
  assert(H.pageAssetVersion('<script src="mapp.js?v=91"></script>') === null, "another file ending in app.js");
  assert(H.pageAssetVersion(null) === null && H.pageAssetVersion("") === null, "nothing fetched");

  /* The decision. */
  var d = H.healDecision({running: "91", fetched: "92", tried: []});
  assert(d.reload && d.key === "v92" && d.query === "v=92", "new index: reload to ?v=92 " + JSON.stringify(d));
  assert(!H.healDecision({running: "91", fetched: "92", tried: ["v92"]}).reload, "never twice for the same version");
  assert(!H.healDecision({running: "91", fetched: "91", tried: []}).reload, "same version: no reload");
  assert(!H.healDecision({running: "91", fetched: null, tried: []}).reload, "index check failed: no reload");
  assert(!H.healDecision({running: "91", fetched: "92", tried: null}).reload, "storage unavailable: no reload");
  assert(!H.healDecision({running: "91", fetched: "92"}).reload && !H.healDecision().reload, "no storage state: no reload");
  /* An old index (the simulated stale cache): one reload, then never again. */
  var old = H.healDecision({running: "91", fetched: "1", tried: []});
  assert(old.reload && old.key === "v1" && old.query === "v=1", "old index: one reload " + JSON.stringify(old));
  assert(!H.healDecision({running: "91", fetched: "1", tried: [old.key]}).reload, "old index after the reload: stays");
  /* Two versions served in turn by a half updated cache: one reload each, then it stops. */
  var tried = [], n = 0, pairs = [["91", "92"], ["92", "91"], ["91", "92"], ["92", "91"], ["91", "92"]];
  pairs.forEach(function (p) { var x = H.healDecision({running: p[0], fetched: p[1], tried: tried}); if (x.reload) { tried.push(x.key); n++; } });
  assert(n === 2, "alternating versions reload at most once each: " + n);
  /* A newer schema than the code knows is a new deploy. */
  var s = H.healDecision({running: "91", schema: 19, known: 18, tried: []});
  assert(s.reload && s.key === "s19" && s.query === "v=91&s=19", "newer schema: one reload " + JSON.stringify(s));
  assert(!H.healDecision({running: "91", schema: 19, known: 18, tried: ["s19"]}).reload, "newer schema: never twice");
  assert(!H.healDecision({running: "91", schema: 18, known: 18, tried: []}).reload && !H.healDecision({running: "91", schema: 17, known: 18, tried: []}).reload, "known or older schema: no reload");
  assert(!H.healDecision({running: "91", schema: "19", known: 18, tried: []}).reload && !H.healDecision({running: "91", schema: null, known: 18, tried: []}).reload, "no numeric schema: no reload");
  assert(!H.healDecision({running: "91", schema: 19, known: 18, tried: null}).reload, "newer schema without storage: no reload");
  var both = H.healDecision({running: "91", fetched: "92", schema: 19, known: 18, tried: ["v92"]});
  assert(both.reload && both.key === "s19" && both.query === "v=92&s=19", "code tried, schema still new: its own single reload " + JSON.stringify(both));
  assert(!H.healDecision({running: "91", fetched: "92", schema: 19, known: 18, tried: ["v92", "s19"]}).reload, "both tried: no reload");
  assert(typeof H.knownSchema === "number", "KNOWN_SCHEMA is a number");

  /* Mixed exports: which files to fetch again. */
  var at = "2026-10-23T07:59:14+00:00", older = "2026-10-22T07:59:14+00:00";
  var same = [{url: "data/meta.json", at: at, schema: 18}, {url: "data/latest.json", at: at, schema: 18}, {url: "data/daily/2026-10.json", at: at, schema: 18},
              {url: "data/spikes.json", at: "2026-10-23T07:59:32+00:00", schema: null}];
  assert(H.stragglers(same).length === 0, "one export, side file seconds later: nothing to fetch " + H.stragglers(same));
  var mixed = same.concat([{url: "data/outlets.json", at: older, schema: 18}]);
  assert(H.stragglers(mixed).join(",") === "data/outlets.json", "old outlets.json is the straggler: " + H.stragglers(mixed));
  var oldMeta = [{url: "data/meta.json", at: older, schema: 17}, {url: "data/latest.json", at: at, schema: 18}];
  assert(H.stragglers(oldMeta).join(",") === "data/meta.json", "old meta is fetched again: " + H.stragglers(oldMeta));
  var schemaOnly = [{url: "data/meta.json", at: at, schema: 18}, {url: "data/latest.json", at: at, schema: 17}];
  assert(H.stragglers(schemaOnly).join(",") === "data/latest.json", "schema differs: " + H.stragglers(schemaOnly));
  var farSide = same.concat([{url: "data/alteration.json", at: older, schema: null}]);
  assert(H.stragglers(farSide).join(",") === "data/alteration.json", "side file a day old: " + H.stragglers(farSide));
  assert(H.stragglers([]).length === 0 && H.stragglers(null).length === 0 && H.stragglers([{url: "data/x.json", at: at, schema: null}]).length === 0, "empty and unstamped only");
  console.log("deploy heal smoke ok");
})();
