"""Measure peak memory and time of vault.seal and vault.open_vault on a huge backup, by hand."""
import os
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from morv.infra import vault  # noqa: E402

MIB = 1024 * 1024
PW = "Morv-Tires-2026"
RK = "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF"


def peak_rss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def build_source(path: Path, size_mib: int) -> None:
    block = os.urandom(MIB)
    with open(path, "wb") as f:
        for i in range(size_mib):
            f.write(bytes([i & 0xFF]) + block[1:])     # Vary each block so a mix-up cannot pass unnoticed
        f.flush()
        os.fsync(f.fileno())


def main() -> int:
    size_mib = int(sys.argv[1]) if len(sys.argv) > 1 else 3072
    work = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/var/tmp/morv_bench")
    work.mkdir(parents=True, exist_ok=True)
    plain, sealed, restored = work / "plain.db", work / "sealed.mtb", work / "restored.db"
    try:
        print(f"source: {size_mib} MiB at {work}", flush=True)
        build_source(plain, size_mib)
        dek, ring = vault.new_keyring(PW, RK)

        t0, base = time.monotonic(), peak_rss_mib()
        vault.seal(plain, sealed, dek, ring)
        seal_s, seal_peak = time.monotonic() - t0, peak_rss_mib()

        t0 = time.monotonic()
        vault.open_vault(sealed, restored, dek=dek)
        open_s, open_peak = time.monotonic() - t0, peak_rss_mib()

        t0 = time.monotonic()
        opened = vault.opens_with(sealed, dek)
        probe_s, probe_peak = time.monotonic() - t0, peak_rss_mib()

        same = vault._hash_file(plain) == vault._hash_file(restored)
        print(f"baseline peak RSS      {base:8.0f} MiB")
        print(f"seal                   {seal_peak:8.0f} MiB   {seal_s:7.1f} s   "
              f"{size_mib / seal_s:6.0f} MiB/s")
        print(f"open_vault             {open_peak:8.0f} MiB   {open_s:7.1f} s   "
              f"{size_mib / open_s:6.0f} MiB/s")
        print(f"opens_with             {probe_peak:8.0f} MiB   {probe_s:7.1f} s   returned {opened}")
        print(f"round trip identical   {same}")
        print(f"sealed size            {sealed.stat().st_size / MIB:8.0f} MiB")
        return 0 if same and opened else 1
    finally:
        for f in (plain, sealed, restored):
            f.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
