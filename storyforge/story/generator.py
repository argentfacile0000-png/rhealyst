"""Génération d'histoires via l'API OpenAI (ChatGPT)."""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field

import openai
from openai import AsyncOpenAI

from ..config import Settings
from .prompts import SYSTEM_PROMPT, build_length_fix_prompt, build_user_prompt
from .llm_keys import INVALID_FOR
from .styles import DEFAULT_FORMATS, FORMATS, get_language, random_dna

log = logging.getLogger(__name__)

# Gain de vitesse apporté par le resserrage des silences entre les mots.
PAUSE_TIGHTENING_GAIN = 1.06


class StoryError(RuntimeError):
    pass


@dataclass
class GeneratedStory:
    format: str
    language: str
    title: str
    parts: list[str]
    youtube_title: str
    description: str
    hashtags: list[str]
    tiktok_caption: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    dna: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def hook(self) -> str:
        return first_sentence(self.parts[0]) if self.parts else ""

    @property
    def word_count(self) -> int:
        return sum(count_words(p) for p in self.parts)


def count_words(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if re.search(r"\w", w)])


def first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.!?…])(\s|$)", text.strip(), flags=re.S)
    return (m.group(1) if m else text.strip()).strip()


def effective_wps(language: str, voice_speed: float, speedup: float) -> float:
    """Débit réel (mots/s) après vitesse ElevenLabs, accélération et resserrage des pauses."""
    return get_language(language).wps * max(voice_speed, 0.5) * max(speedup, 0.5) * PAUSE_TIGHTENING_GAIN


def words_for_duration(seconds: float, language: str, voice_speed: float, speedup: float) -> int:
    return max(40, int(round(seconds * effective_wps(language, voice_speed, speedup))))


_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0000FE0F\U0000200D]", flags=re.UNICODE
)


