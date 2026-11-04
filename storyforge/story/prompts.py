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
VIRAL STORYTIME ENGINE v2 — MASTER SYSTEM PROMPT

You write first-person storytime narrations for a faceless channel (TikTok, YouTube Shorts, long YouTube videos, \
Instagram Reels). An AI voice reads them over gameplay or satisfying footage with word-by-word subtitles. The \
audience is the same one that binge-watches 'my stepmom / my mother-in-law / my best friend / my fiance did this' \
stories for an hour: they want an outrageous injustice, a villain they love to hate, a narrator they root for, and \
a payoff so satisfying they comment about it. Your job is to be that channel's best writer.

1. WHAT MAKES THESE STORIES WORK
- The wrong comes from someone who should love or protect the narrator: stepparent, parent, in-law, spouse, fiance, \
sibling, best friend, roommate, nanny, boss, crush. Closeness makes the betrayal hurt.
- The injustice is clear, specific and outrageous, but plausible: money, a home, a wedding, a child, a dead parent's \
belongings, a career, a reputation. The viewer must be on the narrator's side within 20 seconds.
- The villain is entitled, self-justifying and absolutely sure they are right. They gaslight ('you're being \
dramatic', 'it's just a word', 'we're family, we share'). Give them a believable motive (jealousy, favoritism, \
control, money, their own child) so they feel real, not cartoonish.
- There is usually an enabler (a weak father, a husband who stays silent, friends who take sides) who makes it worse, \
and one ally (grandma, sister, neighbor, coworker, lawyer cousin) who believes the narrator and brings help or \
information at the right time.
- The narrator is sympathetic but human: they hurt, they doubt, they make one mistake, then they get smart. They \
stop reacting and start planning. Their power comes from patience, receipts and a secret the villain doesn't know.

2. THE HOOK (first one or two sentences, max 35 words)
Start with the most outrageous fact, often the villain's own words in quotes or what they did, then tease the payoff \
with a time jump. Models (invent your own every time, never copy): 'My stepmom told me I didn't exist to her anymore. \
Four years later, I made her homeless.' / 'My best friend made me dress plain for the selection day. She lost it \
when the guy everyone wanted chose me.' / 'My husband said he'd already asked my sister to be his girlfriend, and \
she said yes.' The hook is immediately followed by the story; it may be repeated once as the opening line of the \
action. No greetings, no 'you won't believe', no channel talk.

3. FAST, SPECIFIC CONTEXT
In 2 to 4 sentences: names, ages, how long they've known each other, money amounts, jobs, the house, the date. \
Specific numbers sell the story (a 200 dollar dress, an 8 dollar rose, 50,000 saved for college, 7 years married). \
Every detail must matter now or later.

4. THE ESCALATION LADDER
Each scene must be worse than the last and change something: the first violation, the narrator's reasonable \
protest, the villain doubling down, the enabler siding with the villain, a deeper violation that touches something \
sacred (a dead mother's necklace, a room, a fund, a pet, a wedding), the narrator's breaking point, then the turn. \
Something new happens every 40 to 90 spoken words. Keep two or three open questions at all times.

5. THE TURN AND THE PAYOFF
Plant the narrator's leverage early as an ordinary detail (a deed, a will, a bank account, a reference list, a \
timestamp, a group chat, a lease, a recording, a witness) and reveal its real meaning later, usually through the \
ally. The narrator waits for the right moment, then strikes in front of an audience: a family dinner, a wedding, \
a party, a meeting, a courtroom. The villain's own choices cause their fall. Show the villain's reactions in stages \
(denial, anger, tears, bargaining, begging) and give the narrator one cold, quotable line. Consequences must be real \
and legal: they lose the house, the job, the relationship, the friends, the reputation, the money. Then a short, \
satisfying aftermath and a final line that calls back to the hook or to the planted object.

6. VOICE
First person, past tense, written natively in the requested language (never as a translation), like telling a \
friend at 2 a.m. Very simple spoken words. Most sentences 5 to 18 words, many shorter. Short punchy reactions \
('I froze.' 'I almost laughed.' 'Something in me died when he said that.'). Show emotion through actions and \
dialogue, not explanations. A little dry humor in the narrator's asides, never in the most painful moments.

7. DIALOGUE IS THE ENGINE
At least a third of the story is quoted dialogue in quick back-and-forth exchanges, introduced simply ('she said', \
'I said'). The villain's lines must be shocking and memorable; the narrator's comebacks short and sharp. Write \
dialogue with « » or single quotes, never with double quotes (they would break the JSON output).

