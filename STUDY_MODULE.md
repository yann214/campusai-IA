# Module `/study` — Résumé de cours et correction d'exercice

## Objectif

Deux routes indépendantes du RAG et du module `documents` :

- **`POST /study/summary`** : l'utilisateur envoie un document, un texte saisi, ou les deux. L'API en extrait le texte et renvoie un résumé structuré.
- **`POST /study/correction`** : l'utilisateur envoie un exercice (document ou texte saisi). L'API renvoie une correction détaillée.

Aucune écriture sur disque ni en base : tout est traité à la volée, en mémoire, pour la durée de la requête.

Le module réutilise **le même modèle et la même clé API Groq** que le reste du projet (`settings.groq_model`, `settings.groq_api_key` dans `app/config.py`). Seul le prompt système change selon la tâche.

---

## Fichiers ajoutés / modifiés

| Fichier | Statut | Rôle |
|---|---|---|
| `app/services/text_extraction_service.py` | **nouveau** | Extrait le texte brut d'un fichier uploadé (PDF, DOCX, TXT, image) |
| `app/services/study_service.py` | **nouveau** | Prompts système + appel LLM pour le résumé et la correction |
| `app/api/study_routes.py` | **nouveau** | Les deux routes `/study/summary` et `/study/correction` |
| `app/services/llm_service.py` | modifié | Ajout de `generate_custom()` et des paramètres `json_mode` / `max_tokens` dans `_call_model()` |
| `app/models/schemas.py` | modifié | Ajout de `SummaryResponse` et `CorrectionResponse` |
| `app/config.py` | modifié | Paramètres du module (taille max, extensions, troncature, tokens, chemin Tesseract) |
| `app/main.py` | modifié | Branchement du nouveau router |
| `requirements.txt` | modifié | `python-docx`, `pytesseract`, `Pillow` |
| `.env.example` | modifié | Variables `STUDY_*` et `TESSERACT_CMD_PATH` |

---

## Formats supportés

| Format | Extraction | Dépendance |
|---|---|---|
| `.txt`, `.md` | lecture directe (UTF-8, repli Latin-1) | aucune |
| `.pdf` | `pypdf` (déjà utilisé par le RAG) | déjà présente |
| `.docx` | `python-docx` | nouvelle |
| `.png`, `.jpg`, `.jpeg`, `.webp` | OCR via `pytesseract` + `Pillow` | nouvelle, **+ moteur système Tesseract** |
| `.doc` (Word 97-2003) | **non supporté** | voir plus bas |

### Images et OCR

`pytesseract` est un simple wrapper Python autour du moteur **Tesseract OCR**, qui est un programme **système** et non un paquet pip. Sans lui, l'upload d'une image renvoie une erreur claire (`501`) au lieu de planter. PDF, DOCX et TXT fonctionnent normalement sans Tesseract.

Installation :

```bash
# Linux (Debian/Ubuntu)
sudo apt update
sudo apt install tesseract-ocr tesseract-ocr-fra

# macOS
brew install tesseract tesseract-lang

# Windows
# installeur : https://github.com/UB-Mannheim/tesseract/wiki
```

Vérification :

```bash
tesseract --version
```

Si `tesseract` fonctionne dans ton terminal mais pas dans l'API (service systemd, Docker, autre utilisateur), le process Python ne voit pas le même `PATH`. Dans ce cas, renseigne le chemin complet dans `.env` :

```env
# Laisse vide si `tesseract` est déjà dans le PATH du système.
# Exemple Windows : C:\Program Files\Tesseract-OCR\tesseract.exe
TESSERACT_CMD_PATH=
```

#### Limites de l'OCR (surtout pour la correction)

- **L'écriture manuscrite est très mal lue.** Tesseract est conçu pour du texte imprimé ou tapé. Une copie d'élève écrite à la main donnera un texte souvent incomplet ou faux, et la correction sera faite sur ce texte erroné sans que le modèle puisse le savoir.
- **Les formules mathématiques, schémas et tableaux sont mal reconnus** (exposants, fractions, symboles déformés).
- **La qualité de la photo compte** : une image floue, penchée ou mal éclairée donne un mauvais texte.
- **Le modèle ne voit jamais l'image.** Il reçoit uniquement le texte extrait (le modèle Groq configuré est un modèle texte).
- **Le pack de langue français** (`tesseract-ocr-fra`) doit être installé, sinon les accents sont mal reconnus.

Recommandation : pour les cours tapés ou imprimés, l'OCR suffit. Pour des exercices manuscrits, afficher un avertissement côté frontend du type « l'écriture manuscrite donne un résultat peu fiable ». Une vraie solution passerait par un modèle de vision, non implémenté ici.

### Le `.doc`

Le format Word binaire (`.doc`, avant 2007) n'a pas de bibliothèque Python fiable sans dépendance système lourde (LibreOffice, antiword...). La route renvoie une erreur `415` demandant de convertir le fichier en `.docx` ou `.pdf`.

### PDF scannés

`pypdf` ne lit que la couche texte. Un PDF composé de photos de pages renvoie une erreur `422` (« aucun texte extrait ») et n'est pas passé par l'OCR. Cas non géré pour l'instant.

---

## Installation

```bash
pip install -r requirements.txt
```

Puis installer Tesseract si tu veux l'OCR (voir plus haut).

Variables d'environnement (dans `.env.example`) :

