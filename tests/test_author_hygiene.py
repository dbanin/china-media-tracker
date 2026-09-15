"""Author fields are read by author scoped signatures, so they must hold a byline and nothing else.

The Economic Times false positive came from extraction storing "SECTIONS China represented by Foreign
Minister Wang Yi" as the author, which the diplomat list then matched. Every case below is a real
field shape taken from the database, in both directions: chrome that must go, bylines that must stay.
"""
from pipeline import classify_rules as cr, extract, store


def test_real_bylines_survive():
    for value in ("Rana Atef", "Antaranews Com; Xinhua", "Advertorial Desk", "Xinhua",
                  "Judicaël ZOHOUN", "The Scoop", "Poder360 · Xinhua", "India Today Trending Desk"):
        assert extract.clean_author(value) == value, value


def test_markup_is_stripped_not_rejected():
    assert extract.clean_author('di <a href="https://x.test/a.html">Annamaria Grisorio</a>') == "di Annamaria Grisorio"
    assert extract.clean_author('<div class="editorial-container__name" style="font-weight: 500;">Ana Lucia</div>') == "Ana Lucia"
    assert extract.clean_author("Jos&eacute; Mar&iacute;a") == "José María"
    assert extract.clean_author("Jihan  Abdalla") == "Jihan Abdalla"


def test_trailing_furniture_is_trimmed_rather_than_failing_the_byline():
    assert extract.clean_author("Ulviyya Poladova Read more") == "Ulviyya Poladova"


def test_page_chrome_is_rejected():
    assert extract.clean_author("SECTIONS China represented by Foreign Minister Wang Yi") is None
    assert extract.clean_author("SECTIONS Xi; Modi reached most important consensus that India-China should be partners Wang Yi PTI") is None
    assert extract.clean_author("Subscribe to our newsletter") is None
    assert extract.clean_author("   ") is None and extract.clean_author(None) is None


def test_long_fields_keep_the_byline_they_contain():
    """A list of names stays whole; a name followed by an affiliation keeps the name."""
    names = "Himanshu Harsh, Divya A, Shubhajit Roy, Nischai Vats, Nikhil Ghanekar, Richa Shrivastava, Sreenivas Janyala"
    assert extract.clean_author(names) == names
    assert extract.clean_author(
        "Nader Naderpajouh, Associate Professor/Head of School of Project Management, University of Sydney"
    ) == "Nader Naderpajouh"
    assert extract.clean_author("Wanning Sun, Professor of Media and Cultural Studies, University of Technology Sydney") == "Wanning Sun"


def test_a_diplomat_name_in_navigation_no_longer_labels_state_origin():
    """The Economic Times article, reduced to the shape that produced the label."""
    dirty = "SECTIONS China represented by Foreign Minister Wang Yi"
    body = "China was represented by Foreign Minister Wang Yi at the BRICS dinner hosted by India, sources said."
    assert cr.match_signatures("Wang Yi represents China at India's BRICS dinner", body, dirty)["decision"] == "A"
    assert cr.match_signatures("Wang Yi represents China at India's BRICS dinner", body,
                               extract.clean_author(dirty))["decision"] != "A"


def test_stored_authors_are_backfilled(tmp_path):
    conn = store.connect(tmp_path / "a.db")
    keep = "Antaranews Com; Xinhua"
    for i, author in enumerate((keep, "SECTIONS China represented by Foreign Minister Wang Yi",
                                '<a href="https://x.test">Annamaria Grisorio</a>')):
        aid = store.insert_discovered(conn, {"url": "https://x.test/%d" % i, "outlet_id": "o", "country": "ITA",
                                             "language": "it", "title": "t", "status": "fetched", "gate_relevant": 1})
        store.update_article(conn, aid, author=author)
    conn.commit()
    assert extract.clean_stored_authors(conn) == 2
    authors = [r[0] for r in conn.execute("SELECT author FROM articles ORDER BY id")]
    assert authors == [keep, None, "Annamaria Grisorio"]
    # Running it again changes nothing, so it is safe on every export.
    assert extract.clean_stored_authors(conn) == 0
