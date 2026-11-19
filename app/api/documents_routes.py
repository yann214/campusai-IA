"""
Routes de recherche/téléchargement/upload de documents — indépendantes du RAG.

- POST /documents/chat : l'étudiant demande un document en langage naturel,
  le modèle appelle l'outil de recherche et on renvoie les documents trouvés
  avec un lien de téléchargement pour chacun.
- GET /documents/{id}/download : télécharge le fichier correspondant.
- GET /documents/sous-dossiers : liste les sous-dossiers existants dans
  data/files/, à proposer au frontend avant un upload.
- POST /documents/upload : dépose un fichier dans le sous-dossier choisi et
  synchronise immédiatement la table `documents`.
"""

import unicodedata
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.models.schemas import (
    DocumentChatRequest,
    DocumentChatResponse,
    DocumentInfo,
    DocumentUploadResponse,
    SousDossiersResponse,
)
from app.rate_limiter import limiter
from app.services.document_chat_service import document_chat_service
from app.services.document_service import (
    get_document_by_id,
    insert_document,
    list_sous_dossiers_disque,
)

router = APIRouter()
settings = get_settings()

EXTENSION_TO_TYPE = {".pdf": "pdf", ".doc": "doc", ".docx": "docx"}


@router.post("/documents/chat", response_model=DocumentChatResponse, tags=["Documents"])
@limiter.limit(settings.rate_limit_ask)
async def chat_documents(request: Request, payload: DocumentChatRequest):
    """
    Reçoit un message du type "je veux tel document" et renvoie une réponse
    textuelle courte accompagnée des documents trouvés (avec lien de
    téléchargement), ou une liste vide si rien n'a été trouvé.
    """
    try:
        result = document_chat_service.answer(payload.message)
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Le service de recherche de documents est temporairement indisponible : {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la recherche du document : {str(e)}",
        )

    documents = [
        DocumentInfo(
            id=doc["id"],
            nom=doc["nom_original"],
            sous_dossier=doc.get("sous_dossier"),
            type_fichier=doc.get("type_fichier"),
            url=f"{settings.api_prefix}/documents/{doc['id']}/download",
        )
        for doc in result["sources"]
    ]

    return DocumentChatResponse(answer=result["answer"], sources=documents)


@router.get("/documents/{doc_id}/download", tags=["Documents"])
async def download_document(doc_id: int):
    """Télécharge le fichier correspondant à l'id donné."""
    doc = get_document_by_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document introuvable")

    files_root = Path(settings.files_root_dir).resolve()
    file_path = (files_root / doc["chemin_relatif"]).resolve()

    # Empêche toute sortie du dossier autorisé (path traversal), même si
    # chemin_relatif contenait un ".." malencontreux en base.
    if files_root not in file_path.parents and file_path != files_root:
        raise HTTPException(status_code=400, detail="Chemin de fichier invalide")

    if not file_path.exists():
        raise HTTPException(status_code=410, detail="Le fichier n'existe plus sur le serveur")

    media_type = "application/pdf" if doc["type_fichier"] == "pdf" else (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    return FileResponse(
        path=file_path,
        filename=doc["nom_original"],
        media_type=media_type,
    )

@router.get("/documents/sous-dossiers", response_model=SousDossiersResponse, tags=["Documents"])
async def get_sous_dossiers():
    """
    Liste les sous-dossiers existants dans data/files/. Le frontend doit
    appeler cette route pour peupler son menu de destination AVANT de
    proposer l'upload à l'utilisateur : l'upload n'accepte qu'un sous_dossier
    présent dans cette liste.
    """
    return SousDossiersResponse(sous_dossiers=list_sous_dossiers_disque())


def _nom_fichier_securise(nom_original: str) -> str:
    """
    Construit un nom de fichier sûr pour le disque : ascii, sans espace, et
    rendu unique avec un suffixe court pour ne jamais écraser un fichier
    existant portant le même nom.
    """
    original_path = Path(nom_original)
    extension = original_path.suffix.lower()
    base = original_path.stem

    base_normalise = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    base_normalise = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in base_normalise).strip("-")
    base_normalise = base_normalise or "document"

    suffixe = uuid.uuid4().hex[:8]
    return f"{base_normalise}-{suffixe}{extension}"


