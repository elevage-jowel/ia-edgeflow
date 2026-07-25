# Phase 2 — reconnaissance de patterns sur le marché en direct

## Ce qui existe déjà

`engine/market_scanner.py` scanne en direct une liste de symboles (indépendamment de tout trade copié) pour repérer le setup Smart Money Concepts standard — un **order block juste après une cassure de structure** — et alerte (Telegram/email) avec une entrée/SL/TP suggérés. Chaque détection est aussi enregistrée dans `market_patterns`, qu'on agisse dessus ou pas. **Alertes uniquement : rien n'ouvre de position automatiquement.**

Chaque setup est ensuite **suivi jusqu'à sa résolution** : le scanner revérifie à chaque passage si le prix a atteint la zone d'entrée puis touché le SL ou le TP, sur les bougies les plus récentes. Statuts possibles : `PENDING` (en attente d'entrée), `ACTIVE` (entrée touchée, en attente de SL/TP), `HIT_TP`, `HIT_SL`, ou `EXPIRED` (la fenêtre glissante de bougies a dépassé le point de dernière vérification avant résolution — compté ni comme gain ni comme perte, plutôt que deviner). Le dashboard affiche un **taux de réussite par symbole/direction** dès qu'il y a des setups résolus (`/api/market_patterns`, table `market_patterns`).

Voir `mql/README.md` pour l'activer (`ScanSymbols` côté EA + section `market_scan:` de `config.yaml`).

## Combien de données avant de faire confiance aux chiffres

En dessous de ~30 setups résolus pour un même symbole/direction, le taux de réussite affiché est dominé par le bruit statistique — ne pas en tirer de conclusion. À partir de 30-50, une tendance commence à se dessiner ; 100+ donne une vraie confiance. Un order block ne se forme pas tous les jours sur H1 : avec EURUSD/GBPUSD/XAUUSD surveillés, compter plutôt en mois qu'en semaines pour atteindre ces seuils.

## Ce qui reste à faire

1. **Élargir au-delà d'order block + BOS.** Les imbalances (FVG) et prises de liquidité seules sont détectées par `engine/smc_analysis.py` mais n'ont pas encore de règle d'entrée/SL/TP définie — ça se décide avec des exemples concrets de trades que tu juges "bons", pas dans l'abstrait.
2. **Corréler avec tes propres trades copiés.** La table `positions` (tes trades réels, avec résultat) et `market_patterns` (les détections en direct, avec résultat) partagent la même logique de détection — une fois qu'il y aura assez d'historique des deux côtés, on pourra croiser "quels tags SMC apparaissent le plus dans tes trades gagnants" avec "quels setups le scanner détecte le plus souvent".
3. **Entraînement ML**, si nécessaire une fois qu'il y a assez de données : à faire hors du VPS Hostinger (pas de GPU côté KVM), seule l'inférence tournerait ici.
4. **Automatisation de l'ouverture**, seulement si la fiabilité mesurée le justifie — pas avant, et pas sans en reparler explicitement.
