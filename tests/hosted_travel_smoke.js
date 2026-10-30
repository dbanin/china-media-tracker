/* Smoke test for docs/hosted_travel.js, the Hosted travel disclosures section. Run with: node tests/hosted_travel_smoke.js */
var H = require("../docs/hosted_travel.js");
function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

var e = H.summarise({});
assert(e.state_host === 0 && e.unnamed_host === 0 && e.total === 0 && e.by_country.length === 0 && e.patterns_version === null, "empty json summarises to zeros");
assert(H.summarise(null).total === 0 && H.summarise(undefined).by_country.length === 0, "a missing file summarises without throwing");
assert(e.status === "provisional", "provisional by default");
assert(/patterns have found no disclosure/.test(H.emptyState()) && !/No article/.test(H.emptyState()), "empty state says what the patterns found, not what no article did");
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
assert(!/Most /.test(intro) && /does not say the host wrote, commissioned or approved/.test(intro), "intro makes no unsupported most claim and says what a flag is not");
assert(H.basisLabel("bio") === "author note at the end", "bio basis in plain words");
assert(H.scanLine(H.summarise({})) === "", "no scan line without scan status");
assert(H.scanLine(H.summarise({scan_complete: true, bodies_stored: 1})) === "Read in 1 article body stored by the tracker.", "complete scan, singular");
assert(H.scanLine(H.summarise({scan_complete: true, bodies_stored: 90383, bodies_read: 6857})) === "Read in 6,857 article bodies stored by the tracker (83,526 more could not be read).", "bodies read, not rows with a hash");
var part = H.scanLine(H.summarise({scan_complete: false, bodies_left: 7, bodies_stored: 1200}));
assert(/still reading/.test(part) && /7 of 1,200 left/.test(part) && /can still rise/.test(part), "an unfinished scan says so: " + part);
var partHtml = H.bodyHtml({articles: [], state_host: 0, unnamed_host: 0, scan_complete: false, bodies_left: 3, bodies_stored: 9});
assert(partHtml.indexOf("3 of 9 left") !== -1, "the unfinished scan is shown with the empty state");
assert(html.indexOf('tabindex="0"') !== -1 && html.indexOf('role="region"') !== -1 && html.indexOf("<caption") !== -1, "the sideways scrolling table is focusable, labelled and captioned");
assert(html.indexOf("(opens in a new tab)") !== -1, "new tab links say so to screen readers");
assert(html.indexOf('<span dir="auto">Disclosure: the trip') !== -1 && html.indexOf("why it counts: closing line") !== -1, "right to left safe evidence and a labelled basis");
assert(html.indexOf('data-label="Disclosure"') !== -1, "cells labelled for the stacked phone layout");
assert(htmlMany.indexOf('aria-expanded="false"') !== -1 && H.bodyHtml({articles: many}, {showAll: true}).indexOf('aria-expanded="true"') !== -1, "toggle state exposed");
var big = []; for (var k = 0; k < 20000; k++) big.push({klass: k % 2 ? "state_host" : "unnamed_host", country: "C" + (k % 50), outlet: "O", title: "T", url: "https://x.test/" + k, published_at: "2026-10-01"});
var t0 = Date.now(); var bigHtml = H.bodyHtml({articles: big});
assert(Date.now() - t0 < 3000 && (bigHtml.match(/<tr>/g) || []).length === H.ROWS + 1, "a huge file renders the first rows quickly");
assert(H.bodyHtml({articles: "x", by_country: [], state_host: "NaN"}).indexOf("0 articles with a Chinese state host") !== -1, "malformed fields read as empty");
console.log("hosted travel smoke ok");