@router.post("/documents/upload", response_model=DocumentUploadResponse, tags=["Documents"])
@limiter.limit(settings.rate_limit_ask)
async def upload_document(
    request: Request,
    file: UploadFile = File(..., description="Fichier PDF ou Word à déposer"),
    sous_dossier: str = Form(..., description="Sous-dossier de destination (doit exister, voir /documents/sous-dossiers)"),
    description: str | None = Form(default=None, description="Description utilisée pour la recherche (optionnel)"),
    tags: str | None = Form(default=None, description="Mots-clés séparés par des virgules (optionnel)"),
):
    """
    Dépose un fichier dans data/files/<sous_dossier>/ puis insère
    immédiatement la ligne correspondante dans la table `documents`
    (synchronisation automatique base <-> disque, pas besoin de relancer
    scripts/sync_documents.py pour ce fichier).
    """
    # --- 1. Valider le sous-dossier choisi ---
    sous_dossiers_valides = list_sous_dossiers_disque()
    if sous_dossier not in sous_dossiers_valides:
        raise HTTPException(
            status_code=400,
            detail=f"Sous-dossier inconnu : '{sous_dossier}'. "
            f"Sous-dossiers disponibles : {sous_dossiers_valides}",
        )

    # --- 2. Valider l'extension ---
    nom_original = file.filename or "document"
    extension = Path(nom_original).suffix.lower()
    if extension not in settings.upload_allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Extension non autorisée : '{extension}'. "
            f"Autorisées : {settings.upload_allowed_extensions}",
        )

    # --- 3. Lire le contenu et vérifier la taille ---
    contenu = await file.read()
    if len(contenu) > settings.upload_max_size_bytes:
        taille_mo = settings.upload_max_size_bytes / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"Fichier trop volumineux (max {taille_mo:.0f} Mo)",
        )
    if not contenu:
        raise HTTPException(status_code=400, detail="Fichier vide")

    # --- 4. Construire le chemin de destination ---
    files_root = Path(settings.files_root_dir).resolve()
    dossier_destination = (files_root / sous_dossier).resolve()

    # Empêche toute sortie du dossier autorisé (path traversal via un
    # sous_dossier malicieux du type "../../etc").
    if files_root not in dossier_destination.parents and dossier_destination != files_root:
        raise HTTPException(status_code=400, detail="Sous-dossier invalide")

    nom_fichier = _nom_fichier_securise(nom_original)
    chemin_absolu = dossier_destination / nom_fichier
    chemin_relatif = f"{sous_dossier}/{nom_fichier}"

    # --- 5. Écrire le fichier sur disque ---
    try:
        chemin_absolu.write_bytes(contenu)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Impossible d'écrire le fichier : {e}")

    # --- 6. Synchroniser la base immédiatement ---
    try:
        doc = insert_document(
            nom_original=nom_original,
            nom_fichier=nom_fichier,
            sous_dossier=sous_dossier,
            chemin_relatif=chemin_relatif,
            type_fichier=EXTENSION_TO_TYPE.get(extension, extension.lstrip(".")),
            description=description,
            tags=tags,
        )
    except Exception as e:
        # La base a échoué après écriture du fichier : on retire le fichier
        # orphelin pour ne pas désynchroniser disque et base.
        chemin_absolu.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'enregistrement en base : {e}")

    return DocumentUploadResponse(
        message="Document déposé et enregistré avec succès.",
        document=DocumentInfo(
            id=doc["id"],
            nom=doc["nom_original"],
            sous_dossier=doc["sous_dossier"],
            type_fichier=doc["type_fichier"],
            url=f"{settings.api_prefix}/documents/{doc['id']}/download",
        ),
    )
