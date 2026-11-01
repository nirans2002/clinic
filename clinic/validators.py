import re

from django.core.exceptions import ValidationError

MSG = "Enter a valid 10-digit mobile number (starting 6–9), or an international number with + and country code."


def normalize_phone(raw):
    """Return a clean phone string or raise ValidationError.

    Indian mobiles are stored as 10 digits (+91 / 91 / leading 0 are stripped);
    other countries must start with + and have 8–15 digits (E.164 length).
    """
    s = re.sub(r"[\s\-().]", "", (raw or "").strip())
    if not s:
        raise ValidationError("Enter a phone number.")
    if not re.fullmatch(r"\+?\d+", s):
        raise ValidationError("Phone can contain only digits, spaces, dashes and a leading +.")
    if s.startswith("+"):
        d = s[1:]
        if d.startswith("91"):
            d = d[2:]
            return _indian(d)
        if not 8 <= len(d) <= 15 or d[0] == "0":
            raise ValidationError(MSG)
        return "+" + d
    d = s
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    elif len(d) == 12 and d.startswith("91"):
        d = d[2:]
    return _indian(d)


def _indian(d):
    if not (len(d) == 10 and d[0] in "6789") or len(set(d)) == 1:
        raise ValidationError(MSG)
    return d