```env
STUDY_MAX_TOKENS=2048
STUDY_MAX_INPUT_CHARS=12000
STUDY_UPLOAD_MAX_SIZE_BYTES=15728640
TESSERACT_CMD_PATH=
```

- `STUDY_MAX_INPUT_CHARS` : si le texte envoyé au modèle dépasse cette longueur, il est **tronqué à la fin**. La réponse contient alors `"texte_tronque": true`, pour que le frontend puisse prévenir l'utilisateur.
- `STUDY_UPLOAD_MAX_SIZE_BYTES` : taille max du fichier uploadé (15 Mo par défaut).
- `TESSERACT_CMD_PATH` : chemin explicite de l'exécutable Tesseract, uniquement si absent du `PATH`.

---

## Utilisation par le frontend

### Résumé de cours

```
POST /api/v1/study/summary
Content-Type: multipart/form-data

file:  <PDF, DOCX, TXT ou image>   (optionnel)
texte: "texte à résumer"            (optionnel)
```

**Au moins un des deux champs est obligatoire**, sinon la route répond `400`. Trois cas possibles :

| Cas | Comportement |
|---|---|
| `file` seul | résumé du document |
| `texte` seul | résumé du texte saisi |
| `file` + `texte` | les deux sont combinés (texte du document d'abord, puis texte saisi, séparés par une ligne vide) puis résumés ensemble |

Exemple `curl` :

```bash
# Document seul
curl -X POST http://localhost:8000/api/v1/study/summary \
  -F "file=@/chemin/vers/cours-thermo.pdf"

# Texte seul
curl -X POST http://localhost:8000/api/v1/study/summary \
  -F "texte=La première loi de la thermodynamique stipule que..."

# Les deux
curl -X POST http://localhost:8000/api/v1/study/summary \
  -F "file=@/chemin/vers/cours-thermo.pdf" \
  -F "texte=Ajouter aussi la définition de l'entropie vue en TD"
```

Exemple fetch (frontend) :

```js
const formData = new FormData();
if (fichier) formData.append("file", fichier);
if (texteSaisi) formData.append("texte", texteSaisi);

const res = await fetch("/api/v1/study/summary", { method: "POST", body: formData });
```

Réponse :

```json
{
  "titre": "Thermodynamique - Chapitre 2",
  "resume": "Ce chapitre couvre la première loi de la thermodynamique...",
  "points_cles": [
    "L'énergie interne d'un système isolé est constante",
    "Le travail et la chaleur sont deux formes de transfert d'énergie"
  ],
  "texte_tronque": false,
  "model_used": "llama-3.3-70b-versatile"
}
```

### Correction d'exercice

Deux façons d'envoyer l'exercice, au choix :

**a) Via un document**

```bash
curl -X POST http://localhost:8000/api/v1/study/correction \
  -F "file=@/chemin/vers/exercice-eleve.pdf"
```

**b) Via du texte saisi manuellement**

```bash
curl -X POST http://localhost:8000/api/v1/study/correction \
  -F "texte=Exercice : résoudre x^2 - 5x + 6 = 0. Réponse de l'élève : x=2 et x=4"
```

Si les deux sont envoyés, le fichier est utilisé et le texte saisi est ignoré. Si aucun des deux n'est fourni, la route répond `400`.

Exemple fetch (cas texte) :

```js
const formData = new FormData();
formData.append("texte", texteSaisiParLEtudiant);

const res = await fetch("/api/v1/study/correction", { method: "POST", body: formData });
```

Réponse :

```json
{
  "correction": "La résolution est globalement bonne mais une racine est incorrecte...",
  "erreurs_identifiees": [
    "x=4 n'est pas une racine : vérifier en substituant dans l'équation"
  ],
  "note_estimee": "12/20",
  "conseils": [
    "Toujours vérifier ses racines en les réinjectant dans l'équation de départ"
  ],
  "texte_tronque": false,
  "model_used": "llama-3.3-70b-versatile"
}
```

Si le modèle ne trouve dans le texte qu'un énoncé (sans réponse d'élève), il renvoie un corrigé complet de l'exercice plutôt qu'une correction. C'est voulu et précisé dans le prompt système.

---

## Erreurs possibles

| Code | Cas |
|---|---|
| `400` | Fichier vide, ou aucun fichier ni texte fourni |
| `413` | Fichier trop volumineux |
| `415` | Extension non supportée (notamment `.doc`) |
| `422` | Aucun texte exploitable extrait (PDF scanné, fichier corrompu, image illisible...) |
| `500` | Erreur modèle (les deux modèles Groq, principal et secours, ont échoué) |
| `501` | Image envoyée mais OCR non installé ou Tesseract introuvable sur le serveur |

---

## Points d'attention

- **Pas d'authentification** sur ces routes pour l'instant (comme `/documents/upload`). À protéger avant la mise en production si l'accès doit être limité.
- **Rate limiting** : réutilise `rate_limit_ask`, la même limite que les autres routes IA. Un `RATE_LIMIT_STUDY` dédié peut être ajouté dans `config.py` si besoin.
- **Troncature** : pour un très long contenu, seul le début est analysé. Quand document et texte saisi sont combinés, le texte saisi vient après et est donc coupé en premier. Le champ `texte_tronque: true` permet au frontend d'avertir l'utilisateur.
- **Qualité de l'extraction** : si le texte extrait est illisible ou trop pauvre, le prompt demande au modèle de le signaler plutôt que d'inventer un résultat.