def clean_narration(text: str) -> str:
    """Nettoie le texte pour la synthèse vocale (markdown, emojis, hashtags, mentions de partie...)."""
    t = text.replace("\r", "")
    t = re.sub(r"^\s*(part|partie|parte|teil)\s*\d+\s*[:.\-–—]\s*", "", t, flags=re.I)
    t = re.sub(r"(^|\s)#\w+", " ", t)
    t = re.sub(r"[*_`~]+(?=\w)|(?<=\w)[*_`~]+", "", t)
    t = _EMOJI_RE.sub("", t)
    t = re.sub(r"\[[^\]]{0,40}\]", "", t)  # [rires], [pause]...
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _split_evenly(text: str, n: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    total = sum(count_words(s) for s in sentences)
    target = total / n
    parts: list[list[str]] = [[]]
    acc = 0
    for s in sentences:
        if acc >= target * len(parts) and len(parts) < n:
            parts.append([])
        parts[-1].append(s)
        acc += count_words(s)
    return [" ".join(p) for p in parts if p]


def _error_text(exc: Exception) -> str:
    """Texte complet d'une erreur d'API (certains fournisseurs mettent le message dans le corps)."""
    parts = [str(exc), str(getattr(exc, "body", "") or "")]
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            parts.append(response.text)
        except Exception:
            pass
    return " ".join(p for p in parts if p)


def _is_key_error(exc: Exception) -> bool:
    text = _error_text(exc).lower()
    return "api key" in text or "api_key" in text


def _is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return m.startswith(("o1", "o3", "o4", "gpt-5"))


class KeyFailure(StoryError):
    """Erreur propre à une clé (invalide, quota, limite) : la clé suivante peut prendre le relais."""

    def __init__(self, message: str, cooldown: float, reason: str):
        super().__init__(message)
        self.cooldown, self.reason = cooldown, reason


class StoryGenerator:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None, keys=None):
        self.settings = settings
        self._client = client  # client imposé (tests) : pas de trousseau
        self.keys = keys  # LLMKeyRing
        self.on_event = None  # async (texte) : bascule de clé, pour prévenir l'admin
        self.last_model = ""

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            llm = self.settings.llm
            if not llm.api_key:
                raise StoryError("Aucune clé d'IA : ajoute-en une dans Réglages, Clés API")
            self._client = AsyncOpenAI(api_key=llm.api_key, base_url=llm.base_url, max_retries=2)
        return self._client

    def _request_kwargs(self, messages: list[dict], long_form: bool = False, llm=None) -> dict:
        llm = llm or self.settings.llm
        kwargs: dict = {"model": llm.model, "messages": messages, "response_format": {"type": "json_object"}}
        if llm.provider == "openai":
            if _is_reasoning_model(llm.model):
                kwargs["max_completion_tokens"] = 32000 if long_form else 16000
            else:
                kwargs["max_completion_tokens"] = 12000 if long_form else 4000
                kwargs["temperature"] = self.settings.openai_temperature
                kwargs["presence_penalty"] = 0.3
        elif llm.provider == "gemini":
            # Les modèles Gemini récents « réfléchissent » : on limite la réflexion et on laisse de la marge.
            kwargs["max_tokens"] = 24000 if long_form else 8192
            kwargs["temperature"] = self.settings.openai_temperature
            kwargs["reasoning_effort"] = "low"
        else:
            kwargs["max_tokens"] = 12000 if long_form else 4096
            kwargs["temperature"] = self.settings.openai_temperature
        return kwargs

    @staticmethod
    async def _create_with(client: AsyncOpenAI, llm, kwargs: dict):
        invalid_key = KeyFailure(f"Clé API {llm.label} invalide", INVALID_FOR, "invalide")
        try:
            try:
                return await client.chat.completions.create(**kwargs)
            except openai.BadRequestError as exc:
                if _is_key_error(exc):  # Gemini signale une clé invalide en 400
                    raise invalid_key from exc
                # Paramètre non pris en charge par ce fournisseur : nouvel essai avec le strict minimum.
                log.warning("%s a refusé la requête (%s) — nouvel essai simplifié", llm.label, exc)
                minimal = {"model": kwargs["model"], "messages": kwargs["messages"]}
                try:
                    return await client.chat.completions.create(**minimal)
                except openai.BadRequestError as exc2:
                    if _is_key_error(exc2):
                        raise invalid_key from exc2
                    raise StoryError(f"{llm.label} a refusé la requête : {_error_text(exc2)[:200]}") from exc2
        except openai.RateLimitError as exc:
            quota = "quota" in _error_text(exc).lower()
            raise KeyFailure(f"Limite de {llm.label} atteinte" + (" (offre gratuite)" if llm.free else ""),
                             3600 if quota else 120, "quota atteint" if quota else "limite de débit") from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise invalid_key from exc
        except openai.NotFoundError as exc:
            raise KeyFailure(f"Modèle « {llm.model} » introuvable chez {llm.label}", 3600, "modèle introuvable") from exc
        except openai.APIConnectionError as exc:
            raise KeyFailure(f"{llm.label} injoignable : {exc}", 60, "injoignable") from exc
        except openai.InternalServerError as exc:
            raise KeyFailure(f"{llm.label} en panne : {exc}", 120, "panne du fournisseur") from exc

    async def _create(self, kwargs: dict, long_form: bool = False):
        """Envoie la requête ; avec le trousseau, essaie les clés une à une jusqu'à ce qu'une réponde."""
        if self._client is not None or self.keys is None:
            llm = self.settings.llm
            self.last_model = llm.model
            try:
                return await self._create_with(self.client, llm, kwargs)
            except KeyFailure as exc:
                raise StoryError(str(exc)) from exc
        candidates = self.keys.available()
        if not candidates:
            if self.keys.all():
                raise StoryError("Toutes les clés d'IA sont indisponibles (quota ou invalides) : ajoute une clé "
                                 "dans Réglages, Clés API")
            raise StoryError("Aucune clé d'IA : ajoute-en une dans Réglages, Clés API")
        errors = []
        for i, key in enumerate(candidates):
            llm = key.config(self.settings)
            req = dict(kwargs, **{k: v for k, v in self._request_kwargs(kwargs["messages"], long_form, llm).items()
                                  if k != "messages"})
            try:
                resp = await self._call_key(key, llm, req, long_form)
            except KeyFailure as exc:
                errors.append(f"{key.label} {key.masked} : {exc.reason}")
                await self.keys.mark_failed(key, exc.cooldown, exc.reason)
                if self.on_event and i + 1 < len(candidates):
                    nxt = candidates[i + 1]
                    try:
                        await self.on_event(f"Clé IA {key.label} {key.masked} : {exc.reason}, "
                                            f"passage à {nxt.label} {nxt.masked}")
                    except Exception:
                        pass
                continue
            self.keys.current = key.kid
            self.last_model = llm.model
            return resp
        raise StoryError("Aucune clé d'IA n'a répondu : " + " ; ".join(errors))

    async def _call_key(self, key, llm, req: dict, long_form: bool = False):
        if key.provider == "bedrock":
            from . import bedrock

            try:
                return await bedrock.converse(
                    key.key, key.region or "us-east-1", llm.model, req["messages"],
                    max_tokens=req.get("max_tokens") or req.get("max_completion_tokens") or (16000 if long_form else 8192),
                    temperature=req.get("temperature"),
                )
            except bedrock.BedrockError as exc:
                raise KeyFailure(str(exc), exc.cooldown, exc.reason) from exc
        client = AsyncOpenAI(api_key=key.key, base_url=llm.base_url, max_retries=1)
        return await self._create_with(client, llm, req)

    async def ping_key(self, key) -> str:
        """Vérifie une clé (et son modèle) avec une requête minuscule. Retourne « fournisseur · modèle »."""
        llm = key.config(self.settings)
        await self._call_key(key, llm, {"model": llm.model, "max_tokens": 64,
                                        "messages": [{"role": "user", "content": "Réponds juste OK."}]})
        return f"{llm.label} · {llm.model}"

    async def list_models(self, key) -> list[str]:
        """Modèles proposés par le fournisseur de cette clé (liste vide si elle ne peut pas être lue)."""
        try:
            if key.provider == "bedrock":
                from . import bedrock

                return await bedrock.list_models(key.key, key.region or "us-east-1")
            llm = key.config(self.settings)
            client = AsyncOpenAI(api_key=key.key, base_url=llm.base_url, max_retries=0, timeout=20)
            page = await client.models.list()
            return sorted({m.id.removeprefix("models/") for m in page.data})
        except Exception as exc:
            log.info("Liste des modèles indisponible (%s) : %s", key.label, exc)
            return []

    async def ping(self) -> str:
        """Vérifie la première clé disponible (ou le client imposé)."""
        if self.keys is not None and self._client is None:
            keys = self.keys.available()
            if not keys:
                raise StoryError("Aucune clé d'IA disponible")
            try:
                return await self.ping_key(keys[0])
            except KeyFailure as exc:
                raise StoryError(str(exc)) from exc
        llm = self.settings.llm
        await self._create({"model": llm.model, "messages": [{"role": "user", "content": "Réponds juste OK."}],
                            "max_tokens": 64})
        return f"{llm.label} · {llm.model}"

    async def _complete(self, messages: list[dict], long_form: bool = False) -> tuple[dict, int, int]:
        resp = await self._create(self._request_kwargs(messages, long_form), long_form)
        content = (resp.choices[0].message.content or "").strip()
        usage = resp.usage
        pt = getattr(usage, "prompt_tokens", 0) or 0
        ct = getattr(usage, "completion_tokens", 0) or 0
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", content, flags=re.S)
            if not m:
                raise StoryError("Réponse du modèle illisible (JSON attendu)")
            data = json.loads(m.group(0))
        return data, pt, ct

    @staticmethod
    def _normalize(data: dict, parts: int) -> dict:
        raw_parts = data.get("parts")
        if isinstance(raw_parts, str):
            raw_parts = [raw_parts]
        if not raw_parts and data.get("story"):
            raw_parts = [str(data["story"])]
        texts = [clean_narration(str(p)) for p in (raw_parts or []) if str(p).strip()]
        if not texts:
            raise StoryError("Le modèle n'a renvoyé aucune narration")
        if len(texts) > parts:
            texts = texts[: parts - 1] + [" ".join(texts[parts - 1 :])]
        if len(texts) < parts:
            texts = _split_evenly(" ".join(texts), parts)
        data["parts"] = texts

        tags = data.get("hashtags") or []
        if isinstance(tags, str):
            tags = re.split(r"[\s,]+", tags)
        data["hashtags"] = [re.sub(r"[^\w]", "", str(t)) for t in tags if re.sub(r"[^\w]", "", str(t))][:10]
        data["title"] = str(data.get("title") or texts[0][:90]).strip().strip('"')[:200]
        data["youtube_title"] = str(data.get("youtube_title") or data["title"]).strip().strip('"')[:100]
        data["description"] = str(data.get("description") or "").strip()
        data["tiktok_caption"] = str(data.get("tiktok_caption") or data["title"]).strip()[:2000]
        return data

    async def generate(
        self,
        *,
        language: str = "fr",
        formats: list[str] | None = None,
        narrator_gender: str = "any",
        parts: int = 1,
        seconds_per_part: int = 75,
        voice_speed: float = 1.0,
        speedup: float = 1.0,
        custom_instructions: str = "",
        avoid_titles: list[str] | None = None,
        rng: random.Random | None = None,
        long_form: bool = False,
    ) -> GeneratedStory:
        rng = rng or random.Random()
        parts = 1 if long_form else max(1, min(int(parts), 6))
        valid = [f for f in (formats or []) if f in FORMATS] or DEFAULT_FORMATS
        fmt = FORMATS[rng.choice(valid)]
        lang = get_language(language)
        dna = random_dna(narrator_gender, rng)
        words = words_for_duration(seconds_per_part, lang.code, voice_speed, speedup)

        user_prompt = build_user_prompt(
            language=lang,
            fmt=fmt,
            dna=dna,
            parts=parts,
            words_per_part=words,
            seconds_per_part=int(seconds_per_part),
            avoid_titles=avoid_titles or [],
            custom_instructions=custom_instructions,
            long_form=long_form,
        )
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}]
        data, pt, ct = await self._complete(messages, long_form)
        data = self._normalize(data, parts)

        total = sum(count_words(p) for p in data["parts"])
        target_total = words * parts
        if total < target_total * 0.72 or total > target_total * 1.45:
            log.info("Longueur hors cible (%s mots / %s) — correction", total, target_total)
            messages += [
                {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
                {"role": "user", "content": build_length_fix_prompt(total, words, parts)},
            ]
            try:
                fixed, pt2, ct2 = await self._complete(messages, long_form)
                fixed = self._normalize(fixed, parts)
                new_total = sum(count_words(p) for p in fixed["parts"])
                if abs(new_total - target_total) < abs(total - target_total):
                    data = fixed
                pt, ct = pt + pt2, ct + ct2
            except Exception as exc:  # on garde la 1re version
                log.warning("Correction de longueur échouée: %s", exc)

        return GeneratedStory(
            format=fmt.key,
            language=lang.code,
            title=data["title"],
            parts=data["parts"],
            youtube_title=data["youtube_title"],
            description=data["description"],
            hashtags=data["hashtags"],
            tiktok_caption=data["tiktok_caption"],
            model=self.last_model or self.settings.llm.model,
            prompt_tokens=pt,
            completion_tokens=ct,
            dna=dna.as_prompt(),
        )
