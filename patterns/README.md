# Phase 2 — reconnaissance de patterns (pas encore implémenté)

Objectif : donner à l'IA des trades exemples (les tiens, copiés et journalisés
par `engine/db.py` en phase 1), qu'elle en extraie les caractéristiques
communes, puis qu'elle scanne le marché en direct pour repérer les mêmes
configurations.

**Mise à jour** : la détection Smart Money Concepts (imbalance, prise de
liquidité, cassure de structure, order block) tourne déjà à chaque entrée —
voir `engine/smc_analysis.py`. C'est la brique de base : les mêmes
détecteurs, appliqués non plus seulement au moment de l'entrée mais en
continu sur le flux de marché en direct, sont ce qui permettra de repérer
"ce même pattern" en train de se former ailleurs. Ce dossier reste vide tant
qu'il n'y a pas assez d'historique réel (`positions.context_json`) pour
juger ce qui, dans ces patterns détectés, correspond réellement à tes bons
trades.

Ce dossier est un espace réservé volontairement vide — il ne sera construit
qu'une fois le copieur (phase 1) tourne de façon fiable sur au moins un
compte réel, pour deux raisons :

1. **Les données d'entraînement viennent de la phase 1.** La table
   `copy_log` (et son enrichissement à venir avec le résultat de chaque
   trade — pips gagnés/perdus, durée, contexte) est la matière première.
   Sans historique réel, un modèle de pattern-matching n'a rien à apprendre
   d'honnête.
2. **Le choix de méthode dépend de ce que "pattern" veut dire pour toi** :
   structure de prix (figures chartistes), contexte indicateurs (RSI,
   moyennes mobiles, volatilité), séquence de bougies, ou une combinaison.
   Ça se décide avec des exemples concrets de trades que tu juges
   "similaires", pas dans l'abstrait.

## Pistes envisagées (à valider ensemble le moment venu)

- Extraction de features par trade (contexte prix/indicateurs à l'entrée)
  + recherche de similarité (distance vectorielle ou embeddings) contre le
  flux de marché en direct.
- Alerting plutôt qu'exécution automatique dans un premier temps : l'IA
  signale un setup ressemblant, tu valides, elle ne trade pas seule tant que
  la fiabilité n'est pas mesurée sur plusieurs mois.
- Entraînement (si un modèle ML devient nécessaire) hors du VPS Hostinger
  (pas de GPU disponible côté KVM) ; seule l'inférence tournerait ici.
