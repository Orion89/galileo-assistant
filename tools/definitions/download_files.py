from tools.functions.download_external_file import download_external_file_tool

# Definición JSON schema para compatibilidad
download_external_file = {
    "name": "download_external_file",
    "description": "Download a publicly accessible file and attach it to the conversation",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTP/HTTPS link to a file (must be directly downloadable)."
            }
        },
        "required": ["url"]
    }
}

__all__ = ["download_external_file", "download_external_file_tool"]
