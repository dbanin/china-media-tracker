/* Smoke test for the country panel's stake lines (docs/app.js stakeLines and stakeAbsenceNote, exposed on
   window.TrackerApp.helpers), loaded like tests/app_smoke.js after d3, compute.js and app.js with a document
   that never finishes loading. Run by tests/test_foreign_state_stake_2026_10_28.py under node. */
(function () {
  var H = window.TrackerApp && window.TrackerApp.helpers;
  function assert(cond, msg) { if (!cond) throw new Error("FAIL: " + msg); }
  assert(H && H.stakeLines && H.stakeAbsenceNote, "stake helpers are exposed");
  var stk = {state: "CHN", holder: "Interacom <b>Holdings</b> \"Q\"", holder_type: ["state_fund"], state_link: "L", held_in: "Société d'État Ltd", share_percent: 20,
             from: "2013-08", to: null, status: "disputed", as_of: "2026-07-22", source_url: "https://a.example/1", source_title: "Mail & Guardian, 23 August 2013",
             status_source_url: "https://b.example/2", status_source_title: "Daily Maverick", note: "See (https://c.example/x?y=1); also javascript:alert(1) and http://d.example/z."};
  var html = H.stakeLines({foreign_state_stake: [stk]});
  assert(/<span class="stake-status is-disputed">Disputed<\/span>/.test(html), "the disputed status is a visible tag: " + html);
  assert(/sources disagree on whether it still reaches this outlet/.test(html), "the dispute is said in the line");
  assert(html.indexOf("<b>") === -1 && html.indexOf("&lt;b&gt;Holdings&lt;/b&gt; &quot;Q&quot;") !== -1, "holder text is escaped");
  assert(html.indexOf("Société d'État Ltd") !== -1, "Unicode survives");
  assert(/<details class="stake-more"><summary>Sources and caveats<\/summary><p>/.test(html) && html.indexOf(' title="See') === -1, "the note opens in place, not as a tooltip");
  assert(html.indexOf('<a href="https://c.example/x?y=1" target="_blank" rel="noopener">https://c.example/x?y=1</a>);') !== -1, "a note URL is a link, without its closing bracket: " + html);
  assert(html.indexOf('href="javascript') === -1 && html.indexOf("javascript:alert(1)") !== -1, "a javascript: text is never a link");
  assert(html.indexOf('<a href="http://d.example/z"') !== -1 && html.indexOf('http://d.example/z</a>.') !== -1, "the trailing full stop is left out of a link");
  assert(html.indexOf('aria-label="Source for the stake: Mail &amp; Guardian, 23 August 2013">source</a>') !== -1, "the source link names its source to a screen reader");
  assert(html.indexOf('aria-label="Source for its status: Daily Maverick">status source</a>') !== -1, "the status link names its source");
  assert(/<div class="stake-note stake-caveat">A stake records ownership; it says nothing about editorial control\.<\/div>$/.test(html), "the ownership caveat closes the lines, once");
  var bad = H.stakeLines({foreign_state_stake: [Object.assign({}, stk, {source_url: "javascript:alert(1)", status_source_url: " JaVaScRiPt:alert(2)", note: ""})]});
  assert(bad.indexOf("javascript") === -1 && bad.indexOf("JaVaScRiPt") === -1 && bad.indexOf("<a ") === -1 && bad.indexOf("<details") === -1, "unsafe links are dropped, no empty note: " + bad);
  var same = H.stakeLines({foreign_state_stake: [Object.assign({}, stk, {status_source_url: stk.source_url})]});
  assert((same.match(/<a href="https:\/\/a\.example\/1"/g) || []).length === 1, "one link when both sources are the same page");
  var two = H.stakeLines({foreign_state_stake: [stk, Object.assign({}, stk, {status: "ended", to: "2026-07", holder: "B"})]});
  assert((two.match(/class="stake-note"/g) || []).length === 2 && (two.match(/stake-caveat/g) || []).length === 1, "two stakes, two lines, one caveat");
  assert(/<span class="stake-status is-ended">Ended<\/span>/.test(two) && /ended for this outlet in July 2026/.test(two), "the ended line");
  var odd = H.stakeLines({foreign_state_stake: [Object.assign({}, stk, {status: "x\" onmouseover=\"alert(1)"})]});
  assert(odd.indexOf("onmouseover") === -1 && /<span class="stake-status">Status unstated<\/span>/.test(odd), "an unknown status never reaches a class name");
  assert(H.stakeLines({}) === "" && H.stakeLines({foreign_state_stake: null}) === "" && H.stakeLines({foreign_state_stake: []}) === "", "no stake recorded, nothing shown");
  var abs = H.stakeAbsenceNote([{foreign_state_stake: [stk]}, {}]);
  assert(/has none recorded, which is not the same as having none/.test(abs) && !/\bhas none\./.test(abs), "absence is none recorded, not none");
  assert(H.stakeAbsenceNote([{}, {foreign_state_stake: null}]) === "" && H.stakeAbsenceNote([]) === "", "no stake in the country, no note");
  assert(!/[\u2013\u2014]/.test(html + two + abs), "no dashes");
  console.log("stakes smoke ok");
})();
