/* Smoke test for docs/hosted_travel.js, the Hosted travel disclosures section. Run with: node tests/hosted_travel_smoke.js */
var H = require("../docs/hosted_travel.js");
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

var e = H.summarise({});
assert(e.state_host === 0 && e.unnamed_host === 0 && e.total === 0 && e.by_country.length === 0 && e.patterns_version === null, "empty json summarises to zeros");
assert(H.summarise(null).total === 0 && H.summarise(undefined).by_country.length === 0, "a missing file summarises without throwing");
assert(e.status === "provisional", "provisional by default");
assert(/No article/.test(H.emptyState()), "empty state sentence");
var html0 = H.bodyHtml({articles: [], state_host: 0, unnamed_host: 0});
assert(html0.indexOf(H.emptyState()) !== -1 && html0.indexOf("<table") === -1, "empty file shows the empty state, no table");

var intro = H.intro({languages: ["en", "fr"], patterns_version: "2026.10.1"}, true);
assert(/not state origin/.test(intro) && /Provisional/.test(intro) && /2 languages, version 2026\.10\.1/.test(intro), "intro says not state origin and provisional: " + intro);
assert(intro.indexOf("—") === -1 && intro.indexOf("–") === -1, "intro has no dashes");

var arts = [
  {article_id: 1, klass: "unnamed_host", country: "KEN", outlet: "Paper A", title: "Port <b>news</b>", url: "https://a.test/1", published_at: "2026-10-20T00:00:00", hosts: ["the organisers"], bases: ["self"], evidence: "The writer travelled as a guest of the organisers."},
  {article_id: 2, klass: "state_host", country: "KEN", outlet: "Paper B", title: "Rail", url: "javascript:alert(1)", published_at: "2026-10-10T00:00:00", hosts: ["Chinese Embassy"], bases: ["tail"], evidence: "Jane Doe travelled as a guest of the Chinese Embassy."},
  {article_id: 3, klass: "state_host", country: "MYS", outlet: "Paper C", title: "Trade", url: "https://c.test/3", published_at: "2026-10-12T00:00:00", hosts: ["Chinese government"], bases: ["label"], evidence: "Disclosure: the trip was paid for by the Chinese government."}
];
var s = H.summarise({articles: arts});
assert(s.state_host === 2 && s.unnamed_host === 1 && s.total === 3, "counted from the articles when the file has no totals");
assert(s.by_country.map(function (r) { return r.iso + ":" + r.state_host + "/" + r.unnamed_host; }).join(",") === "KEN:1/1,MYS:1/0", "by_country sorted by state host count then code: " + JSON.stringify(s.by_country));
var s2 = H.summarise({articles: arts.slice(0, 1), state_host: 9, unnamed_host: 4, by_country: {USA: {state_host: 9, unnamed_host: 0}, KEN: {state_host: 0, unnamed_host: 4}}, by_outlet: {a: {}, b: {}}});
assert(s2.state_host === 9 && s2.unnamed_host === 4 && s2.by_country[0].iso === "USA" && s2.outlets === 2, "the file's own totals and tallies win over a capped list");

var html = H.bodyHtml({articles: arts}, {countryName: function (iso) { return iso === "KEN" ? "Kenya" : iso; }});
assert(html.indexOf("Kenya") !== -1, "country names used");
assert(html.indexOf("<b>news</b>") === -1 && html.indexOf("&lt;b&gt;news&lt;/b&gt;") !== -1, "titles escaped");
assert(html.indexOf("javascript:") === -1, "unsafe links dropped");
assert(html.indexOf("Chinese state host") !== -1 && html.indexOf("host not named") !== -1, "class labels shown");
assert(html.indexOf("Paper C") < html.indexOf("Paper B") && html.indexOf("Paper B") < html.indexOf("Paper A"), "state hosts first, newest first within a class");
assert(html.indexOf("hosted-travel-toggle") === -1, "no toggle under the row limit");
var many = [];
for (var i = 0; i < H.ROWS + 5; i++) many.push({klass: "state_host", country: "KEN", outlet: "O" + i, title: "T" + i, url: "https://x.test/" + i, published_at: "2026-10-" + (10 + (i % 18))});
var htmlMany = H.bodyHtml({articles: many});
assert(htmlMany.indexOf("hosted-travel-toggle") !== -1 && (htmlMany.match(/<tr>/g) || []).length === H.ROWS + 1, "capped at ROWS rows plus the header, with a toggle");
assert((H.bodyHtml({articles: many}, {showAll: true}).match(/<tr>/g) || []).length === many.length + 1, "show all");
assert(H.classLabel("other_kind") === "other kind" && H.basisLabel("tail") === "closing line", "labels in plain words");
console.log("hosted travel smoke ok");
