"""Turning the game's internal names into something readable.

`waterRoute_3` -> "Water Route 3". It lives in one place because the area list, the ranking,
the map header and the Coromon tab all show the same names, and while it was a helper inside
the window each of those was a chance to disagree with the others.
"""

import re


def pretty(name):
    """A map or area name as it is shown: split camelCase, then tidy each word.

    The digits are separated out (`waterRoute_3` -> "Water Route 3") because the game's names
    are not consistent about the underscore, and "Route 3" reads as a place where "Route3" reads
    as an identifier.
    """
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")
    words = []
    for word in re.sub(r"\s+", " ", text).strip().split(" "):
        match = re.match(r"^([A-Za-z]+)(\d+)$", word)
        if match:
            words.extend([match.group(1)[:1].upper() + match.group(1)[1:], match.group(2)])
        elif word:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)
