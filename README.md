# Sentinel (projet EdgeFlow) — copieur de positions risk-parity

**Sentinel** est le nom de l'IA ; **EdgeFlow** reste le nom du dépôt/projet technique qui l'héberge.

Réplique automatiquement les positions d'un compte MetaTrader (source) vers un ou plusieurs autres comptes (cibles), en risquant le **même pourcentage de capital** sur chaque compte — pas le même nombre de lots, pas un ratio d'equity naïf.

## Comment ça marche

```
Compte source (MT4/MT5)          VPS Hostinger                    Compte(s) cible(s) (MT4/MT5)
┌─────────────────────┐   fichiers   ┌──────────────────┐  fichiers  ┌─────────────────────┐
│ EA SignalPublisher   │ ───────────▶ │ engine/ (Python)  │ ─────────▶ │ EA CommandExecutor   │
│ détecte OPEN/MODIFY/ │              │  risk_engine.py   │            │ exécute l'ordre,     │
│ CLOSE, écrit un JSON │              │  calcule la taille│            │ publie l'équité      │
└─────────────────────┘              │  à risque égal    │            │ (heartbeat.json)     │
                                      └──────────────────┘            └─────────────────────┘
                                             │
                                             ▼
                                    dashboard/ (statut + arrêt d'urgence)
                                    data/edgeflow.db (journal SQLite)
```

Le calcul central (`engine/risk_engine.py`) :

```
risque_$ = équité_cible × risk_pct
distance_SL = |prix_entrée - stop_loss|
volume_cible = risque_$ ÷ (distance_SL en ticks × valeur_du_tick_cible)
```

Si le volume calculé tombe sous le minimum du broker, le trade est **ignoré**, jamais arrondi vers le haut — on ne sur-expose jamais un compte pour forcer une copie.

Chaque position copiée avec succès est enregistrée dans la table `positions` (voir `engine/db.py`) avec un **score de qualité 0-100** (`engine/scoring.py`) : ratio risque/récompense de la position (poids 70) + écart entre le risque visé et le risque réellement pris après arrondi du volume (poids 30). Ce score et l'historique des positions sont visibles dans le dashboard, et constituent la base de données que la phase 2 (reconnaissance de patterns) utilisera.

À l'ouverture, l'EA source joint aussi les 30 dernières bougies H1 du symbole, que `engine/smc_analysis.py` analyse pour détecter les éléments Smart Money Concepts / ICT que tu utilises pour entrer : **imbalance (Fair Value Gap)**, **prise de liquidité** (mèche qui balaie un plus haut/bas puis rejette), **cassure de structure (BOS)**, et **order block**. Le résultat est stocké avec chaque position (`has_fvg`, `has_liquidity_grab`, `has_bos`, `has_order_block`, et le détail en JSON) et visible dans le dashboard. Le **breaker block** n'est pas encore implémenté — il demande de suivre l'invalidation d'un order block dans le temps, prochaine étape une fois cette base validée sur de vrais trades.

À la fermeture, le **prix de clôture et le profit réel** (source) sont aussi enregistrés (`close_price`, `source_profit`) — sans ça, impossible de savoir plus tard quels patterns détectés à l'entrée menaient à de bons trades.

## Structure du repo

- `engine/` — moteur Python (risque, score de qualité, analyse SMC du contexte d'entrée, bridge fichiers, base SQLite, kill-switch, boucle principale).
- `mql/` — EA MetaTrader (MQL4 et MQL5) : `SignalPublisher` (source) et `CommandExecutor` (cible).
- `dashboard/` — statut en direct + bouton d'arrêt d'urgence (FastAPI).
- `deploy/` — guide de déploiement sur Hostinger VPS + unités systemd.
- `config/` — exemple de configuration (comptes, risque par compte, specs symboles).
- `patterns/` — phase 2 (reconnaissance de patterns), pas encore implémentée — voir `patterns/README.md`.
- `tests/` — tests du moteur de risque (`pytest tests/`).

## Démarrage rapide (local, avant tout déploiement)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/               # vérifie le moteur de risque
cp config/config.example.yaml config/config.yaml   # puis éditer les chemins réels
```

Pour le déploiement complet sur Hostinger (choix du plan VPS, installation MetaTrader sous Wine, services systemd), voir **`deploy/hostinger-setup.md`**.

## Robustesse

- Toute erreur inattendue en copiant vers une cible (fichier corrompu, écriture disque, etc.) est journalisée et n'interrompt jamais le service — elle est isolée à ce trade/cette cible (`SKIPPED_UNEXPECTED_ERROR` dans `copy_log`), le moteur continue de tourner.
- Un fichier de signal JSON valide mais incomplet (bug EA) est mis en quarantaine dans `out/error/` plutôt que de bloquer indéfiniment le pipeline.
- `config/config.yaml` mal formé échoue au démarrage avec un message clair (`ConfigError`) plutôt qu'un `KeyError` cryptique.
- SQLite tourne en mode WAL pour que le dashboard puisse lire pendant que le moteur écrit, sans erreur "database is locked".
- Le service s'arrête proprement sur `SIGTERM`/`SIGINT` (utile pour `systemctl stop`), et les logs sont structurés (niveau, horodatage) au lieu de simples `print`.

## Avant de connecter un compte réel

- Teste d'abord sur un **compte démo** de bout en bout (ouverture, modification, clôture) et vérifie le dashboard.
- Vérifie les CGU de chaque broker concernant l'automatisation/la copie de trades.
- `dashboard/` n'a aucune authentification — ne jamais l'exposer directement sur internet (voir `deploy/hostinger-setup.md`).
- Le kill-switch (bouton dashboard ou `touch data/KILL_SWITCH`) bloque toute nouvelle copie mais laisse toujours passer les fermetures de position.

## Feuille de route

1. **Phase 1 (ce repo aujourd'hui)** — copie risk-parity, MT4/MT5, dashboard, journal SQLite.
2. **Phase 2** — donner des trades exemples à l'IA, qu'elle en extraie le pattern et scanne le marché réel pour des setups similaires. Voir `patterns/README.md` pour l'approche envisagée.
