"""
cli.py -- command-line entry point for Fitini.

    fitini serve   [--host H] [--port P] [--reload] [--open]
    fitini analyze <path> [--subject "a, b"] [--format md|pdf] [--out FILE]

`serve` launches the local web app; `analyze` runs the deterministic engine over a
file or folder and prints (or writes) a report -- no browser, no LLM.
"""
from __future__ import annotations

import argparse
import os
import sys


def _cmd_serve(args):
    import uvicorn
    if args.open:
        import threading
        import webbrowser
        host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
        url = "http://%s:%d" % (host, args.port)
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("fitini.server:app", host=args.host, port=args.port, reload=args.reload)


def _collect_files(path):
    if os.path.isfile(path):
        return [path]
    out = []
    if os.path.isdir(path):
        for root, _dirs, names in os.walk(path):
            for n in sorted(names):
                out.append(os.path.join(root, n))
    return out


def _cmd_analyze(args):
    from . import doc_ingest, dossier_analyze, dossier_export
    files = _collect_files(args.path)
    if not files:
        print("Aucun fichier a analyser dans : %s" % args.path, file=sys.stderr)
        return 2
    sources = []
    for i, f in enumerate(files):
        if not doc_ingest.is_text_like(f):
            continue
        try:
            text = doc_ingest.extract_text(f)
        except Exception as exc:
            print("  (ignore) %s : %s" % (os.path.basename(f), exc), file=sys.stderr)
            continue
        sources.append({
            "id": "s%d" % (i + 1), "title": os.path.basename(f), "url": "",
            "published_date": "", "platform": "fichier local",
            "orientation": "source", "raw_text": text,
        })
    if not sources:
        print("Aucun texte exploitable (formats : txt, md, csv, pdf, docx, images).",
              file=sys.stderr)
        return 2
    dossier = {"id": "CLI", "name": os.path.basename(os.path.abspath(args.path)) or "dossier",
               "operator": "cli", "sources": sources}
    analysis = dossier_analyze.analyze(dossier, args.subject or "")
    if args.format == "pdf":
        data = dossier_export.to_pdf(dossier, analysis)
        out = args.out or (dossier["name"] + ".pdf")
        with open(out, "wb") as fh:
            fh.write(data)
        print("Rapport PDF ecrit : %s" % out)
    else:
        md = dossier_export.to_markdown(dossier, analysis)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(md)
            print("Rapport Markdown ecrit : %s" % args.out)
        else:
            print(md)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="fitini", description="Fitini - du cas au dossier.")
    sub = p.add_subparsers(dest="cmd")

    ps = sub.add_parser("serve", help="Lancer l'interface web locale.")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8000)
    ps.add_argument("--reload", action="store_true")
    ps.add_argument("--open", action="store_true", help="Ouvrir le navigateur au demarrage.")
    ps.set_defaults(func=_cmd_serve)

    pa = sub.add_parser("analyze", help="Analyser un fichier/dossier -> rapport.")
    pa.add_argument("path")
    pa.add_argument("--subject", default="", help="Sujet / selecteurs (separes par des virgules).")
    pa.add_argument("--format", choices=["md", "pdf"], default="md")
    pa.add_argument("--out", help="Fichier de sortie (sinon stdout pour md).")
    pa.set_defaults(func=_cmd_analyze)

    args = p.parse_args(argv)
    if not getattr(args, "cmd", None):
        p.print_help()
        return 1
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
