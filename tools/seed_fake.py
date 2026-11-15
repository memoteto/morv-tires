"""Fake data for testing and performance runs. Never for a real database."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIRST = ["أحمد", "محمد", "محمود", "مصطفى", "إبراهيم", "خالد", "سامي", "ياسين", "عمر", "حسن", "منصور", "طارق"]
LAST = ["علي", "حسين", "عبد الله", "سالم", "إسماعيل", "فتحي", "رمضان", "الشرقاوي", "عبد الرحمن", "السيد"]
BRANDS = ["Michelin", "Continental", "Hankook", "Bridgestone", "Yokohama", "Pirelli", "Dunlop", "Lassa"]
BAT_BRANDS = ["ACDelco", "Varta", "Bosch", "Amaron", "Exide"]
SIZES = ["175/70R13", "185/65R15", "195/65R15", "205/55R16", "215/60R16", "225/45R17", "235/55R18", "7.50R16"]


def seed(con, by, n_customers=3000, n_items=1500, n_suppliers=200, rng=None) -> None:
    from morv.infra import customers, items, suppliers
    from morv.infra.customers import Customer, Vehicle
    from morv.infra.items import Item
    from morv.infra.suppliers import Supplier
    rng = rng or random.Random(7)
    if customers.count(con):
        raise SystemExit("القاعدة فيها عملاء؛ لا تُزرع بيانات وهمية فوق بيانات حقيقية")
    # One transaction per create, as in real use; disk sync is off because the data is fake
    con.execute("PRAGMA synchronous=OFF")
    try:
        for i in range(n_customers):
            name = f"{rng.choice(FIRST)} {rng.choice(FIRST)} {rng.choice(LAST)}"
            v = [Vehicle(rng.choice(["تويوتا", "هيونداي", "كيا", "نيسان"]), "موديل", 2015 + i % 10,
                         f"{rng.choice('أبجدهوزحطي')} {rng.choice('أبجدهوزحطي')} {rng.choice('أبجدهوزحطي')} {1000 + i}",
                         rng.choice(SIZES))] if i % 2 == 0 else []
            customers.create(con, by, Customer(None, name, f"01{i:09d}"[:11], price_level=rng.choice(["retail", "retail", "wholesale"]),
                                               vehicles=v))
        for i in range(n_suppliers):
            suppliers.create(con, by, Supplier(None, f"مورد {rng.choice(LAST)} {i}", f"015{i:08d}"[:11]))
        for i in range(n_items):
            r = i % 10
            if r < 7:
                size = rng.choice(SIZES)
                brand = rng.choice(BRANDS)
                items.create(con, by, Item(None, "tire", f"TR-{i:05d}", f"إطار {brand} M{i % 40} {size}", f"622{i:010d}",
                                           brand, f"M{i % 40}", rng.randint(2500, 9000) * 100, 0, 0, 4, "", True,
                                           {"size": size, "li": str(rng.randint(82, 104)), "si": rng.choice(["H", "T", "V"]),
                                            "cat": "ملاكي", "cons": "Radial"}))
            elif r < 9:
                ah = rng.choice(["45", "55", "60", "70", "75", "100"])
                items.create(con, by, Item(None, "battery", f"BT-{i:05d}", f"بطارية {rng.choice(BAT_BRANDS)} {ah} أمبير",
                                           f"623{i:010d}", rng.choice(BAT_BRANDS), "", rng.randint(2000, 6000) * 100, 0, 0, 3, "", True,
                                           {"volt": "12V", "ah": ah, "cca": "600", "tech": "EFB", "box": "L3", "pol": "الموجب يمين", "dims": "278×175×190"}))
            else:
                items.create(con, by, Item(None, "service", f"SV-{i:05d}", f"خدمة رقم {i}", price_retail=rng.randint(30, 300) * 100))
    finally:
        con.execute("PRAGMA synchronous=FULL")


def main() -> int:
    from morv.infra import users
    from morv.infra.db.bootstrap import open_main_db
    args = [int(a) for a in sys.argv[1:]]
    con = open_main_db()
    by = users.list_users(con)[0] if users.count_users(con) else None
    seed(con, by, *args)
    print("تم")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
