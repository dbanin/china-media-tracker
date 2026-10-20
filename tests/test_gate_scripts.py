"""Script aware keyword matching (gate 2026.10.2): a term never starts inside a word of an abugida,
the Indic stems carry an asterisk for their suffixed forms, joiners are optional, and the
substring languages keep their substring matching. Headlines below are the forms read in the
outlets named in pipeline/keywords.yaml."""
import re

import pytest

from pipeline import gate, termlists, themes


def admits(text, lang):
    return gate.check(text, "", lang)[0]


# The word for ancient contains the word for China after a vowel sign: प्राचीन, প্রাচীন.
@pytest.mark.parametrize("lang,text", [
    ("hi", "प्राचीन रमणिक नाथ जी मंदिर में पंचामृत अभिषेक"),
    ("hi", "कोचीन बंदरगाह पर नया टर्मिनल"),            # Cochin: को + चीन
    ("bn", "মিসরের পিরামিডের গায়ে লুকিয়ে থাকা প্রাচীন সংকেতের নতুন রহস্য"),
    ("ne", "प्राचीन मन्दिरको जीर्णोद्धार"),
    ("as", "প্ৰাচীন মন্দিৰ"),
    ("mr", "प्राचीन मंदिरात उत्सव"),
    ("fa", "فناوری بلاک‌چین در بانک‌ها"),   # blockchain: a joiner inside the word
])
def test_term_does_not_start_inside_a_word(lang, text):
    assert not admits(text, lang)


@pytest.mark.parametrize("lang,text", [
    ("hi", "चीन की सेना ने अभ्यास किया"),
    ("hi", "प्राचीन मंदिर और चीन का दौरा"),
    ("bn", "চীনে এআই প্রযুক্তির গোপন তথ্য পাচার"),     # the end still admits a vowel sign
    ("bn", "ইরানকে মার্কিন ঘাঁটিতে হামলায় চীনের সহায়তার খবর"),
    ("ta", "சீனாவின் பொருளாதாரம்"),
    ("ne", "चीन र नेपालबीच सम्झौता"),
    ("fa", "چین و ایران"),
])
def test_real_mentions_still_match(lang, text):
    assert admits(text, lang)


def test_a_lone_joiner_before_a_latin_word_does_not_hide_it():
    assert gate.check("‌China trade talks resume", "", "en") == (True, ["China"])


@pytest.mark.parametrize("lang,text", [
    ("th", "ที่จีน"),            # a tone mark right before the term: substring matching is kept
    ("th", "การค้ากับจีนเพิ่มขึ้น"),
    ("lo", "ປະເທດຈີນ"),
    ("km", "ប្រទេសចិន"),
    ("my", "တရုတ်နိုင်ငံ"),
])
def test_substring_scripts_keep_substring_matching(lang, text):
    assert admits(text, lang)


# Amharic writes its prepositions onto the noun.
@pytest.mark.parametrize("text", [
    "የቻይናው ፕሬዚዳንት ሺ ጂንፒንግ ሰሜን ኮሪያ ገቡ",
    "ጠቅላይ ሚኒስትር ከቻይናው ፕሬዚዳንት ጋር ተወያዩ",
    "ቻይናን ሳስባት",
    "የኢትዮጵያና የቻይናን ዘላቂ ትብብር",
    "ቻይና በታይዋን ጉዳይ ከአሜሪካ ጋር",
    "ፕሬዚዳንቱ በቤጂንግ ተወያዩ",
    "ለቻይና ባለሀብቶች",
    "በቻይና የተደረገ ጥናት",
])
def test_amharic_prefixed_forms(text):
    assert admits(text, "am")


def test_amharic_unrelated():
    assert not admits("የኢትዮጵያ አየር መንገድ አዲስ መስመር ከፈተ", "am")


