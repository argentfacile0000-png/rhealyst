"""Amazon Bedrock avec une clé API Bedrock (jeton « Bearer ») : API Converse et liste des modèles.

L'API Converse fonctionne avec tous les modèles texte du catalogue Bedrock (Claude, Llama, Nova, Mistral,
gpt-oss…). La réponse est remise au format attendu par le générateur (choices[0].message.content, usage).
"""

from __future__ import annotations

import urllib.parse
from types import SimpleNamespace

import httpx

INVALID = 10 * 365 * 86400


class BedrockError(RuntimeError):
    def __init__(self, message: str, cooldown: float, reason: str):
        super().__init__(message)
        self.cooldown, self.reason = cooldown, reason


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"}


def _raise_for(resp: httpx.Response, model: str) -> None:
    if resp.status_code < 400:
        return
    try:
        body = resp.json()
    except ValueError:
        body = {}
    msg = str(body.get("message") or body.get("Message") or resp.text[:200])
    low = msg.lower()
    kind = resp.headers.get("x-amzn-errortype", "").split(":")[0]
    if resp.status_code in (401, 403):
        if "model" in low and ("access" in low or "not authorized" in low or "subscription" in low):
            raise BedrockError(f"Modèle « {model} » non activé sur ce compte AWS (console Bedrock, Accès aux "
                               f"modèles) : {msg}", 3600, "modèle non activé")
        raise BedrockError(f"Clé AWS Bedrock refusée : {msg}", INVALID, "invalide")
    if resp.status_code == 429 or "Throttling" in kind:
        raise BedrockError("Limite AWS Bedrock atteinte", 120, "limite de débit")
    if "ServiceQuotaExceeded" in kind:
        raise BedrockError("Quota AWS Bedrock atteint", 3600, "quota atteint")
    if resp.status_code in (400, 404):
        hint = ""
        if "inference profile" in low or "on-demand throughput" in low:
            hint = (" Ce modèle s'utilise via un profil d'inférence : choisis l'ID préfixé par la zone "
                    "(us., eu., apac. ou global.).")
        raise BedrockError(f"Modèle « {model} » refusé par Bedrock : {msg}.{hint}", 3600, "modèle invalide")
    raise BedrockError(f"AWS Bedrock en panne ({resp.status_code}) : {msg}", 120, "panne du fournisseur")


async def converse(key: str, region: str, model: str, messages: list[dict], *, max_tokens: int = 8192,
                   temperature: float | None = None, timeout: float = 300) -> SimpleNamespace:
    system = [{"text": m["content"]} for m in messages if m["role"] == "system"]
    convo = [{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages
             if m["role"] in ("user", "assistant")]
    url = (f"https://bedrock-runtime.{region}.amazonaws.com/model/"
           f"{urllib.parse.quote(model, safe='')}/converse")
    cap = max(256, min(int(max_tokens), 32000))
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            body: dict = {"messages": convo, "inferenceConfig": {"maxTokens": cap}}
            if system:
                body["system"] = system
            if temperature is not None:
                body["inferenceConfig"]["temperature"] = temperature
            try:
                resp = await client.post(url, json=body, headers=_headers(key))
            except httpx.HTTPError as exc:
                raise BedrockError(f"AWS Bedrock injoignable : {exc}", 60, "injoignable") from exc
            # Certains modèles acceptent moins de jetons de sortie : nouvel essai avec une limite plus basse.
            if resp.status_code == 400 and attempt == 0 and "token" in resp.text.lower() and cap > 4096:
                cap = 4096
                continue
            _raise_for(resp, model)
            break
    data = resp.json()
    parts = (((data.get("output") or {}).get("message") or {}).get("content")) or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    usage = data.get("usage") or {}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=usage.get("inputTokens", 0), completion_tokens=usage.get("outputTokens", 0)),
    )


async def list_models(key: str, region: str) -> list[str]:
    """Modèles texte disponibles dans la région : profils d'inférence puis modèles à la demande."""
    base = f"https://bedrock.{region}.amazonaws.com"
    out: list[str] = []
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(f"{base}/inference-profiles", params={"maxResults": 100}, headers=_headers(key))
            if r.status_code < 400:
                out += [p.get("inferenceProfileId", "") for p in r.json().get("inferenceProfileSummaries", [])]
            r = await client.get(f"{base}/foundation-models",
                                 params={"byOutputModality": "TEXT", "byInferenceType": "ON_DEMAND"},
                                 headers=_headers(key))
            _raise_for(r, "")
            out += [m.get("modelId", "") for m in r.json().get("modelSummaries", [])]
        except httpx.HTTPError as exc:
            raise BedrockError(f"AWS Bedrock injoignable : {exc}", 60, "injoignable") from exc
    seen, models = set(), []
    for m in out:
        if m and m not in seen:
            seen.add(m)
            models.append(m)
    return models
