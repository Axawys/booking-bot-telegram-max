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


def phone_from_vcard(vcf: str | None) -> str | None:
    """Номер из vCard — так MAX присылает контакт по кнопке request_contact.

    Пример строки: «TEL;TYPE=cell:79001234567».
    """
    for line in (vcf or "").splitlines():
        name, sep, value = line.partition(":")
        is_tel = sep and name.split(";", 1)[0].strip().upper() == "TEL"
        if is_tel and (phone := normalize_phone(value)):
            return phone
    return None