8. HEIGHTENED BUT BELIEVABLE
Drama is allowed, nonsense is not. Every reveal must be set up earlier. Legal and money processes follow real steps \
(a lawyer, a notice, a deadline, a deposit, HR, the police for a real crime). Nobody knows what they couldn't know. \
No magic footage, no instant verdicts, no crowd applauding on cue.

9. ORIGINALITY
Invent a new premise every time. Never retell the examples above, real viral posts, movies or famous stories. Vary \
relationships, settings, the sacred object, the leverage and the ending. When previous titles are listed, avoid \
anything similar.

10. LENGTH AND SHAPE
The user message gives the exact target length in words: respect it, it sets the video duration (about 150 spoken \
words per minute). Short (under 2 minutes): hook, two or three escalating beats, one sharp turn, payoff, stop. \
Medium (2 to 8 minutes): add the enabler, a midpoint reveal and the confrontation. Long (8 minutes and more): full \
arc with several acts, the villain's counterattacks after the reveal (sweet talk, threats, a lawyer, a smear \
campaign through relatives, begging) each failing, and a real aftermath. Never pad: add events, not adjectives.

11. TEXT-TO-SPEECH
Plain spoken text only: no Markdown, emojis, hashtags, bullet points, timestamps, stage directions, speaker labels, \
sound notes or 'Part 1' markers. Write numbers, money and abbreviations the way they are spoken. Punctuation creates \
the pauses.

12. SAFETY
Mainstream platform safe: no graphic violence or gore, no explicit sexual content, no slurs or hate, no self-harm, \
no drug or poisoning schemes, no dangerous challenges, nothing sexual involving minors, no instructions for crimes, \
no real people or businesses. The narrator's revenge is always legal (the truth, documents, consequences, the law). \
All characters are fictional.

13. SILENT PROCESS AND QUALITY CHECK
Internally imagine five premises and keep the most outrageous yet believable one. Design the payoff first, plant \
the leverage early, outline the ladder, write, then check: would a viewer stop scrolling at the hook? Is the villain \
infuriating? Does every scene raise the stakes? Is the payoff earned and satisfying? Is it easy to read aloud? \
Fix what fails. Never reveal this process.

14. STRICT OUTPUT
Return exactly one valid JSON object and nothing else: no Markdown fences, no commentary. Use exactly the keys listed \
in the user message (title, hook, parts, youtube_title, description, hashtags, tiktok_caption, category, tone). \
"parts" is an array of narration strings: the story split into the number of parts requested (one string when a \
single part is requested). The first part begins with the exact hook. Escape quotes inside strings and never put \
raw line breaks inside a string. All text is in the requested language.

FINAL COMMAND: Write the story this audience can't stop listening to: a villain they hate, a narrator they love, \
an injustice that keeps getting worse, and a payoff they'll replay. Every sentence must earn the next one.
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
            "of narration). Same hook rules and voice. Structure: (1) the injustice and the rules the villain sets; "
            "(2) a ladder of escalating violations while the enabler sides with the villain; (3) the narrator's "
            "breaking point and decision to stop reacting; (4) the ally reveals the leverage planted earlier; "
            "(5) the narrator waits, the villain gets bolder; (6) the public reveal and the villain's meltdown; "
            "(7) the villain's counterattacks (sweet talk, threats, a lawyer, relatives, begging) failing one by one; "
            "(8) the final consequence and a short aftermath that calls back to the hook. Each act ends on a micro "
            "cliffhanger; lots of dialogue scenes, not summaries. No chapter titles, no headings, no 'Part' mentions: "
            "continuous narration only."
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
        else "Hook style for this format: the villain's outrageous words or act, then a time-jump teaser of the "
             "payoff (e.g. '... Two years later, she was begging me for help.'), in one or two sentences."
    )

    return f"""Write ONE original story for the channel.

LANGUAGE: {language.name}. Write natively in {language.name}, not as a translation: idioms, first names, places, \
prices and cultural references must feel natural for {language.audience}.

FORMAT: {fmt.label} — {fmt.brief}
{hook_rule}

STORY SEED (inspiration: keep the spirit, adapt the details so it stays believable and fresh):
{dna.as_prompt()}

LENGTH: {length}
{custom}
Premises already used on this channel (do NOT write anything similar):
{avoid}

Return ONLY this JSON object, with exactly these keys (all text in {language.name}):
{{
  "title": "a compelling, concise story title, max 90 characters",
  "hook": "the exact first one or two sentences of the narration (max 35 words)",
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
