"""Lo que el revisor lee del entorno y comparte entre sus pasos: variables obligatorias, proveedores del modelo, identidad de hallazgos y la clave de error del resultado."""

import os
import sys

import review_domain


# Only DeepSeek V4.1 Flash is allowed. OpenCode Go serves it in OpenAI format only, so
# Claude Code reaches it through a local LiteLLM proxy; DeepSeek's own API speaks Anthropic.
PROVIDERS = {
    "opencode-go": {
        "label": "DeepSeek V4.1 Flash · OpenCode Go",
        "model": "deepseek-v4.1-flash",
        "upstream": "https://opencode.ai/zen/go/v1",
        "via_proxy": True,
        "prices": None,
    },
    "deepseek": {
        "label": "DeepSeek V4.1 Flash · API DeepSeek",
        "model": "deepseek-flash[1m]",
        "upstream": "https://api.deepseek.com/anthropic",
        "via_proxy": False,
        "prices": (0.30, 0.006, 1.20),
    },
}


def env(name):
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"ai-review: falta la variable {name}")
    return value


ERROR_KEY = "ai_review_error"
SECRETOS_DEL_ENTORNO = ("API_KEY", "FALLBACK_API_KEY", "GH_TOKEN", "GITHUB_TOKEN")


def secretos_del_entorno():
    """Los valores que nunca se publican: redact los quita de todo texto visible."""
    return [os.environ.get(nombre, "") for nombre in SECRETOS_DEL_ENTORNO]


def get_provider():
    name = os.environ.get("PROVIDER") or "opencode-go"
    if name not in PROVIDERS:
        sys.exit(
            f"ai-review: proveedor '{name}' no permitido; usa uno de: {', '.join(PROVIDERS)}"
        )
    return name, PROVIDERS[name]


IDENTIDADES = ("current", "anchors")


def politica_de_identidad():
    """FINDING_IDENTITY del env: current (default) o anchors; lo demás se
    rechaza antes de llamar al modelo (plan F0)."""
    valor = (os.environ.get("FINDING_IDENTITY") or "current").strip().lower()
    if valor not in IDENTIDADES:
        sys.exit(
            f"ai-review: FINDING_IDENTITY '{valor}' no admitido; usa current o anchors"
        )
    return valor


def politica_de_revision(manifest=None, identidad=None):
    """ReviewPolicy normaliza la identidad externa (`current` → `titles`)."""
    return review_domain.ReviewPolicy(
        finding_identity=identidad or politica_de_identidad(),
        diff_mode=(manifest or {}).get("mode", "full"),
    )
