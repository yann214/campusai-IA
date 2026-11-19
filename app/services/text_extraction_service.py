"""
Extraction de texte depuis un fichier envoyé par l'utilisateur (PDF, DOCX,
TXT, image). Utilisé par le module "study" (résumé de cours, correction
d'exercice) — indépendant du chargeur RAG (app/ingestion/loader.py), qui lui
lit des fichiers déjà présents sur le disque de l'application.
"""

from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}


def _extract_pdf(contenu: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(contenu))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


def _extract_docx(contenu: bytes) -> str:
    import docx

    document = docx.Document(BytesIO(contenu))
    paragraphes = [p.text for p in document.paragraphs]
    return "\n".join(paragraphes).strip()


def _extract_txt(contenu: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return contenu.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="Impossible de lire l'encodage du fichier texte.")


def _extract_image(contenu: bytes) -> str:
    """
    OCR best-effort : nécessite les paquets Python 'pytesseract' + 'Pillow'
    ET le moteur Tesseract installé sur la machine (paquet système, pas pip).
    Si l'un des deux manque, on renvoie une erreur claire plutôt qu'un crash.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail=(
                "L'extraction de texte depuis une image (OCR) nécessite les paquets "
                "Python 'pytesseract' et 'Pillow', ainsi que le moteur Tesseract OCR "
                "installé sur le serveur (ex: apt install tesseract-ocr tesseract-ocr-fra). "
                "En attendant, envoie plutôt un PDF, DOCX ou TXT."
            ),
        )

    try:
        image = Image.open(BytesIO(contenu))
        texte = pytesseract.image_to_string(image, lang="fra+eng")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Impossible de lire le texte dans l'image : {e}")

    return texte.strip()


EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".txt": _extract_txt,
    ".md": _extract_txt,
    **{ext: _extract_image for ext in IMAGE_EXTENSIONS},
}


async def extract_text_from_upload(
    file: UploadFile,
    max_size_bytes: int,
    allowed_extensions: list[str],
) -> str:
    """Lit le fichier envoyé, vérifie taille/format, et renvoie son texte brut extrait."""
    extension = Path(file.filename or "").suffix.lower()

    if extension == ".doc":
        raise HTTPException(
            status_code=415,
            detail="Le format .doc (Word 97-2003) n'est pas supporté. "
            "Enregistre le fichier en .docx ou .pdf et réessaie.",
        )

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=415,
            detail=f"Format non supporté : '{extension}'. Formats acceptés : {sorted(allowed_extensions)}",
        )

    if extension not in EXTRACTORS:
        raise HTTPException(status_code=500, detail=f"Aucun extracteur configuré pour '{extension}'.")

    contenu = await file.read()
    if not contenu:
        raise HTTPException(status_code=400, detail="Fichier vide.")
    if len(contenu) > max_size_bytes:
        taille_mo = max_size_bytes / (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"Fichier trop volumineux (max {taille_mo:.0f} Mo).")

    texte = EXTRACTORS[extension](contenu)
    if not texte:
        raise HTTPException(
            status_code=422,
            detail="Aucun texte n'a pu être extrait de ce fichier "
            "(page scannée sans OCR, fichier corrompu, contenu vide...).",
        )
    return texte
