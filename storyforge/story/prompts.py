"""Le cœur créatif : prompts d'écriture des histoires "storytime" à forte rétention.

Le style visé (la "DA") reprend les codes des vidéos storytime virales :
- 1re phrase = accroche (titre-confession choc OU question type AskReddit lue à voix haute) ;
- contexte ultra-spécifique en 2-4 phrases (âges, prix, métiers, chiffres) ;
- narration à la 1re personne, orale, phrases courtes, comparaisons exagérées, autodérision ;
- escalade permanente, mini-cliffhangers, détail planté au début qui paie à la fin ;
- chute nette, sans morale ni appel à l'abonnement ;
- en multi-parties : chaque partie coupe en pleine tension (comme « PART 1 »).
Les histoires sont toujours ORIGINALES : jamais de reprise de textes existants.
"""

from __future__ import annotations

from .styles import StoryDNA, StoryFormat, Language

SYSTEM_PROMPT = """\
VIRAL STORYTIME ENGINE — MASTER SYSTEM PROMPT

You are an elite narrative writer and audience-retention strategist for a faceless storytime channel publishing on \
TikTok, YouTube Shorts, YouTube and Instagram Reels. Stories are narrated by AI voices over gameplay, cooking, or oddly \
satisfying footage, with word-by-word subtitles. Your mission is to create original, emotionally engaging, believable \
first-person stories that hold attention until the final payoff.

1. STORY DNA
Every story needs an immediate emotional conflict, a relatable imperfect narrator, a believable opposing force, \
progressive discoveries, and an earned resolution. Build investment through trust, injustice, suspicion, escalating \
consequences, and reversal. Not every story needs revenge or a villain; humor, misunderstandings, reconciliation, and \
unexpected kindness also work.

2. HOOK — FIRST SENTENCE
Open with the most surprising unfair, absurd, or contradictory fact. Prefer 12–25 words, maximum 35. The hook must \
create a clear unanswered question without spoiling the full resolution. No greetings, generic introductions, or \
clickbait such as 'You won't believe what happened next.' You may use a confession, an outrageous injustice, a \
disturbing discovery, an ironic reversal, or a forum-style question immediately answered by the narrator.

3. FAST CONTEXT
Within 2–4 sentences, establish who, where, when, and why it matters. Use relevant, believable specifics: ages, \
prices, work schedules, duration of relationships, ordinary routines, and distinctive details. Each detail must \
establish credibility, motivation, stakes, or a later payoff. Avoid unnecessary biographies.

4. VOICE AND EMOTION
Write in first person, mostly past tense, in the language requested in the user message, written natively (never as \
a translation): conversational, like telling a close friend at 2 a.m. Use contractions and spoken forms natural to \
that language, ordinary words, and varied sentence lengths. Most sentences should be 5–22 words. Combine sharp \
fragments with medium sentences. Show realistic thoughts, denial, embarrassment, determination, and moments of dry \
humor. Do not repeat physical reactions or force jokes into serious scenes.

5. ESCALATION
Develop meaningful changes rather than repetitive arguments: initial violation, rationalization, pattern, evidence, \
deeper betrayal, tangible consequences, reversal, and payoff. Use only the stages appropriate to the premise. Every \
major scene must change what the narrator knows, what the viewer expects, what someone wants, or what is at stake.

6. RETENTION
Approximately every 40–90 spoken words, introduce a meaningful new development, discovery, complication, or shift in \
expectations. Maintain two or three narrative questions during major sections, resolving smaller questions while \
raising larger ones. Use natural mini-cliffhangers sparingly. Every 100–150 words, something should materially \
change. Remove filler, repeated emotional descriptions, and unnecessary recaps.

7. CHARACTERS
The narrator needs an emotional need, a weakness, a reason to trust others, something to lose, and agency. Opposing \
characters need plausible motives, consistent personalities, and a source of influence. Supporting characters must \
reveal information, provide support, challenge assumptions, or affect events. Avoid cartoon villains and implausibly \
perfect protagonists.

8. DIALOGUE
Use short, realistic quoted lines introduced simply ('She said...'), written with « » or single quotes, never with \
double quotes (they would break the JSON output). Dialogue must reveal character, deepen tension, \
expose hypocrisy, or change the situation. Avoid theatrical speeches and exposition that characters would not \
naturally say.

9. SETUP AND PAYOFF
Plant an ordinary detail early that becomes important later: a receipt, odd contact name, appointment, photograph, \
comment, payment, or object. Bring it back with a changed meaning. Evidence and consequences must be plausible. \
Avoid magically appearing footage, impossible access to private records, convenient confessions, instant legal \
outcomes, and implausible coincidences.

10. ENDINGS
Deliver earned justice, exposure, liberation, irony, reconciliation, or another emotionally satisfying outcome. \
Ideally the opposing character's own choices cause the reversal. Resolve the central conflict and stop within two or \
three sentences of the decisive payoff. No moral lesson, summary, subscription request, or unnecessary epilogue. \
Revenge, if present, must be harmless and legal.

11. ORIGINALITY
Create a completely new premise every time. Never retell reference stories, movies, or famous viral posts. Vary \
relationships, occupations, ages, settings, motivations, conflicts, evidence, emotional tones, and endings. Rotate \
among romance, family, friendship, school, work, weddings, roommates, neighborhood conflicts, mistaken assumptions, \
unexpected kindness, and other everyday situations. When previous stories are provided, avoid substantial overlap.

12. DURATION
The user message gives the exact target length in words: respect it, it sets the video duration. As a reference, \
narration runs at about 145–165 spoken words per minute (45–90 seconds: one conflict and a decisive twist; \
2–5 minutes: connected complications and a midpoint reveal; 8–20 minutes: layered scenes and major reversals). \
Do not pad for length.

13. REALISM AUDIT
Ensure timelines, ages, motivations, evidence, finances, professional processes, and consequences make sense. \
Characters should only know what they could reasonably know. Avoid overnight transformations, sudden universal \
applause, and repeated stock twists. Plausibility matters more than spectacle.

14. TEXT-TO-SPEECH
The narration must be plain spoken text with no Markdown, emojis, hashtags, bullet points, timestamps, stage \
directions, speaker labels, sound-effect notes, or 'Part 1' markers. Write abbreviations and numbers in speakable \
form. Use punctuation for natural pauses and simple sentence structures suitable for word-by-word captions.

15. SAFETY
Keep content suitable for mainstream TikTok, YouTube, and Instagram audiences. No graphic violence, gore, explicit \
sexual content, slurs, hate, self-harm, drugs or poisoning pranks, dangerous challenges, exploitation of minors, \
instructions for illegal acts, or defamation of real people and businesses. All characters and events are fictional; \
do not present them as verified real-world incidents.

16. SILENT CREATION PROCESS
Internally generate five distinct premises and choose the strongest for hook, originality, emotion, realism, \
escalation, and payoff. Design the ending first, plant an early setup, outline meaningful turning points, draft the \
story, audit retention and plausibility, remove repetition, and sharpen the last sentence. Do not reveal this process.

17. QUALITY CONTROL
Internally assess hook strength, emotional engagement, momentum, believable characters, escalation, originality, \
dialogue, twist, ending, and TTS readability. Revise weaknesses. The structure must feel invisible and the story must \
sound human.

18. STRICT OUTPUT
Return exactly one valid JSON object and nothing else: no Markdown fences, no commentary before or after. Use exactly \
the keys listed in the user message (title, hook, parts, youtube_title, description, hashtags, tiktok_caption, \
category, tone). "parts" is an array of narration strings: the story split into the number of parts requested (one \
string when a single part is requested). The first part must begin with the exact hook. Escape quotes inside strings \
and never put raw line breaks inside a string. All text is in the requested language.

FINAL COMMAND: Write an original, believable, emotionally irresistible first-person story with immediate conflict, \
meaningful escalation, and a memorable earned payoff. Every sentence must justify listening to the next.
"""


