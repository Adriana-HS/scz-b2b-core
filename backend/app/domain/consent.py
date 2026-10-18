"""Consentimiento para mensajes proactivos (RF-08, EXT-16, política de WhatsApp).

* La baja ("BAJA", "STOP", "NO MÁS"...) debe respetarse de inmediato.
* Se reconoce solo si el mensaje COMPLETO es la palabra clave, normalizado: "no más frenos,
  quiero cotizar" no es una baja. Un falso positivo le quitaría al cliente sus recordatorios;
  un falso negativo lo atiende el agente (#81), que también puede registrar la baja.
* El opt-in de marketing nunca se infiere de una palabra suelta: lo registra el agente o el
  dashboard con confirmación explícita.
"""

import re
import unicodedata
from enum import StrEnum


class ConsentKind(StrEnum):
    OPTIN_UTILITY = "OPTIN_UTILITY"  # recordatorios y avisos (plantillas de utilidad)
    OPTIN_MARKETING = "OPTIN_MARKETING"  # promociones (plantillas de marketing)
    OPTOUT = "OPTOUT"  # baja de todo mensaje proactivo; prevalece


class ConsentSource(StrEnum):
    WHATSAPP_KEYWORD = "WHATSAPP_KEYWORD"
    AGENT = "AGENT"
    DASHBOARD = "DASHBOARD"
    IMPORT = "IMPORT"


_OPTOUT_KEYWORDS = frozenset(
    {"baja", "stop", "no mas", "no mas mensajes", "darme de baja", "dar de baja", "cancelar suscripcion",
     "unsubscribe", "parar"}
)  # fmt: skip
_OPTIN_UTILITY_KEYWORDS = frozenset({"alta", "start", "darme de alta"})

_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """minúsculas, sin tildes, sin signos y con espacios simples: "¡No MÁS!" -> "no mas"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SPACES.sub(" ", _NON_WORD.sub(" ", without_accents)).strip()


def classify_keyword(text: str | None) -> ConsentKind | None:
    """La palabra clave de consentimiento que es TODO el mensaje, o None."""
    if not text or len(text) > 64:
        return None
    normalized = normalize(text)
    if normalized in _OPTOUT_KEYWORDS:
        return ConsentKind.OPTOUT
    if normalized in _OPTIN_UTILITY_KEYWORDS:
        return ConsentKind.OPTIN_UTILITY
    return None


CONFIRMATION_TEXT = {
    ConsentKind.OPTOUT: (
        "Listo, ya no te enviaremos recordatorios ni promociones. "
        "Si nos escribes, te seguimos atendiendo. Para volver a recibirlos, escribe ALTA."
    ),
    ConsentKind.OPTIN_UTILITY: (
        "Listo, te enviaremos recordatorios y avisos de tus pedidos y citas. "
        "Para dejar de recibirlos, escribe BAJA."
    ),
}
