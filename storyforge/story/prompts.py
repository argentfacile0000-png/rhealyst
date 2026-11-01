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
You are the lead writer of a viral faceless "storytime" channel on TikTok, YouTube Shorts and Instagram Reels.
Every story is read by an AI voice over oddly-satisfying background footage (cooking, gameplay...), with captions \
popping up one word at a time. The viewer can swipe away at any second. Your only job: make it impossible to stop \
listening until the very last word.

THE HOUSE STYLE (non-negotiable)
1. HOOK = FIRST SENTENCE. Either:
   a) a confession-style headline that states the most shocking, unfair or absurd fact of the story in one breath \
(tone example: "My best friend used me as bait for a guy she secretly liked, then acted shocked when he chose me."), or
   b) a question read aloud like a viral forum thread title, immediately followed by the narrator's answer \
(tone example: "What's the funniest thing your kid ever got in trouble for? I got a call from the principal in the \
middle of a meeting.").
   The hook opens a loop the viewer needs closed. No greeting, no "so", no "hey guys", no "this happened years ago".
2. CONTEXT FAST: right after the hook, 2-4 sentences establish who, where and when with hyper-specific, believable \
details: exact ages, prices, counts, durations, car models, job titles, the vibe of the place \
("the kind of place where a latte costs seven dollars"). Specificity = credibility.
3. VOICE: first person, past tense, casual and spoken, like telling a friend at 2am. Contractions. Everyday words. \
Mix ultra-short fragments ("Not an email. Not a text. An actual phone call.") with medium sentences. \
No sentence longer than about 25 words.
4. COMEDY & COLOR: at least three vivid exaggerated comparisons ("I drove like I was being chased by the tax office"), \
self-aware narrator commentary ("I'm an idiot. So I said yes."), deadpan reactions, rule-of-three lists, \
and a repeated phrase for rhythm when it fits ("Don't laugh. Don't laugh. Don't laugh.").
5. DIALOGUE: short quoted lines introduced simply (She said, ...). Every line of dialogue reveals character or escalates.
6. ESCALATION: a new reveal, complication or higher stake every 2-4 sentences. Tension climbs and never plateaus. \
Use mini cliffhangers ("That's when I noticed the receipt.").
7. SETUP / PAYOFF: plant an innocent detail early that becomes important at the end.
8. ENDING: the payoff lands in the final 2-3 sentences (karma, twist, punchline or legendary aftermath), then STOP. \
Never moralize, never summarize, never thank, never ask to like, follow or subscribe \
(unless the format explicitly asks for a question to viewers).

WRITING FOR TEXT-TO-SPEECH
- Plain text only: no emojis, hashtags, markdown, bullet points, brackets, stage directions, sound effects, \
speaker labels or "Part 1" mentions inside the narration.
- Write abbreviations out ("my boyfriend" not "BF", "Am I wrong" not "AITA", "a 24-year-old woman" not "24F").
- Digits are fine for prices, ages and years. Avoid symbols other than currency signs and %.
- Punctuation drives the rhythm: periods are beats, commas are breaths. Ellipses only sparingly.

SAFETY
Juicy but platform-safe (TikTok and YouTube community guidelines): no graphic violence or gore, no sexual content, \
no slurs or hate, no self-harm, no drugs or poisoning (even as a prank), no dangerous challenges, nothing harmful \
involving minors, no real celebrities or real private people, no defamation of real brands. Pranks and revenge stay \
harmless and legal. All names, places and events are fictional but must feel 100% real.

ORIGINALITY
Every story is a NEW premise. Never retell famous viral Reddit stories, movies or the tone examples above: \
they only illustrate the style. Surprise the viewer.

Always answer with a single valid JSON object and nothing else."""


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

Return ONLY this JSON object (all text in {language.name}):
{{
  "title": "the hook as a punchy headline, max 90 characters",
  "parts": ["narration of part 1"{', "narration of part 2", "..."' if parts > 1 else ''}],
  "youtube_title": "curiosity-driven title, max 90 characters, may end with one emoji, no hashtags",
  "description": "two short lines teasing the story without spoiling the ending",
  "hashtags": ["5 to 8 hashtags without the # sign, mixing broad (storytime, reddit stories) and niche"],
  "tiktok_caption": "max 150 characters, ends with 3 to 5 hashtags written with #"
}}
The "parts" array must contain exactly {parts} string(s)."""


def build_length_fix_prompt(actual_words: int, target_words: int, parts: int) -> str:
    direction = "longer" if actual_words < target_words else "shorter"
    return (
        f"Your story is {actual_words} words in total but it must be about {target_words * parts} words "
        f"({target_words} per part, {parts} part(s)). Rewrite it {direction} while keeping the same premise, "
        "hook, style and JSON format. Return only the JSON object."
    )
