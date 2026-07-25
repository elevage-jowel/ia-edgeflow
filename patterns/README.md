# Phase 2 — reconnaissance de patterns sur le marché en direct

## Ce qui existe déjà

`engine/market_scanner.py` scanne en direct une liste de symboles (indépendamment de tout trade copié) pour repérer le setup Smart Money Concepts standard — un **order block juste après une cassure de structure** — et alerte (Telegram/email) avec une entrée/SL/TP suggérés. Chaque détection est aussi enregistrée dans `market_patterns`, qu'on agisse dessus ou pas. **Alertes uniquement : rien n'ouvre de position automatiquement.**

Voir `mql/README.md` pour l'activer (`ScanSymbols` côté EA + section `market_scan:` de `config.yaml`).

## Ce qui reste à faire

1. **Mesurer la fiabilité réelle des setups détectés.** `market_patterns` n'enregistre pour l'instant que la détection (entrée/SL/TP suggérés), pas le résultat — on ne sait pas encore si le prix touche le TP ou le SL après coup. Prochaine étape : faire re-vérifier chaque setup par le scanner sur les bougies suivantes, et enregistrer l'issue (`hit_tp`/`hit_sl`).
2. **Élargir au-delà d'order block + BOS.** Les imbalances (FVG) et prises de liquidité seules sont détectées par `engine/smc_analysis.py` mais n'ont pas encore de règle d'entrée/SL/TP définie — ça se décide avec des exemples concrets de trades que tu juges "bons", pas dans l'abstrait.
3. **Corréler avec tes propres trades copiés.** La table `positions` (tes trades réels, avec résultat) et `market_patterns` (les détections en direct) partagent la même logique de détection — une fois qu'il y aura assez d'historique des deux côtés, on pourra croiser "quels tags SMC apparaissent le plus dans tes trades gagnants" avec "quels setups le scanner détecte le plus souvent".
4. **Entraînement ML**, si nécessaire une fois qu'il y a assez de données : à faire hors du VPS Hostinger (pas de GPU côté KVM), seule l'inférence tournerait ici.

## Pourquoi ce n'est pas allé plus loin pour l'instant

Sans assez de setups **résolus** (dont on connaît l'issue), impossible de dire honnêtement si un pattern détecté est fiable — le construire maintenant produirait des chiffres qui ont l'air précis mais ne veulent rien dire. La priorité est de laisser tourner le scan et la copie assez longtemps pour accumuler des exemples réels.
