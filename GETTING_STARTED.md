# Mise en route de Sentinel — guide pas à pas

Ce guide part de zéro (aucun VPS, aucun code installé) jusqu'à Sentinel qui tourne et copie tes positions. Suis les étapes dans l'ordre. Chaque étape renvoie vers le fichier du repo qui contient le détail si besoin.

---

## 1. Souscrire le VPS Hostinger

- hPanel → **VPS** → choisir **KVM 2** (2 vCPU, 8 Go RAM — suffisant pour le copieur + dashboard + scan de marché).
- Template : **Ubuntu 24.04** (ou "Ubuntu 24.04 with MetaTrader" si Hostinger le propose).
- Noter l'IP du VPS, configurer l'accès SSH par **clé** (pas par mot de passe).

## 2. Sécuriser le VPS

```bash
ufw allow OpenSSH && ufw enable

adduser edgeflow
usermod -aG sudo edgeflow
```

Tout ce qui suit se fait sous cet utilisateur `edgeflow`, pas en root.

## 3. Installer Wine et les terminaux MetaTrader

```bash
sudo apt update && sudo apt install -y wine sqlite3 python3.12-venv
winecfg   # initialise le préfixe Wine
```

Installer ensuite les `.exe` MT4/MT5 de chaque broker (le compte **source** que tu veux copier, et chaque compte **cible**) dans ce préfixe. Un terminal par compte, jamais mélangés.

## 4. Récupérer le code

Option A — via le zip envoyé :
```bash
# transfère edgeflow-sentinel.zip sur le VPS (scp, upload...), puis :
mkdir -p /home/edgeflow/ia-edgeflow
unzip edgeflow-sentinel.zip -d /home/edgeflow/ia-edgeflow
cd /home/edgeflow/ia-edgeflow
```

Option B — via git (permet les mises à jour avec `git pull`) :
```bash
cd /home/edgeflow
git clone -b claude/agent-copy-positions-brokers-ffgl42 https://github.com/elevage-jowel/ia-edgeflow.git
cd ia-edgeflow
```

## 5. Installer et configurer les EA MetaTrader

Détail complet : `mql/README.md`.

1. Sur le compte **source** : copier `mql/mql4/SignalPublisher.mq4` (ou `mql/mql5/SignalPublisher.mq5` pour MT5) dans `MQL4|5/Experts/` du terminal, compiler dans MetaEditor (F7), attacher à n'importe quel graphique.
   - Si tu veux le scan de marché en direct : renseigner le paramètre `ScanSymbols = "EURUSD,GBPUSD,XAUUSD"` dans les propriétés de l'EA.
2. Sur **chaque compte cible** : copier `CommandExecutor.mq4` (ou `.mq5`), compiler, attacher.
3. Dans les options du terminal (et sur chaque graphique) : activer **"Autoriser le trading algorithmique"**.

## 6. Installer l'environnement Python

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest tests/
```
→ doit afficher `67 passed`. Si ce n'est pas le cas, ne continue pas, dis-le moi.

## 7. Configurer l'application

```bash
cp config/config.example.yaml config/config.yaml
nano config/config.yaml
```

À renseigner obligatoirement :
- `source_account.files_dir` : chemin réel vers `MQL4|5/Files/edgeflow` du terminal source (sous Wine, typiquement `~/.wine/drive_c/Program Files/<Terminal>/MQL4/Files/edgeflow`).
- `targets[].files_dir` : idem pour chaque terminal cible.
- `targets[].symbol_specs` : **vraies** specs de chaque broker (`tick_value`, `contract_size`...) — à récupérer dans les spécifications de contrat du broker, celles de l'exemple sont génériques.
- `targets[].risk_pct` et `max_absolute_volume` (plafond de sécurité).
- `risk.max_daily_drawdown_pct`.

Optionnel mais recommandé :
- `notifications.telegram_bot_token` / `telegram_chat_id` (créer un bot via [@BotFather](https://t.me/BotFather), récupérer ton chat id via [@userinfobot](https://t.me/userinfobot)).
- `market_scan.enabled: true` si tu veux les alertes de scan de marché (symboles déjà pré-remplis : EURUSD, GBPUSD, XAUUSD).

## 8. Lancer en service (systemd)

```bash
sudo cp deploy/systemd/edgeflow-engine.service /etc/systemd/system/
sudo cp deploy/systemd/edgeflow-dashboard.service /etc/systemd/system/
sudo cp deploy/systemd/edgeflow-backup.service /etc/systemd/system/
sudo cp deploy/systemd/edgeflow-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now edgeflow-engine edgeflow-dashboard edgeflow-backup.timer

# vérifier que tout tourne sans erreur
sudo systemctl status edgeflow-engine edgeflow-dashboard
sudo journalctl -u edgeflow-engine -f
```

## 9. Accéder au dashboard en sécurité

Le dashboard n'a pas d'authentification — ne jamais l'exposer directement sur internet.

```bash
ssh -L 8000:localhost:8000 edgeflow@<ip-du-vps>
```
puis ouvrir `http://localhost:8000` sur ton poste.

## 10. Tester avant tout compte réel

Sur des **comptes démo uniquement** :
- Ouvrir une position micro-lot sur la source → vérifier qu'elle apparaît dans l'onglet "Positions" du dashboard avec le bon volume calculé côté cible.
- La modifier (SL/TP), la fermer partiellement, puis totalement → vérifier chaque étape dans le dashboard.
- Redémarrer le terminal source en pleine position → vérifier qu'aucun doublon n'apparaît côté cible.
- Vérifier les CGU de chaque broker sur l'automatisation/la copie de trades.

## 11. Compte réel

Seulement une fois l'étape 10 validée sans surprise. Commence petit (risk_pct faible, un seul compte cible), augmente progressivement.

---

**En cas de blocage à une étape**, reviens vers moi avec le message d'erreur exact (log `journalctl`, message MetaEditor, etc.) — c'est plus rapide à diagnostiquer qu'une description générale du problème.
