"""Build the Maraton 13 diesel digital twin from the homologation VECTO files.

    python tools/build_maraton_twin.py            # writes into library/
    python tools/build_maraton_twin.py --force    # overwrite existing files

Reads every XML in "ARAÇ VERİLERİ/MARATON ÖRNEK ARAÇ" and adds the glazing
areas from the ANSA CFD report in "ARAÇ VERİLERİ/Cam Ve Ön alan bilgisi"
(CdAx değerleri.png), which are not in any VECTO file:

    Rear_Window        1.358 m²
    Windshield         4.001 m²
    First_Door_Window  0.706 m²
    Panorama_Window    0.550 m²
    Driver_Window      0.564 m²
    Side_Windows      21.401 m²
    total             28.581 m²

The same import is available in the web interface (Veri yükle → VECTO
dosyaları) and on the command line (python -m temsasim vecto-import ...).
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from temsasim.library import Library                     # noqa: E402
from temsasim.vecto_import import plan_twin, read_folder, write_twin   # noqa: E402

CDXA_M2 = 3.528
GLAZING_M2 = {"Rear_Window": 1.35817, "Windshield": 4.00146, "First_Door_Window": 0.706010,
              "Panarama_Window": 0.550195, "Driver_Window": 0.564190, "Side_Windows": 21.4009}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=str(ROOT / "ARAÇ VERİLERİ" / "MARATON ÖRNEK ARAÇ"))
    ap.add_argument("--library", default=str(ROOT / "library"))
    ap.add_argument("--stem", default="maraton_13_dizel")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    bundle = read_folder(a.source)
    plan = plan_twin(bundle)
    print("Kaynaklar:")
    for row in plan.summary()["rows"]:
        print(f"  {row['label']:<22} {row['value']:<60} <- {row['source']}")
    for w in plan.warnings:
        print("  !", w)
    if plan.missing:
        print("Eksik:", "; ".join(plan.missing))
        return 2
    # CdA from the CFD study (given by the user, 3 Nov 2026), not the 3.40 of the example step file
    plan.cdxa_m2 = CDXA_M2
    plan.sources["cdxa"] = f"ANSA CFD çalışması (CdAx {CDXA_M2} m²); örnek tamamlanmış araç dosyasındaki 3,40 m² kullanılmadı"
    glazing = round(sum(GLAZING_M2.values()), 3)
    notes = (f"Cam alanları ANSA CFD raporundan: {', '.join(f'{k} {v:.3f} m²' for k, v in GLAZING_M2.items())} "
             f"(toplam {glazing} m²).")
    out = write_twin(Library(a.library), plan, name="Maraton 13 dizel", stem=a.stem,
                     variant="13,1 m · 55 koltuk · DAF MX-11 330 · ZF TraXon 12",
                     overwrite=a.force, glazing_area_m2=glazing, notes=notes)
    print("\nYazılan dosyalar:")
    for k, v in out["written"].items():
        print(f"  {k:<13} {v}")
    print()
    print(out["describe"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
