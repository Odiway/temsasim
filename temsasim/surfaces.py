"""Road surfaces: rolling-resistance multipliers relative to good dry asphalt.

The chassis coefficients describe the surface the coast-down was run on. A
worn, patched or paved street rolls worse; the multiplier scales the rolling
term only. Typical literature values for heavy-vehicle tyres, not
measurements -- the interface says so."""

SURFACES = [
    ("yeni_asfalt", "Yeni asfalt", 0.95, "pürüzsüz, yeni serilmiş"),
    ("asfalt", "İyi asfalt", 1.00, "şasi katsayılarının tanımlandığı yüzey"),
    ("beton", "Beton", 0.97, "derzli beton kaplama"),
    ("eski_asfalt", "Eskimiş asfalt", 1.15, "yamalı, çatlaklı"),
    ("parke", "Parke taşı", 1.40, "kilitli beton parke"),
    ("arnavut", "Arnavut kaldırımı", 1.80, "doğal taş, çok pürüzlü"),
    ("stabilize", "Stabilize yol", 2.30, "sıkıştırılmış çakıl ve toprak"),
]
SURFACE_FACTOR = {k: f for k, _, f, _ in SURFACES}
SURFACE_LABEL = {k: lab for k, lab, _, _ in SURFACES}


def surface_factor(key_or_factor) -> float:
    """Accept a surface key, a numeric factor, or None (asphalt)."""
    if key_or_factor is None or key_or_factor == "":
        return 1.0
    if isinstance(key_or_factor, (int, float)):
        return float(key_or_factor)
    return SURFACE_FACTOR.get(str(key_or_factor), 1.0)
