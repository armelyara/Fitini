"""
dossier_export.py -- deterministic export of an investigation dossier (no LLM).

Builds a shareable report from a dossier + its analysis:
  * Markdown  -- no dependency.
  * PDF       -- via fpdf2 (pure Python, offline); core font, text sanitised to
                 Latin-1 so no font file needs bundling.

Content: synthesis, dated before/now juxtaposition, extracted entities, and the
traced source list. Fitini surfaces and aligns; the human judges.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List


def _header(dossier: Dict[str, Any], analysis: Dict[str, Any]) -> List[str]:
    sel = ", ".join(analysis.get("selectors", []))
    gen = time.strftime("%d/%m/%Y %H:%M")
    return [
        "# DOSSIER — %s" % dossier.get("name", ""),
        "",
        "`%s`  ·  généré le %s  ·  opérateur : %s" % (
            dossier.get("id", ""), gen, dossier.get("operator", "—")),
        "",
        "**Sujet / sélecteurs :** %s" % (sel or "—"),
        "",
        "**Synthèse :** %d source(s) versée(s) · %d passage(s) retenu(s) · %d entité(s) extraite(s)." % (
            analysis.get("sources", 0), len(analysis.get("passages", [])),
            len(analysis.get("entities", []))),
        "",
    ]


def to_markdown(dossier: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    out: List[str] = []
    out += _header(dossier, analysis)

    out += ["## Juxtaposition dans le temps (avant → maintenant)", ""]
    passages = analysis.get("passages", [])
    if passages:
        for p in passages:
            date = p.get("date") or "date inconnue"
            src = p.get("title") or p.get("platform") or "source"
            url = p.get("url") or ""
            matched = ", ".join(p.get("matched", []))
            out.append("- **%s** — %s%s" % (date, src, (" (%s)" % url if url else "")))
            out.append("  > %s" % (p.get("passage", "").strip()))
            if matched:
                out.append("  _termes : %s_" % matched)
            out.append("")
    else:
        out += ["_Aucun passage mentionnant les sélecteurs dans les sources versées._", ""]

    out += ["## Entités extraites", ""]
    ents = analysis.get("entities", [])
    if ents:
        for e in ents:
            out.append("- %s (%s) — %s mention(s)" % (
                e.get("name", ""), e.get("category", ""), e.get("count", "")))
    else:
        out.append("_Aucune._")
    out.append("")

    out += ["## Sources versées", ""]
    for s in dossier.get("sources", []):
        out.append("- %s%s — %s · %s · %s" % (
            s.get("title", s.get("id", "")),
            (" (%s)" % s.get("url")) if s.get("url") else "",
            s.get("published_date", "—"), s.get("platform", "—"),
            s.get("orientation", "unknown")))
    out.append("")

    out += [
        "---",
        "_Rapport déterministe : il organise et trace les éléments extraits, "
        "il n'émet aucun jugement. Vérifier avant d'agir._",
        "_Développé par VD Info — open source._",
    ]
    return "\n".join(out)


# --------------------------------- PDF ------------------------------------- #
_SUB = {
    "—": "-", "–": "-", "…": "...", "↳": ">", "•": "-",
    "→": "->", "←": "<-", "↑": "^", "↓": "v",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "€": "EUR", "→": "->",
}


def _latin1(s: str) -> str:
    for k, v in _SUB.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def to_pdf(dossier: Dict[str, Any], analysis: Dict[str, Any]) -> bytes:
    try:
        from fpdf import FPDF  # fpdf2
    except Exception:
        raise RuntimeError("Export PDF : installer fpdf2 (pip install fpdf2) cote serveur")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def h(txt, size=13):
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", size)
        pdf.multi_cell(0, 7, _latin1(txt))
        pdf.set_font("Helvetica", "", 10)

    def line(txt, style="", size=10):
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(0, 5, _latin1(txt))

    sel = ", ".join(analysis.get("selectors", []))
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 9, _latin1("DOSSIER - " + dossier.get("name", "")))
    line("%s  -  genere le %s  -  operateur : %s" % (
        dossier.get("id", ""), time.strftime("%d/%m/%Y %H:%M"),
        dossier.get("operator", "-")), size=9)
    line("Sujet / selecteurs : " + (sel or "-"), "B")
    line("Synthese : %d source(s) - %d passage(s) - %d entite(s)" % (
        analysis.get("sources", 0), len(analysis.get("passages", [])),
        len(analysis.get("entities", []))))

    h("Juxtaposition dans le temps (avant -> maintenant)")
    passages = analysis.get("passages", [])
    if passages:
        for p in passages:
            line("%s  -  %s" % (p.get("date") or "date inconnue",
                                p.get("title") or p.get("platform") or "source"), "B")
            if p.get("url"):
                line(p["url"], size=8)
            line(p.get("passage", "").strip())
            if p.get("matched"):
                line("termes : " + ", ".join(p["matched"]), "I", 9)
            pdf.ln(1)
    else:
        line("Aucun passage mentionnant les selecteurs.", "I")

    h("Entites extraites")
    ents = analysis.get("entities", [])
    if ents:
        for e in ents:
            line("- %s (%s) - %s mention(s)" % (
                e.get("name", ""), e.get("category", ""), e.get("count", "")))
    else:
        line("Aucune.", "I")

    h("Sources versees")
    for s in dossier.get("sources", []):
        line("- %s [%s] - %s - %s" % (
            s.get("title", s.get("id", "")), s.get("published_date", "-"),
            s.get("platform", "-"), s.get("orientation", "unknown")))

    pdf.ln(3)
    line("Rapport deterministe : il trace les elements extraits, il n'emet aucun "
         "jugement. Verifier avant d'agir.", "I", 9)
    line("Developpe par VD Info - open source.", "I", 9)

    out = pdf.output()
    return bytes(out)
