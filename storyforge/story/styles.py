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
}
FORMATS = {k: replace(f, pitch=PITCHES.get(k, "")) for k, f in FORMATS.items()}

DEFAULT_FORMATS = ["askreddit", "drama", "revenge"]


# --- ADN aléatoire -----------------------------------------------------------

JOBS = [
    "barista in an upscale coffee shop", "night-shift nurse", "car mechanic", "middle-school teacher",
    "real-estate assistant", "wedding planner", "supermarket cashier", "junior accountant", "bartender",
    "delivery driver", "hairdresser", "IT support technician", "hotel receptionist", "physiotherapy student",
    "bank teller", "personal trainer", "flight attendant", "pharmacy assistant", "line cook", "dog groomer",
    "event photographer", "call-center agent", "construction site manager", "florist", "veterinary assistant",
    "university student working part-time", "stay-at-home parent", "small bakery owner", "insurance adjuster",
    "lifeguard", "electrician", "dental hygienist",
]

SETTINGS = [
    "a family barbecue that got out of hand", "a cousin's wedding", "a tiny apartment with paper-thin walls",
    "the office open space", "a school parents' meeting", "a luxury neighborhood", "a cruise ship",
    "a road trip with in-laws", "a 24-hour diner", "a gym at 6am", "a hospital waiting room",
    "a small-town supermarket", "a ski resort", "a group chat that leaked", "a birthday dinner at a fancy restaurant",
    "an airport gate during a delay", "a homeowners association meeting", "a holiday dinner",
    "a bachelorette weekend", "a shared vacation rental", "a parking lot dispute", "a job interview",
    "a high-school reunion", "a funeral reception", "a baby shower", "a garage sale", "a camping trip",
    "a car dealership", "a hair salon on a Saturday", "a moving day",
]

SIDE_CHARACTERS = [
    "a manipulative best friend", "a mother-in-law who keeps score", "a spoiled coworker whose dad owns everything",
    "an overconfident neighbor", "a deadpan teenager", "a too-perfect new boyfriend", "a lazy roommate",
    "a boss who steals credit", "a sister who always plays the victim", "a grandma with zero filter",
    "a principal at the end of her patience", "a landlord who ignores every rule", "an influencer who wants freebies",
    "a quiet coworker who saw everything", "a toddler with perfect timing", "an ex who reappears",
    "a twin nobody talks about", "a security guard who takes his job very seriously", "a karen with a coupon",
    "a dad who communicates only in puns",
]

TWISTS = [
    "the villain exposes themselves in front of everyone", "a receipt, screenshot or recording surfaces at the worst moment",
    "an unexpected ally switches sides", "a rule the antagonist ignored destroys them", "a misunderstanding with a hilarious reveal",
    "the narrator's quiet detail planted at the start becomes the key", "karma arrives through a total stranger",
    "malicious compliance backfires on the person giving orders", "a secret identity or relationship is revealed",
    "the 'victim' was the planner all along", "a child innocently says the one thing that ruins the lie",
    "the person everyone underestimated wins", "a public announcement goes horribly wrong",
    "an inheritance or will contains a surprise condition", "the narrator turns out to be the hero of someone else's story",
]

DEVICES = [
    "an absurd comparison involving the tax office", "a repeated phrase used three times for rhythm",
    "a self-deprecating aside from the narrator", "a deadpan one-line reaction", "a rule-of-three list",
    "a hyper-specific number that becomes a running gag", "a sound described in a funny way",
    "a facial expression described like a nature documentary",
]

ENGINES = [
    "injustice that turns into satisfying karma", "humiliation that turns into triumph", "confusion that turns into a reveal",
    "panic that turns into laughter", "suspicion that turns into proof", "guilt that turns into relief",
    "jealousy that turns into exposure",
]


@dataclass
class StoryDNA:
    narrator: str
    setting: str
    side_character: str
    engine: str
    twist: str
    device: str

    def as_prompt(self) -> str:
        return (
            f"- Narrator: {self.narrator}\n"
            f"- Setting / situation: {self.setting}\n"
            f"- Key side character: {self.side_character}\n"
            f"- Emotional engine: {self.engine}\n"
            f"- Payoff type: {self.twist}\n"
            f"- Comedic device to include at least once: {self.device}"
        )


def random_dna(narrator_gender: str = "any", rng: random.Random | None = None) -> StoryDNA:
    rng = rng or random.Random()
    gender = narrator_gender if narrator_gender in ("male", "female") else rng.choice(["male", "female"])
    age = rng.randint(19, 46)
    who = "man" if gender == "male" else "woman"
    narrator = f"{age}-year-old {who}, {rng.choice(JOBS)}"
    return StoryDNA(
        narrator=narrator,
        setting=rng.choice(SETTINGS),
        side_character=rng.choice(SIDE_CHARACTERS),
        engine=rng.choice(ENGINES),
        twist=rng.choice(TWISTS),
        device=rng.choice(DEVICES),
    )
