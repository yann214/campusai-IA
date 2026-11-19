"""
Routes du module "study" — résumé de cours et correction d'exercice.
Indépendant du RAG et du module documents : rien n'est lu/écrit dans
data/files/ ni dans la table MySQL `documents`, tout est traité à la volée.

- POST /study/summary    : envoie un document (pdf, docx, txt, image) -> résumé.
- POST /study/correction : envoie un document OU un texte saisi -> correction.
"""

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.config import get_settings
from app.models.schemas import CorrectionResponse, SummaryResponse
from app.rate_limiter import limiter
from app.services.study_service import correct_exercise, summarize_course
from app.services.text_extraction_service import extract_text_from_upload

router = APIRouter()
settings = get_settings()


@router.post("/study/summary", response_model=SummaryResponse, tags=["Study"])
@limiter.limit(settings.rate_limit_ask)
async def summary_from_document(
    request: Request,
    file: UploadFile | None = File(default=None, description="Document de cours : PDF, DOCX, TXT ou image (optionnel)"),
    texte: str | None = Form(default=None, description="Texte à résumer, seul ou en complément du document (optionnel)"),
):
    """
    Résume un cours à partir d'un document, d'un texte saisi, ou des deux.
    Si les deux sont fournis, le texte extrait du document et le texte saisi
    sont combinés avant d'être envoyés au modèle.
    """
    texte_saisi = texte.strip() if texte else ""

    if not file and not texte_saisi:
        raise HTTPException(
            status_code=400,
            detail="Fournis au moins un fichier ('file') ou un texte ('texte').",
        )

    morceaux: list[str] = []

    if file:
        texte_document = await extract_text_from_upload(
            file,
            max_size_bytes=settings.study_upload_max_size_bytes,
            allowed_extensions=settings.study_allowed_extensions,
        )
        morceaux.append(texte_document)

    if texte_saisi:
        morceaux.append(texte_saisi)

    contenu_complet = "\n\n".join(morceaux)

    try:
        resultat = summarize_course(contenu_complet)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la génération du résumé : {e}")

    return SummaryResponse(**resultat)

@router.post("/study/correction", response_model=CorrectionResponse, tags=["Study"])
@limiter.limit(settings.rate_limit_ask)
async def correction_exercice(
    request: Request,
    file: UploadFile | None = File(default=None, description="Exercice en document : PDF, DOCX, TXT ou image"),
    texte: str | None = Form(default=None, description="Exercice saisi manuellement (alternative au fichier)"),
):
    """Corrige un exercice fourni soit en document, soit en texte saisi manuellement."""
    if not file and not (texte and texte.strip()):
        raise HTTPException(
            status_code=400,
            detail="Fournis soit un fichier ('file'), soit un texte ('texte') — au moins l'un des deux.",
        )

    if file:
        contenu_texte = await extract_text_from_upload(
            file,
            max_size_bytes=settings.study_upload_max_size_bytes,
            allowed_extensions=settings.study_allowed_extensions,
        )
    else:
        contenu_texte = texte.strip()

    try:
        resultat = correct_exercise(contenu_texte)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la correction : {e}")

    return CorrectionResponse(**resultat)
