"""Hindi and English content for the seeded catalogue.

Same status as the rest of seed.py: fictional clinic details, authored for
the prototype, not sourced from a real clinic. Kept in its own file so the
Bengali seed data stays readable and so `backfill_i18n()` can bring an
EXISTING database up to date without a destructive reseed (which would
wipe every appointment booked since the last boot).

Aliases are what a caller's ASR output actually looks like: Hindi ASR
emits Devanagari, including for English loan words ("यूरिक एसिड"), so the
Devanagari spelling is the lookup key, exactly as aliases_bn is for Bengali.
"""

from __future__ import annotations

TEST_ALIASES_HI: dict[str, list[str]] = {
    "Complete Blood Count (CBC)": ["सीबीसी", "सी बी सी"],
    "ESR": ["ईएसआर", "ई एस आर"],
    "Blood Sugar Fasting": ["ब्लड शुगर फास्टिंग", "शुगर फास्टिंग", "खाली पेट शुगर"],
    "Blood Sugar PP": ["शुगर पीपी", "पीपी शुगर", "खाने के बाद शुगर"],
    "HbA1c": ["एचबीए वन सी", "हीमोग्लोबिन ए वन सी"],
    "Lipid Profile": ["लिपिड प्रोफाइल", "कोलेस्ट्रॉल टेस्ट"],
    "Liver Function Test (LFT)": ["लिवर फंक्शन टेस्ट", "एलएफटी", "लिवर टेस्ट"],
    "Kidney Function Test (KFT)": ["किडनी फंक्शन टेस्ट", "केएफटी", "किडनी टेस्ट"],
    "Thyroid Profile (T3 T4 TSH)": ["थायराइड प्रोफाइल", "थायराइड टेस्ट"],
    "TSH": ["टीएसएच", "टी एस एच"],
    "Urine Routine Examination": ["यूरिन टेस्ट", "पेशाब की जांच", "यूरिन रूटीन"],
    "Widal Test": ["विडाल टेस्ट", "टाइफाइड टेस्ट"],
    "Dengue NS1 Antigen": ["डेंगू एनएस वन", "डेंगू टेस्ट"],
    "Dengue IgG/IgM": ["डेंगू आईजीजी", "डेंगू आईजीएम"],
    "Malaria Antigen": ["मलेरिया टेस्ट", "मलेरिया एंटीजन"],
    "CRP (C-Reactive Protein)": ["सीआरपी"],
    "Vitamin D (25-OH)": ["विटामिन डी"],
    "Vitamin B12": ["विटामिन बी बारह", "बी बारह"],
    "Serum Creatinine": ["क्रिएटिनिन", "सीरम क्रिएटिनिन"],
    "Serum Electrolytes": ["इलेक्ट्रोलाइट्स", "इलेक्ट्रोलाइट टेस्ट"],
    "Blood Grouping & Rh Typing": ["ब्लड ग्रुप", "खून का ग्रुप"],
    "HIV Test (ELISA)": ["एचआईवी टेस्ट", "एड्स टेस्ट", "एचआईवी"],
    "HBsAg": ["एचबीएसएजी", "हेपेटाइटिस बी"],
    "HCV": ["एचसीवी", "हेपेटाइटिस सी"],
    "ECG": ["ईसीजी", "इलेक्ट्रोकार्डियोग्राम"],
    "Chest X-Ray (PA view)": ["छाती का एक्स-रे", "चेस्ट एक्सरे"],
    "USG Whole Abdomen": ["पेट का अल्ट्रासाउंड", "होल एब्डोमेन यूएसजी", "पेट का यूएसजी"],
    "USG Pregnancy Profile": ["प्रेगनेंसी अल्ट्रासाउंड", "प्रेगनेंसी यूएसजी"],
    "2D Echocardiography": ["इको टेस्ट", "एकोकार्डियोग्राफी", "इकोकार्डियोग्राम"],
    "TMT (Treadmill Test)": ["टीएमटी", "ट्रेडमिल टेस्ट"],
    "Pap Smear": ["पैप स्मियर"],
    "PSA (Prostate Specific Antigen)": ["पीएसए", "पी एस ए"],
    "Uric Acid": ["यूरिक एसिड"],
    "Calcium (Serum)": ["कैल्शियम", "सीरम कैल्शियम"],
}

