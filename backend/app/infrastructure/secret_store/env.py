"""Secretos desde variables de entorno (MVP; Supabase Vault en una fase posterior, §8.4).

`whatsapp_channels.access_token_ref` guarda el NOMBRE del secreto, nunca el valor. En este
adaptador el nombre es el de una variable de entorno, por ejemplo `WA_TOKEN_IMPORTADORA_A`.
Solo se aceptan nombres en mayúsculas con prefijo propio, para que una fila de la BD no
pueda pedir cualquier variable del proceso (por ejemplo DATABASE_URL).
"""

import os
import re
from collections.abc import Mapping

_NAME = re.compile(r"^WA_TOKEN_[A-Z0-9_]{1,60}$")


class EnvSecretStore:
    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def get(self, name: str) -> str | None:
        if not _NAME.fullmatch(name):
            return None
        value = self._environ.get(name)
        return value or None
