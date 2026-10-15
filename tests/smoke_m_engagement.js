/* Smoke test for docs/m_engagement.js. Run with: node tests/smoke_m_engagement.js (or under jsc through
   the measures shim). Wrapped in a function so it leaves no globals for the scripts loaded after it. */
(function () {
  var M = require("../docs/measures.js");
  var E = require("../docs/m_engagement.js");
  function assert(cond, msg) { if (!cond) { console.error("FAIL: engagement: " + msg); process.exit(1); } }
  function api(slot) {
    return {id: "engagement", slot: slot, section: null, meta: {}, names: {}, esc: M.esc, M: M,
            countryName: function (iso) { return {ITA: "Italy", IDN: "Indonesia"}[iso] || iso; }};
  }
  var prov = {status: "provisional", label: "Engagement", check: {state: "none", n: 0, coded: 0}};
  var chk = {status: "checked", label: "Engagement", check: {state: "passed", n: 20, coded: 20, statistic: "band agreement", value: 0.9, threshold: 0.8, checked_on: "2026-11-01"}};
  var wh = {status: "withheld", label: "Engagement", check: {state: "failed", n: 20, coded: 20, statistic: "band agreement", value: 0.6, threshold: 0.8, checked_on: "2026-11-01"}};
  var data = {
    min_cell: 5, query_dates: {first: "2026-10-14", last: "2026-10-15"},
    coverage: {state_origin_urls: 1642, looked_up: 1200, found: 300, share_found: 0.25, looked_up_share: 0.73},
    totals: {all: {posts_n: 4321, reactions: 120000}},
    by_country: [{id: "ITA", urls: 500, found: 120, posts_n: 3000, reactions: 100000, shares: 4000, comments: 2500},
                 {id: "WLD", urls: 200, found: 20, posts_n: 150, reactions: 5000, shares: 400, comments: 80},
                 {id: "<x>", urls: 1, found: 1, posts_n: 5, reactions: 1, shares: 0, comments: 0},
                 {id: "CHN", urls: 9, found: 9, posts_n: 99999, reactions: 9, shares: 9, comments: 9}]
  };

  assert(E.ID === "engagement" && M.get("engagement") && M.get("engagement").render === E.render, "registered");
  assert(E.fmt(1234567) === "1,234,567" && E.fmt(0) === "0" && E.fmt(null) === "n/a", "number format");

  /* Off, as shipped: hidden and empty, whatever the data. */
  var slot = {hidden: false, innerHTML: "stale"};
  E.render({status: "off"}, data, api(slot));
  assert(slot.hidden === true && slot.innerHTML === "", "off hides and clears");
  E.render(undefined, null, api(slot));
  assert(slot.hidden === true && slot.innerHTML === "", "no block is off");

  /* On, with nothing looked up yet, or the file missing: the empty state. */
  E.render(prov, null, api(slot));
  assert(slot.hidden === false && slot.innerHTML.indexOf("did not load") !== -1, "missing file");
  E.render(prov, {coverage: {looked_up: 0}, by_country: []}, api(slot));
  assert(slot.innerHTML.indexOf("Nothing to show yet") !== -1 && slot.innerHTML.indexOf("<table") === -1, "no rows");

  /* Provisional with data: the line, the chip, the table, the limits. */
  E.render(prov, data, api(slot));
  var h = slot.innerHTML;
  assert(slot.hidden === false, "shown when provisional");
  assert(h.indexOf("m-chip-provisional") !== -1 && h.indexOf("(provisional)") !== -1, "chip and suffix");
  assert(h.indexOf("<strong>1,200</strong>") !== -1 && h.indexOf("<strong>300</strong> (25%)") !== -1, "lead line");
  assert(h.indexOf("<td>Italy</td>") !== -1 && h.indexOf("<td>Worldwide distribution</td>") !== -1, "country names");
  assert(h.indexOf("3,000") !== -1 && h.indexOf("100,000") !== -1, "formatted counts");
  assert(h.indexOf("<x>") === -1 && h.indexOf("&lt;x&gt;") !== -1, "escapes");
  assert(h.indexOf("CHN") === -1 && h.indexOf("99,999") === -1, "China is never a row");
  assert(h.indexOf("on 2026-10-15") !== -1 && h.indexOf("1 to 4 posts") !== -1 && h.indexOf("lower bound") !== -1, "limits");
  assert(h.indexOf("Reach, not supply") !== -1, "reach, not supply");
  assert(h.indexOf("Category") === -1 && !/[\u2013\u2014]/.test(h), "no category letters, no dashes");
  assert(h.indexOf("http") === -1, "no address on the page");
  var rowsIdx = h.indexOf("<td>Italy</td>") < h.indexOf("<td>Worldwide distribution</td>");
  assert(rowsIdx, "most posts first");

  /* At most ten rows. */
  var many = {coverage: data.coverage, min_cell: 5, by_country: []};
  for (var i = 0; i < 14; i++) many.by_country.push({id: "C" + i, urls: 1, found: 1, posts_n: i, reactions: 0, shares: 0, comments: 0});
  E.render(prov, many, api(slot));
  assert((slot.innerHTML.match(/<tr>/g) || []).length === E.MAX_ROWS + 1, "ten rows and the header");

  /* Checked: the chip, no suffix. Withheld: no figure at all. */
  E.render(chk, data, api(slot));
  assert(slot.innerHTML.indexOf("m-chip-checked") !== -1 && slot.innerHTML.indexOf("(provisional)") === -1, "checked");
  E.render(wh, data, api(slot));
  assert(slot.innerHTML.indexOf("withheld") !== -1 && slot.innerHTML.indexOf("<table") === -1 && slot.innerHTML.indexOf("1,200") === -1, "withheld shows no figure");

  /* No slot is fine. */
  E.render(prov, data, {id: "engagement", slot: null});

  console.log("engagement smoke ok");
}());
