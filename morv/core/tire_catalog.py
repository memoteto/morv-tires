"""Egyptian tyre catalogue and non-binding category suggestions."""
from morv.core.text import norm_ar

CATEGORIES = (
    "ملاكي", "ربع نقل", "نصف نقل", "نقل متوسط", "جامبو", "ميكروباص",
    "ميني باص", "أتوبيس", "فان", "تريلا", "مقطورات", "جرارات زراعية",
    "معدات ثقيلة", "كلارك", "لودر", "موتوسيكل", "توك توك", "تروسيكل", "أخرى",
)
LEGACY_CATEGORIES = ("دفع رباعي", "نقل خفيف", "نقل ثقيل")

# Seed sizes from Egyptian retail catalogues; categories are commercial hints, not fitment advice.
# Sources: tyresonline.eg, eg.continental-me.com/ar/tires/sizes, kawetchagy.com,
# marketplace.octane-tech.io/ar/category/tbr, hanksugi.com/eg/tires/sizes/.
_GROUPS = {
    "ملاكي": (
        "145/70R12 155/70R13 165/65R13 175/70R13 175/65R14 185/60R14 185/65R14 "
        "185/60R15 185/65R15 195/55R15 195/60R15 195/65R15 205/60R15 205/65R15 "
        "215/65R15 195/55R16 195/60R16 205/55R16 205/60R16 215/60R16 "
        "205/50R17 215/45R17 215/50R17 215/55R17 225/45R17 225/55R17 "
        "225/60R17 225/65R17 235/55R18 245/45ZR18 245/45R19 265/70R16"
    ),
    "ربع نقل": "195R14C 205/75R14C 215/70R15C",
    "ميكروباص": "195R15C 225/70R15C",
    "فان": "195/70R15C 215/65R16C 215/75R16C 235/65R16C",
    "جامبو": "7.00R15C 7.00R16 7.50R16",
    "نقل متوسط": "8.25R16 8.25R20",
    "أتوبيس": "11R22.5 215/75R17.5",
    "تريلا": "11.00R20 12.00R20 12.00R24 12R22.5 295/80R22.5 315/70R22.5 315/80R22.5 325/95R24",
    "مقطورات": "385/65R22.5",
}
DEFAULT_CATEGORY = {size: category for category, sizes in _GROUPS.items() for size in sizes.split()}
SIZES = tuple(sorted(DEFAULT_CATEGORY))


def size_search_key(value: str) -> str:
    return norm_ar(value).replace(" ", "").replace("/", "").replace("×", "x")


def suggested_category(size: str, counts: dict[str, int]) -> str:
    valid = {cat: count for cat, count in counts.items() if cat in CATEGORIES and count > 0}
    default = DEFAULT_CATEGORY.get(size, "")
    if not valid:
        return default
    highest = max(valid.values())
    tied = [cat for cat, count in valid.items() if count == highest]
    if len(tied) == 1:
        return tied[0]
    return default if default in tied else ""