# One verified headline per new language, plus a form carrying a suffix written onto the noun.
@pytest.mark.parametrize("lang,text", [
    ("mr", "तैवानने चीनच्या दिशेने रॉकेट्स डागले"),
    ("mr", "बीजिंगमध्ये गुप्त खलबतं; तैवानचे विरोधी पक्षनेते चीनमध्ये दाखल"),
    ("mr", "चायनाच्या सदर्न एअरलाईन्सने"),
    ("te", "పాక్, చైనాలది విడదీయలేని మైత్రీబంధం: షీ జిన్‌పింగ్‌"),
    ("te", "తైవాన్‌పై రాజీ ప్రసక్తే లేదు"),
    ("te", "హాంకాంగ్‌లో భారీ అగ్నిప్రమాదం"),
    ("kn", "ಕ್ಷಿಪ್ರ ಕ್ರಾಂತಿ: ಚೀನಾದ ಜಿನ್‌ಪಿಂಗ್‌ಗೆ ಗೃಹಬಂಧನ?"),
    ("kn", "ಬೀಜಿಂಗ್‌ನಲ್ಲಿ ಐತಿಹಾಸಿಕ ಭೇಟಿ"),
    ("kn", "ಟ್ರಂಪ್, ಜಿನ್‍ಪಿಂಗ್ ಜೊತೆ ತ್ರಿಪಕ್ಷೀಯ ಶೃಂಗಸಭೆ"),   # zero width joiner
    ("kn", "ಟ್ರಂಪ್, ಜಿನ್ಪಿಂಗ್ ಜೊತೆ"),                       # no joiner
    ("ml", "ചൈനയിലെ സൈനിക അട്ടിമറി"),
    ("ml", "ട്രംപ്-ഷി ജിൻപിങ് കൂടിക്കാഴ്ച ഇന്ന് ബെയ്ജിങില്‍"),
    ("ml", "ഷി ചിൻപിങ്ങിന് പക്ഷാഘാതം"),
    ("ml", "തായ്‍വാന് പിന്നാലെ"),
    ("ml", "വനിതാ ഏഷ്യാകപ്പിൽ ഇന്ത്യ ഇന്ന് ഹോങ്കോങ്ങിനെതിരെ"),
    ("gu", "અમેરિકન પ્રમુખ ટ્રમ્પની ચીનમાં ફજેતી"),
    ("gu", "શી જિનપિંગની તંદુરસ્તી ખરાબ છે"),
    ("gu", "એજન્સી શિન્હુઆના જણાવ્યા"),
    ("pa", "ਤਾਈਵਾਨ 'ਤੇ ਕਬਜ਼ੇ ਦੀ ਤਿਆਰੀ 'ਚ ਚੀਨ"),
    ("pa", "ਸ਼ੀ ਜਿਨਪਿੰਗ ਨੇ ਪਾਰਟੀ ਲੀਡਰਸ਼ਿਪ ਨੂੰ ਮਜ਼ਬੂਤ ਕਰਨ 'ਤੇ ਦਿੱਤਾ ਜ਼ੋਰ"),
    ("or", "ଚୀନ୍‌କୁ ଇରାନରୁ ତେଲ କିଣିବାକୁ ଦେବୁ ନାହିଁ"),
    ("or", "ଭାରତରେ ରହି ଚୀନରେ ରୋଗୀଙ୍କ ସଫଳ ସର୍ଜରୀ"),
    ("or", "ଚାଇନା ଏଜେଣ୍ଟଙ୍କୁ ସମ୍ବେଦନଶୀଳ ତଥ୍ୟ"),
    ("or", "ସରିଲା ଜିନପିଙ୍ଗଙ୍କ 3 ଦିନିଆ ଆମେରିକା ଗସ୍ତ"),
    ("as", "চীনৰ আগ্ৰাসন"),
    ("as", "আমেৰিকাই চীনক বশ কৰিব"),
])
def test_new_indian_language_lists(lang, text):
    assert admits(text, lang)


@pytest.mark.parametrize("lang,text", [
    ("mr", "मुंबईत पावसाचा जोर वाढला"),
    ("te", "హైదరాబాద్‌లో భారీ వర్షం"),
    ("kn", "ಬೆಂಗಳೂರಿನಲ್ಲಿ ಮಳೆ"),
    ("ml", "കൊച്ചിയിൽ കനത്ത മഴ"),
    ("gu", "અમદાવાદમાં વરસાદ"),
    ("pa", "ਲੁਧਿਆਣਾ ਵਿੱਚ ਮੀਂਹ"),
    ("or", "ଭୁବନେଶ୍ୱରରେ ବର୍ଷା"),
    ("as", "গুৱাহাটীত বৰষুণ"),
])
def test_new_lists_do_not_admit_local_news(lang, text):
    assert not admits(text, lang)


def test_new_languages_are_supported():
    assert {"mr", "te", "kn", "ml", "gu", "pa", "or", "as"} <= gate.supported_languages()


def test_asterisk_is_the_rest_of_the_word_and_respects_the_start():
    pat = gate._term_pattern("चीन*", "mr", set(), set())
    assert pat.search("चीनमध्ये आज").group(0) == "चीनमध्ये"
    assert not pat.search("प्राचीन")


def test_joiner_in_a_term_matches_either_or_none():
    pat = gate._term_pattern("జిన్‌పింగ*", "te", set(), set())
    for text in ("జిన్‌పింగ్", "జిన్‍పింగ్", "జిన్పింగ్"):
        assert pat.search(text), text


def test_latin_terms_keep_their_boundary_in_substring_languages():
    assert not admits("Taxi drivers protest", "th")


def test_termlists_and_themes_share_the_start_rule():
    ts = termlists.compile({"bn": ["চীন"]}, "bn")
    assert ts.count("প্রাচীন সভ্যতা আর চীনের অর্থনীতি") == 1
    # A substring language in an Indic script: no start right after a vowel sign either.
    ts = termlists.compile({"hi": ["चीन"]}, "hi", substring_langs=["hi"])
    assert ts.count("प्राचीन") == 0 and ts.count("चीनी निवेश") == 1
    ts = termlists.compile({"th": ["จีน"]}, "th", substring_langs=["th"])
    assert ts.count("ที่จีน") == 1
    assert re.compile(termlists.WORD_START + "চীন").search("প্রাচীন") is None
    pats = [p for _, p in themes._compiled("hi")]
    assert pats  # compiles with the shared start rule