SURNAME_HI: dict[str, list[str]] = {
    "Mukherjee": ["मुखर्जी"],
    "Sen": ["सेन"],
    "Ghosh": ["घोष"],
    "Chowdhury": ["चौधरी"],
    "Bhattacharya": ["भट्टाचार्य"],
    "Roy": ["रॉय", "राय"],
    "Banerjee": ["बनर्जी"],
    "Dutta": ["दत्ता"],
    "Chatterjee": ["चटर्जी"],
    "Basu": ["बसु"],
    "Mitra": ["मित्रा"],
    "Sengupta": ["सेनगुप्ता"],
    "Das": ["दास"],
    "Bose": ["बोस"],
    "Kar": ["कर"],
    "Nandi": ["नंदी"],
    "Pal": ["पाल"],
    "Halder": ["हालदार"],
    "Guha": ["गुहा"],
    "Chanda": ["चंदा"],
    "Saha": ["साहा"],
    "Dey": ["डे"],
    "Adhikari": ["अधिकारी"],
    "Bagchi": ["बागची"],
    "Biswas": ["बिस्वास"],
    "Majumder": ["मजूमदार"],
    "Mondal": ["मंडल"],
    "Ganguly": ["गांगुली"],
    "Sinha": ["सिन्हा"],
    "Ray": ["रे", "राय"],
    "Sarkar": ["सरकार"],
    "Chakraborty": ["चक्रवर्ती"],
}

# test name -> (Hindi, English). Tests not listed get the default below.
PREP_I18N: dict[str, tuple[str, str]] = {
    "Blood Sugar Fasting": (
        "इस टेस्ट से पहले कम से कम आठ घंटे कुछ न खाएँ, सिर्फ़ पानी पी सकते हैं। सुबह खाली पेट आकर टेस्ट कराना बेहतर है।",
        "Do not eat anything for at least eight hours before this test; you may drink water. "
        "It is best to come in the morning on an empty stomach.",
    ),
    "Lipid Profile": (
        "इस टेस्ट से पहले दस से बारह घंटे उपवास रखना होगा, पानी के अलावा कुछ न लें। पिछली रात हल्का खाना खाना अच्छा रहता है।",
        "Fast for ten to twelve hours before this test, nothing except water. A light meal the night before is best.",
    ),
    "HbA1c": (
        "इस टेस्ट के लिए उपवास की ज़रूरत नहीं है, सामान्य खाने के बाद भी करा सकते हैं।",
        "No fasting is needed for this test; you can have it after your normal meals.",
    ),
    "Kidney Function Test (KFT)": (
        "इस टेस्ट से पहले छह से आठ घंटे उपवास रखने की सलाह दी जाती है।",
        "Fasting for six to eight hours is advised before this test.",
    ),
    "Liver Function Test (LFT)": (
        "इस टेस्ट से पहले आठ घंटे उपवास रखने की सलाह दी जाती है।",
        "Fasting for eight hours is advised before this test.",
    ),
    "USG Whole Abdomen": (
        "इस टेस्ट से पहले छह घंटे कुछ न खाएँ और पेशाब रोककर रखें, मूत्राशय भरा होना ज़रूरी है।",
        "Do not eat anything for six hours before this test, and hold your urine; the bladder needs to be full.",
    ),
    "USG Pregnancy Profile": (
        "इस टेस्ट से पहले ज़्यादा पानी पिएँ ताकि मूत्राशय भरा रहे, और टेस्ट से ठीक पहले पेशाब न करें।",
        "Drink plenty of water before this test to keep your bladder full, and do not urinate just before the test.",
    ),
    "TMT (Treadmill Test)": (
        "हल्के, आरामदायक कपड़े और जूते पहनकर आएँ। टेस्ट से दो घंटे पहले भारी खाना न खाना बेहतर है।",
        "Wear light, comfortable clothes and shoes. It is best not to eat a heavy meal "
        "in the two hours before the test.",
    ),
}

DEFAULT_PREP_HI = "इस टेस्ट के लिए किसी ख़ास तैयारी की ज़रूरत नहीं है, आप सामान्य रूप से आकर करा सकते हैं।"
DEFAULT_PREP_EN = "No special preparation is needed for this test; you can come in normally."

