"""
web_collect.py -- OPTIONAL web source collection (Phase 3, human-in-the-loop).

OFF by default. Enabled only when FITINI_WEB=1 AND a provider API key is set.
The offline core never calls a provider unless a search is actually requested.

Providers (auto-selected by whichever key is present):
  * Exa     -> EXA_API_KEY      (https://api.exa.ai/search)
  * Tavily  -> TAVILY_API_KEY   (https://api.tavily.com/search)

Flow:
  search(subject, query) -> list[candidate dicts]      (discovery, no download)
  fetch_text(url)        -> rough deterministic text    (used by the collect step)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
import ipaddress
import socket
from typing import Any, Dict, List
from urllib.parse import urlparse

_TIMEOUT = 20


class WebCollectError(RuntimeError):
    """Operator-facing error (disabled / missing key / provider failure)."""


def _enabled() -> bool:
    return os.environ.get("FITINI_WEB", "0") == "1"


def _provider():
    """Return (name, api_key) or raise an operator-facing error."""
    if not _enabled():
        raise WebCollectError("Collecte web desactivee : lancer le serveur avec FITINI_WEB=1.")
    if os.environ.get("EXA_API_KEY"):
        return "exa", os.environ["EXA_API_KEY"]
    if os.environ.get("TAVILY_API_KEY"):
        return "tavily", os.environ["TAVILY_API_KEY"]
    raise WebCollectError("Aucune cle de recherche : definir EXA_API_KEY ou TAVILY_API_KEY cote serveur.")


def _host(url: str) -> str:
    try:
        return (urlparse(url or "").netloc or "").replace("www.", "")
    except Exception:
        return ""


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _candidate(type_: str, title: str, url: str, author: str,
               platform: str, date: str, preview: str) -> Dict[str, Any]:
    cid = hashlib.sha1((url or title or "").encode("utf-8")).hexdigest()[:12]
    return {
        "id": cid, "type": type_, "title": title or url or "(sans titre)",
        "url": url or "", "author": author or "", "platform": platform or _host(url),
        "published_date": (date or "")[:10], "preview": (preview or "").strip()[:400],
        "orientation": "unknown",
    }


def _search_exa(key: str, subject: str, query: str) -> List[Dict[str, Any]]:
    q = (subject + " " + query).strip()
    out = _post_json(
        "https://api.exa.ai/search",
        {"query": q, "numResults": 15, "type": "auto",
         "contents": {"text": {"maxCharacters": 600}}},
        {"Content-Type": "application/json", "x-api-key": key},
    )
    return [_candidate("article", r.get("title"), r.get("url"), r.get("author"),
                       _host(r.get("url")), r.get("publishedDate"), r.get("text"))
            for r in out.get("results", [])]


def _search_tavily(key: str, subject: str, query: str) -> List[Dict[str, Any]]:
    q = (subject + " " + query).strip()
    out = _post_json(
        "https://api.tavily.com/search",
        {"api_key": key, "query": q, "max_results": 15, "search_depth": "advanced"},
        {"Content-Type": "application/json"},
    )
    return [_candidate("article", r.get("title"), r.get("url"), "",
                       _host(r.get("url")), r.get("published_date"), r.get("content"))
            for r in out.get("results", [])]


def search(subject: str, query: str) -> List[Dict[str, Any]]:
    name, key = _provider()
    try:
        return _search_exa(key, subject, query) if name == "exa" \
            else _search_tavily(key, subject, query)
    except urllib.error.HTTPError as e:
        raise WebCollectError("%s : HTTP %s (cle ou quota ?)" % (name, e.code))
    except urllib.error.URLError as e:
        raise WebCollectError("%s : reseau indisponible (%s)" % (name, e.reason))


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def _is_public_host(host: str) -> bool:
    """Reject internal/private/loopback targets (basic SSRF guard)."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def fetch_text(url: str) -> str:
    """Download a page and return a rough text extraction (deterministic, no LLM)."""
    p = urlparse(url or "")
    if p.scheme not in ("http", "https"):
        raise WebCollectError("URL non supportee (http/https uniquement).")
    if not _is_public_host(p.hostname or ""):
        raise WebCollectError("Hote interne/prive bloque.")
    req = urllib.request.Request(url, headers={"User-Agent": "Fitini/0.3"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        raw = r.read().decode("utf-8", "ignore")
    return _strip_html(raw)
