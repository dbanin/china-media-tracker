/* Smoke test for outlets selected for China coverage, not reach (RULES D12): compute.js selectionBadge,
   chinaSelectedCountry and selectionTop10Note, and the country panel's line (docs/app.js selectionLine, on
   window.TrackerApp.helpers), loaded after d3, compute.js and app.js with a document that never finishes
   loading. Run by tests/test_selection_basis.py under node. */
(function () {
  var C = window.TrackerCompute, H = window.TrackerApp && window.TrackerApp.helpers;
  function assert(cond, msg) { if (!cond) throw new Error("FAIL: " + msg); }
  assert(C && H && H.selectionLine, "selection helpers are exposed");
  var url = "https://freedomhouse.org/country/nigeria/beijings-global-media-influence/2022";
  var o = {name: "The <b>Authority</b>", reach_selected: false, selection_evidence: url,
           selection_basis: "selected for documented pro-China content: it republished a People's Daily commentary, not reach; evidence: " + url};
  var b = C.selectionBadge(o);
  assert(b && b.label === "selected for China coverage" && b.url === url, "badge for a selected outlet");
  assert(b.text === "Selected for documented pro-China content: it republished a People's Daily commentary, not reach.", "basis without its evidence tail: " + b.text);
  // Old data (schema 18): no field, and a reach selected outlet: nothing shown.
  assert(C.selectionBadge({name: "X"}) === null && C.selectionBadge({reach_selected: true}) === null && C.selectionBadge(null) === null, "no badge without reach_selected false");
  assert(C.selectionBadge({reach_selected: false}).text === "Selected for its China coverage, not reach.", "a missing basis still says why");
  var html = H.selectionLine(o);
  assert(html.indexOf("<b>") === -1 && html.indexOf("The &lt;b&gt;Authority&lt;/b&gt;") !== -1, "name escaped in the aria label: " + html);
  assert(html.indexOf('<span class="badge">selected for China coverage</span>') !== -1, "visible tag");
  assert(html.indexOf('<a href="' + url + '" target="_blank" rel="noopener"') !== -1 && html.indexOf(">evidence</a>") !== -1, "evidence link");
  var bad = H.selectionLine({name: "Y", reach_selected: false, selection_basis: "x, not reach; evidence: javascript:alert(1)", selection_evidence: " javascript:alert(1)"});
  assert(bad.indexOf("<a ") === -1 && bad.indexOf("javascript") === -1, "an unsafe evidence URL is never a link: " + bad);
  assert(H.selectionLine({name: "Z"}) === "", "nothing for an outlet chosen by reach");
  assert(C.chinaSelectedCountry({warnings: [{type: "few_items", text: "a"}, {type: "china_selected", text: "b"}]}) === true, "country with the warning");
  assert(C.chinaSelectedCountry({warnings: [{type: "few_items"}]}) === false && C.chinaSelectedCountry({}) === false && C.chinaSelectedCountry(null) === false && C.chinaSelectedCountry({warnings: null}) === false, "no warning, no mark");
  assert(C.selectionTop10Note([]) === "" && C.selectionTop10Note(null) === "", "no note without a marked country");
  var one = C.selectionTop10Note(["Nigeria"]), many = C.selectionTop10Note(["Nigeria", "Uganda", "South Africa"]);
  assert(one.indexOf("† Figures for Nigeria include outlets") === 0 && one.indexOf("add to its counts") !== -1, one);
  assert(many.indexOf("Nigeria, Uganda and South Africa") !== -1 && many.indexOf("those countries' counts") !== -1, many);
  assert(!/[–—]/.test(html + one + many + b.text), "no dashes");
  console.log("selection smoke ok");
})();
