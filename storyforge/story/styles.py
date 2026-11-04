"""Formats d'histoires, langues et "ADN" aléatoire pour varier les récits à l'infini."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Language:
    code: str
    name: str  # nom pour le prompt
    label: str  # nom affiché
    flag: str
    audience: str  # culture de référence (prénoms, lieux, prix)
    part_word: str  # "PARTIE" sur la vidéo
    wps: float  # mots/seconde d'une voix ElevenLabs à vitesse 1.0


LANGUAGES: dict[str, Language] = {
    "fr": Language("fr", "French", "Français", "🇫🇷", "France (euros, French first names, French towns)", "PARTIE", 2.55),
    "en": Language("en", "English", "English", "🇺🇸", "the United States (dollars, American names and places)", "PART", 2.75),
    "es": Language("es", "Spanish", "Español", "🇪🇸", "Spain and Latin America (euros or local currency, Spanish names)", "PARTE", 2.8),
    "de": Language("de", "German", "Deutsch", "🇩🇪", "Germany (euros, German names and cities)", "TEIL", 2.35),
    "pt": Language("pt", "Brazilian Portuguese", "Português", "🇧🇷", "Brazil (reais, Brazilian names and cities)", "PARTE", 2.7),
    "it": Language("it", "Italian", "Italiano", "🇮🇹", "Italy (euros, Italian names and cities)", "PARTE", 2.65),
}


def get_language(code: str) -> Language:
    return LANGUAGES.get(code, LANGUAGES["en"])


@dataclass(frozen=True)
class StoryFormat:
    key: str
    emoji: str
    label: str  # libellé FR pour le dashboard
    brief: str  # consigne pour le modèle
    hook_style: str  # "headline" | "question"
    pitch: str = ""  # résumé en une ligne pour le dashboard


FORMATS: dict[str, StoryFormat] = {
    f.key: f
    for f in [
        StoryFormat(
            "askreddit", "🤣", "Question Reddit",
            "Viral forum-question format. The first sentence is a brand-new AskReddit-style question read aloud "
            "(invent your own, e.g. about kids, coworkers, teachers, exes, weddings, neighbors, customers), "
            "and the narrator immediately answers with their own story. Light, absurd, laugh-out-loud escalation. "
            "The narrator is often a parent, sibling, roommate or coworker witnessing chaos they secretly admire. "
            "End on the legendary aftermath (the person becomes a legend, the rule that got created because of it...).",
            "question",
        ),
        StoryFormat(
            "drama", "💔", "Trahison & drama",
            "Friendship or relationship betrayal with social tension: jealousy, money gap, fake friends, "
            "someone using the narrator. Slow-burn reveals, receipts, a confrontation in public, the villain "
            "exposing themselves. Bittersweet or deliciously satisfying ending.",
            "headline",
        ),
        StoryFormat(
            "revenge", "😈", "Vengeance",
            "Petty or pro revenge. Someone wrongs the narrator, the narrator stays calm, plans something clever, "
            "legal and harmless, and the payback lands perfectly in front of everyone. Make the villain truly "
            "insufferable so the payoff feels earned.",
            "headline",
        ),
        StoryFormat(
            "aita", "⚖️", "Ai-je tort ?",
            "Moral-dilemma format. The hook is 'Am I wrong for ...?' (written in full, in the target language). "
            "Tell the conflict so that viewers will argue in the comments: the narrator has a strong point but did "
            "something bold. This is the ONLY format allowed to end with a short question to the viewer.",
            "question",
        ),
        StoryFormat(
            "entitled", "🙄", "Gens insupportables",
            "Entitled people: a customer, neighbor, parent, homeowners association president or influencer who "
            "thinks rules don't apply to them. Escalating demands, absurd confidence, then a perfect humbling "
            "(manager, police, their own family, a rule they ignored).",
            "headline",
        ),
        StoryFormat(
            "workplace", "💼", "Histoires de boulot",
            "Workplace story: toxic boss, credit-stealing coworker, malicious compliance, quitting in style, "
            "a customer from hell. Specific job details make it believable. The power dynamic flips at the end.",
            "headline",
        ),
        StoryFormat(
            "family", "👨‍👩‍👧", "Secrets de famille",
            "Family drama: favoritism, inheritance, a wedding gone wrong, a hidden sibling, a DNA test, "
            "a parent's secret. Emotional stakes, a reveal at a family gathering, and a twist nobody saw coming.",
            "headline",
        ),
        StoryFormat(
            "school", "🎒", "École & profs",
            "School story told by a parent or by an adult remembering their school years: principal's office, "
            "an unfair teacher, a legendary prank (harmless), an exam twist. Funny and nostalgic.",
            "question",
        ),
        StoryFormat(
            "karma", "⚡", "Karma instantané",
            "Instant karma: someone rude, arrogant or cruel gets exactly what they deserve within minutes, "
            "in a way that feels like the universe is writing comedy. Several escalating beats before the hit.",
            "headline",
        ),
        StoryFormat(
            "love", "💘", "Love & dating",
            "Dating story: disastrous first date, a meet-cute with a twist, an ex who comes back, a lie that "
            "unravels. Romantic tension, humor, and a surprising last line. Keep it PG-13.",
            "headline",
        ),
        StoryFormat(
            "wholesome", "🥹", "Feel-good",
            "Wholesome story with a twist: kindness to a stranger that comes back years later, a grumpy person "
            "with a secret heart of gold. Start with tension or mystery, end with goosebumps.",
            "headline",
        ),
        StoryFormat(
            "stepfamily", "🏚️", "Belle-famille recomposée",
            "Stepfamily injustice: a new stepparent moves in and pushes the narrator out (their room, their dead "
            "parent's belongings, their money, their place at the table) while favoring their own child, and the "
            "biological parent stays weak. Years of quiet survival, then the narrator discovers what the late parent "
            "protected for them and takes everything back, legally, in front of the whole family.",
            "headline",
        ),
        StoryFormat(
            "inlaws", "👵", "Belle-mère toxique",
            "Mother-in-law (or father-in-law) from hell: she never accepted the narrator, sabotages the wedding, the "
            "baby, the house or the career, and turns relatives against them while the spouse avoids conflict. Receipts "
            "pile up, the spouse finally sees it, and she is exposed at the event she cared about most.",
            "headline",
        ),
        StoryFormat(
            "cheating", "💍", "Infidélité démasquée",
            "Cheating partner or fiance: an ordinary detail (a notification, a contact name, a receipt, a second phone) "
            "reveals a betrayal far bigger than expected (several partners, a sibling, a best friend, a double life). "
            "The narrator stays calm, gathers proof, finds the other victims, and exposes everything at the worst "
            "possible moment for the cheater (engagement party, anniversary, family dinner). Clean, legal payback.",
            "headline",
        ),
        StoryFormat(
            "bestfriend", "🐍", "Fausse meilleure amie",
            "Jealous best friend: for years she kept the narrator small ('you're the supportive friend type'), then "
            "when the narrator finally gets something she wanted (a date, a job, a guy, attention) she sabotages it "
            "(texts from the narrator's phone, lies, a smear post). The narrator fixes it quietly, the truth comes out "
            "with screenshots, the friend group splits, and the narrator outgrows her.",
            "headline",
        ),
        StoryFormat(
            "comeback", "🌹", "Humiliation puis revanche",
            "Public humiliation turned around: the narrator is mocked or rejected in front of everyone (a crush, a "
            "classmate, a coworker, a family member), then time passes, roles flip, and the person who laughed needs "
            "the narrator, begs, or finally understands. Can end with sweet revenge or a mature, earned "
            "reconciliation; callback to the object or words of the humiliation.",
            "headline",
        ),
        StoryFormat(
            "entitledfam", "💸", "Famille profiteuse",
            "Entitled family: parents or siblings demand the narrator's money, home, time or career (quit your job to "
            "raise your brother's kids, pay for your sister's wedding, give us your house) and escalate to "
            "manipulation and real wrongdoing (emails to the boss, identity misuse, lies to relatives). The narrator "
            "sets boundaries, documents everything and lets the law and the truth do the rest.",
            "headline",
        ),
        StoryFormat(
            "creepy", "👻", "Creepy (histoire vraie)",
            "Realistic creepy story (no supernatural gore): a stalker-ish neighbor, a strange babysitting night, "
            "a message that shouldn't exist, a rental with a locked room. Build dread with small wrong details. "
            "End on a chilling final line.",
            "headline",
        ),
    ]
}

PITCHES = {
    "askreddit": "Une question type Reddit lue à voix haute, puis une réponse absurde et drôle.",
    "drama": "Trahison entre amis ou en couple, révélations en cascade.",
    "revenge": "On lui fait du tort, il prépare une vengeance maligne et légale.",
    "aita": "Dilemme moral « Ai-je tort ? » qui fait débattre en commentaires.",
    "entitled": "Client, voisin ou parent insupportable remis à sa place.",
    "workplace": "Patron toxique, collègue voleur de mérite, démission mémorable.",
    "family": "Héritage, mariage, secret de famille révélé en public.",
    "school": "Bureau du proviseur, prof injuste, blague devenue légendaire.",
    "karma": "Quelqu'un d'odieux reçoit sa leçon en quelques minutes.",
    "love": "Rendez-vous catastrophe, ex qui revient, mensonge qui s'effondre.",
    "wholesome": "Une bonne action qui revient des années plus tard.",
    "creepy": "Histoire inquiétante et réaliste, chute glaçante.",
    "stepfamily": "Belle-mère qui efface l'enfant, le parent défunt avait tout prévu.",
    "inlaws": "Belle-mère qui sabote le couple, démasquée devant toute la famille.",
    "cheating": "Un détail révèle une double vie, la vérité éclate au pire moment.",
    "bestfriend": "Amie jalouse qui sabote en secret, captures d'écran à l'appui.",
    "comeback": "Humilié(e) en public, les rôles s'inversent des mois plus tard.",
    "entitledfam": "Famille qui exige argent ou sacrifices, et finit par tout perdre.",
}
FORMATS = {k: replace(f, pitch=PITCHES.get(k, "")) for k, f in FORMATS.items()}

DEFAULT_FORMATS = ["stepfamily", "inlaws", "cheating", "bestfriend", "entitledfam", "drama", "revenge"]


# --- ADN aléatoire -----------------------------------------------------------
# Chaque « graine » est cohérente : qui fait du tort, ce qu'il fait, et pour quels formats elle convient.
# Le levier secret, la scène de révélation et la conséquence sont tirés à part pour multiplier les combinaisons.

JOBS = [
    "night-shift nurse", "car mechanic", "middle-school teacher", "real-estate assistant", "supermarket cashier",
    "junior accountant", "bartender", "delivery driver", "hairdresser", "IT support technician", "hotel receptionist",
    "bank teller", "flight attendant", "pharmacy assistant", "line cook", "veterinary assistant", "data analyst",
    "university student working weekends", "small bakery owner", "electrician", "dental hygienist", "paralegal",
    "high-school senior with a part-time job", "software developer", "social worker", "physical therapist",
]

PREMISES = [
    ("stepfamily", "a stepmother who moves in and tells the narrator they are 'on their own now', while her own "
                   "daughter gets everything"),
    ("stepfamily", "a stepfather who sells the late mother's belongings and turns her sewing room into his man cave"),
    ("stepfamily", "a stepmother who spends the college fund the late mother left on her own child's hobbies"),
    ("stepfamily", "a step-sister who wears the dead mother's jewelry and mocks the narrator's grief"),
    ("stepfamily", "a father's new wife who 'forgets' the narrator at every family event and cuts them out of photos"),
    ("inlaws", "a mother-in-law who wears white to the wedding and tries to give a speech about the 'real' bride"),
    ("inlaws", "a mother-in-law who tells everyone the baby isn't her son's and demands a DNA test"),
    ("inlaws", "a mother-in-law who secretly redecorates the couple's house with her own key"),
    ("inlaws", "a father-in-law who tries to get the narrator fired so his son will 'come home'"),
    ("inlaws", "a mother-in-law who teaches the grandchild to call her 'mama'"),
    ("cheating", "a fiance who sends the exact same love message to several other women"),
    ("cheating", "a husband who announces he is dating the narrator's sister and expects everyone to adapt"),
    ("cheating", "a husband who skips the anniversary dinner and brings his affair partner to the couple's home"),
    ("cheating", "a boyfriend whose 'work trips' are weekends with a second family"),
    ("cheating", "a wife who empties the joint savings for a lover while planning a vow renewal"),
    ("bestfriend", "a best friend who makes the narrator dress plain so she can shine, then sabotages their date"),
    ("bestfriend", "a best friend who fakes an illness to get sympathy, money and excuses for cruel behavior"),
    ("bestfriend", "a best friend who takes credit for the narrator's business idea and pitches it as her own"),
    ("bestfriend", "a maid of honor who secretly tries to get the narrator's wedding cancelled"),
    ("bestfriend", "a roommate and best friend who reads the narrator's messages and leaks them to the group chat"),
    ("comeback", "a crush who pretends to like the narrator back, then laughs about it in front of the whole class"),
    ("comeback", "a classmate who humiliates the narrator's homemade gift in front of everyone"),
    ("comeback", "a coworker who mocks the narrator's 'cheap' clothes and job at the team party"),
    ("comeback", "a cousin who calls the narrator the family failure at every holiday"),
    ("entitledfam", "a mother who demands the narrator quit a well-paid job to raise a brother's kids"),
    ("entitledfam", "parents who expect the narrator to pay for the golden child's wedding"),
    ("entitledfam", "a sister who moves in 'for two weeks' and tries to take over the narrator's apartment"),
    ("entitledfam", "a brother who opens credit cards in the narrator's name to cover his debts"),
    ("entitledfam", "grandparents who give everything to one grandchild and ask the narrator to sign away the rest"),
    ("drama", "a partner who hides a huge debt and asks the narrator to co-sign a loan"),
    ("drama", "a group of friends who plan a trip using the narrator's card and 'forget' to invite them"),
    ("drama", "a nanny who slowly turns the narrator's child against them"),
    ("drama", "a friend who calls the narrator's partner horrible names, then blames a fake condition"),
    ("revenge", "a neighbor who parks in front of the narrator's garage and gets their car towed instead"),
    ("revenge", "a landlord who keeps the full deposit with fake damage photos"),
    ("revenge", "a boss who steals the narrator's project and presents it to the CEO"),
    ("revenge", "a wedding guest who tries to turn the narrator's wedding into a gender reveal"),
    ("workplace", "a manager who makes the narrator train his nephew, then gives the nephew the promotion"),
    ("workplace", "a coworker who reports the narrator to HR with invented complaints"),
    ("entitled", "a customer who demands a refund for a cake she already ate half of, then calls the narrator's boss"),
    ("entitled", "an HOA president who fines the narrator for a mailbox color he chose himself"),
    ("family", "a father who reveals at Christmas dinner that the inheritance goes to his secret second family"),
    ("family", "parents who skip the narrator's graduation for the sibling's soccer game"),
    ("karma", "a stranger who cuts the line, insults the cashier and turns out to be meeting her for a job interview"),
    ("love", "a date who orders the most expensive dish and 'forgets' his wallet for the third time"),
    ("wholesome", "a grumpy neighbor everyone avoids who turns out to have been protecting the narrator for years"),
    ("aita", "a sister who demands the narrator cancel their own wedding because it is on her birthday weekend"),
    ("askreddit", "a coworker whose lies become so elaborate that the whole office starts playing along"),
    ("school", "a teacher who accuses the narrator's kid of cheating because the essay is 'too good'"),
    ("creepy", "a new neighbor who knows details about the narrator's routine they never shared"),
]

LEVERAGES = [
    "a house deed the late parent quietly put in the narrator's name", "a separate savings account the villain never knew about",
    "screenshots with timestamps", "a reference list with numbers of the villain's past victims",
    "a lease with the narrator's name only", "an old will with a surprise condition", "a voice message saved by accident",
    "a receipt kept in a drawer for years", "a doorbell camera the villain forgot about", "a group chat the narrator was never removed from",
    "a lawyer cousin who reads the fine print", "a grandmother who kept every document", "a coworker who saw everything and kept notes",
    "a joint bank statement", "a notebook the villain wrote in", "the narrator's own calm, documented paper trail",
]

REVEAL_SCENES = [
    "a family dinner", "the engagement party", "the wedding reception", "a holiday gathering", "a 'family meeting' the narrator calls",
    "a team meeting with the CEO present", "a birthday party", "a court hearing", "the reading of a will", "graduation day",
    "a baby shower", "a housewarming",
]

CONSEQUENCES = [
    "the villain loses the house they thought was theirs", "the villain loses their job and reputation",
    "the villain's partner leaves them on the spot", "the friend group and family finally see who they are",
    "the villain has to repay everything", "the villain begs and the narrator calmly says no",
    "the villain is exposed in front of the people whose opinion they cared about most",
    "an earned, bittersweet reconciliation on the narrator's terms",
]

DEVICES = [
    "the villain's catchphrase thrown back at them at the end", "a repeated phrase used three times for rhythm",
    "a self-deprecating aside from the narrator", "a deadpan one-line reaction", "a hyper-specific amount of money that comes back at the end",
    "an object that appears broken at the start and whole at the end", "the narrator counting down the days",
]


LIGHT_FORMATS = {"askreddit", "wholesome", "creepy", "love", "school", "karma"}


@dataclass
class StoryDNA:
    narrator: str
    premise: str
    leverage: str
    reveal: str
    consequence: str
    device: str

    def as_prompt(self) -> str:
        lines = [f"- Narrator: {self.narrator}", f"- Premise: {self.premise}"]
        if self.leverage:
            lines += [f"- Leverage planted early (ordinary at first): {self.leverage}",
                      f"- Where the truth comes out: {self.reveal}", f"- Payoff: {self.consequence}"]
        lines.append(f"- Texture to include once: {self.device}")
        return "\n".join(lines)


def random_dna(narrator_gender: str = "any", rng: random.Random | None = None, fmt: str = "") -> StoryDNA:
    rng = rng or random.Random()
    gender = narrator_gender if narrator_gender in ("male", "female") else rng.choice(["male", "female"])
    age = rng.randint(17, 42)
    who = "man" if gender == "male" else "woman"
    if age < 19:
        who = "boy" if gender == "male" else "girl"
    narrator = f"{age}-year-old {who}, {rng.choice(JOBS)}"
    pool = [p for f, p in PREMISES if f == fmt] or [p for _, p in PREMISES]
    light = fmt in LIGHT_FORMATS  # formats légers : pas de levier ni de règlement de comptes imposés
    return StoryDNA(
        narrator=narrator,
        premise=rng.choice(pool),
        leverage="" if light else rng.choice(LEVERAGES),
        reveal="" if light else rng.choice(REVEAL_SCENES),
        consequence="" if light else rng.choice(CONSEQUENCES),
        device=rng.choice(DEVICES),
    )
