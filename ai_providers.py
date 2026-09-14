"""
ai_providers.py
===============
Couche d'abstraction pour appeler UNE IA de validation, quel que soit le
fournisseur.
"""

import os
import json
import config
import ai_usage_tracker


def ask_ai_opinion(context_summary: str) -> dict | None:
    """
    Envoie un résumé de la situation à l'IA configurée et récupère son
    avis structuré. Retourne None si l'appel échoue pour quelque raison
    que ce soit.
    """
    if not config.USE_AI_LAYER:
        return None

    ai_usage_tracker.record_ai_call()   # compte la tentative, succès ou non

    try:
        if config.AI_PROVIDER == "openrouter":
            return _ask_openrouter(context_summary)
        elif config.AI_PROVIDER == "anthropic":
            return _ask_anthropic(context_summary)
        else:
            print(f"[ai_providers] Fournisseur inconnu : {config.AI_PROVIDER}")
            return None
    except Exception as e:
        print(f"[ai_providers] Appel IA échoué, repli sur le Niveau 1. Détail : {e}")
        return None


PROMPT_TEMPLATE = """Tu es un filtre de validation pour un bot de trading crypto en mode démo.
Voici un signal technique déjà détecté par des règles quantitatives :

{context}

Réponds UNIQUEMENT avec un JSON de cette forme, sans aucun texte autour :
{{
  "decision": "APPROVE" ou "REJECT",
  "confidence": nombre de 0 à 100,
  "reason": "explication en une phrase",
  "size_factor": nombre de 0.3 à 1.0 (optionnel, 1.0 par défaut -- réduis-le si le contexte te semble incertain, jamais au-dessus de 1.0),
  "sl_tightness": nombre de 0.6 à 1.0 (optionnel, 1.0 par défaut -- réduis-le pour un stop-loss plus serré si tu vois un risque particulier),
  "tp_extension": nombre de 1.0 à 2.0 (optionnel, 1.0 par défaut -- augmente-le si le contexte te semble très favorable pour viser plus loin)
}}
"""


def _parse_ai_json_response(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Pas de JSON trouvé dans la réponse de l'IA")
    parsed = json.loads(text[start:end + 1])
    assert parsed["decision"] in ("APPROVE", "REJECT")
    assert 0 <= float(parsed["confidence"]) <= 100

    parsed.setdefault("size_factor", 1.0)
    parsed.setdefault("sl_tightness", 1.0)
    parsed.setdefault("tp_extension", 1.0)
    return parsed


def _ask_openrouter(context_summary: str) -> dict:
    from openai import OpenAI

    api_key = os.environ["OPENROUTER_API_KEY"]
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    response = client.chat.completions.create(
        model=config.OPENROUTER_MODEL,
        messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(context=context_summary)}],
        temperature=0.2,
        timeout=20,
    )
    text = response.choices[0].message.content
    return _parse_ai_json_response(text)


def _ask_anthropic(context_summary: str) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(context=context_summary)}],
        timeout=20,
    )
    text = response.content[0].text
    return _parse_ai_json_response(text)
