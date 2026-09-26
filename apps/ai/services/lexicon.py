"""Multilingual phrase to canonical symptom matching.

AUDIT 6, Tier 1 asks for free text in Hindi or Marathi to become structured
English symptoms. This is the deterministic half of that, and it works today
with no vendor, no credentials and no network.

## What it is

A curated mapping from things people actually say - in English, in Hindi and
Marathi in Devanagari, and in the romanised Hinglish people type on a phone
keyboard - to the canonical symptom names the triage model was fitted on. Every
match carries the phrase that produced it, so a clinician reviewing a draft can
check the reading instead of trusting it.

## What it is not

**This content has not been reviewed by a clinician.** AUDIT 11.6 - who owns
clinical content sign-off - is unanswered, and this file is squarely in its
scope, alongside the descriptions, precautions and red-flag rules. The mappings
here are ordinary-language equivalences, deliberately chosen to be the obvious
ones rather than the clever ones, and marked so the set is greppable:
``CLINICAL-REVIEW-NEEDED``.

It is also **not complete**, and does not pretend to be. Of the 131 canonical
symptoms it covers the ones a patient is likely to volunteer unprompted;
findings nobody describes in conversation (``family_history``,
``receiving_unsterile_injections``, ``toxic_look_(typhos)``) are intentionally
absent, because a phrase list that guesses at those would produce confident
nonsense. Anything the matcher does not recognise is returned as an unmapped
phrase and stored on the draft, which makes the gaps visible instead of silent -
that list is the backlog for this file.

## Confidence here is specificity, not probability

``confidence`` is how *specific* the matched phrase was: a multi-word phrase
that means one thing scores higher than a single broad word that could mean
several. It is a hand-set weight, not a learned quantity and not a probability
of anything. The same discipline the project applies to the word "confidence"
on the differential applies here: the UI leads with the matched phrase, because
the phrase is the evidence and the number is not.

## Negation

"No fever" must not produce ``high_fever``. A small negation window handles the
common cases in all three languages. It is genuinely limited - it will not
survive "I had a fever last week but not now" - so the honest position is that
this reduces a common error rather than eliminating a class of them, which is
also why every draft needs a human before it becomes a record.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from dataclasses import dataclass

#: Bump when the phrase table changes, so a draft records which version read it.
VERSION = "lexicon-2026-09-26.1"

# Confidence bands. Hand-set weights describing how specific a phrase is.
SPECIFIC = 0.95  # a multi-word phrase that means one thing
CLEAR = 0.85  # a single distinctive term
BROAD = 0.60  # a term that could reasonably point elsewhere


@dataclass(frozen=True)
class Match:
    symptom: str
    evidence: str
    confidence: float


# ---------------------------------------------------------------------------
# The phrase table
# ---------------------------------------------------------------------------
# CLINICAL-REVIEW-NEEDED: every entry below (AUDIT 11.6).
#
# Keyed by canonical symptom name, exactly as the model artefact spells it -
# including the source data's stray spaces, which are load-bearing here and must
# not be tidied. A name that does not match the artefact is dropped at match
# time rather than silently proposing a symptom the model cannot accept.
#
# Order within a list does not matter; the matcher tries longer phrases first
# across the whole table, so "chest pain" wins over "pain".

PHRASES: dict[str, tuple[str, ...]] = {
    "itching": (
        "itching", "itchy", "itch", "scratching",
        "खुजली", "खाज", "खुजली हो रही", "खाज सुटते",
        "khujli", "khaj", "kharish",
    ),
    "skin_rash": (
        "skin rash", "rash", "rashes", "red patches on skin", "skin eruption",
        "चकत्ते", "दाने", "त्वचा पर दाने", "पुरळ", "लाल चट्टे",
        "daane", "chakatte", "pural",
    ),
    "nodal_skin_eruptions": (
        "lumps on skin", "skin nodules", "raised bumps on skin",
        "त्वचा पर गांठ", "कातडीवर गाठी",
    ),
    "continuous_sneezing": (
        "sneezing", "continuous sneezing", "keep sneezing", "sneezing a lot",
        "छींक", "छींक आ रही", "शिंका", "शिंका येत",
        "chheenk", "shinka",
    ),
    "shivering": (
        "shivering", "shivers", "trembling",
        "कंपकंपी", "थरथराहट", "हुडहुडी",
        "kampkampi", "thartharat",
    ),
    "chills": (
        "chills", "feeling cold", "cold shivers",
        "ठंड लग रही", "सर्दी लग रही", "थंडी वाजते",
        "thand lag rahi", "thandi vajte",
    ),
    "joint_pain": (
        "joint pain", "pain in joints", "aching joints", "arthritis pain",
        "जोड़ों में दर्द", "जोड़ दर्द", "सांध्यांमध्ये वेदना", "सांधेदुखी",
        "jodo me dard", "sandhedukhi",
    ),
    "stomach_pain": (
        "stomach pain", "stomach ache", "tummy pain", "pain in stomach",
        "पेट में दर्द", "पेट दर्द", "पोटात दुखणे", "पोटदुखी",
        "pet me dard", "pet dard", "potdukhi",
    ),
    "acidity": (
        "acidity", "acid reflux", "heartburn", "sour burps", "burning in chest after eating",
        "एसिडिटी", "खट्टी डकार", "छाती में जलन", "आंबट ढेकर",
        "acidity", "khatti dakar",
    ),
    "ulcers_on_tongue": (
        "mouth ulcers", "ulcers on tongue", "sores in mouth", "tongue sores",
        "मुंह के छाले", "जीभ पर छाले", "तोंड आले", "जिभेवर फोड",
        "muh ke chhale",
    ),
    "muscle_wasting": (
        "muscle wasting", "muscles shrinking", "losing muscle",
        "मांसपेशी क्षय", "स्नायू झिजणे",
    ),
    "vomiting": (
        "vomiting", "vomit", "throwing up", "puking",
        "उल्टी", "वमन", "ओकारी", "उलटी हो रही",
        "ulti", "vomiting ho rahi", "okari",
    ),
    "burning_micturition": (
        "burning urination", "burning while urinating", "burning when passing urine",
        "pain while urinating", "burning sensation while peeing",
        "पेशाब में जलन", "मूत्र त्याग में जलन", "लघवी करताना जळजळ",
        "peshab me jalan", "laghvi jaljal",
    ),
    "spotting_ urination": (
        "spotting while urinating", "blood spots in urine",
        "पेशाब में खून के धब्बे",
    ),
    "fatigue": (
        "fatigue", "tired", "tiredness", "exhausted", "no energy", "feeling drained",
        "थकान", "थकावट", "कमजोरी महसूस", "थकवा", "दमल्यासारखे",
        "thakan", "thakva", "thak gaya",
    ),
    "weight_gain": (
        "weight gain", "gaining weight", "put on weight",
        "वजन बढ़ना", "वजन वाढणे",
        "wajan badhna",
    ),
    "anxiety": (
        "anxiety", "anxious", "worried all the time", "panic", "nervousness",
        # bechaini is unease and belongs to restlessness alone.
        "घबराहट", "चिंता", "अस्वस्थता",
        "ghabrahat",
    ),
    "cold_hands_and_feets": (
        "cold hands and feet", "hands and feet feel cold",
        "हाथ पैर ठंडे", "हात पाय थंड",
        "hath pair thande",
    ),
    "mood_swings": (
        "mood swings", "mood changes", "irritable then sad",
        "मनोदशा में बदलाव", "मूड बदलना", "मनःस्थिती बदलते",
    ),
    "weight_loss": (
        "weight loss", "losing weight", "lost weight",
        "वजन कम होना", "वजन घटना", "वजन कमी होणे",
        "wajan kam", "wajan ghat",
    ),
    "restlessness": (
        "restlessness", "restless", "cannot sit still",
        "बेचैनी", "अशांति", "अस्वस्थ वाटते", "bechaini",
    ),
    "lethargy": (
        "lethargy", "lethargic", "sluggish", "no motivation to move",
        "सुस्ती", "आलस", "सुस्तपणा",
        "susti", "aalas",
    ),
    "patches_in_throat": (
        "patches in throat", "white patches in throat", "spots in throat",
        "गले में सफेद धब्बे", "घशात पांढरे डाग",
    ),
    "irregular_sugar_level": (
        "irregular sugar", "sugar level fluctuating", "blood sugar up and down",
        "शुगर ऊपर नीचे", "साखर कमी जास्त",
    ),
    "cough": (
        "cough", "coughing", "dry cough", "chest cough",
        "खांसी", "खोकला", "खांसी आ रही",
        "khansi", "khokla",
    ),
    "high_fever": (
        "high fever", "high temperature", "burning up", "fever of 102", "strong fever",
        "तेज बुखार", "ज्यादा बुखार", "जास्त ताप", "तीव्र ताप",
        "tez bukhar", "jast tap",
    ),
    "mild_fever": (
        "mild fever", "slight fever", "low grade fever", "low fever",
        "हल्का बुखार", "थोड़ा बुखार", "सौम्य ताप", "थोडा ताप",
        "halka bukhar", "thoda tap",
    ),
    "sunken_eyes": (
        "sunken eyes", "eyes look hollow",
        "धंसी आंखें", "डोळे खोल गेले",
    ),
    "breathlessness": (
        "breathlessness", "shortness of breath", "cannot breathe properly",
        "difficulty breathing", "out of breath", "breathless",
        "सांस फूलना", "सांस लेने में तकलीफ", "दम लागणे", "श्वास घेण्यास त्रास",
        "saans phoolna", "dam lagto",
    ),
    "sweating": (
        "sweating", "sweaty", "perspiring a lot", "night sweats",
        "पसीना", "पसीना आ रहा", "घाम", "घाम येतो",
        "pasina", "gham",
    ),
    "dehydration": (
        "dehydration", "dehydrated", "very thirsty and dry",
        "निर्जलीकरण", "पानी की कमी", "शरीरातील पाणी कमी",
    ),
    "indigestion": (
        "indigestion", "cannot digest", "heavy after eating", "upset digestion",
        "अपच", "बदहजमी", "अपचन", "पचत नाही",
        "apach", "badhazmi",
    ),
    "headache": (
        "headache", "head pain", "head hurts", "head is aching", "migraine pain",
        "सिरदर्द", "सर दर्द", "सिर में दर्द", "डोकेदुखी", "डोके दुखते",
        "sirdard", "sar dard", "dokedukhi", "doke dukhte",
    ),
    "yellowish_skin": (
        "yellowish skin", "yellow skin", "skin turning yellow", "jaundiced skin",
        "पीली त्वचा", "त्वचा पीली", "पिवळी कातडी",
        "peeli twacha",
    ),
    "dark_urine": (
        "dark urine", "urine is dark", "brown urine",
        "गहरा पेशाब", "गडद लघवी",
    ),
    "nausea": (
        "nausea", "nauseous", "feel like vomiting", "queasy", "feeling sick",
        "जी मिचलाना", "उबकाई", "मळमळ", "उलटी जैसा लग रहा",
        "ji michal", "malmal", "ubkai",
    ),
    "loss_of_appetite": (
        "loss of appetite", "no appetite", "not hungry", "cannot eat",
        "भूख नहीं", "भूख कम", "भूख न लगना", "भूक लागत नाही",
        "bhookh nahi", "bhuk lagat nahi",
    ),
    "pain_behind_the_eyes": (
        "pain behind the eyes", "pain behind eyes", "eyes hurt from behind",
        "आंखों के पीछे दर्द", "डोळ्यांच्या मागे दुखणे",
    ),
    "back_pain": (
        "back pain", "backache", "pain in back", "lower back pain",
        "पीठ दर्द", "कमर दर्द", "पाठदुखी", "पाठ दुखते",
        "kamar dard", "peeth dard", "pathdukhi",
    ),
    "constipation": (
        "constipation", "constipated", "cannot pass stool", "hard stool",
        "कब्ज", "कब्ज़", "शौच नहीं", "बद्धकोष्ठता", "शौचास होत नाही",
        "kabz", "kabaj",
    ),
    "abdominal_pain": (
        "abdominal pain", "pain in abdomen", "lower abdomen pain",
        "उदर दर्द", "पेट के निचले हिस्से में दर्द", "ओटीपोटात दुखणे",
    ),
    "diarrhoea": (
        "diarrhoea", "diarrhea", "loose motions", "loose stool", "watery stool",
        "दस्त", "पतले दस्त", "हगवण", "जुलाब",
        "dast", "loose motion", "julab",
    ),
    "yellow_urine": (
        "yellow urine", "urine is yellow",
        "पीला पेशाब", "पिवळी लघवी",
    ),
    "yellowing_of_eyes": (
        "yellowing of eyes", "yellow eyes", "eyes turned yellow",
        "आंखें पीली", "पीली आंखें", "डोळे पिवळे",
        "aankhen peeli",
    ),
    "acute_liver_failure": (
        "liver failure", "acute liver failure",
        "यकृत विफलता", "यकृत निकामी",
    ),
    "swelling_of_stomach": (
        "swelling of stomach", "stomach is swollen", "swollen belly",
        "पेट में सूजन", "पोट सुजले",
    ),
    "swelled_lymph_nodes": (
        "swollen glands", "swollen lymph nodes", "lumps in neck",
        "गिल्टी", "गांठ गले में", "गळ्यात गाठ",
    ),
    "malaise": (
        "malaise", "generally unwell", "not feeling well overall", "feeling off",
        "तबीयत ठीक नहीं", "अस्वस्थ", "बरं वाटत नाही",
        "tabiyat theek nahi",
    ),
    "blurred_and_distorted_vision": (
        "blurred vision", "blurry vision", "distorted vision", "cannot see clearly",
        "धुंधला दिखना", "नजर धुंधली", "अंधुक दिसते", "दृष्टी अंधुक",
        "dhundhla dikhna",
    ),
    "phlegm": (
        "phlegm", "mucus when coughing", "cough with phlegm",
        "कफ", "बलगम", "कफ येतो",
        "kaf", "balgam",
    ),
    "throat_irritation": (
        "throat irritation", "scratchy throat", "throat feels rough", "sore throat",
        "गले में खराश", "गला खराब", "घसा खवखवतो",
        "gale me kharash", "gala kharab",
    ),
    "redness_of_eyes": (
        "red eyes", "redness of eyes", "eyes are red",
        "लाल आंखें", "आंखें लाल", "डोळे लाल",
        "aankhen lal",
    ),
    "sinus_pressure": (
        "sinus pressure", "pressure in sinuses", "face feels heavy",
        "साइनस दबाव", "चेहरे में भारीपन",
    ),
    "runny_nose": (
        "runny nose", "nose running", "watery nose", "cold with running nose",
        "नाक बहना", "नाक बह रही", "नाक वाहते", "सर्दी",
        "naak behna", "naak vahte",
    ),
    "congestion": (
        "blocked nose", "nasal congestion", "stuffy nose", "cannot breathe through nose",
        "नाक बंद", "नाक जाम", "नाक चोंदली",
        "naak band",
    ),
    "chest_pain": (
        "chest pain", "pain in chest", "chest hurts", "tightness in chest",
        "सीने में दर्द", "छाती में दर्द", "छातीत दुखणे", "छाती दुखते",
        "seene me dard", "chhati me dard", "chhati dukhte",
    ),
    "weakness_in_limbs": (
        "weakness in limbs", "arms and legs feel weak", "weak arms and legs",
        "हाथ पैर में कमजोरी", "हात पाय अशक्त",
    ),
    "fast_heart_rate": (
        "fast heart rate", "heart beating fast", "racing heart", "rapid heartbeat",
        "दिल तेज धड़कना", "धड़कन तेज", "हृदयाचे ठोके जलद",
        "dhadkan tez",
    ),
    "pain_during_bowel_movements": (
        "pain during bowel movement", "pain when passing stool", "hurts to pass stool",
        "शौच में दर्द", "शौचास दुखते",
    ),
    "pain_in_anal_region": (
        "pain in anal region", "pain near anus",
        "गुदा में दर्द", "गुदद्वारात दुखणे",
    ),
    "bloody_stool": (
        "blood in stool", "bloody stool", "blood when passing stool",
        "मल में खून", "शौच में खून", "शौचातून रक्त",
        "mal me khoon",
    ),
    "irritation_in_anus": (
        "irritation in anus", "itching near anus",
        "गुदा में जलन", "गुदद्वारात जळजळ",
    ),
    "neck_pain": (
        "neck pain", "pain in neck", "neck hurts",
        "गर्दन दर्द", "गर्दन में दर्द", "मानदुखी", "मान दुखते",
        "gardan dard", "mandukhi",
    ),
    "dizziness": (
        "dizziness", "dizzy", "light headed", "feel faint", "head spinning",
        "चक्कर", "चक्कर आ रहे", "गरगरणे", "चक्कर येते",
        "chakkar", "gargar",
    ),
    "cramps": (
        "cramps", "cramping", "muscle cramps",
        "ऐंठन", "मरोड़", "पेटके", "गोळे येणे",
        "ainthan", "petke",
    ),
    "bruising": (
        "bruising", "bruises", "black and blue marks",
        "नील पड़ना", "चोट के निशान", "काळे निळे डाग",
    ),
    "obesity": (
        "obesity", "obese", "very overweight",
        "मोटापा", "स्थूलता", "लठ्ठपणा",
        "motapa",
    ),
    "swollen_legs": (
        "swollen legs", "legs are swollen", "swelling in legs",
        "पैरों में सूजन", "पाय सुजले", "पायाला सूज",
        "pairon me soojan",
    ),
    "swollen_blood_vessels": (
        "swollen blood vessels", "veins are swollen",
        "नसों में सूजन", "रक्तवाहिन्या सुजल्या",
    ),
    "puffy_face_and_eyes": (
        "puffy face", "swollen face and eyes", "face is puffy",
        "चेहरे पर सूजन", "चेहरा सुजला",
        "chehre par soojan",
    ),
    "enlarged_thyroid": (
        "enlarged thyroid", "swelling in front of neck", "thyroid swelling",
        "थायराइड सूजन", "थायरॉईड सूज",
    ),
    "brittle_nails": (
        "brittle nails", "nails breaking easily",
        "नाखून टूटना", "नखे तुटतात",
    ),
    "swollen_extremeties": (
        "swollen hands and feet", "swelling in hands and feet",
        "हाथ पैर में सूजन", "हात पाय सुजले",
    ),
    "excessive_hunger": (
        "excessive hunger", "always hungry", "very hungry all the time",
        "ज्यादा भूख", "भूख बहुत लगती", "जास्त भूक",
        "jyada bhookh",
    ),
    "drying_and_tingling_lips": (
        "dry tingling lips", "lips are dry and tingling", "dry lips",
        "होंठ सूखे", "ओठ कोरडे",
    ),
    "slurred_speech": (
        "slurred speech", "speech is slurred", "cannot speak clearly",
        "बोलने में लड़खड़ाहट", "अस्पष्ट बोली", "बोलणे अस्पष्ट",
    ),
    "knee_pain": (
        "knee pain", "pain in knee", "knees hurt",
        "घुटने में दर्द", "घुटना दर्द", "गुडघेदुखी", "गुडघा दुखतो",
        "ghutne me dard", "gudghedukhi",
    ),
    "hip_joint_pain": (
        "hip pain", "hip joint pain", "pain in hip",
        "कूल्हे में दर्द", "कंबरेच्या सांध्यात दुखणे",
    ),
    "muscle_weakness": (
        "muscle weakness", "muscles feel weak",
        "मांसपेशियों में कमजोरी", "स्नायू अशक्त",
    ),
    "stiff_neck": (
        "stiff neck", "neck is stiff", "cannot turn neck",
        "गर्दन अकड़ना", "मान अवघडली",
        "gardan akad",
    ),
    "swelling_joints": (
        "swollen joints", "swelling in joints", "joints are swollen",
        "जोड़ों में सूजन", "सांधे सुजले",
    ),
    "movement_stiffness": (
        "stiffness when moving", "stiff movement", "body feels stiff",
        "चलने में अकड़न", "हालचालीत ताठरता",
    ),
    "spinning_movements": (
        "room is spinning", "spinning sensation", "vertigo",
        "सब घूमता", "गरगरत आहे",
    ),
    "loss_of_balance": (
        "loss of balance", "cannot balance", "keep losing balance",
        "संतुलन खोना", "तोल जातो",
    ),
    "unsteadiness": (
        "unsteady", "unsteadiness", "wobbly when walking",
        "लड़खड़ाना", "अस्थिरता", "डळमळणे",
    ),
    "weakness_of_one_body_side": (
        "weakness on one side", "one side of body weak", "left side weak",
        "right side weak",
        "एक तरफ कमजोरी", "शरीराची एक बाजू अशक्त",
    ),
    "loss_of_smell": (
        "loss of smell", "cannot smell", "no sense of smell",
        "सूंघने की शक्ति", "गंध नहीं आ रही", "वास येत नाही",
    ),
    "bladder_discomfort": (
        "bladder discomfort", "discomfort in bladder",
        "मूत्राशय में तकलीफ", "मूत्राशयात अस्वस्थता",
    ),
    "foul_smell_of urine": (
        "foul smelling urine", "urine smells bad", "bad smell in urine",
        "पेशाब में बदबू", "लघवीला दुर्गंध",
        "peshab me badbu",
    ),
    "continuous_feel_of_urine": (
        "constant urge to urinate", "always feel like urinating",
        "feeling of urine all the time",
        "बार बार पेशाब का मन", "सतत लघवीची भावना",
    ),
    "passage_of_gases": (
        "passing gas", "lot of gas", "flatulence",
        "गैस निकलना", "गैस बनना", "गॅस होतो",
        "gas ban rahi",
    ),
    "internal_itching": (
        "internal itching", "itching inside",
        "अंदरूनी खुजली", "आतून खाज",
    ),
    "depression": (
        "depression", "depressed", "feeling hopeless", "low mood for weeks",
        "अवसाद", "उदासी", "मन उदास", "नैराश्य",
        "udasi",
    ),
    "irritability": (
        "irritability", "irritable", "short tempered", "getting angry easily",
        "चिड़चिड़ापन", "गुस्सा जल्दी", "चिडचिड",
        "chidchidapan",
    ),
    "muscle_pain": (
        "muscle pain", "body ache", "body pain", "muscles ache", "aching all over",
        "बदन दर्द", "शरीर में दर्द", "मांसपेशी दर्द", "अंगदुखी", "अंग दुखते",
        "badan dard", "sharir me dard", "angdukhi",
    ),
    "altered_sensorium": (
        "confused", "confusion", "not making sense", "disoriented",
        "भ्रम", "होश में नहीं", "गोंधळलेले",
    ),
    "red_spots_over_body": (
        "red spots on body", "red spots all over", "red dots on skin",
        "शरीर पर लाल दाने", "लाल चकत्ते", "अंगावर लाल ठिपके",
        "lal dane",
    ),
    "belly_pain": (
        "belly pain", "pain in belly",
        "पेट में मरोड़", "पोटात कळ",
    ),
    "abnormal_menstruation": (
        "irregular periods", "abnormal periods", "heavy periods", "missed periods",
        "अनियमित मासिक", "महावारी अनियमित", "पाळी अनियमित",
        "periods irregular",
    ),
    "dischromic _patches": (
        "discoloured patches", "light patches on skin", "white patches on skin",
        "त्वचा के रंग के धब्बे", "कातडीवर पांढरे डाग",
    ),
    "watering_from_eyes": (
        "watery eyes", "eyes watering", "tears keep coming",
        "आंखों से पानी", "डोळ्यातून पाणी",
        "aankhon se pani",
    ),
    "increased_appetite": (
        "increased appetite", "eating much more",
        "भूख बढ़ना", "भूक वाढली",
    ),
    "polyuria": (
        "urinating frequently", "passing urine often", "frequent urination",
        "बार बार पेशाब", "वारंवार लघवी",
        "bar bar peshab",
    ),
    "mucoid_sputum": (
        "thick sputum", "mucoid sputum", "sticky phlegm",
        "गाढ़ा कफ", "घट्ट कफ",
    ),
    "rusty_sputum": (
        "rusty sputum", "brown phlegm", "rust coloured sputum",
        "जंग रंग का कफ",
    ),
    "lack_of_concentration": (
        "cannot concentrate", "lack of concentration", "trouble focusing",
        "ध्यान नहीं लगता", "एकाग्रता नाही",
    ),
    "visual_disturbances": (
        "visual disturbance", "seeing spots", "vision problems",
        "दृष्टि में गड़बड़", "दृष्टीत अडथळा",
    ),
    "coma": ("unconscious", "not waking up", "बेहोश", "बेशुद्ध"),
    "stomach_bleeding": (
        "stomach bleeding", "vomiting blood",
        "पेट से खून", "रक्ताची उलटी",
    ),
    "distention_of_abdomen": (
        "bloated abdomen", "abdomen is distended", "stomach feels bloated",
        "पेट फूलना", "पोट फुगले",
        "pet phool",
    ),
    "fluid_overload": ("fluid retention", "water retention", "शरीर में पानी भरना"),
    "blood_in_sputum": (
        "blood in sputum", "coughing blood", "blood when coughing",
        "खांसी में खून", "कफात रक्त",
        "khansi me khoon",
    ),
    "prominent_veins_on_calf": (
        "prominent veins on calf", "veins showing on legs", "varicose veins",
        "पिंडली में नसें", "पोटरीवर शिरा",
    ),
    "palpitations": (
        "palpitations", "heart fluttering", "feel my heartbeat",
        "धड़कन महसूस", "धडधड",
        "dhadkan",
    ),
    "painful_walking": (
        "painful walking", "hurts to walk", "pain when walking",
        "चलने में दर्द", "चालताना दुखते",
        "chalne me dard",
    ),
    "pus_filled_pimples": (
        "pus filled pimples", "pimples with pus", "boils with pus",
        "मवाद वाले दाने", "पू भरलेले पुरळ",
    ),
    "blackheads": ("blackheads", "काले दाने", "काळे ठिपके"),
    "skin_peeling": (
        "skin peeling", "skin is peeling off", "flaking skin",
        "त्वचा उतरना", "कातडी सोलते",
    ),
    "small_dents_in_nails": (
        "dents in nails", "small pits in nails",
        "नाखून में गड्ढे", "नखांवर खड्डे",
    ),
    "inflammatory_nails": (
        "inflamed nails", "nails are swollen and sore",
        "नाखून में सूजन", "नखांना सूज",
    ),
    "blister": ("blister", "blisters", "फफोला", "छाला", "फोड"),
    "red_sore_around_nose": (
        "sore around nose", "red sore near nose",
        "नाक के पास घाव", "नाकाजवळ जखम",
    ),
    "yellow_crust_ooze": (
        "yellow crust", "yellow ooze", "crusting with yellow discharge",
        "पीली पपड़ी", "पिवळी खपली",
    ),
}


# Phrases that point at more than one thing, so a match on them is weaker.
BROAD_PHRASES = frozenset(
    {
        "pain", "fever", "cold", "सर्दी", "tired", "weak", "sick",
        "feeling sick", "malaise", "dhadkan", "gas ban rahi",
    }
)

# Phrases that name a real symptom without naming which one, where the
# vocabulary forces a choice the patient did not make. CLINICAL-REVIEW-NEEDED.
#
# "Fever" is the case that matters: it is among the most common things a patient
# volunteers, and the vocabulary has only ``mild_fever`` and ``high_fever``.
# Picking one would invent a grade nobody stated - and the choice is not neutral,
# because it changes which diseases match, not just the urgency band. Dropping it
# silently is no better: the most common complaint in the clinic would vanish
# from the draft.
#
# So it is neither matched nor dropped. It is reported as a question, which the
# clinician answers in the one place a grade can legitimately be decided.
AMBIGUOUS: dict[str, tuple[str, ...]] = {
    "fever": ("mild_fever", "high_fever"),
    "temperature": ("mild_fever", "high_fever"),
    "बुखार": ("mild_fever", "high_fever"),
    "ताप": ("mild_fever", "high_fever"),
    "bukhar": ("mild_fever", "high_fever"),
    "taap": ("mild_fever", "high_fever"),
}


# Negators. A match is dropped when one of these appears in the few words
# before it. CLINICAL-REVIEW-NEEDED, and see the module docstring on the limits.
PRE_NEGATORS = frozenset(
    {
        "no", "not", "never", "without", "denies", "negative",
        "बिना", "विना",
    }
)

# English puts the negator first ("no fever"). Hindi and Marathi are verb-final
# and put it after ("khansi nahi hai"). Checking only one side meant negation
# worked in English and silently failed in the two languages this feature exists
# for. Checking both sides for every negator would be worse: "I have a headache.
# No fever." would then deny the headache too.
POST_NEGATORS = frozenset(
    {"nahi", "nahin", "naahi", "नहीं", "ना", "नाही", "नको"}
)

#: How many words either side of a phrase a negator still applies to it.
NEGATION_WINDOW = 3


# ---------------------------------------------------------------------------
# Duration
# ---------------------------------------------------------------------------
# The intake form asks how many days, and someone describing their problem
# usually says it unprompted. Reading it here means the patient is not asked
# twice, and `days` already feeds the urgency rules.

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "half": 1,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "panch": 5, "chhe": 6, "saat": 7,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाच": 5, "छह": 6, "सात": 7,
    "दोन": 2,
}

DAY_WORDS = ("day", "days", "din", "दिन", "दिवस")
WEEK_WORDS = ("week", "weeks", "hafte", "hafta", "सप्ताह", "हफ्ते", "आठवडा", "आठवडे")
MONTH_WORDS = ("month", "months", "mahine", "महीने", "महिना", "महिने")

#: Longer than this and "how many days" stops being the right question; the
#: field is a PositiveSmallIntegerField and a year of symptoms is a different
#: conversation. Capped rather than rejected so the draft still saves.
MAX_DAYS = 365


def extract_duration(text: str) -> int | None:
    """Days the symptoms have lasted, or None when nothing was said.

    Returns the **longest** duration mentioned, not the first: "a headache for
    two days, itching for a week" is a week-long episode and the urgency rules
    should see the larger number.
    """
    normalised = _normalise(text)
    best: int | None = None

    for count, unit in _duration_candidates(normalised):
        if unit in DAY_WORDS:
            days = count
        elif unit in WEEK_WORDS:
            days = count * 7
        elif unit in MONTH_WORDS:
            days = count * 30
        else:
            continue
        days = min(days, MAX_DAYS)
        if best is None or days > best:
            best = days

    return best


def _duration_candidates(normalised: str):
    units = DAY_WORDS + WEEK_WORDS + MONTH_WORDS
    unit_pattern = "|".join(re.escape(u) for u in units)
    number_pattern = "|".join(re.escape(w) for w in NUMBER_WORDS)

    # "4 days", "four days", "चार दिन"
    for match in re.finditer(
        rf"(\d{{1,3}}|{number_pattern})\s*({unit_pattern})", normalised
    ):
        raw, unit = match.group(1), match.group(2)
        count = int(raw) if raw.isdigit() else NUMBER_WORDS.get(raw, 1)
        yield count, unit

    # "since yesterday", "from yesterday" - one day, and common enough to read.
    if re.search(r"\b(yesterday|kal se|कल से|कालपासून)\b", normalised):
        yield 1, "day"
    if re.search(r"\b(today|since (?:this )?morning|aaj se|आज से|आजपासून)\b", normalised):
        yield 1, "day"


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Lowercase, NFC-normalise, and collapse whitespace and punctuation.

    NFC matters for Devanagari: the same word typed on two keyboards can differ
    by combining-mark composition and would otherwise not compare equal.
    """
    text = unicodedata.normalize("NFC", str(text or "")).lower()
    # Keep Devanagari and Latin letters, digits and spaces; drop the rest.
    text = re.sub(r"[^\wऀ-ॿ\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _confidence_for(phrase: str) -> float:
    if phrase in BROAD_PHRASES:
        return BROAD
    return SPECIFIC if " " in phrase else CLEAR


def _is_negated(haystack: str, start: int, end: int | None = None) -> bool:
    """Whether a negator near this phrase denies it.

    Looks before for the negators that precede their target and after for the
    ones that follow it - see :data:`POST_NEGATORS`. ``end`` is optional so a
    caller holding only a start position still gets the English behaviour.

    **A negator belongs to the nearest symptom, so both scans stop when they
    reach another one.** Without that, "khaj ani angavar pural aahe, taap nahi"
    - itching and a rash, no fever - loses the rash, because the trailing
    negator falls inside a fixed window three words wide. Dropping a symptom the
    patient did report is a worse failure than the one this whole mechanism
    exists to prevent, and it was found by running the flow rather than by a
    test, which is why the walkthrough is worth doing.
    """
    boundary = _symptom_tokens()

    preceding = haystack[:start].split()
    for word in reversed(preceding[-NEGATION_WINDOW:]):
        if word in PRE_NEGATORS:
            return True
        if word in boundary:
            break

    if end is None:
        return False

    for word in haystack[end:].split()[:NEGATION_WINDOW]:
        if word in POST_NEGATORS:
            return True
        if word in boundary:
            break

    return False


@lru_cache(maxsize=1)
def _symptom_tokens() -> frozenset[str]:
    """Every word that appears in any known phrase.

    Used as a clause boundary for negation: if one of these sits between a
    phrase and a negator, the negator is about that one instead. Cached because
    it is derived from module-level tables that never change at runtime.
    """
    tokens: set[str] = set()
    for phrases in PHRASES.values():
        for phrase in phrases:
            tokens.update(_normalise(phrase).split())
    for phrase in AMBIGUOUS:
        tokens.update(_normalise(phrase).split())
    return frozenset(tokens)


def match(text: str, *, vocabulary: list[str]) -> tuple[list[Match], list[str]]:
    """Canonical symptoms the text points at, and the phrases left over.

    ``vocabulary`` is the model's feature list. A phrase whose canonical symptom
    is not in it is ignored, so this can never propose a symptom the model would
    reject - the lexicon and the artefact can be updated independently without
    producing an invalid draft in between.

    Longest phrase first, so "chest pain" is not read as "pain", and a region of
    the text that has already produced a match is not reused - one phrase means
    one symptom.
    """
    known = set(vocabulary)
    haystack = _normalise(text)
    if not haystack:
        return [], []

    candidates = sorted(
        (
            (phrase, symptom)
            for symptom, phrases in PHRASES.items()
            if symptom in known
            for phrase in phrases
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )

    matches: list[Match] = []
    seen: set[str] = set()
    # Character spans already consumed by a longer phrase.
    consumed: list[tuple[int, int]] = []

    for phrase, symptom in candidates:
        if symptom in seen:
            continue
        needle = _normalise(phrase)
        if not needle:
            continue

        for found in re.finditer(_boundary_pattern(needle), haystack):
            span = (found.start(), found.end())
            if any(span[0] < end and start < span[1] for start, end in consumed):
                continue
            if _is_negated(haystack, span[0], span[1]):
                # Consumed anyway: "no fever" should not then match "fever" on
                # a shorter phrase at the same place.
                consumed.append(span)
                continue

            matches.append(
                Match(
                    symptom=symptom,
                    evidence=phrase,
                    confidence=_confidence_for(needle),
                )
            )
            seen.add(symptom)
            consumed.append(span)
            break

    matches.sort(key=lambda m: (-m.confidence, m.symptom))

    questions = _ambiguous_phrases(haystack, consumed, known=known, found=seen)
    return matches, questions + _leftover_phrases(haystack, consumed)


def _ambiguous_phrases(
    haystack: str, consumed: list[tuple[int, int]], *, known: set[str], found: set[str]
) -> list[str]:
    """Phrases that name a symptom without naming which one.

    Skipped when a graded form already matched - "high fever for two days, mild
    fever since" should not also ask the question it has already answered. Also
    skipped when negated, so "no fever" stays silent.
    """
    questions: list[str] = []

    for phrase, candidates in AMBIGUOUS.items():
        options = [c for c in candidates if c in known]
        if not options or any(c in found for c in options):
            continue

        needle = _normalise(phrase)
        for hit in re.finditer(_boundary_pattern(needle), haystack):
            span = (hit.start(), hit.end())
            if any(span[0] < end and start < span[1] for start, end in consumed):
                continue
            if _is_negated(haystack, span[0], span[1]):
                consumed.append(span)
                continue

            readable = " or ".join(o.replace("_", " ") for o in options)
            questions.append(f"{phrase} - which one? ({readable})")
            consumed.append(span)
            break

    return questions


def _boundary_pattern(needle: str) -> re.Pattern[str]:
    r"""Match ``needle`` as whole words.

    ``\b`` works for Devanagari here because those code points are word
    characters, so a boundary still falls at a space or at the string edge. It
    does not segment a Devanagari compound - which is a real limitation, and the
    reason a missed match shows up as an unmapped phrase rather than as silence.
    """
    return re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")


def _leftover_phrases(haystack: str, consumed: list[tuple[int, int]]) -> list[str]:
    """Runs of text no phrase claimed, as the backlog for this file.

    Filtered to fragments with some substance: a leftover list full of "and",
    "i", "for" is noise that would bury the useful entries.
    """
    if not haystack:
        return []

    spans = sorted(consumed)
    leftovers: list[str] = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            leftovers.append(haystack[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(haystack):
        leftovers.append(haystack[cursor:])

    return [
        fragment
        for fragment in (f.strip() for f in leftovers)
        if len(fragment) >= 4 and _has_content(fragment)
    ]


STOPWORDS = frozenset(
    {
        "i", "have", "has", "had", "am", "is", "are", "was", "were", "been",
        "my", "me", "mine", "the", "a", "an", "and", "or", "but", "for", "of",
        "in", "on", "at", "to", "from", "with", "since", "there", "it", "this",
        "that", "very", "also", "been", "feel", "feeling", "felt", "get",
        "getting", "got", "some", "lot", "much", "days", "day", "week", "weeks",
        "mujhe", "hai", "ho", "raha", "rahi", "se", "mera", "meri", "aur",
        "मुझे", "है", "हो", "रहा", "रही", "और", "मेरा", "मेरी", "से", "मला",
        "आहे", "आहेत", "आणि", "माझा", "माझी", "पासून", "दिवसांपासून", "पण",
        "but", "no", "not", "since", "about", "around", "last", "today",
        "yesterday", "morning", "night", "bit", "hard", "explain",
    }
    # Duration words are not lexicon gaps: extract_duration already reads them,
    # and leaving them here made every unmapped list start with "for four days
    # and a". The backlog is only useful if it contains phrases somebody should
    # actually add.
    | set(NUMBER_WORDS)
    | set(DAY_WORDS)
    | set(WEEK_WORDS)
    | set(MONTH_WORDS)
    # Negators are grammar, not gaps. A leftover list containing "nahi" invites
    # someone to add it to the phrase table, which is the opposite of correct.
    | set(PRE_NEGATORS)
    | set(POST_NEGATORS)
)


def _has_content(fragment: str) -> bool:
    words = [w for w in fragment.split() if w not in STOPWORDS and not w.isdigit()]
    return bool(words)


def coverage() -> dict[str, int]:
    """How much of the vocabulary this file covers. Read by ``ai_status``.

    A number worth being able to print, because the honest description of this
    component is "a curated subset", and a subset whose size nobody knows tends
    to be described as complete.
    """
    return {
        "symptoms_covered": len(PHRASES),
        "phrases": sum(len(p) for p in PHRASES.values()),
    }
