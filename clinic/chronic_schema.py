"""Classical homoeopathic chronic case-taking format.

Drives the data-entry form, the read-only view and the print-out, so adding a field here is all it takes.
Field kinds: text (one line) · area (several lines) · choice (list of options).
"""
THERMAL = ["Hot", "Chilly", "Ambithermal"]
THIRST = ["Thirstless", "Normal", "Thirsty", "Large quantities, long intervals", "Small quantities, frequent sips"]
APPETITE = ["Good", "Poor", "Ravenous", "Variable", "Aversion to food"]
DIET = ["Vegetarian", "Non-vegetarian", "Eggetarian", "Mixed"]
SIDE = ["Right", "Left", "Both / alternating"]
TEMPERAMENT = ["Sanguine", "Choleric", "Melancholic", "Phlegmatic", "Mixed"]
CONSOLATION = ["Ameliorates", "Aggravates", "Indifferent"]
COMPANY = ["Desires company", "Desires solitude", "Indifferent"]
MIASM = ["Psora", "Sycosis", "Syphilis", "Tubercular", "Psora + Sycosis", "Mixed / undetermined"]

SECTIONS = [
    ("complaints", "1 · Presenting complaints", [
        ("chief_complaints", "Chief complaints — in order of importance, with duration", "area"),
        ("onset_causation", "Onset & causation (ailments from…)", "area"),
        ("location_sensation", "Location · sensation · extension · direction", "area"),
        ("modalities_agg", "Modalities — aggravation (<): time, weather, position, motion, food…", "area"),
        ("modalities_amel", "Modalities — amelioration (>)", "area"),
        ("concomitants", "Concomitants (accompanying symptoms)", "area"),
        ("hpi", "History of present illness · progress · treatment taken so far", "area"),
    ]),
    ("past", "2 · Past history", [
        ("childhood_illnesses", "Childhood illnesses", "area"),
        ("past_illnesses", "Previous illnesses", "area"),
        ("surgeries_injuries", "Surgeries · injuries · accidents", "area"),
        ("vaccination", "Vaccination history & reactions", "text"),
        ("suppressions", "Suppressions — skin eruptions, discharges, steroids, long-term drugs", "area"),
        ("allergies", "Allergies · drug reactions", "text"),
    ]),
    ("family", "3 · Family history", [
        ("family_parents", "Parents (health / cause of death)", "text"),
        ("family_siblings", "Siblings · children", "text"),
        ("family_diseases", "Hereditary diseases — TB, diabetes, hypertension, cancer, asthma, skin, mental…", "area"),
    ]),
    ("personal", "4 · Personal history & physical generals", [
        ("occupation", "Occupation · working conditions", "text"),
        ("environment", "Residence · living conditions · social / marital status", "text"),
        ("habits", "Habits & addictions — tea, coffee, tobacco, alcohol…", "text"),
        ("diet", "Diet", "choice", DIET),
        ("appetite", "Appetite", "choice", APPETITE),
        ("thirst", "Thirst", "choice", THIRST),
        ("desires", "Desires", "area"),
        ("aversions", "Aversions", "area"),
        ("intolerances", "Aggravated by / intolerances (foods)", "text"),
        ("stool", "Stool — frequency, consistency, sensation", "text"),
        ("urine", "Urine — frequency, colour, sensation", "text"),
        ("sweat", "Perspiration — site, amount, odour, stains", "text"),
        ("thermal", "Thermal reaction", "choice", THERMAL),
        ("side_affinity", "Side affinity", "choice", SIDE),
        ("sleep", "Sleep — quality, refreshing?", "text"),
        ("sleep_position", "Sleep position", "text"),
        ("dreams", "Dreams", "area"),
        ("discharges", "Other discharges", "text"),
    ]),
    ("menstrual", "5 · Menstrual & obstetric history (if applicable)", [
        ("menarche", "Menarche · cycle · duration", "text"),
        ("flow", "Flow · colour · clots · pain · pre/post-menstrual symptoms", "area"),
        ("lmp", "LMP", "text"),
        ("leucorrhoea", "Leucorrhoea", "text"),
        ("obstetric", "Obstetric history — G P L A, deliveries, complications", "area"),
        ("menopause", "Menopause · climacteric symptoms", "text"),
    ]),
    ("mind", "6 · Mental generals", [
        ("temperament", "Temperament", "choice", TEMPERAMENT),
        ("disposition", "Disposition · mood · nature", "area"),
        ("fears", "Fears · anxieties", "area"),
        ("anger", "Anger · irritability · jealousy · other emotions", "area"),
        ("mental_causation", "Mental causation — grief, fright, disappointment, humiliation…", "area"),
        ("consolation", "Consolation", "choice", CONSOLATION),
        ("company", "Company / solitude", "choice", COMPANY),
        ("memory", "Memory · concentration · intellect", "text"),
        ("mind_other", "Other mental symptoms (delusions, habits, peculiarities)", "area"),
    ]),
    ("particulars", "7 · Particulars — system review", [
        ("p_head", "Head · vertigo · headache", "area"),
        ("p_eyes_ears", "Eyes · ears", "area"),
        ("p_nose_throat", "Nose · throat · mouth · teeth", "area"),
        ("p_chest", "Respiratory · cardiovascular", "area"),
        ("p_abdomen", "Stomach · digestion · abdomen", "area"),
        ("p_genito_urinary", "Genito-urinary", "area"),
        ("p_back_limbs", "Back · limbs · joints", "area"),
        ("p_skin", "Skin · nails · hair", "area"),
    ]),
    ("exam", "8 · Examination", [
        ("built", "Built · nutrition", "text"),
        ("complexion", "Complexion · facies", "text"),
        ("pulse", "Pulse", "text"),
        ("bp", "Blood pressure", "text"),
        ("temperature", "Temperature", "text"),
        ("weight", "Weight", "text"),
        ("height", "Height", "text"),
        ("general_exam", "General examination — anaemia, icterus, oedema, lymph nodes…", "area"),
        ("systemic_exam", "Systemic examination", "area"),
        ("investigations", "Investigations / reports", "area"),
    ]),
    ("analysis", "9 · Analysis & evaluation", [
        ("diagnosis", "Provisional / clinical diagnosis", "text"),
        ("miasm", "Predominant miasm", "choice", MIASM),
        ("gen_summary", "Characteristic generals (mental + physical)", "area"),
        ("part_summary", "Characteristic particulars", "area"),
        ("totality", "Totality of symptoms · evaluation", "area"),
        ("repertorisation", "Repertorisation notes", "area"),
        ("differential", "Differential remedies · Materia Medica comparison", "area"),
    ]),
    ("rx", "10 · Prescription & plan", [
        ("remedy", "Remedy selected", "text"),
        ("potency", "Potency", "text"),
        ("repetition", "Repetition · dose schedule", "text"),
        ("plan", "Plan — diet, regimen, prognosis, things to observe", "area"),
        ("follow_up", "Follow-up date / interval", "text"),
    ]),
]


def fields():
    for key, title, flds in SECTIONS:
        for f in flds:
            yield f if len(f) == 4 else (*f, None)


FIELD_NAMES = [f[0] for f in fields()]
