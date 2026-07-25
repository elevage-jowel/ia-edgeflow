# EA MetaTrader — SignalPublisher / CommandExecutor

Deux Expert Advisors par plateforme (MQL4 pour MT4, MQL5 pour MT5), qui font le pont entre les terminaux MetaTrader et le moteur Python `engine/`.

## Rôles

- **SignalPublisher** : à installer sur le compte **source** (celui qu'on copie). Détecte OPEN/MODIFY/CLOSE et écrit un fichier JSON par événement dans `MQL4|MQL5/Files/edgeflow/out/`. Sur un événement OPEN, il joint aussi les `ContextCandleCount` dernières bougies H1 (30 par défaut) du symbole — c'est la matière première utilisée côté Python pour détecter imbalance/liquidité/cassure de structure/order block (`engine/smc_analysis.py`). Il gère aussi, optionnellement, le **scan du marché en direct** (voir plus bas).
- **CommandExecutor** : à installer sur **chaque compte cible**. Lit les ordres écrits par le moteur Python dans `edgeflow/in/`, les exécute, et publie l'équité du compte toutes les 500 ms dans `edgeflow/heartbeat.json` (indispensable pour le calcul de risque proportionnel).

## Installation

1. Copier le `.mq4`/`.mq5` correspondant dans `MQL4/Experts/` ou `MQL5/Experts/` du terminal concerné, puis compiler dans MetaEditor (F7). Ces fichiers n'ont pas été compilés dans cet environnement — vérifie la compilation avant tout usage réel.
2. Activer **Autoriser le trading algorithmique** dans les options du terminal, et sur le graphique où l'EA est attaché.
3. Autoriser l'accès fichier : ces EA utilisent seulement le bac à sable (`Files/`) du terminal, aucune permission réseau n'est nécessaire.
4. Sur le compte source : attacher `SignalPublisher` à n'importe quel graphique (un seul suffit, il scanne toutes les positions du compte).
5. Sur chaque compte cible : attacher `CommandExecutor` à n'importe quel graphique.

## Chemin des fichiers côté VPS Linux

Sous Wine, `MQL4/Files/` (ou `MQL5/Files/`) d'un terminal correspond à un vrai dossier sur le disque du VPS, typiquement :

```
~/.wine/drive_c/Program Files/<Terminal>/MQL4/Files/edgeflow/
```

C'est **ce chemin absolu** qu'il faut renseigner dans `config/config.yaml` (`source_account.files_dir` / `targets[].files_dir`), pas un chemin "vu depuis Windows". Voir `deploy/hostinger-setup.md`.

## Résilience au redémarrage

`SignalPublisher` et `CommandExecutor` reconstruisent leur état en mémoire dans `OnInit()` à partir des positions déjà ouvertes (et, côté `CommandExecutor`, du commentaire `edgeflow:<ticket_source>` attaché à chaque ordre) — un redémarrage du terminal (reboot VPS, mise à jour, rechargement de l'EA) ne doit ni dupliquer une position déjà ouverte sur la source, ni rendre une position orpheline côté cible (plus jamais modifiée/fermée). C'est indispensable : sans ça, un simple reboot pouvait doubler l'exposition ou laisser une position ouverte indéfiniment sur le compte cible même après sa fermeture sur la source.

## Clôtures partielles

Une clôture partielle sur la source (fermer une partie du lot) est détectée et répercutée proportionnellement sur la cible : si la source ferme 30% de sa position, la cible ferme aussi environ 30% de la sienne (arrondi vers le bas, jamais vers le haut, pour ne jamais laisser la cible plus exposée que proportionnellement correct). Le calcul se base toujours sur les volumes d'origine à l'ouverture, pas sur un total cumulé — donc pas de dérive d'arrondi même après plusieurs clôtures partielles successives sur le même trade.

## Scan du marché en direct (alertes)

`SignalPublisher` peut aussi surveiller une liste de symboles indépendamment de tout trade copié, pour repérer des setups Smart Money Concepts (order block juste après une cassure de structure) et t'alerter par Telegram/email avec une entrée/SL/TP suggérés — **jamais d'ouverture automatique**, uniquement des alertes.

Pour l'activer :
1. Renseigner `ScanSymbols` dans les paramètres de l'EA (ex: `"EURUSD,GBPUSD,XAUUSD"`), et `ScanIntervalSeconds` (300 par défaut).
2. Activer et configurer la section `market_scan:` de `config/config.yaml` (voir `config/config.example.yaml`) avec **la même liste de symboles**.
3. Le moteur Python lit `edgeflow/market/<symbole>.json` (écrit par l'EA), détecte les setups (`engine/market_scanner.py`), les enregistre dans la table `market_patterns`, et alerte sur chaque **nouveau** setup (pas de répétition sur un setup déjà vu).

Chaque symbole scanné doit être visible dans le Market Watch du terminal (sinon `iTime`/`CopyRates` renvoient des données vides).

## Limites connues de ce MVP

- MT4 n'a pas d'événement `OnTradeTransaction` : `SignalPublisher.mq4` détecte les changements en comparant l'état des ordres toutes les `PollMillis` (500 ms par défaut). Une position ouverte et refermée entre deux scans serait manquée — réduire `PollMillis` si besoin.
- Le parseur JSON côté MQL est volontairement minimal (StringFind), adapté au schéma fixe émis par le moteur Python — ce n'est pas un parseur JSON général.
- Les ordres en attente (pending orders) ne sont pas copiés, seulement les positions ouvertes.
- **Pas de retour d'échec d'exécution vers Python** : si `OrderSend`/`trade.Buy` échoue côté cible (refus du broker, requote, fonds insuffisants...), l'erreur part dans le journal Experts du terminal (`Print`/`PrintFormat`) mais le fichier de commande est quand même archivé dans `in/done/`, et `positions`/`copy_log` côté Python affichent toujours `COPIED` puisque l'écriture du fichier a réussi — l'échec réel d'exécution est invisible dans le dashboard. À surveiller manuellement (journal Experts du terminal cible) tant qu'un canal de retour (fichier d'accusé de réception lu par le moteur Python) n'est pas construit.
