"""
Service "study" — résumé de cours et correction d'exercice.

Réutilise le même client/modèle Groq que le reste du projet
(app/services/llm_service.py : settings.groq_model, settings.groq_api_key).
Seul le prompt système change selon la tâche demandée.
"""

import json

from app.config import get_settings
from app.services.llm_service import llm_service

settings = get_settings()

SYSTEM_PROMPT_RESUME = """Tu es un assistant pédagogique qui aide des étudiants à réviser.
On te donne un texte de cours : extrait d'un document (PDF, Word, image scannée...) et/ou saisi directement par l'étudiant.
Règles à respecter strictement :
- Base-toi UNIQUEMENT sur le texte fourni, n'invente aucune information absente du texte.
- Si le texte est trop court, incohérent ou ne ressemble pas à un contenu de cours \
(page blanche, texte illisible après extraction...), dis-le clairement dans "resume" \
et laisse "points_cles" vide.
- Rédige un résumé clair, structuré et fidèle au contenu, adapté à la révision avant un examen.
- Identifie entre 3 et 8 points clés à retenir, sous forme de phrases courtes.
- Réponds en français si question en francais et anglais si question en anglais.

Tu dois répondre UNIQUEMENT avec un objet JSON valide, sans aucun texte avant ou après, \
au format exact suivant :
{
  "titre": "titre court du cours/chapitre, déduit du contenu",
  "resume": "résumé structuré du cours (plusieurs paragraphes si nécessaire)",
  "points_cles": ["point clé 1", "point clé 2", "..."]
}
"""

SYSTEM_PROMPT_CORRECTION = """Tu es un enseignant qui corrige l'exercice d'un étudiant.
On te donne le texte brut extrait d'un document (énoncé et/ou réponse de l'étudiant) \
ou saisi directement par l'étudiant.

Règles à respecter strictement :
- Identifie dans le texte l'énoncé de l'exercice et la réponse de l'étudiant si elle est \
présente. Si seul un énoncé est fourni (sans réponse de l'étudiant), fournis un corrigé \
complet de l'exercice plutôt qu'une correction d'une réponse.
- Si une réponse d'étudiant est présente, corrige-la précisément : indique ce qui est juste, \
ce qui est faux, et pourquoi.
- Sois rigoureux mais bienveillant dans le ton, comme un enseignant qui veut faire progresser \
l'étudiant plutôt que simplement sanctionner.
- Si le texte fourni ne ressemble pas à un exercice exploitable (texte illisible, hors-sujet), \
dis-le clairement dans "correction" et laisse les autres champs vides.
- Réponds en français.

Tu dois répondre UNIQUEMENT avec un objet JSON valide, sans aucun texte avant ou après, \
au format exact suivant :
{
  "correction": "corrigé détaillé et/ou correction de la réponse de l'étudiant",
  "erreurs_identifiees": ["erreur 1 identifiée dans la réponse de l'étudiant", "..."],
  "note_estimee": "note estimée sur 20 sous forme de texte, ex: '14/20', ou null si non pertinent",
  "conseils": ["conseil 1 pour progresser", "conseil 2", "..."]
}
"""


def _tronquer(texte: str) -> tuple[str, bool]:
    """Coupe le texte s'il dépasse la taille max envoyée au modèle, et le signale."""
    if len(texte) <= settings.study_max_input_chars:
        return texte, False
    return texte[: settings.study_max_input_chars], True


def _parse_json_response(raw_content: str, champs_defaut: dict, champ_principal: str) -> dict:
    """Parse la sortie JSON du modèle ; en cas d'échec, renvoie le texte brut dans `champ_principal`."""
    try:
        data = json.loads(raw_content)
        return {**champs_defaut, **data}
    except (json.JSONDecodeError, TypeError):
        print("⚠️  Réponse du modèle non-JSON malgré le mode structuré (module study).")
        return {**champs_defaut, champ_principal: raw_content}


def summarize_course(texte_extrait: str) -> dict:
    """Résume un texte de cours. Retourne un dict conforme à SummaryResponse (sans model_used en trop)."""
    texte, tronque = _tronquer(texte_extrait)
    user_prompt = f"Texte extrait du document de cours :\n\n{texte}"

    resultat = llm_service.generate_custom(SYSTEM_PROMPT_RESUME, user_prompt, max_tokens=settings.study_max_tokens)
    parsed = _parse_json_response(
        resultat["content"],
        champs_defaut={"titre": "", "resume": "", "points_cles": []},
        champ_principal="resume",
    )

    return {
        "titre": str(parsed.get("titre", "")).strip() or "Résumé",
        "resume": str(parsed.get("resume", "")).strip(),
        "points_cles": [str(p).strip() for p in parsed.get("points_cles", []) if str(p).strip()][:8],
        "texte_tronque": tronque,
        "model_used": resultat["model_used"],
    }


def correct_exercise(texte_extrait: str) -> dict:
    """Corrige un exercice. Retourne un dict conforme à CorrectionResponse (sans model_used en trop)."""
    texte, tronque = _tronquer(texte_extrait)
    user_prompt = f"Exercice à corriger :\n\n{texte}"

    resultat = llm_service.generate_custom(
        SYSTEM_PROMPT_CORRECTION, user_prompt, max_tokens=settings.study_max_tokens
    )
    parsed = _parse_json_response(
        resultat["content"],
        champs_defaut={"correction": "", "erreurs_identifiees": [], "note_estimee": None, "conseils": []},
        champ_principal="correction",
    )

    note = parsed.get("note_estimee")
    return {
        "correction": str(parsed.get("correction", "")).strip(),
        "erreurs_identifiees": [str(e).strip() for e in parsed.get("erreurs_identifiees", []) if str(e).strip()],
        "note_estimee": str(note).strip() if note else None,
        "conseils": [str(c).strip() for c in parsed.get("conseils", []) if str(c).strip()],
        "texte_tronque": tronque,
        "model_used": resultat["model_used"],
    }
