import re

_NON_DIGITS = re.compile(r"\D")


def normalize_phone(raw: str) -> str | None:
    """Привести номер к виду +7XXXXXXXXXX (или +<код страны>...). None — если не похоже на номер."""
    raw = raw.strip()
    digits = _NON_DIGITS.sub("", raw)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    # Иностранные номера: только в международном формате с «+».
    if raw.startswith("+") and 11 <= len(digits) <= 15:
        return "+" + digits
    return None