# topic -> (Hindi, English)
FAQ_I18N: dict[str, tuple[str, str]] = {
    "hours": (
        "हमारा क्लिनिक हर दिन सुबह आठ बजे से रात आठ बजे तक खुला रहता है, और रविवार को सुबह आठ बजे से दोपहर दो बजे तक।",
        "Our clinic is open every day from 8 AM to 8 PM, and on Sundays from 8 AM to 2 PM.",
    ),
    "location": (
        "हमारा क्लिनिक कोलकाता के राजारहाट न्यू टाउन में है, सिटी सेंटर टू के पास।",
        "Our clinic is in New Town, Rajarhat, Kolkata, near City Centre 2.",
    ),
    "payment_methods": (
        "नकद, सभी तरह के कार्ड और यूपीआई, सभी से भुगतान लिया जाता है।",
        "We accept cash, all kinds of cards, and UPI.",
    ),
    "insurance": (
        "प्रमुख बीमा कंपनियों की कैशलेस सुविधा उपलब्ध है। अपने कार्ड का नाम बताइए, काउंटर से पुष्टि कर दी जाएगी।",
        "Cashless facility is available with major insurance companies. "
        "Tell us your card's name and the counter will confirm it for you.",
    ),
    "parking": (
        "क्लिनिक की अपनी पार्किंग है, कोई शुल्क नहीं लगता।",
        "The clinic has its own parking, and there is no charge.",
    ),
    "report_collection": (
        "रिपोर्ट सीधे काउंटर से ले सकते हैं, या हम इसे व्हाट्सऐप और ईमेल पर भी भेजते हैं।",
        "You can collect your report directly from the counter, or we can also send it on WhatsApp and email.",
    ),
    "contact_number": (
        "आप हमारे हेल्पडेस्क नंबर पर फ़ोन करके सीधे बात कर सकते हैं, इस कॉल के बाद नंबर एसएमएस से भी भेज दिया जाएगा।",
        "You can call our helpdesk number and speak to us directly. We will also SMS you the number after this call.",
    ),
    "home_collection": (
        "ज़्यादातर ब्लड टेस्ट के लिए घर आकर सैंपल लेने की सुविधा है। बुकिंग के समय बता दीजिए।",
        "Home sample collection is available for most blood tests. Please mention it when you book.",
    ),
}


# topic -> (Hindi keyword phrases, English keyword phrases). What FastPath matches a caller's words against for
# a clinic FAQ question (KCD-095). Phrases, not single generic words: a fast-path hit is answered without a
# model, so "time" alone would send a question about report time to the opening hours. REASONED, not measured
# against real calls; a native reviewer should add the phrasings callers actually use.
FAQ_KEYWORDS_I18N: dict[str, tuple[list[str], list[str]]] = {
    "hours": (
        [
            "क्लिनिक कब खुलता है", "क्लिनिक कब बंद होता है", "खुलने का समय", "बंद होने का समय", "क्लिनिक का समय",
            # live 2026-10-07: "आपकी टाइमिंग क्या है" was answered with the ADDRESS; "आज खुला है" matched nothing
            "कब खुलता", "कब खुला", "खुला है", "खुला रहता", "कितने बजे खुलता", "कितने बजे तक खुला", "टाइमिंग",
            "रविवार को खुला", "आज खुला", "कब बंद",
        ],
        [
            "opening hours",
            "opening time",
            "closing time",
            "what time do you open",
            "what time do you close",
            "clinic timings",
            "clinic hours",
            "are you open",
            # "What are your timings?" / "Is the clinic open today?" were answered with the ADDRESS
            "when are you open",
            "when do you open",
            "when do you close",
            "your timings",
            "timings",
            "open today",
            "open on sunday",
            "clinic open",
            "till what time",
            "what time does the clinic",
            # A form shorter than 5 characters must match a WHOLE word exactly (fast_path_cues
            # exact_below_chars), so this is "open" the word, never "open" inside another one.
            "open",
        ],
    ),
    "location": (
        [
            "आप कहाँ हैं", "आप कहां हैं", "क्लिनिक कहाँ है", "क्लिनिक कहां है", "आपका पता", "पता क्या है", "कैसे पहुँचें",
            "कैसे पहुंचें", "किस जगह", "पता बताइए", "पता बताओ", "एड्रेस", "अड्रेस", "ऐड्रेस",
            # the same question, by "go" and "come", and by the way ("2026-10-13": the Bengali one was missed)
            "कैसे जाऊँ", "कैसे जाऊं", "कैसे आऊँ", "कैसे आऊं", "कैसे जाएँ", "कैसे जाएं", "कैसे आएँ", "कैसे आएं",
            # a landmark near the clinic (2026-10-13): the address answer names City Centre 2
            "लैंडमार्क", "लैंड मार्क", "आसपास क्या है", "किस इलाके में",
        ],
        [
            "where are you",
            "where is the clinic",
            "clinic address",
            "your address",
            "how do i get there",
            "how to reach",
            "where are you located",
            "address",
        ],
    ),
    "payment_methods": (
        ["पेमेंट कैसे", "भुगतान कैसे", "कार्ड चलता है", "यूपीआई", "नकद"],
        [
            "payment methods",
            "pay by card",
            "do you take cards",
            "do you accept upi",
            "can i pay in cash",
            "how can i pay",
            "payment options",
        ],
    ),
    "insurance": (
        ["इंश्योरेंस", "बीमा", "कैशलेस", "मेडिक्लेम"],
        ["insurance", "cashless", "mediclaim", "health insurance"],
    ),
    "parking": (
        ["पार्किंग", "गाड़ी कहाँ खड़ी", "गाड़ी कहां खड़ी", "गाड़ी रखने", "गाड़ी कहाँ रखें", "गाड़ी कहां रखें"],
        ["parking", "car parking", "where can i park", "park my car"],
    ),
    "report_collection": (
        ["रिपोर्ट कैसे मिलेगी", "रिपोर्ट कहाँ से", "रिपोर्ट कहां से", "रिपोर्ट लेने"],
        [
            "collect my report",
            "report collection",
            "how do i get my report",
            "where to collect the report",
            "get the report",
            "reports on whatsapp",
            "send the report",
        ],
    ),
    "contact_number": (
        ["फोन नंबर", "संपर्क नंबर", "हेल्पलाइन नंबर", "नंबर क्या है"],
        ["phone number", "contact number", "helpline number", "how can i contact you", "your number"],
    ),
    "home_collection": (
        ["होम कलेक्शन", "घर से सैंपल", "घर आकर", "घर पर सैंपल"],
        [
            "home collection", "sample from home", "collect at home", "come to my house", "home visit for sample",
            "come home to take", "samples from home", "blood from home",
        ],
    ),
}


