"""The diplomat list (pipeline/diplomats.yaml) and the diplomatic byline route it feeds."""
import re

import yaml

from pipeline import classify_rules as cr, config, export

OPED = ("When the mudslide struck the border in August, China came to the rescue. Rescue teams worked "
        "around the clock alongside Nepali crews, and relief supplies crossed within days.")


def _ids(res):
    return [m["id"] for m in res["matches"]]


def _entries():
    with open(config.DIPLOMATS_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["personnel"]


def test_current_australian_mission_is_listed():
    names = set(cr.load_diplomats())
    for n in ("Liu Jinsong", "Wang Yu", "Fang Xinwen", "Sun Zhaoyang", "Fu Lihua"):
        assert n in names, n
    posts = {e["name"]: e["post"] for e in _entries() if "Australia" in e["post"]}
    assert posts["Liu Jinsong"].startswith("ambassador to Australia")
    assert posts["Xiao Qian"].startswith("former ambassador to Australia")


def test_author_field_byline_is_diplomatic_route():
    """The AFR op-ed form: the name in the author field and the title at the top of the piece."""
    res = cr.match_signatures("China came to the rescue after Nepal mudslide",
                              "Liu Jinsong is the Chinese Ambassador to Australia.\n\n" + OPED, "Liu Jinsong")
    assert res["decision"] == "A" and "diplomat_list_author" in _ids(res)
    assert cr.route_of(res["matches"]) == "diplomatic_byline"


def test_body_byline_with_adjective_title_is_diplomatic_route():
    """"By Liu Jinsong, Chinese Ambassador to Australia" with an empty author field. diplomat_byline_head
    needs the title before the country word and does not match this form; the list check does."""
    body = "By Liu Jinsong, Chinese Ambassador to Australia\n\n" + OPED
    assert "diplomat_byline_head" not in _ids(cr.match_signatures("", body, ""))
    res = cr.match_signatures("China came to the rescue after Nepal mudslide", body, "")
    hit = [m for m in res["matches"] if m["id"] == "diplomat_list_author"]
    assert res["decision"] == "A" and hit and hit[0]["scope"] == "head" and hit[0]["strength"] == "strong"
    assert cr.route_of(res["matches"]) == "diplomatic_byline"
    # the evidence highlighter finds the name inside the stored span
    s, e = export._diplomat_offset(hit[0]["span"])
    assert hit[0]["span"][s:e] == "Liu Jinsong"


def test_consul_general_body_byline():
    res = cr.match_signatures("", "By FU Lihua, Consul General of China in Perth\n\n" + OPED, None)
    assert res["decision"] == "A" and "diplomat_list_author" in _ids(res)


def test_listed_name_as_reporter_without_title_does_not_fire():
    """A reporter who shares a listed name: a byline with no title on its own line is nothing, even when
    the next line names an ambassador."""
    body = "By Wang Yu\nThe Chinese ambassador to Australia said on Monday that trade was recovering.\n\n" + OPED
    res = cr.match_signatures("", body, "")
    assert not any(i.startswith("diplomat_list") for i in _ids(res))


def test_report_about_the_ambassador_is_not_a_byline():
    body = ("By Matthew Knott, political correspondent\nChinese ambassador Liu Jinsong has begun a charm "
            "offensive in Canberra, meeting MPs from both sides.\n\n" + OPED)
    res = cr.match_signatures("", body, "Matthew Knott")
    assert not any(i.startswith("diplomat_list") for i in _ids(res))


def test_former_ambassador_still_matches():
    """Former post-holders stay listed: a piece Xiao Qian signed as ambassador is still state origin when
    it is collected after he left the post. The name alone stays a weak candidate for the model, because
    another person of the same name (an AI safety researcher writes as Xiao Qian) is not a diplomat."""
    signed = cr.match_signatures("", OPED, "Xiao Qian, Chinese Ambassador to Australia")
    assert signed["decision"] == "A" and "diplomat_list_author" in _ids(signed)
    body = cr.match_signatures("", "By Xiao Qian, Chinese Ambassador to Australia\n\n" + OPED, "")
    assert body["decision"] == "A" and "diplomat_list_author" in _ids(body)
    namesake = cr.match_signatures("", "Beijing and Washington can build AI safety despite mutual distrust.", "Xiao Qian")
    assert namesake["decision"] == "A_candidate" and _ids(namesake) == ["diplomat_list_name_only"]


def test_list_entries_are_well_formed():
    for e in _entries():
        assert e.get("name") and e.get("post"), e
        if "source" in e:
            assert re.match(r"https?://\S+$", e["source"]), e
    for e in _entries():
        assert "–" not in str(e) and "—" not in str(e)


def test_list_change_bumped_the_ruleset():
    assert cr.load_signatures()["ruleset_version"] == config.RULESET_VERSION


def _strong(res):
    return [m for m in res["matches"] if m["id"] == "diplomat_list_author"]


def test_namesake_with_an_ambassador_in_the_piece_does_not_fire():
    """A namesake of a listed diplomat (an AI safety researcher writes as Xiao Qian) whose piece mentions
    an ambassador in its opening or closing lines stays a weak candidate for the model: the title has to
    describe the author, not merely appear near the piece."""
    body = ("During the summit, the Chinese ambassador to the United States, Xie Feng, met officials.\n\n" + OPED
            + "\n\nXiao Qian is deputy director of a research centre in Beijing. The ambassador did not comment.")
    for labels in ("", "Analysis\narticleSection: Analysis\ncreator: Xiao Qian"):
        res = cr.match_signatures("Beijing and Washington can build AI safety", body, "Xiao Qian", labels=labels)
        assert res["decision"] == "A_candidate" and _ids(res) == ["diplomat_list_name_only"], labels
    res = cr.match_signatures("", OPED + "\n\nWang Yu is a reporter who interviewed the Chinese ambassador.", "Wang Yu")
    assert _ids(res) == ["diplomat_list_name_only"]


def test_namesake_reporter_in_opinion_section_does_not_fire():
    """Next to the name in an opinion section means a title line under the name, not a sentence that opens
    with an ambassador, and never outside an opinion section."""
    body = "Wang Yu\nThe Chinese ambassador to Australia said on Monday that trade was recovering.\n\n" + OPED
    assert _ids(cr.match_signatures("", body, "Wang Yu", labels="Opinion")) == ["diplomat_list_name_only"]
    body = "Li Dong\nChinese ambassador Liu Jinsong visits Adelaide\n\n" + OPED
    assert _ids(cr.match_signatures("", body, "Li Dong", labels="News")) == ["diplomat_list_name_only"]


def test_title_describing_the_author_fires():
    cases = [
        # byline line at the top of the body naming the author
        ("By Liu Jinsong, Chinese Ambassador to Australia\n\n" + OPED, "Liu Jinsong", ""),
        # closing bio line naming the author
        (OPED + "\n\nJiang Zaidong is China's ambassador to Pakistan.", "Jiang Zaidong", ""),
        (OPED + "\n\nLiu Jinsong, Chinese Ambassador to Australia", "Liu Jinsong", ""),
        # bio line about "the writer" in the page's author box
        (OPED, "Jiang Zaidong", "Opinions\nJiang Zaidong\nThe writer is the Chinese Ambassador to Pakistan."),
        (OPED + "\n\nL'auteur est l'ambassadeur de Chine en France.", "Deng Li", ""),
        # footnote under an author field marked with an asterisk
        (OPED + "\n*Embajador de la República Popular China en México", "Chen Daojiang*", ""),
        # opinion section, title on the line under the name
        (OPED, "Chen Daojiang", "@type: OpinionNewsArticle\nColaborador Invitado\nChen Daojiang\nEmbajador de China en México"),
        ("Analistas\n08/10/2026\nZhu Jingyang\nEmbajador de China en Colombia\n" + OPED,
         "Editorial La República S A S; Zhu Jingyang", "Analistas"),
    ]
    for body, author, labels in cases:
        res = cr.match_signatures("", body, author, labels=labels)
        hit = _strong(res)
        assert res["decision"] == "A" and hit and hit[0]["strength"] == "strong", (author, body[-80:])
        assert cr.route_of(res["matches"]) == "diplomatic_byline"
        s, e = export._diplomat_offset(hit[0]["span"])  # the evidence highlighter still finds the name
        assert hit[0]["span"][s:e].lower() in author.lower()


def test_title_naming_another_countrys_mission_does_not_fire():
    """The title has to make the listed name China's diplomat. A namesake who signs as another country's
    envoy ("Li Dong, Ambassador of Singapore", "Singaporean ambassador") stays a weak candidate, also when
    China is the host country after "to"; the same title phrase naming China fires."""
    cases = [
        ("By Li Dong, Singaporean Ambassador to China\n\n" + OPED, "", ""),
        (OPED, "Li Dong, Ambassador of Singapore", ""),
        ("China and Singapore marked 35 years of ties.\n\n" + OPED, "Li Dong; Singaporean ambassador", "China"),
        (OPED + "\n\nLi Dong is the ambassador of Singapore to China.", "Li Dong", ""),
        (OPED + "\n\nLi Dong, Ambassador of Singapore to China", "Li Dong", ""),
        (OPED + "\n\nThe writer is the Singaporean ambassador to China.", "Li Dong", ""),
        ("Li Dong\nAmbassador of Singapore to China\n\n" + OPED, "Li Dong", "Opinion"),
    ]
    for body, author, labels in cases:
        res = cr.match_signatures("", body, author, labels=labels)
        assert not _strong(res), (author, body[:60], body[-60:])
        if author:
            assert "diplomat_list_name_only" in _ids(res)
    signed = cr.match_signatures("", OPED, "Li Dong, Consul General of China in Adelaide")
    assert _strong(signed)
    # a title naming no country counts on the byline line only when the line names a Chinese mission
    assert not _strong(cr.match_signatures("", "By Fu Lihua, Consul General in Perth\n\n" + OPED, ""))
    assert _strong(cr.match_signatures(
        "", "By Fu Lihua, Consul General, Consulate-General of the People's Republic of China in Perth\n\n" + OPED, ""))


def test_author_field_title_without_country_needs_china_on_the_page():
    """Sada Elbalad signs Liao Liqiang as "Liao Liqiang; Ambassador". A title in the author field that
    names no country fires when the page labels or the head of the body name China, else the name is a
    weak candidate."""
    body = ("Chinese President Xi Jinping paid a historic state visit to Egypt at the invitation of "
            "President Abdel Fattah El-Sisi.\n\n" + OPED)
    res = cr.match_signatures("Leader-to-Leader Diplomacy Sets the Course", body, "Liao Liqiang; Ambassador")
    assert res["decision"] == "A" and _strong(res)
    res = cr.match_signatures("Leader-to-Leader Diplomacy Sets the Course",
                              "Two leaders met in Cairo and agreed to deepen trade.", "Liao Liqiang; Ambassador",
                              labels="Leader-to-Leader Diplomacy Sets the Course | Sada Elbalad")
    assert res["decision"] == "A_candidate" and _ids(res) == ["diplomat_list_name_only"]


def test_native_language_china_titles_fire():
    cases = [
        ("Von Xiao Qian, chinesischer Botschafter in Australien\n\n" + OPED, ""),
        (OPED + "\n\nXiao Qian ist der chinesische Botschafter in Australien.", "Xiao Qian"),
        ("Di Jia Guide, ambasciatore cinese in Italia\n\n" + OPED, ""),
        ("Por Chen Daojiang, embajador chino en México\n\n" + OPED, ""),
        ("Por Chen Daojiang, embajadora de China en México\n\n" + OPED, ""),
        ("Par Deng Li, ambassadeur chinois en France\n\n" + OPED, ""),
        (OPED + "\n\nL'auteur est l'ambassadeur de Chine en France.", "Deng Li"),
        ("Por Chen Daojiang, embaixador chinês no México\n\n" + OPED, ""),
        ("Por Chen Daojiang, embaixador da China no México\n\n" + OPED, ""),
        (OPED, "Xiao Qian, Ambassador of the P.R.C."),
    ]
    for body, author in cases:
        res = cr.match_signatures("", body, author)
        assert res["decision"] == "A" and _strong(res), (author, body[:60], body[-60:])


# Native-script titles and names (ruleset 2026.10.1). One case per script: the title paired with the
# country word fires, the same title naming another country (China only as the host) does not.
NATIVE_SCRIPT_CASES = [
    # Cyrillic: Russian, Ukrainian, Bulgarian, Serbian, Macedonian
    ("Чжан Ханьхуэй, Посол Китая в России", "Чжан Ханьхуэй, посол России в Китае"),
    ("Хань Чуньлинь, Чрезвычайный и Полномочный Посол КНР в Казахстане", "Хань Чуньлинь, посол Казахстана в КНР"),
    ("Ма Шенкунь, Посол Китаю в Україні", "Ма Шенкунь, Посол України у Китаї"),
    ("Дай Цинли, посланик на Китай в България", "Дай Цинли, посланик на България в Китай"),
    ("Li Ming, амбасадор Кине у Србији", "Li Ming, амбасадор Србије у Кини"),
    ("Jiang Xiaoyan, амбасадор на Кина во Северна Македонија", "Jiang Xiaoyan, амбасадор на Србија во Кина"),
    # Arabic and Persian
    ("لياو ليتشيانغ، سفير الصين لدى مصر", "لياو ليتشيانغ، سفير مصر لدى الصين"),
    ("شاو تشنغ، القائم بأعمال السفارة الصينية لدى اليمن", "شاو تشنغ، القائم بأعمال السفارة اليمنية لدى الصين"),
    ("Cong Peiwu، سفیر چین در ایران", "Cong Peiwu، سفیر ایران در چین"),
    # Hebrew
    ("שיאו ג'ון-ג'נג, שגריר סין בישראל", "שיאו ג'ון-ג'נג, שגריר ישראל בסין"),
    # Greek
    ("Σιάο Τζουντσένγκ, Πρέσβης της Κίνας στην Ελλάδα", "Σιάο Τζουντσένγκ, Πρέσβης της Ελλάδας στην Κίνα"),
    # Korean: 주한 is the host post (in Korea), 주중 the Korean envoy's (in China)
    ("다이빙 주한 중국대사", "다이빙 주중 한국대사"),
    # Japanese: 駐日 is the host post, 駐中国 makes China the host
    ("呉江浩駐日中国大使", "呉江浩 駐中国日本大使"),
    # Thai: ประจำ introduces the host
    ("นายจาง เจี้ยนเว่ย เอกอัครราชทูตจีนประจำประเทศไทย", "หาน จื้อเฉียง เอกอัครราชทูตไทยประจำประเทศจีน"),
    # Hindi: the host comes before में
    ("Xu Feihong, भारत में चीन के राजदूत", "Xu Feihong, चीन में भारत के राजदूत"),
    # Khmer: ប្រចាំ introduces the host
    ("Wang Wenbin ឯកអគ្គរដ្ឋទូតចិនប្រចាំកម្ពុជា", "Wang Wenbin ឯកអគ្គរដ្ឋទូតកម្ពុជាប្រចាំប្រទេសចិន"),
]


def test_native_script_titles_fire_only_for_china():
    for china, other in NATIVE_SCRIPT_CASES:
        res = cr.match_signatures("", OPED, china)
        assert res["decision"] == "A" and _strong(res), china
        res = cr.match_signatures("", OPED, other)
        assert _ids(res) == ["diplomat_list_name_only"], other


def test_native_name_forms_are_listed_with_a_source():
    names = set(cr.load_diplomats())
    for n in ("Чжан Ханьхуэй", "Ма Шенкунь", "Дай Цинли", "لياو ليتشيانغ", "다이빙", "呉江浩",
              "จาง เจี้ยนเว่ย", "שיאו ג'ון-ג'נג", "Σιάο Τζουντσένγκ"):
        assert n in names, n
    for e in _entries():
        if e.get("native"):
            assert isinstance(e["native"], list) and e.get("native_source"), e
            for u in e["native_source"]:
                assert re.match(r"https?://\S+$", u), e


def test_native_script_bylines_and_bios():
    """The byline at the top of the body in the other scripts (بقلم, מאת, Του), a bio line after an
    Arabic comma, and a Japanese or Korean name followed directly by its title."""
    for body, author in [
        ("بقلم: لياو ليتشيانغ، سفير جمهورية الصين الشعبية لدى مصر\n\n" + OPED, ""),
        ("מאת שיאו ג'ון-ג'נג, שגריר סין בישראל\n\n" + OPED, ""),
        ("Του Σιάο Τζουντσένγκ, Πρέσβη της Λαϊκής Δημοκρατίας της Κίνας στην Ελλάδα\n\n" + OPED, ""),
        (OPED + "\n\nلياو ليتشيانغ، سفير الصين لدى مصر", "لياو ليتشيانغ"),
        ("싱하이밍 주한 중국대사\n\n" + OPED, "싱하이밍"),
        ("呉江浩・駐日中国大使\n\n" + OPED, "呉江浩"),
        ("Лю Цзянпин - ЧПП КНР в КР\n\n" + OPED, "Лю Цзянпин"),
    ]:
        res = cr.match_signatures("", body, author)
        assert res["decision"] == "A" and _strong(res), body[:60]


def test_native_script_mission_or_name_alone_is_not_a_title():
    """The embassy is not the ambassador (посольство, 대사관), and a Korean name that is also a word
    (다이빙, diving) in the author field with no title stays a weak candidate."""
    for body, author in [
        ("Посольство Китая в России сообщило о визите.\n\n" + OPED, "Чжан Ханьхуэй"),
        ("중국대사관 앞에서 다이빙 대회가 열렸다.\n\n" + OPED, "다이빙"),
        ("السفارة الصينية لدى مصر\n\n" + OPED, "لياو ليتشيانغ"),
    ]:
        res = cr.match_signatures("", body, author)
        assert _ids(res) == ["diplomat_list_name_only"], body[:40]
    # a byline naming nobody on the list fires nothing, whatever its title
    res = cr.match_signatures("", "بقلم: محمد علي، سفير الصين السابق\n\n" + OPED, "")
    assert not any(i.startswith("diplomat_list") for i in _ids(res))


def test_title_line_directly_under_the_name():
    """Expresso das Ilhas prints the author box as the name on one line and the title on the next
    ("Zhang Yang" then "Embaixador da República Popular da China em Cabo Verde", article 888044), in
    the page labels outside any opinion section; the same in the body's head or tail."""
    labels = ("Continuar a Amizade Tradicional e Abrir Juntos Novos Horizontes entre a China, Cabo Verde e África\n"
              "China, Cabo Verde, África, Cooperação\narticle\nZhang Yang\n"
              "Embaixador da República Popular da China em Cabo Verde\nExpresso das Ilhas")
    res = cr.match_signatures("Continuar a Amizade Tradicional", OPED, "Zhang Yang", labels=labels)
    assert res["decision"] == "A" and _strong(res)
    s, e = export._diplomat_offset(_strong(res)[0]["span"])
    assert _strong(res)[0]["span"][s:e] == "Zhang Yang"
    assert _strong(cr.match_signatures("", OPED + "\n\nKang Yan\nveleposlanica Ljudske republike Kitajske v Sloveniji",
                                       "Kang Yan"))
    assert _strong(cr.match_signatures("", "Chen Li\n\nConsul General of the People's Republic of China in New York\n\n"
                                       + OPED, "Chen Li"))


def test_line_under_the_name_that_is_not_a_title_line_does_not_fire():
    """A headline or a sentence under a namesake reporter's byline is not a title line, outside an
    opinion section as in one; nor is a title naming another country, nor a title two lines down."""
    for body in [
        "Li Dong\nChinese ambassador Liu Jinsong visits Adelaide\n\n" + OPED,
        "Li Dong\nChinese Ambassador Liu Jinsong Visits Adelaide\n\n" + OPED,
        "Li Dong\nAmbassador of China visits Adelaide for trade talks\n\n" + OPED,
        "Li Dong\nThe Chinese ambassador to Australia said on Monday that trade was recovering.\n\n" + OPED,
        "Li Dong\nAmbassador of Singapore to China\n\n" + OPED,
        "Li Dong\nAdelaide\n\nChinese Ambassador to Australia\n\n" + OPED,
        "Li Dong reports\nChinese Ambassador to Australia\n\n" + OPED,
    ]:
        res = cr.match_signatures("", body, "Li Dong", labels="News")
        assert _ids(res) == ["diplomat_list_name_only"], body[:70]


def test_titles_in_more_european_and_asian_languages():
    """The title in the languages of active outlets the title test did not read (Delo signs Kang Yan
    as "veleposlanica Kitajske", article 1376880), with the China form that makes the person China's
    diplomat; the same title naming the host country's own envoy to China stays a weak candidate."""
    fires = [
        ("Kang Yan, veleposlanica Kitajske", ""),
        ("Kang Yan", OPED + "\n\nAvtorica je veleposlanica LR Kitajske v Sloveniji."),
        ("Kang Yan", OPED + "\n\nKang Yan je veleposlanica Ljudske republike Kitajske v Sloveniji."),
        ("Lu Shan, ambasador Chin w Polsce", ""),
        ("Lu Shan", OPED + "\n\nAutor jest ambasadorem ChRL w Polsce."),
        ("Lu Shan, Ambasador Nadzwyczajny i Pełnomocny Chińskiej Republiki Ludowej w RP", ""),
        ("Zhou Qian, velvyslanec Číny v ČR", ""),
        ("Cai Ge, veľvyslanec Čínskej ľudovej republiky na Slovensku", ""),
        ("Qi Qianjin, veleposlanik NR Kine u Hrvatskoj", ""),
        ("Li Ming, ambasador Kine u Srbiji", ""),
        ("Gong Tao, Kína budapesti nagykövete", ""),
        ("Chen Feng, ambasadorul Chinei în România", ""),
        ("Shen Bo, ambassadeur van China in Nederland", ""),
        ("Zhou Limin, Kinas ambassadör i Sverige", ""),
        ("Wang Xuefeng, Kinas ambassadør i Danmark", ""),
        ("Zhou Jingxing, kinesisk ambassadør", ""),
        ("Wang Tongqing, Kiinan suurlähettiläs Suomessa", ""),
        ("Jiang Xuebin, Çin'in Ankara Büyükelçisi", ""),
        ("Wang Lutong, Duta Besar China untuk Indonesia", ""),
        ("Ouyang Yujing, Duta Besar China ke Malaysia", ""),
        ("He Wei, Đại sứ Trung Quốc tại Việt Nam", ""),
        ("Chen Mingjian, Balozi wa China nchini Tanzania", ""),
    ]
    for author, body in fires:
        res = cr.match_signatures("", body or OPED, author)
        assert res["decision"] == "A" and _strong(res), author
    stays = [
        "Lu Shan, ambasador Polski w Chinach",
        "Zhou Qian, velvyslanec ČR v Číně",
        "Chen Feng, ambasadorul României în China",
        "Wang Xuefeng, Danmarks ambassadør i Kina",
        "Wang Tongqing, Suomen suurlähettiläs Kiinassa",
        "Wang Lutong, Duta Besar Indonesia untuk China",
        "He Wei, Đại sứ Việt Nam tại Trung Quốc",
    ]
    for author in stays:
        res = cr.match_signatures("", OPED, author)
        assert not _strong(res) and "diplomat_list_name_only" in _ids(res), author
    # the mission is not the title: ambasada, veleposlaništvo, nagykövetség, büyükelçilik, đại sứ quán
    for body in ["Ambasada Chin w Polsce poinformowała.", "Veleposlaništvo Kitajske v Ljubljani.",
                 "Kína budapesti nagykövetsége közölte.", "Çin Büyükelçiliği açıkladı.",
                 "Đại sứ quán Trung Quốc tại Việt Nam."]:
        assert not cr._TITLE_RE.search(body), body


def test_opinion_section_names_in_more_languages():
    """Delo files the Kang Yan piece under articleSection: Mnenja; the opinion section names of the
    other languages read the same way, from a short label only."""
    for label in ["articleSection: Mnenja", "Mišljenja", "Opinie", "Názory", "Vélemény", "Opinii", "Opinie",
                  "Debatt", "Kronikk", "Mielipide", "Görüş", "Opini", "Rencana", "Ý kiến", "Maoni"]:
        assert cr._in_opinion_section("", "", label), label
    assert not cr._in_opinion_section("", "", "Mnenja bralcev o novem zakonu o davkih so deljena in glasna")
    res = cr.match_signatures("", OPED, "Kang Yan", labels="articleSection: Mnenja\nKang Yan\nveleposlanica Kitajske")
    assert _strong(res)


def test_listed_name_inside_a_hyphenated_name_does_not_match():
    """The Guardian's Taipei reporter Yu-chen Li is not the New York consul-general Chen Li: the name
    matcher counts a hyphen joined to a letter as part of the name, before or after it."""
    assert "Chen Li" in cr.load_diplomats()
    for author in ["Yu-chen Li in Taipei", "Danny Vincent in Hong Kong and Yu-chen Li in Taipei",
                   "Yu-chen Li and Amy Hawkins", "Chen Li-wen"]:
        assert not any(i.startswith("diplomat_list") for i in _ids(cr.match_signatures("", OPED, author))), author
    for author in ["Chen Li", "Chen Li, Consul General of China in New York", "CHEN Li*", "(Chen Li)"]:
        assert any(i.startswith("diplomat_list") for i in _ids(cr.match_signatures("", OPED, author))), author
    # nor in the byline at the top of the body
    assert not _strong(cr.match_signatures("", "By Yu-chen Li, Chinese Consul General in New York\n\n" + OPED, ""))
    assert _strong(cr.match_signatures("", "By Chen Li, Chinese Consul General in New York\n\n" + OPED, ""))


def test_hungarian_verbless_author_bio():
    """A Hungarian bio line has no verb: "A szerző Kína budapesti nagykövete" (the writer [is] China's
    ambassador in Budapest). It counts in the head, tail or page labels with China as the possessor
    right after "A szerző" and the title with its possessive ending (nagykövete, főkonzulja, ügyvivője)."""
    hu = ("Kína és Magyarország kapcsolatai az elmúlt években sokat fejlődtek, és a két ország "
          "együttműködése tovább erősödik a gazdaság, a kultúra és az oktatás területén is.")
    fires = [
        "A szerző Kína budapesti nagykövete.",
        "A szerző a Kínai Népköztársaság magyarországi nagykövete.",
        "A szerző a KNK budapesti nagykövete",
        "A szerző Kína rendkívüli és meghatalmazott nagykövete Magyarországon.",
        "A szerző Kína volt budapesti nagykövete.",
        "A szerző a Kínai Népköztársaság budapesti főkonzulja.",
        "A szerző Kína ideiglenes budapesti ügyvivője.",
    ]
    for bio in fires:
        res = cr.match_signatures("", hu + "\n\n" + bio, "Gong Tao")
        assert res["decision"] == "A" and _strong(res), bio
    assert _strong(cr.match_signatures("", bio + "\n\n" + hu, "Gong Tao"))
    assert _strong(cr.match_signatures("", hu, "Gong Tao", labels="A szerző Kína budapesti nagykövete"))
    stays = [
        "A szerző Magyarország pekingi nagykövete.",            # Hungary's ambassador in Beijing
        "A szerző Kína-szakértő, Magyarország pekingi nagykövete.",
        "A szerző Kínában Magyarország nagykövete.",            # Kínában is the host country
        "A szerző Kína budapesti nagykövetének tanácsadója.",   # the ambassador's adviser
        "A szerző Kína budapesti nagykövete mellett dolgozik.",  # works beside the ambassador
        "A szerző kínai nagykövet.",                             # no possessor, no possessive ending
        "A szerző a pekingi magyar nagykövetség munkatársa.",   # the embassy, not the title
        "A szerző Kína és Magyarország nagykövete.",
    ]
    for bio in stays:
        res = cr.match_signatures("", hu + "\n\n" + bio, "Gong Tao")
        assert not _strong(res) and "diplomat_list_name_only" in _ids(res), bio
    # only in the bio positions: in the middle of a long body it is not the writer's bio
    middle = hu * 6 + "\n\nA szerző Kína budapesti nagykövete.\n\n" + hu * 6
    assert not _strong(cr.match_signatures("", middle, "Gong Tao"))
    # and only for a listed name in the author field: a reporter's byline gets nothing from it
    assert not _strong(cr.match_signatures("", hu + "\n\nA szerző Kína budapesti nagykövete.", "Kovács Anna"))


def test_china_in_the_host_slot_is_not_chinas_diplomat():
    """Languages that put the host country where others put the sender: Turkish "Türkiye'nin Çin
    Büyükelçisi" is Turkey's ambassador to China, Hungarian "Magyarország kínai nagykövete" and Finnish
    "Suomen Kiinan-suurlähettiläs" Hungary's and Finland's, a hyphenated "Kina-ambassadør" is an envoy to
    China, Swahili names the sender right after wa ("Balozi wa Tanzania China"), and Indonesian and Malay
    introduce the host with di. A China word in that slot never makes the listed name China's diplomat,
    nor does a bare title whose host is China ("Ambassador to China") on a page that names China."""
    reverse = [
        "Jiang Xuebin, Türkiye'nin Çin Büyükelçisi",
        "Jiang Xuebin, Türkiye’nin Çin Büyükelçisi",
        "Jiang Xuebin, Türkiye'nin Çin Halk Cumhuriyeti Büyükelçisi",
        "Jiang Xuebin, Türkiye'nin Çin'deki Büyükelçisi",
        "Gong Tao, Magyarország kínai nagykövete",
        "Gong Tao, Magyarország Kínai Nagykövete",
        "Gong Tao, Magyarország kínai főkonzulja",
        "Wang Tongqing, Suomen Kiinan-suurlähettiläs",
        "Wang Tongqing, Suomen Kiinan suurlähettiläs",
        "Wang Tongqing, Suomen Kiinan-asiainhoitaja",
        "Wang Xuefeng, Danmarks Kina-ambassadør",
        "Zhou Limin, Sveriges Kina-ambassadör",
        "Wang Lutong, Duta Besar RI di Tiongkok",
        "Wang Lutong, Kuasa Usaha RI di Tiongkok",
        "Ouyang Yujing, Duta Besar Malaysia di China",
        "Chen Mingjian, Balozi wa Tanzania China",
        "Chen Mingjian, Balozi wa Tanzania katika China",
        # the forms the brief listed that the host words already stopped; kept as regression cases
        "Gong Tao, Magyarország pekingi nagykövete",
        "Lu Shan, ambasador RP w Chinach",
        "Zhou Qian, velvyslanec ČR v Číně",
        "He Wei, Đại sứ Việt Nam tại Trung Quốc",
        "Wang Lutong, Duta Besar RI untuk Tiongkok",
        "Chen Mingjian, Balozi wa Tanzania nchini China",
    ]
    for author in reverse:
        name = author.split(",")[0]
        res = cr.match_signatures("", OPED, author)
        assert not _strong(res) and "diplomat_list_name_only" in _ids(res), author
        # nor on the byline line at the top of the body, in a bio line, or on a title line under the name
        title = author.split(", ", 1)[1]
        assert not _strong(cr.match_signatures("", "By %s\n\n%s" % (author, OPED), "")), author
        assert not _strong(cr.match_signatures("", OPED + "\n\n" + author, name)), author
        assert not _strong(cr.match_signatures("", OPED, name, labels="%s\n%s" % (name, title))), author
    # a bare title whose host is China does not take China from the page
    for author in ["Li Dong; Ambassador to China", "Wang Tongqing; Kiinan-suurlähettiläs",
                   "Wang Lutong; Duta Besar untuk Tiongkok"]:
        res = cr.match_signatures("", OPED, author, labels="China | Kiina | Tiongkok")
        assert not _strong(res), author
    fires = [
        "Jiang Xuebin, Çin'in Ankara Büyükelçisi",
        "Jiang Xuebin, Çin’in Ankara Büyükelçisi",
        "Jiang Xuebin, Çin'in Türkiye Büyükelçisi",
        "Jiang Xuebin, Çin Halk Cumhuriyeti'nin Ankara Büyükelçisi",
        "Jiang Xuebin, Çin Halk Cumhuriyeti'nin Türkiye Büyükelçisi",
        "Jiang Xuebin, ÇHC'nin Ankara Büyükelçisi",
        "Jiang Xuebin, Çin Büyükelçisi",
        "Gong Tao, Kína budapesti nagykövete",
        "Gong Tao, a Kínai Népköztársaság budapesti nagykövete",
        "Gong Tao, a Kínai Népköztársaság nagykövete",
        "Wang Tongqing, Kiinan Suomen-suurlähettiläs",
        "Wang Tongqing, Kiinan Suomen suurlähettiläs",
        "Wang Tongqing, Kiinan Kansantasavallan suurlähettiläs",
        "Wang Lutong, Duta Besar Tiongkok di Indonesia",
        "Chen Mingjian, Balozi wa Jamhuri ya Watu wa China nchini Tanzania",
        "Li Dong, Chinese Vice-Consul General in Perth",
    ]
    for author in fires:
        res = cr.match_signatures("", OPED, author)
        assert res["decision"] == "A" and _strong(res), author


REPORT = ("Officials from both sides said the talks in Canberra had covered barley, wine and lobster, and that "
          "a further round was planned for next month after the federal budget, according to the trade minister.")


def test_titled_name_in_reported_speech_or_narrative_does_not_fire():
    """A namesake reporter or commentator whose piece quotes or describes the diplomat stays a weak
    candidate: (a) a story that opens with the titled name and a speech verb, (b) an opinion piece that
    mentions "Chinese Ambassador Wang Yu" inside a sentence, (c) "Wang Yu is the Chinese ambassador to
    Australia" as reported fact with more reporting after it."""
    cases = [
        # (a) reported speech at the top of the body
        ("Wang Yu, Chinese Ambassador to Australia, said on Monday that trade was recovering.\n\n" + OPED, ""),
        ("Wang Yu, Chinese Ambassador to Australia, told reporters in Canberra the talks went well.\n\n" + OPED, ""),
        ("Wang Yu, the Chinese Consul General in Sydney, noted that student numbers had recovered.\n\n" + OPED, ""),
        ("Wang Yu, embajador de China en Australia, dijo el lunes que el comercio se recupera.\n\n" + OPED, ""),
        ("Wang Yu, ambassadeur de Chine en Australie, a déclaré lundi que le commerce reprenait.\n\n" + OPED, ""),
        # (b) opinion section, the titled name inside a running sentence
        (OPED + "\n\nThe visit was welcomed by Chinese Ambassador Wang Yu at a reception in Sydney.\n\n" + REPORT,
         "Opinion"),
        ("Chinese Ambassador Wang Yu said on Monday that the barley dispute was over.\n\n" + OPED, "Opinion"),
        (OPED + "\n\nAccording to Wang Yu, Chinese Ambassador to Australia, the dispute is over.\n\n" + REPORT,
         "Opinion"),
        # (c) a reported fact in the tail, with more reporting after it
        (OPED + "\n\nWang Yu is the Chinese ambassador to Australia.\n\n" + REPORT, ""),
        (OPED + "\n\nWang Yu is the Chinese ambassador to Australia. " + REPORT, ""),
        (OPED + "\n\nWang Yu is the Chinese ambassador to Australia.\nOfficials said the talks went well.", ""),
    ]
    for body, labels in cases:
        res = cr.match_signatures("", body, "Wang Yu", labels=labels)
        assert not _strong(res) and "diplomat_list_name_only" in _ids(res), (labels, body[:70], body[-70:])
    # nor a byline-shaped line that is reported speech, when the author field is empty
    res = cr.match_signatures("", "De acuerdo con Chen Daojiang, embajador de China en México, el comercio "
                              "crece.\n\n" + OPED, "")
    assert not _strong(res)


def test_signed_pieces_still_fire_after_the_reported_speech_check():
    """Bylines, author boxes, signatures and bios standing alone keep firing."""
    cases = [
        ("By Xiao Qian, Chinese Ambassador to Australia\n\n" + OPED, "Xiao Qian", ""),
        (OPED, "Wang Yu", "Wang Yu | Chinese Ambassador to Australia"),
        ("Wang Yu | Chinese Consul General in Sydney\n\n" + OPED, "Wang Yu", "Opinion"),
        (OPED + "\n\nXiao Qian is the Chinese Ambassador to Australia.", "Xiao Qian", ""),
        (OPED + "\n\nXiao Qian is the Chinese Ambassador to Australia.\nThe views expressed are his own.",
         "Xiao Qian", ""),
        (REPORT + "\n\n" + OPED + "\n*Embajador de China en México", "Chen Daojiang*", ""),
        (OPED, "Xiao Qian", "Opinion\nChinese Ambassador Xiao Qian"),
        (OPED, "Xiao Qian", "H.E. Ambassador Xiao Qian is the current Chinese ambassador to Australia, a post "
                            "which he assumed in January 2022."),
        (OPED, "Zhang Yang", "article\nZhang Yang\nEmbaixador da República Popular da China em Cabo Verde"),
    ]
    for body, author, labels in cases:
        res = cr.match_signatures("", body, author, labels=labels)
        assert res["decision"] == "A" and _strong(res), (author, labels, body[-60:])


def test_author_field_title_with_china_as_host_does_not_fire():
    """diplomat_title_author_field took any China word within 80 characters of the title, so China as the
    host country fired it. Its post_check (author_title_is_chinese) now asks the title phrase to make the
    person China's diplomat, by the test the diplomat list and diplomat_byline_head use."""
    sig = next(p for p in cr.load_signatures()["groups"]["authored_by_state"]
               if p["id"] == "diplomat_title_author_field")
    assert sig["post_check"] == "author_title_is_chinese" and sig["post_check"] in cr.POST_CHECKS
    fires = [
        "Xiao Qian, Chinese Ambassador to Australia",
        "Embajador de China en México",
        "Ambassador of the People's Republic of China to Kenya",
        "Botschafter der Volksrepublik China in Deutschland",
        "Ambassadeur de Chine en France",
        "Ambasciatore della Repubblica Popolare Cinese in Italia",
        "Chen Wei, Embajadora de China en Argentina",
        "Minister Counsellor of the Chinese Embassy in Canberra",
        "Chinese Consul-General in Perth",
        # a title naming no country, on a line that names a Chinese embassy
        "Wang Lutong, Ambassador, Embassy of the People's Republic of China",
        # the regex swallows the second title with the first; the check reads both
        "Li Dong, Ambassador of Singapore to China; Xiao Qian, Chinese Ambassador to Australia",
    ]
    for author in fires:
        res = cr.match_signatures("", OPED, author)
        assert res["decision"] == "A" and "diplomat_title_author_field" in _ids(res), author
    stays = [
        "Shen Bo, Nederlands ambassadeur in China",
        "Deutschlands China-Botschafterin",
        "Li Dong; Ambassador to China",
        "Hans Müller, deutscher Botschafter in China",
        "Marie Dupont, ambassadeur de France en Chine",
        "Former Australian ambassador to China Geoff Raby",
    ]
    for author in stays:
        res = cr.match_signatures("", OPED, author)
        assert "diplomat_title_author_field" not in _ids(res) and not _strong(res), author


def test_kenyan_swahili_uchina_is_china():
    """Kenyan Swahili writes China as Uchina: "Balozi wa Uchina nchini Kenya" is China's ambassador to
    Kenya, "Balozi wa Kenya nchini Uchina" Kenya's ambassador to China."""
    for author in ["Guo Haiyan, Balozi wa Uchina nchini Kenya",
                   "Guo Haiyan, Balozi wa Jamhuri ya Watu wa Uchina nchini Kenya"]:
        res = cr.match_signatures("", OPED, author)
        assert res["decision"] == "A" and _strong(res), author
    assert _strong(cr.match_signatures("", "By Guo Haiyan, Balozi wa Uchina nchini Kenya\n\n" + OPED, ""))
    assert _strong(cr.match_signatures("", OPED, "Guo Haiyan", labels="Guo Haiyan\nBalozi wa Uchina nchini Kenya"))
    res = cr.match_signatures("", OPED, "Guo Haiyan, Balozi wa Kenya nchini Uchina")
    assert not _strong(res) and "diplomat_list_name_only" in _ids(res)
    assert not _strong(cr.match_signatures("", "By Guo Haiyan, Balozi wa Kenya nchini Uchina\n\n" + OPED, ""))
    assert not _strong(cr.match_signatures("", OPED, "Guo Haiyan", labels="Guo Haiyan\nBalozi wa Kenya nchini Uchina"))
    # a bare title whose host is Uchina does not take China from the page
    assert not _strong(cr.match_signatures("", OPED, "Guo Haiyan; Balozi nchini Uchina", labels="Uchina"))


def test_verbless_author_label_bio():
    """A bio line with no verb, the writer word and then a dash, a colon or a comma (Polish and Czech
    "Autor", Indonesian "Penulis", Vietnamese "Tác giả", English "The writer"), or Turkish "Yazar" with
    the copula as a suffix on the title, counts in the head, tail or page labels when the title makes the
    author China's diplomat; China in the host slot does not count."""
    fires = [
        ("Lu Shan", "Autor \u2013 ambasador ChRL w Polsce"),
        ("Lu Shan", "Autor: ambasador ChRL w Polsce"),
        ("Zhou Qian", "Autor \u2013 velvyslanec ČLR v ČR"),
        ("Jiang Xuebin", "Yazar, Çin'in Ankara Büyükelçisidir"),
        ("Jiang Xuebin", "Yazar Çin'in Ankara Büyükelçisi"),
        ("Wang Lutong", "Penulis, Duta Besar China untuk Indonesia"),
        ("Wang Lutong", "Penulis: Duta Besar China untuk Indonesia"),
        ("He Wei", "Tác giả: Đại sứ Trung Quốc tại Việt Nam"),
        ("Xiao Qian", "The writer, Chinese Ambassador to Australia"),
        ("Xiao Qian", "The author: Chinese Ambassador to Australia"),
    ]
    for name, bio in fires:
        for res in (cr.match_signatures("", OPED + "\n\n" + bio, name),
                    cr.match_signatures("", bio + "\n\n" + OPED, name),
                    cr.match_signatures("", OPED, name, labels=bio)):
            assert res["decision"] == "A" and _strong(res), bio
    stays = [
        ("Lu Shan", "Autor \u2013 ambasador RP w Chinach"),               # Poland's ambassador in China
        ("Jiang Xuebin", "Yazar, Türkiye'nin Çin Büyükelçisidir"),        # Turkey's ambassador to China
        ("Wang Lutong", "Penulis, Duta Besar RI untuk Tiongkok"),         # Indonesia's ambassador to China
        ("He Wei", "Tác giả: Đại sứ Việt Nam tại Trung Quốc"),            # Vietnam's ambassador in China
        ("Jiang Xuebin", "Yazar Çin'in Ankara Büyükelçisi ile görüştü."),  # the writer met the ambassador
        ("Xiao Qian", "The writer, Chinese Ambassador to Australia, said on Monday that trade was recovering."),
        ("Lu Shan", "Rozmawiał z nim autor \u2013 ambasador ChRL w Polsce"),  # not a label line
    ]
    for name, bio in stays:
        for res in (cr.match_signatures("", OPED + "\n\n" + bio, name),
                    cr.match_signatures("", bio + "\n\n" + OPED, name),
                    cr.match_signatures("", OPED, name, labels=bio)):
            assert not _strong(res) and "diplomat_list_name_only" in _ids(res), bio
    # the host slot alone decides those: _title_is_chinese rejects each reverse form
    for bio in ["Autor \u2013 ambasador RP w Chinach", "Yazar, Türkiye'nin Çin Büyükelçisidir",
                "Penulis, Duta Besar RI untuk Tiongkok", "Tác giả: Đại sứ Việt Nam tại Trung Quốc"]:
        m = cr._AUTHOR_LABEL_RE.search(bio)
        t = m.start("t")
        assert not cr._title_is_chinese("," + " " * (t - 1) + bio[t:], "Lu Shan", t, m.end()), bio
    # only in the bio positions: in the middle of a long body it is not read
    middle = OPED * 8 + "\n\nAutor \u2013 ambasador ChRL w Polsce\n\n" + OPED * 8
    assert not _strong(cr.match_signatures("", middle, "Lu Shan"))
    # and only for a listed name in the author field
    res = cr.match_signatures("", OPED + "\n\nAutor \u2013 ambasador ChRL w Polsce", "Kowalska Anna")
    assert not _strong(res) and "diplomat_list_name_only" not in _ids(res)
