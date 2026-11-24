/* Smoke test for docs/m_exposure.js. Run with: node tests/smoke_m_exposure.js (or under jsc through the
   measures shim). Wrapped in a function so it leaves no globals for the scripts loaded after it. */
(function () {
  var M = require("../docs/measures.js");
  var X = require("../docs/m_exposure.js");
  function assert(cond, msg) { if (!cond) { console.error("FAIL: exposure: " + msg); process.exit(1); } }
  function api(slot) {
    return {id: "exposure", slot: slot, section: null, meta: {}, names: {}, esc: M.esc, M: M,
            countryName: function (iso) { return {ITA: "Italy", MYS: "Malaysia", AUS: "Australia"}[iso] || iso; }};
  }
  var prov = {status: "provisional", label: "Exposure", check: {state: "none", n: 0, coded: 0}};
  var chk = {status: "checked", label: "Exposure", check: {state: "passed", n: 40, coded: 40, statistic: "placement agreement", value: 0.9, threshold: 0.8, checked_on: "2026-12-01"}};
  var wh = {status: "withheld", label: "Exposure", check: {state: "failed", n: 40, coded: 40, statistic: "placement agreement", value: 0.6, threshold: 0.8, checked_on: "2026-12-01"}};
  /* The snapshot of 2026-11-24, before any homepage read. */
  var noReads = {
    window: {first: "2026-09-03", last: "2026-11-24"},
    totals: {placements: 2312, news_feed: 1702, release_only: 610},
    newsrooms: {placements: 2059, news_feed: 1702, release_only: 357, share_release_only: 0.1734, checked: 0, on_homepage: 0},
    release_kinds: {press_release: 356, sponsored: 1, partner: 0, distribution_wire: 253},
    homepage: {reads: 0, reads_ok: 0, first: null, check_after_hours: 12},
    by_country: [{id: "ITA", placements: 936, news_feed: 919, release_only: 17},
                 {id: "WLD", placements: 253, news_feed: 0, release_only: 253},
                 {id: "MYS", placements: 140, news_feed: 46, release_only: 94},
                 {id: "<x>", placements: 1, news_feed: 1, release_only: 0},
                 {id: "CHN", placements: 9, news_feed: 9, release_only: 0}]
  };
  var reads = JSON.parse(JSON.stringify(noReads));
  reads.homepage = {reads: 50, reads_ok: 44, first: "2026-12-01T10:00:00+00:00", check_after_hours: 12};
  reads.newsrooms.checked = 30; reads.newsrooms.on_homepage = 3; reads.newsrooms.share_on_homepage = 0.1;
  reads.newsrooms.release_only_checked = 2; reads.newsrooms.release_only_on_homepage = 0;
  reads.by_country[0].checked = 22; reads.by_country[0].on_homepage = 2;

  assert(X.ID === "exposure" && M.get("exposure") && M.get("exposure").render === X.render, "registered");
  assert(X.fmt(1234567) === "1,234,567" && X.fmt(null) === "n/a", "number format");
  assert(X.pct(0.1734) === "17%" && X.pct(0.0182) === "1.8%" && X.pct(0) === "0%" && X.pct(null) === "n/a", "shares");

  /* Off, as shipped: hidden and empty. */
  var slot = {hidden: false, innerHTML: "stale"};
  X.render({status: "off"}, noReads, api(slot));
  assert(slot.hidden === true && slot.innerHTML === "", "off hides and clears");

  /* On, with the file missing or empty: the empty state. */
  X.render(prov, null, api(slot));
  assert(slot.hidden === false && slot.innerHTML.indexOf("did not load") !== -1, "missing file");
  X.render(prov, {totals: {placements: 0}, newsrooms: {placements: 0}, by_country: []}, api(slot));
  assert(slot.innerHTML.indexOf("Nothing to show yet") !== -1 && slot.innerHTML.indexOf("<table") === -1, "no placements");

  /* Provisional, no homepage read yet: the line with numerator, denominator and window, no homepage columns. */
  X.render(prov, noReads, api(slot));
  var h = slot.innerHTML;
  assert(h.indexOf("m-chip-provisional") !== -1 && h.indexOf("(provisional)") !== -1, "chip and suffix");
  assert(h.indexOf("<strong>2,059</strong>") !== -1 && h.indexOf("<strong>357</strong> (17%)") !== -1 && h.indexOf("<strong>1,702</strong>") !== -1, "lead line");
  assert(h.indexOf("from 2026-09-03 to 2026-11-24") !== -1, "window");
  assert(h.indexOf("253 placements on distribution wires are releases") !== -1, "wires apart");
  assert(h.indexOf("Homepage reads have not started yet") !== -1 && h.indexOf("On the homepage") === -1, "no reads yet");
  assert(h.indexOf("<td>Italy</td>") !== -1 && h.indexOf("<td>Worldwide distribution</td>") !== -1, "country names");
  assert(h.indexOf("&lt;x&gt;") !== -1 && h.indexOf("<x>") === -1, "escapes");
  assert(h.indexOf("CHN") === -1, "China is never a row");
  assert(h.indexOf("<td>Italy</td>") < h.indexOf("<td>Worldwide distribution</td>"), "most placements first");
  assert(h.indexOf("lower bounds") !== -1 && h.indexOf("less than an hour") !== -1, "limits");
  assert(h.indexOf("Category") === -1 && !/[–—]/.test(h) && h.indexOf("http") === -1, "no category letters, dashes or addresses");
  assert(!/\bmost\b/i.test(h.replace(/most placements/g, "")), "no unearned 'most'");

  /* With homepage reads: the homepage line and columns. */
  X.render(prov, reads, api(slot));
  h = slot.innerHTML;
  assert(h.indexOf("since 2026-12-01") !== -1 && h.indexOf("<strong>30</strong>") !== -1 && h.indexOf("<strong>3</strong> (10%)") !== -1, "homepage line");
  assert(h.indexOf("within 12 hours") !== -1 && h.indexOf("of the 2 among them found only in a release section, 0 were") !== -1, "release items on the homepage");
  assert(h.indexOf("On the homepage") !== -1 && h.indexOf('<td class="num">22</td>') !== -1, "homepage columns");

  /* Reads, but nothing checked yet. */
  var none = JSON.parse(JSON.stringify(reads)); none.newsrooms.checked = 0;
  X.render(prov, none, api(slot));
  assert(slot.innerHTML.indexOf("no newsroom placement was discovered within 12 hours") !== -1, "reads without checked placements");

  /* At most MAX_ROWS rows; ties ordered by code. */
  var many = JSON.parse(JSON.stringify(noReads)); many.by_country = [];
  for (var i = 0; i < 15; i++) many.by_country.push({id: "C" + (i < 10 ? "0" : "") + i, placements: 5, news_feed: 5, release_only: 0});
  X.render(prov, many, api(slot));
  assert((slot.innerHTML.match(/<tr>/g) || []).length === X.MAX_ROWS + 1, "rows capped");
  assert(slot.innerHTML.indexOf("<td>C00</td>") < slot.innerHTML.indexOf("<td>C01</td>"), "ties by code");

  /* Checked: chip, no suffix. Withheld: no figure. No slot is fine. */
  X.render(chk, noReads, api(slot));
  assert(slot.innerHTML.indexOf("m-chip-checked") !== -1 && slot.innerHTML.indexOf("(provisional)") === -1, "checked");
  X.render(wh, noReads, api(slot));
  assert(slot.innerHTML.indexOf("withheld") !== -1 && slot.innerHTML.indexOf("2,059") === -1, "withheld shows no figure");
  X.render(prov, noReads, {id: "exposure", slot: null});

  console.log("exposure smoke ok");
}());
