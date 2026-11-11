import os
import json
import config
import ai_usage_tracker


def ask_ai_opinion(context_summary):
    """Avis de l'IA, ou None si indisponible (le bot se rabat alors sur le Niveau 1, sans jamais planter)."""
    if not config.USE_AI_LAYER:
        return None
    ai_usage_tracker.record_ai_call()
    try:
        if config.AI_PROVIDER == "openrouter":
            return _ask_openrouter(context_summary)
        elif config.AI_PROVIDER == "anthropic":
            return _ask_anthropic(context_summary)
        else:
            return None
    except Exception as e:
        print(f"[ai_providers] Appel IA échoué, repli sur le Niveau 1 : {e}")
        return None


PROMPT_TEMPLATE = """Tu es un filtre de validation pour un bot de trading crypto en mode démo.
{context}
Réponds UNIQUEMENT avec un JSON:
{{"decision": "APPROVE" ou "REJECT", "confidence": 0-100, "reason": "...", "size_factor": 0.3-1.0, "sl_tightness": 0.6-1.0, "tp_extension": 1.0-2.0}}
"""


def _parse_ai_json_response(text):
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Pas de JSON trouvé")
    parsed = json.loads(text[start:end + 1])
    assert parsed["decision"] in ("APPROVE", "REJECT")
    assert 0 <= float(parsed["confidence"]) <= 100
    parsed.setdefault("size_factor", 1.0)
    parsed.setdefault("sl_tightness", 1.0)
    parsed.setdefault("tp_extension", 1.0)
    return parsed


def _ask_openrouter(context_summary):
    """Essaie les modèles de config.OPENROUTER_MODELS dans l'ordre (les modèles gratuits sont
    régulièrement retirés : erreur 404 -> on passe automatiquement au suivant)."""
    from openai import OpenAI
    api_key = os.environ["OPENROUTER_API_KEY"]
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    models = getattr(config, "OPENROUTER_MODELS", None) or [config.OPENROUTER_MODEL]

    last_error = None
    for model in models:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(context=context_summary)}],
                temperature=0.2, timeout=40,
            )
            text = response.choices[0].message.content
            if not text:
                raise ValueError("Réponse vide")
            return _parse_ai_json_response(text)
        except Exception as e:
            last_error = e
            print(f"[ai_providers] Modèle {model} indisponible ou réponse invalide ({str(e)[:120]}) -> essai du suivant")
    raise last_error


def _ask_anthropic(context_summary):
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL, max_tokens=300,
        messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(context=context_summary)}],
        timeout=20,
    )
    return _parse_ai_json_response(response.content[0].text)
