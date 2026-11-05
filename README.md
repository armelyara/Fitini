# Fitini — moteur d'aide à l'investigation

Fitini analyse des **sources texte et audio** (les fichiers de l'utilisateur) pour une
investigation : il extrait les entités d'intérêt, les corrèle, bâtit un graphe et une
chronologie, flague les signaux, et prépare une ébauche de dossier — tout **tracé à la
source**. Hors-ligne et déterministe par défaut.

Trois domaines (packs de connaissances) : **cybersécurité (CTI)**, **justice / e-Discovery /
OSINT**, **biomédical / pharmacovigilance**.

## Extraction ouverte
- **Sélecteurs génériques** (sans lexique) : e-mail, téléphone, IP, URL, @pseudo,
  montants/FCFA, dates, coordonnées GPS, CVE, IBAN.
- **NER spaCy** (optionnelle, côté serveur) : PERSON / ORG / LIEU / DATE / MONEY, même pour
  des entités jamais listées. Repli propre si le modèle n'est pas installé.
- **Watchlist utilisateur** : le sujet / les sélecteurs saisis pilotent flags, lock et flèches.
- Les packs de domaine (`profiles/*.json`) apportent une connaissance métier **optionnelle**
  (TTP cyber, gènes/médicaments, red-flags financiers, termes de haine/violence), jamais la
  condition d'entrée.

## Flag déterministe
Un mot est flagué s'il est le **sujet suivi**, d'un **type à fort signal** (argent, IBAN,
coordonnée, CVE, violence…), s'il **déclenche une alerte** (`on_label` / `all_of`) ou
**satisfait un critère d'hypothèse**, ou s'il est une **nouvelle entité corrélée au sujet**.

## Architecture
| Fichier | Rôle |
|---|---|
| `fitini_core.py` | moteur : extraction (sélecteurs + lexique + NER) → corrélation → hypothèses/lock → alertes → timeline |
| `packs.py` | charge et fusionne les packs de domaine sélectionnés |
| `server.py` | FastAPI : auth, WebSocket d'analyse, sessions / cibles / réglages, analyse de fichier |
| `audio_transcriber.py` | transcription (micro / fichier) via faster-whisper, hors de la boucle asyncio |
| `db.py` | persistance SQLite (sessions, cibles, réglages) ; aucune donnée de démo |
| `auth.py` | gate par token (démo-grade — à remplacer en production) |
| `static/index.html` | interface : INTERCEPT · ANALYSE · REPORT · DOSSIER · ARCHIVE · CONFIG |
| `corpus_analyze.py`, `fitini_query.py` | outils CLI (cœur Phase 1 / analyste), pas encore câblés à l'UI |

## Lancer
```bash
pip install -r requirements.txt
uvicorn server:app --port 8000
```
NER spaCy (optionnelle, serveur) :
```bash
pip install spacy==3.7.6 && python -m spacy download fr_core_news_sm
```
Désactiver la NER : `FITINI_NER=0`.

## Auteur & licence
Développé par **VD Info** — open source. Vous êtes libres de télécharger, utiliser et modifier le code.
Les modèles (Whisper, OCR RapidOCR, spaCy) ne sont **pas inclus** dans le dépôt : ils sont téléchargés / servis côté serveur. Le code reste donc utilisable tel quel.
