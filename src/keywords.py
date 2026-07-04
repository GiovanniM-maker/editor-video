"""
Vocabolario keyword multilingua (IT/EN) usato sia per taggare le scene sia per
interpretare la richiesta dell'utente.

Struttura: ogni "categoria" (tag) mappa una lista di sinonimi/parole spia.
Il matching è sempre fatto su testo normalizzato (minuscolo, senza accenti).
"""

from __future__ import annotations

# tag -> parole spia (IT + EN). Mantenuto piatto e semplice per l'MVP.
TAG_KEYWORDS: dict[str, list[str]] = {
    "fight": [
        "combattimento", "combatte", "battaglia", "scontro", "lotta", "duello",
        "fight", "fighting", "battle", "clash", "combat", "brawl",
    ],
    "action": [
        "azione", "corre", "insegue", "esplode", "salta", "veloce",
        "action", "chase", "explosion", "run", "attack", "attacca",
    ],
    "weapon": [
        "spada", "lama", "pugnale", "arco", "freccia", "pistola", "arma",
        "sword", "blade", "dagger", "bow", "arrow", "gun", "weapon",
    ],
    "monster": [
        "mostro", "mostri", "demone", "bestia", "creatura", "nemico", "nemici",
        "monster", "beast", "demon", "creature", "enemy", "enemies", "boss",
    ],
    "dungeon": [
        "dungeon", "sotterraneo", "caverna", "grotta", "labirinto",
        "cave", "cavern", "labyrinth", "gate", "portale",
    ],
    "blood": [
        "sangue", "ferita", "ferito", "morte", "muore",
        "blood", "wound", "wounded", "death", "die", "dies",
    ],
    "dialogue": [
        "dice", "parla", "chiede", "risponde", "dialogo", "conversazione",
        "say", "says", "talk", "talks", "ask", "asks", "reply", "conversation",
    ],
    "emotional": [
        "piange", "lacrime", "triste", "addio", "amore", "cuore", "dolore",
        "cry", "cries", "tears", "sad", "goodbye", "love", "heart", "pain", "sorrow",
    ],
    "slow": [
        "lento", "lenta", "calma", "pausa", "silenzio", "riposo",
        "slow", "calm", "quiet", "rest", "silence",
    ],
    "protagonist": [
        "protagonista", "eroe", "io ", "mi ", "sono io",
        "protagonist", "hero", " i ", " me ", " my ",
    ],
}

# Mappa: intento presente nella richiesta utente -> tag rilevanti da cercare.
# Le chiavi sono parole/frasi che possono comparire nel --request.
REQUEST_INTENTS: dict[str, list[str]] = {
    # combattimento / azione
    "combattimento": ["fight", "action", "weapon", "monster", "dungeon", "blood"],
    "combatt": ["fight", "action", "weapon", "monster", "dungeon", "blood"],
    "fight": ["fight", "action", "weapon", "monster", "dungeon", "blood"],
    "battaglia": ["fight", "action", "weapon", "monster", "blood"],
    "battle": ["fight", "action", "weapon", "monster", "blood"],
    "azione": ["action", "fight", "weapon"],
    "action": ["action", "fight", "weapon"],
    # dialogo
    "dialogo": ["dialogue"],
    "dialoghi": ["dialogue"],
    "dialogue": ["dialogue"],
    "parla": ["dialogue", "protagonist"],
    "talk": ["dialogue"],
    # emozionale
    "emozional": ["emotional"],
    "emotional": ["emotional"],
    "commovent": ["emotional"],
    # lento
    "lent": ["slow"],
    "slow": ["slow"],
    # protagonista
    "protagonist": ["protagonist", "dialogue"],
}
