import aiohttp, tempfile, mimetypes, json
from pathlib import Path
from openai import OpenAI

async def download_and_attach(url: str, allowed_file_exts:dict=None, code_interpreter_file_exts:dict=None, oai_client:OpenAI=None) -> dict:
    # 1) descargamos
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url, timeout=30) as resp:
            if resp.status != 200:
                return None
            suffix = Path(url).suffix or mimetypes.guess_extension(resp.headers.get("content-type",""))
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(await resp.read())
                local_path = Path(tmp.name)

    # 2) elegimos herramientas
    ext = local_path.suffix.lower()
    if ext in allowed_file_exts:          # lista que ya usas
        tools = [{"type":"code_interpreter"},{"type":"file_search"}]
    elif ext in code_interpreter_file_exts:
        tools = [{"type":"code_interpreter"}]
    else:
        return None             # formato no soportado

    # 3) subimos a File Storage
    up = await oai_client.files.create(file=local_path, purpose="assistants")
    return {"file_id": up.id, "tools": tools}
