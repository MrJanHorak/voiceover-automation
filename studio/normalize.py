"""Turn written English into the words a narrator would say.

Runs just before synthesis, so the script, sections and takes keep the text as written.
Bracketed sound cues such as [laugh] pass through unchanged. The owner's pronunciation
list is applied first, so a saved replacement always wins over these rules.
"""

import re

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
SCALES = [(10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")]
ORDINAL_WORDS = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
                 "nine": "ninth", "twelve": "twelfth"}
DIGIT_NAMES = ["oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

MONTHS = {"jan": "January", "feb": "February", "mar": "March", "apr": "April", "jun": "June",
          "jul": "July", "aug": "August", "sep": "September", "sept": "September", "oct": "October",
          "nov": "November", "dec": "December"}
MONTH_NAMES = "January|February|March|April|May|June|July|August|September|October|November|December"
ABBREVIATIONS = [
    (r"\be\.g\.,?", "for example,"), (r"\bi\.e\.,?", "that is,"), (r"\betc\.", "et cetera"),
    (r"\bvs\.?(?=\s)", "versus"), (r"\bapprox\.", "approximately"),
    (r"\bDr\.(?=\s)", "Doctor"), (r"\bMr\.(?=\s)", "Mister"), (r"\bMrs\.(?=\s)", "Missus"),
    (r"\bMs\.(?=\s)", "Miz"), (r"\bProf\.(?=\s)", "Professor"), (r"\bJr\.", "Junior"),
    (r"\bSt\.(?=\s+[A-Z])", "Saint"), (r"\bSt\.", "Street"), (r"\bAve\.", "Avenue"),
    (r"\bBlvd\.", "Boulevard"), (r"\bNo\.(?=\s*\d)", "number"), (r"#(?=\d)", "number "),
    (r"\bInc\.", "Incorporated"), (r"\bLtd\.", "Limited"), (r"\bCorp\.", "Corporation"),
    (r"\s&\s", " and "),
]


def cardinal(number):
    """Words for a non-negative integer, e.g. 1299 -> one thousand two hundred ninety-nine."""
    if number < 20:
        return ONES[number]
    if number < 100:
        tens, ones = divmod(number, 10)
        return TENS[tens] + (f"-{ONES[ones]}" if ones else "")
    if number < 1000:
        hundreds, rest = divmod(number, 100)
        return f"{ONES[hundreds]} hundred" + (f" {cardinal(rest)}" if rest else "")
    for value, name in SCALES:
        if number >= value:
            count, rest = divmod(number, value)
            return f"{cardinal(count)} {name}" + (f" {cardinal(rest)}" if rest else "")
    return str(number)


def ordinal(number):
    words = cardinal(number)
    head, separator, last = words.rpartition("-" if "-" in words.split(" ")[-1] else " ")
    if last in ORDINAL_WORDS:
        last = ORDINAL_WORDS[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return f"{head}{separator}{last}"


def year(number):
    """Read a year the way people say it: 1999 nineteen ninety-nine, 2005 two thousand five."""
    if 2000 <= number <= 2009:
        return cardinal(number)
    first, last = divmod(number, 100)
    if last == 0:
        return f"{cardinal(first)} hundred"
    return f"{cardinal(first)} {'oh ' + ONES[last] if last < 10 else cardinal(last)}"


def decimal(text):
    whole, _, fraction = text.replace(",", "").partition(".")
    words = cardinal(int(whole or "0"))
    if fraction:
        words += " point " + " ".join(ONES[int(digit)] for digit in fraction)
    return words


def money(match):
    whole = int(match["whole"].replace(",", ""))
    cents = int((match["cents"] or "0").ljust(2, "0")[:2])
    scale = match["scale"]
    if scale:
        amount = decimal(match["whole"] + (f".{match['cents']}" if match["cents"] else ""))
        return f"{amount} {scale.lower()} dollars"
    parts = []
    if whole or not cents:
        parts.append(f"{cardinal(whole)} {'dollar' if whole == 1 else 'dollars'}")
    if cents:
        parts.append(f"{cardinal(cents)} {'cent' if cents == 1 else 'cents'}")
    return " and ".join(parts)


def clock(match):
    hour, minute = int(match["hour"]), int(match["minute"] or 0)
    meridiem = match["meridiem"]
    if minute == 0:
        words = cardinal(hour) + ("" if meridiem else " o'clock")
    elif minute < 10:
        words = f"{cardinal(hour)} oh {ONES[minute]}"
    else:
        words = f"{cardinal(hour)} {cardinal(minute)}"
    if meridiem:
        words += " A M" if meridiem.lower().startswith("a") else " P M"
    return words


def digits(text):
    return " ".join(DIGIT_NAMES[int(digit)] for digit in text)


def phone(match):
    groups = [group for group in match.groups() if group]
    return ", ".join(digits(group) for group in groups)


def web_address(match):
    text = match.group(0)
    text = re.sub(r"^https?://", "", text)
    spoken = text.replace("@", " at ").replace(".", " dot ").replace("/", " slash ")
    spoken = re.sub(r"\bwww\b", "w w w", spoken)
    return " ".join(spoken.split())


def normalize_plain(text):
    # Web and email addresses before anything touches their dots.
    text = re.sub(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", web_address, text)
    text = re.sub(r"\b(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|ai|co|edu|gov|us|uk|ca)\b(?:/[\w/-]*)?",
                  web_address, text)

    # Months: "Jan. 5, 2027" -> "January fifth, twenty twenty-seven".
    text = re.sub(r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.(?=\s|$)",
                  lambda match: MONTHS[match.group(1).lower()], text)
    text = re.sub(r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)(?=\s+\d{1,2}\b)",
                  lambda match: MONTHS[match.group(1).lower()], text)
    text = re.sub(rf"\b({MONTH_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!:)",
                  lambda match: f"{match.group(1)} {ordinal(int(match.group(2)))}", text)

    for pattern, replacement in ABBREVIATIONS:
        text = re.sub(pattern, replacement, text)

    # Initialisms: "U.S." -> "U S", keeping the full stop when it ends a sentence.
    text = re.sub(r"\b((?:[A-Za-z]\.){2,})(?=(\s+[A-Z]|\s*$)?)",
                  lambda match: " ".join(match.group(1).upper().replace(".", " ").split())
                  + ("." if match.group(2) is not None else ""), text)

    # Times: 9 a.m., 3:30 PM, 10:05.
    text = re.sub(r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[ap]\.?m\.?)(?=\W|$)",
                  clock, text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)\b(?P<meridiem>)", clock, text)

    # Phone numbers: (555) 123-4567, 555-123-4567, 555-0123.
    text = re.sub(r"\(?\b(\d{3})\)?[-.\s](\d{3})[-.](\d{4})\b", phone, text)
    text = re.sub(r"\b(\d{3})-(\d{4})\b", phone, text)

    # Number ranges such as 2020-2024 or 5–10 are read with "to".
    text = re.sub(r"(?<=\d)\s?[\u2013-]\s?(?=\$?\d)", " to ", text)

    # Money and percentages.
    text = re.sub(r"\$(?P<whole>\d[\d,]*)(?:\.(?P<cents>\d{1,2}))?(?:\s*(?P<scale>million|billion|trillion|thousand|[MBK])\b)?",
                  lambda match: money(_scale(match)), text, flags=re.IGNORECASE)
    text = re.sub(r"(\d[\d,]*(?:\.\d+)?)\s?%", lambda match: f"{decimal(match.group(1))} percent", text)

    # Ordinals, years, then remaining numbers.
    text = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", lambda match: ordinal(int(match.group(1))), text)
    text = re.sub(r"(?<![\d,.])\b(1[1-9]\d\d|20\d\d)\b(?!,\d|\.\d|\d)", lambda match: year(int(match.group(1))), text)
    text = re.sub(r"\b(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\b", lambda match: decimal(match.group(0)), text)
    return text


def _scale(match):
    values = match.groupdict()
    letter = (values["scale"] or "").upper()
    values["scale"] = {"K": "thousand", "M": "million", "B": "billion"}.get(letter, values["scale"])
    return values


def speakable(spelling):
    """Make a respelling easy for a voice model to read as one word.

    "noo-VEX-uh" is joined into "noovexuh": models read hyphens as pauses or say "dash", and
    capital letters inside a word ("nooVEXuh") can be spelled out letter by letter. Spaced
    letters such as "A W S" and all-capital acronyms are left as written.
    """
    def fix(match):
        word = match.group(0)
        if "-" in word or (re.search(r"[a-z]", word) and re.search(r"[A-Z]{2,}", word)):
            return word.replace("-", "").lower()
        return word

    return re.sub(r"[A-Za-z]+(?:-[A-Za-z]+)*", fix, spelling)


def apply_pronunciations(text, pronunciations):
    """Replace whole words or phrases from the owner's list, longest entries first."""
    pronunciations = [(written, speakable(spoken)) for written, spoken in pronunciations]
    for written, spoken in sorted(pronunciations, key=lambda item: -len(item[0])):
        pattern = r"(?<![\w])" + re.escape(written) + r"(?![\w])"
        text = re.sub(pattern, lambda _match, spoken=spoken: spoken, text, flags=re.IGNORECASE)
    return text


def spoken_text(text, pronunciations=()):
    """Text to send to a voice model. Bracketed sound cues are left exactly as written."""
    parts = re.split(r"(\[[^\]]*\])", text)
    for index in range(0, len(parts), 2):
        parts[index] = normalize_plain(apply_pronunciations(parts[index], pronunciations))
    return " ".join("".join(parts).split())


def spoken_paragraphs(text, pronunciations=()):
    """Paragraphs of the script with each changed stretch of words marked, for the preview.

    Each paragraph is a list of segments: {"text": written} for words read as written, or
    {"text": written, "spoken": spoken, "kind": "pronunciation" | "auto"} where the voice
    will say something different. Words are aligned with difflib, so a change such as
    "Jan. 5" -> "January fifth" is one segment.
    """
    from difflib import SequenceMatcher

    keys = [written.lower() for written, _spoken in pronunciations]
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text or ""):
        written = paragraph.split()
        if not written:
            continue
        spoken = spoken_text(paragraph, pronunciations).split()
        segments = []
        matcher = SequenceMatcher(a=written, b=spoken, autojunk=False)
        for operation, a1, a2, b1, b2 in matcher.get_opcodes():
            before, after = " ".join(written[a1:a2]), " ".join(spoken[b1:b2])
            if operation == "equal":
                segments.append({"text": before})
                continue
            lowered = re.sub(r"[^\w\s'-]", " ", before.lower())
            listed = any(re.search(rf"(?<!\w){re.escape(key)}(?!\w)", lowered) for key in keys)
            segments.append({"text": before, "spoken": after, "kind": "pronunciation" if listed else "auto"})
        paragraphs.append(segments)
    return paragraphs
