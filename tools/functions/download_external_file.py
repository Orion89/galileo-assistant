import aiohttp
import json
import logging
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Optional

from agents import function_tool
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

FS_EXTS = {
    ".txt", ".md", ".rtf",
    ".json", ".yaml", ".yml", ".xml",
    ".html", ".htm",
    ".pdf",
    ".doc", ".docx", ".ppt", ".pptx",
    ".odt", ".odp",
    ".epub",
}

CI_ONLY_EXTS = {
    ".csv", ".tsv", ".tab",
    ".xls", ".xlsx", ".ods",
}

ALL_SUPPORTED_EXTS = FS_EXTS | CI_ONLY_EXTS


async def download_file_from_url(url: str) -> tuple[Path, str]:
    """Descarga un archivo desde una URL pública a un archivo temporal local."""
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url, timeout=30) as resp:
            if resp.status != 200:
                raise ValueError(f"Error descargando archivo HTTP {resp.status} desde {url}")
            
            content_type = resp.headers.get("content-type", "")
            suffix = Path(url).suffix or mimetypes.guess_extension(content_type) or ".bin"
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await resp.read()
                tmp.write(content)
                return Path(tmp.name), suffix.lower()


async def download_and_process_external_file(
    url: str,
    vector_store_id: Optional[str] = None,
    client: Optional[AsyncOpenAI] = None,
) -> str:
    """
    Descarga un archivo público, valida el formato, lo sube a OpenAI File Storage
    y opcionalmente lo indexa en el Vector Store de la sesión.
    """
    try:
        local_path, ext = await download_file_from_url(url)
    except Exception as e:
        logger.error(f"Fallo al descargar archivo {url}: {e}")
        return json.dumps({
            "status": "error",
            "message": f"No se pudo descargar el archivo desde {url}: {str(e)}"
        }, ensure_ascii=False)

    if ext not in ALL_SUPPORTED_EXTS:
        return json.dumps({
            "status": "rejected",
            "message": f"El formato '{ext}' no está soportado. Formatos válidos: {', '.join(sorted(ALL_SUPPORTED_EXTS))}"
        }, ensure_ascii=False)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return json.dumps({
            "status": "downloaded_local",
            "filename": local_path.name,
            "message": f"Archivo descargado localmente ({local_path.name}). API Key no configurada para indexación."
        }, ensure_ascii=False)

    oai = client or AsyncOpenAI(api_key=api_key)

    try:
        # Subir a OpenAI File Storage
        uploaded_file = await oai.files.create(
            file=local_path,
            purpose="responses" if hasattr(oai, "responses") else "assistants"
        )

        vs_status = "no_vector_store"
        target_vs_id = vector_store_id or os.getenv("VECTOR_STORE_ID")
        
        # Si es un formato documental apto para búsqueda y hay un Vector Store activo, agregarlo
        if ext in FS_EXTS and target_vs_id:
            try:
                await oai.beta.vector_stores.files.create(
                    vector_store_id=target_vs_id,
                    file_id=uploaded_file.id
                )
                vs_status = f"indexed_in_vector_store:{target_vs_id}"
            except Exception as ve:
                logger.warning(f"No se pudo indexar en Vector Store {target_vs_id}: {ve}")
                vs_status = f"vector_store_error:{str(ve)}"

        return json.dumps({
            "status": "success",
            "file_id": uploaded_file.id,
            "filename": Path(url).name or local_path.name,
            "extension": ext,
            "vector_store_status": vs_status,
            "tools_enabled": ["code_interpreter"] + (["file_search"] if ext in FS_EXTS else []),
            "message": f"Archivo '{Path(url).name}' procesado con éxito (file_id: {uploaded_file.id})."
        }, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Error al subir archivo a OpenAI: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Error al procesar y subir archivo a OpenAI: {str(e)}"
        }, ensure_ascii=False)


@function_tool(
    name_override="download_external_file",
    description_override="Download a publicly accessible file and attach it to the conversation/vector store."
)
async def download_external_file_tool(url: str) -> str:
    """
    Descarga un archivo accesible públicamente (HTTP/HTTPS) y lo pone a disposición
    del agente y sus herramientas (File Search o Code Interpreter).
    
    Args:
        url: Enlace directo HTTP/HTTPS al archivo descargable.
    """
    return await download_and_process_external_file(url=url)