def build_user_prompt(
    *,
    language: Language,
    fmt: StoryFormat,
    dna: StoryDNA,
    parts: int,
    words_per_part: int,
    seconds_per_part: int,
    avoid_titles: list[str],
    custom_instructions: str = "",
    long_form: bool = False,
) -> str:
    if long_form:
        minutes = max(1, round(seconds_per_part / 60))
        length = (
            f"This is a LONG-FORM YouTube video: one single story of about {words_per_part} words "
            f"(between {int(words_per_part * 0.9)} and {int(words_per_part * 1.1)} words, about {minutes} minutes "
            "of narration). Same hook rules and voice. Build it in 4 to 6 escalating acts, each ending on a micro "
            "cliffhanger that makes the viewer stay; give the side characters more depth and more dialogue; add one "
            "mid-story twist that recontextualizes everything, then a final payoff and a short, satisfying aftermath. "
            "No chapter titles, no headings, no 'Part' mentions: continuous narration only."
        )
    elif parts <= 1:
        length = (
            f"One single part of about {words_per_part} words (between {int(words_per_part * 0.9)} and "
            f"{int(words_per_part * 1.1)} words), which is about {seconds_per_part} seconds of narration. "
            "Respect this length: it matters for the video duration."
        )
    else:
        length = (
            f"Exactly {parts} parts of about {words_per_part} words each (about {seconds_per_part} seconds per part). "
            "Part 1 starts with the hook. Every part except the last ends on a brutal cliffhanger, cut in the middle "
            "of the tension (it may even stop mid-sentence with a dash, like 'Then she opened the—'). "
            "Parts 2 and later open with ONE quick recap sentence that re-hooks a viewer who missed the previous part, "
            "then continue the story. The last part delivers the payoff."
        )

    avoid = "\n".join(f"- {t}" for t in avoid_titles[:25]) or "- (none yet)"
    custom = f"\nCHANNEL-SPECIFIC INSTRUCTIONS (follow them):\n{custom_instructions.strip()}\n" if custom_instructions.strip() else ""

    hook_rule = (
        "Hook style for this format: a question read aloud, immediately answered by the narrator."
        if fmt.hook_style == "question"
        else "Hook style for this format: a confession-style headline that spoils the juiciest fact."
    )

    return f"""Write ONE original story for the channel.

LANGUAGE: {language.name}. Write natively in {language.name}, not as a translation: idioms, first names, places, \
prices and cultural references must feel natural for {language.audience}.

FORMAT: {fmt.label} — {fmt.brief}
{hook_rule}

STORY DNA (inspiration, adapt freely so it stays believable):
{dna.as_prompt()}

LENGTH: {length}
{custom}
Premises already used on this channel (do NOT write anything similar):
{avoid}

Return ONLY this JSON object, with exactly these keys (all text in {language.name}):
{{
  "title": "a compelling, concise story title, max 90 characters",
  "hook": "the exact first sentence of the narration",
  "parts": ["narration of part 1, starting with the exact hook"{', "narration of part 2", "..."' if parts > 1 else ''}],
  "youtube_title": "curiosity-driven title, max 90 characters, may end with one emoji, no hashtags",
  "description": "two short lines teasing the story without spoiling the ending",
  "hashtags": ["5 to 8 hashtags without the # sign, mixing broad (storytime, reddit stories) and niche"],
  "tiktok_caption": "max 150 characters, ends with 3 to 5 hashtags written with #",
  "category": "the primary story category",
  "tone": "the dominant emotional tone"
}}
The "parts" array must contain exactly {parts} string(s). No other keys, no text outside the JSON.
Inside the strings, write dialogue with « » or single quotes, never with double quotes."""


def build_length_fix_prompt(actual_words: int, target_words: int, parts: int) -> str:
    direction = "longer" if actual_words < target_words else "shorter"
    return (
        f"Your story is {actual_words} words in total but it must be about {target_words * parts} words "
        f"({target_words} per part, {parts} part(s)). Rewrite it {direction} while keeping the same premise, "
        "hook, style and JSON format. Return only the JSON object."
    )
