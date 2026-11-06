"""
packs.py — app-layer loader: maps a UI domain id to one or more knowledge packs.

File I/O lives here, NOT in fitini-core (the core stays pure/offline). The UI's
mandatory multi-select domain choice is resolved through load_domains(): selected
domain ids -> their packs are loaded and merged into one pipeline via
combine_profiles.
"""
import json
import os

from .fitini_core import combine_profiles

HERE = os.path.dirname(os.path.abspath(__file__))  # packaged profiles live here

# UI domain id -> knowledge pack file(s) under profiles/
DOMAIN_PACKS = {
    "justice": ["investigation/ediscovery", "investigation/intercept"],
    "osint":   ["investigation/social"],
    "cti":     ["cybersecurity/cti"],
    "pharma":  ["healthcare/pharmacovigilance"],
}


def _load_pack(name):
    path = os.path.join(HERE, "profiles", name + ".json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_domains(domain_ids):
    """Combine the packs of the selected UI domains into one profile.

    domain_ids: a non-empty list of ids from DOMAIN_PACKS. Order preserved,
    duplicates removed. Raises ValueError if nothing valid was selected.
    """
    names = []
    for d in domain_ids:
        names += DOMAIN_PACKS.get(d, [])
    names = list(dict.fromkeys(names))  # dedup, keep order
    if not names:
        raise ValueError("no valid domain selected: %r" % (domain_ids,))
    profiles = [_load_pack(n) for n in names]
    label = "+".join(domain_ids)
    return combine_profiles(profiles, name=label, pid="sel:" + label)


DOMAIN_CATALOG = [
    {"id": "justice", "name": "Justice / e-Discovery",
     "desc": "Pieces, correspondances, depositions."},
    {"id": "osint", "name": "Reseaux sociaux (OSINT)",
     "desc": "Comptes, pseudonymes, lieux, resolution d'alias."},
    {"id": "cti", "name": "Cybersecurite (CTI)",
     "desc": "Acteurs, IOC, infrastructures, campagnes."},
    {"id": "pharma", "name": "Biomedical / Pharmacovigilance",
     "desc": "Substances, effets indesirables, signaux."},
]


def domain_catalog():
    """UI-facing list of selectable domains (id, name, desc)."""
    return DOMAIN_CATALOG


# Per-domain demo scenario (the stream the live flux replays for that domain)
DEMO_PACK = {
    "justice": "investigation/intercept",
    "osint":   "investigation/social",
    "cti":     "cybersecurity/cti",
    "pharma":  "healthcare/pharmacovigilance",
}


def demo_lines(domain_ids):
    """Stream lines to replay for the selected domain(s) in simulation mode."""
    for d in domain_ids:
        name = DEMO_PACK.get(d)
        if name:
            pack = _load_pack(name)
            if pack.get("stream_lines"):
                return pack["stream_lines"]
    return []