# ---------------------------------------------------------------- departments
# Bengali and Hindi spoken forms of every seeded department name, because the
# Latin name is UNSPEAKABLE on the Bengali and Hindi voices: agent/tts.py's
# unspeakable_spans() blocks any reply still carrying Latin script after
# normalisation, so "General Medicine বিভাগে এই ডাক্তাররা বসেন" never reached
# the caller at all -- they heard the system-busy fallback instead. Measured on
# this tree before this table existed: four of the eight seeded departments
# were blocked in Bengali (General Medicine, Gynaecology & Obstetrics,
# Orthopaedics, Diabetology & Endocrinology); the other four only spoke because
# agent/pronunciation.py's DRAFT lexicon happened to carry them, and that
# lexicon is unreviewed and off by default.
#
# Storing the spoken form as DATA, next to the department it belongs to, fixes
# it at the source: the reply speaks a Bengali or Hindi string and the gate has
# nothing to block. It also means a clinic can correct a name without a code
# change, which a lexicon entry does not allow.
#
# Transliterations, not translations, and deliberately so: a caller in Kolkata
# asks for "কার্ডিওলজি", not "হৃদরোগ বিভাগ". The four that agent/pronunciation.py
# already carried (Cardiology, ENT, Dermatology, Paediatrics) reuse ITS spelling
# exactly, so the voice says the same thing whichever path produced the text.
#
# SAME REVIEW STATUS AS THE REST OF THIS FILE, AND AS THE LEXICON: drafted by a
# non-native speaker. "গাইনি" is what is actually said at a Kolkata counter,
# which is why it is here rather than the textbook "স্ত্রীরোগ ও প্রসূতিবিদ্যা" --
# but that is a judgement a native speaker must confirm, not accept from this
# file. Have it reviewed with agent/pronunciation.review_sheet()'s output.
DEPARTMENT_NAMES_I18N: dict[str, tuple[str, str]] = {
    "General Medicine": ("জেনারেল মেডিসিন", "जनरल मेडिसिन"),
    "Cardiology": ("কার্ডিওলজি", "कार्डियोलॉजी"),
    "Gynaecology & Obstetrics": ("গাইনি ও প্রসূতি", "गायनी एवं प्रसूति"),
    "Orthopaedics": ("অর্থোপেডিক", "ऑर्थोपेडिक"),
    "ENT": ("ইএনটি", "ईएनटी"),
    "Dermatology": ("ডার্মাটোলজি", "डर्मेटोलॉजी"),
    "Paediatrics": ("পেডিয়াট্রিক্স", "पीडियाट्रिक्स"),
    "Diabetology & Endocrinology": ("ডায়াবেটোলজি ও এন্ডোক্রিনোলজি", "डायबेटोलॉजी एवं एंडोक्रिनोलॉजी"),
}


