<p align="center">
  <img src="static/fitini_logo.png" alt="Fitini" width="180">
</p>

<h1 align="center">Fitini — Les murs ont des oreilles</h1>


Fitini analyse des **sources texte et audio** (les fichiers de l'utilisateur, ou des sources web qu'il verse lui-même) pour mener une investigation : il extrait les entités d'intérêt, les corrèle, bâtit un graphe et une chronologie, flague les signaux, monte un **dossier d'enquête** et l'exporte — le tout **tracé à la source**. 

Trois domaines (packs de connaissances) : **cybersécurité (CTI)**, **justice / Journalisme**, **santé / biomédical / pharmacovigilance**.

## Fonctions clés
- **INTERCEPT** — écoute micro en direct (FR/EN auto, transcription faster-whisper, VAD énergétique), flags et flèches en temps réel, modal d'alerte.
- **ANALYSE** — dépôt de fichiers (PDF / DOCX / images OCR / audio) → extraction → corrélation → rapport.
- **DOSSIER** — monte un dossier autour d'un sujet : **recherche web** (opt-in) → résultats **cochables** → téléchargement dans le dossier → **juxtaposition avant/maintenant** datée et sourcée → **export Markdown / PDF**.
- **Rapport déterministe** — ce qui ressort, qui est lié à qui, chronologie, flags, passages-clés **sourcés**. Aucun jugement émis : l'outil surface et aligne, **l'humain juge**.

## Extraction ouverte
- **Sélecteurs génériques** (sans lexique) : e-mail, téléphone, IP, URL, @pseudo, montants/FCFA, dates, coordonnées GPS, CVE, IBAN.
- **NER spaCy** (optionnelle) : PERSON / ORG / LIEU / DATE / MONEY, même pour des entités jamais listées. Repli propre si le modèle est absent.
- **Watchlist utilisateur** : le sujet / les sélecteurs saisis pilotent flags, priorité et alertes.
- Les packs de domaine (`profiles/*.json`) apportent une connaissance métier **optionnelle**, jamais la condition d'entrée.

## Flag (par type, puis par sujet)
Toute **entité détectée** (personne, organisation, lieu, montant, GPS, IBAN, CVE, arme/violence…) est flaguée automatiquement → flèche vers la matrice. Le **sujet déclaré** (watchlist) ajoute une couche de **priorité** et déclenche le **modal d'alerte URGENT**.

## Architecture
| Fichier | Rôle |
|---|---|
| `fitini_core.py` | moteur : extraction (sélecteurs + lexique + NER) → corrélation → hypothèses/lock → alertes → timeline |
| `packs.py` | charge et fusionne les packs de domaine sélectionnés |
| `server.py` | FastAPI : auth, WebSocket d'analyse, sessions, **dossiers**, **recherche web**, **export** |
| `audio_transcriber.py` | transcription micro / fichier (faster-whisper, VAD énergétique, hors boucle asyncio) |
| `doc_ingest.py` | extraction de texte PDF (PyMuPDF) / DOCX / images (OCR RapidOCR) |
| `dossiers.py` | store local des dossiers d'enquête (fichiers, hors-ligne) |
| `web_collect.py` | collecte web **opt-in** (Exa / Tavily) — presse, archives |
| `dossier_analyze.py` | juxtaposition avant/maintenant + entités sur les sources d'un dossier |
| `dossier_export.py` | export d'un dossier en Markdown / PDF |
| `db.py` | persistance SQLite (sessions, réglages) |
| `auth.py` | gate par token (démo-grade — à remplacer en production) |
| `static/index.html` | interface : INTERCEPT · ANALYSE · REPORT · DOSSIER · ARCHIVE · CONFIG |
| `profiles/*.json` | packs de connaissances par domaine |

## Structure du projet
```
Fitini/
├── app/                 # package Python (moteur + serveur + modules — voir tableau)
│   ├── server.py        # FastAPI — point d'entrée : app.server:app
│   ├── fitini_core.py
│   └── …                # packs, dossiers, web_collect, dossier_analyze/export, db, auth…
├── static/              # interface web + logo (servis par le serveur)
│   ├── index.html
│   └── fitini_logo.png
├── profiles/            # packs de domaine (cybersécurité / investigation / santé)
├── config.json          # auth, pipeline, réglages par défaut
├── requirements.txt
├── LICENSE
└── README.md
```
Données d'exécution non versionnées (voir `.gitignore`) : `uploads/`, `dossiers/`, `fitini.db`, `*.pdf`.

## Lancer
```bash
pip install -r requirements.txt
uvicorn app.server:app --port 8000
# puis ouvrir http://127.0.0.1:8000
```
Recherche web (optionnelle, une clé suffit) :
```bash
FITINI_WEB=1 EXA_API_KEY=... uvicorn app.server:app --port 8000
# ou TAVILY_API_KEY=...
```
Les **modèles** (Whisper, OCR RapidOCR, spaCy) ne sont **pas inclus** dans le dépôt : ils sont téléchargés / servis côté serveur. Le code reste utilisable tel quel.

## Auteur & licence
Développé par **The Day Info**. Open source sous licence **Apache-2.0** (voir [`LICENSE`](LICENSE) et [`NOTICE`](NOTICE)) — libre de télécharger, utiliser, modifier et redistribuer, avec clause de brevet.