# ------------------------------------------------------- department aliases
# How a caller actually NAMES a department, in every script this line hears.
#
# Nobody rings a clinic and says "Gynaecology & Obstetrics". They say "মেয়েদের
# ডাক্তার", "lady doctor", "महिला डॉक्टर" -- and a caller who does not know a
# doctor's name says the KIND of doctor instead: "হার্টের ডাক্তার", "heart doctor",
# "दिल का डॉक्टर". Before this table, clinic-api matched departments against the
# Latin name only, so every one of those found nothing. Measured on this tree on
# 2026-10-06: "কার্ডিওলজি", "মেডিসিন", "হার্টের ডাক্তার", "দিল का डॉक्टर", "skin
# doctor" -- all not found; only the Latin "cardiology" resolved.
#
# Provenance: the Latin and Bengali entries are carried over from the sibling
# tree's clinic-api/seed.py DEPARTMENT_ALIASES (the tree the sprint-1 stories
# were first delivered in), which has none in Devanagari -- its Hindi is
# romanised Hinglish only, but the Hindi recogniser here writes Devanagari, so a
# Hindi caller matched nothing. The Devanagari entries and the extra Bengali
# phrasings are new. Same review status as everything else in this file:
# drafted by a non-native speaker, to be confirmed by one.
#
# Matching is done by clinic-api/department_match.py, which removes the words
# that name no department ("ডাক্তার", "doctor", "का") before comparing, so an
# alias's own "doctor" does not make every "<X> doctor" look alike.
DEPARTMENT_ALIASES: dict[str, list[str]] = {
    'General Medicine': [
        'general medicine',
        'general',
        'medicine',
        'physician',
        'general doctor',
        'general medicine doctor',
        'medicine ka doctor',
        'general doctor chahiye',
        'general medicine doctor chai',
        'medicine er doctor',
        'general doctor chai',
        'জেনারেল মেডিসিন',
        'জেনারেল',
        'মেডিসিন',
        'মেডিসিন ডাক্তার',
        'সাধারণ ডাক্তার',
        'মেডিসিনের ডাক্তার',
        'ফিজিশিয়ান',
        'जनरल मेडिसिन',
        'जनरल डॉक्टर',
        'मेडिसिन',
        'मेडिसिन का डॉक्टर',
        'फिजिशियन',
        'सामान्य डॉक्टर',
    ],
    'Cardiology': [
        'cardiology',
        'cardiologist',
        'cardio',
        'heart',
        'heart doctor',
        'heart ka doctor',
        'dil ka doctor',
        'heart specialist',
        'cardio doctor',
        'heart er doctor',
        'hridoy er doctor',
        'heart specialist chai',
        'cardio doctor chai',
        'কার্ডিওলজি',
        'হার্ট',
        'হার্টের ডাক্তার',
        'হৃদরোগের ডাক্তার',
        'হৃদরোগ',
        'হার্ট স্পেশালিস্ট',
        'কার্ডিওলজিস্ট',
        'कार्डियोलॉजी',
        'कार्डियोलॉजिस्ट',
        'दिल का डॉक्टर',
        'हार्ट',
        'हार्ट का डॉक्टर',
        'हृदय रोग',
    ],
    'Gynaecology & Obstetrics': [
        'gynaecology',
        'gynecology',
        'gynae',
        'gyne',
        'obstetrics',
        'women doctor',
        "women's doctor",
        'gynae doctor',
        'ladies doctor',
        'lady doctor',
        'women ka doctor',
        'pregnancy doctor',
        'gynae doctor chai',
        'ladies doctor chai',
        'meyeder doctor',
        'pregnancy doctor chai',
        'গাইনি',
        'গাইনোকলজি',
        'মহিলাদের ডাক্তার',
        'প্রেগন্যান্সি ডাক্তার',
        'গাইনোকোলজি',
        'স্ত্রীরোগ',
        'মেয়েদের ডাক্তার',
        'প্রসূতি',
        'गायनी',
        'गायनेकोलॉजी',
        'स्त्री रोग',
        'महिला डॉक्टर',
        'लेडी डॉक्टर',
        'प्रसूति',
        # English "gy" has two settled Devanagari spellings and the recogniser writes both:
        # गाय (gaay) above, गाइ (gaai) here. Live 2026-10-09 a Hindi caller said
        # गाइनेकोलॉजी twice, matched nothing, and was sent to the counter. The bare stem
        # (गायन / गाइन) is the same turn's other miss -- it is a whole word to the matcher, so it
        # cannot also match गायनी. ॉ / ो and the unspaced स्त्रीरोग are the same kind of variant.
        'गाइनी',
        'गाइनेकोलॉजी',
        'गाइनोकोलॉजी',
        'गायनेकोलोजी',
        'गाइनेकोलोजी',
        'गायन',
        'गाइन',
        'स्त्रीरोग',
    ],
    'Orthopaedics': [
        'orthopaedics',
        'orthopedics',
        'ortho',
        'bone doctor',
        'orthopedic doctor',
        'ortho doctor',
        'haddi ka doctor',
        'bone ka doctor',
        'haddi wala doctor',
        'ortho doctor chai',
        'haddi er doctor',
        'bone er doctor',
        'haddi doctor',
        'অর্থোপেডিক্স',
        'অর্থো',
        'হাড়ের ডাক্তার',
        'হাড়ের ডাক্তার চাই',
        'অর্থোপেডিক',
        'হাড়',
        'ऑर्थोपेडिक',
        'हड्डी',
        'हड्डी का डॉक्टर',
        'ऑर्थो',
    ],
    'ENT': [
        'ent',
        'ear',
        'nose',
        'throat',
        'ent doctor',
        'ear nose throat',
        'kaan ka doctor',
        'naak ka doctor',
        'gale ka doctor',
        'ent specialist',
        'kan er doctor',
        'nak er doctor',
        'golar doctor',
        'ent doctor chai',
        'ইএনটি',
        'কানের ডাক্তার',
        'নাকের ডাক্তার',
        'গলার ডাক্তার',
        'নাক কান গলা',
        'নাক কান গলার ডাক্তার',
        'ईएनटी',
        'नाक कान गला',
        'कान का डॉक्टर',
        'गले का डॉक्टर',
    ],
    'Dermatology': [
        'dermatology',
        'dermatologist',
        'derma',
        'skin',
        'skin doctor',
        'skin ka doctor',
        'skin specialist',
        'derma doctor',
        'skin er doctor',
        'skin specialist chai',
        'chormo rog er doctor',
        'ডার্মাটোলজি',
        'স্কিন',
        'ত্বকের ডাক্তার',
        'চর্মরোগের ডাক্তার',
        'চর্মরোগ',
        'চামড়ার ডাক্তার',
        'স্কিনের ডাক্তার',
        'डर्मेटोलॉजी',
        'त्वचा',
        'त्वचा का डॉक्टर',
        'स्किन का डॉक्टर',
        'चमड़ी',
    ],
    'Paediatrics': [
        'paediatrics',
        'pediatrics',
        'paediatrician',
        'pediatrician',
        'child doctor',
        'children doctor',
        'bachche ka doctor',
        'bache ka doctor',
        'child specialist',
        'kids doctor',
        'bacchar doctor',
        'bachader doctor',
        'shishu doctor',
        'bacchar doctor chai',
        'পিডিয়াট্রিক্স',
        'শিশুর ডাক্তার',
        'বাচ্চাদের ডাক্তার',
        'শিশু বিশেষজ্ঞ',
        'শিশু',
        'শিশুরোগ',
        'পেডিয়াট্রিক',
        'पीडियाट्रिक्स',
        'बच्चों का डॉक्टर',
        'बच्चे का डॉक्टर',
        'शिशु रोग',
    ],
    'Diabetology & Endocrinology': [
        'diabetology',
        'diabetes',
        'diabetic doctor',
        'endocrinology',
        'endocrinologist',
        'endo',
        'sugar ka doctor',
        'diabetes ka doctor',
        'sugar specialist',
        'hormone doctor',
        'sugar er doctor',
        'diabetes er doctor',
        'sugar specialist chai',
        'hormone er doctor',
        'ডায়াবেটোলজি',
        'সুগারের ডাক্তার',
        'ডায়াবেটিসের ডাক্তার',
        'এন্ডোক্রিনোলজি',
        'ডায়াবেটিস',
        'থাইরয়েড',
        'হরমোন',
        'डायबिटीज़',
        'डायबिटीज',
        'शुगर का डॉक्टर',
        'थायराइड',
        'हार्मोन',
        'एंडोक्रिनोलॉजी',
    ],
}
