# Déploiement sur Hostinger VPS

## 1. Choisir et souscrire le plan

Pour le MVP (copie de positions, 2-3 comptes MetaTrader, dashboard léger) : **KVM 2** (2 vCPU, 8 Go RAM, ~6,99 $/mo en tarif de lancement). Passer à **KVM 4** (4 vCPU, 16 Go RAM) quand le module de reconnaissance de patterns (phase 2) tourne en continu à côté.

Étapes :
1. Compte Hostinger → hPanel → **VPS** → choisir **KVM 2**.
2. À la création, choisir un template **Ubuntu 24.04** (ou le template "Ubuntu 24.04 with MetaTrader" proposé par Hostinger, qui préinstalle MT4/MT5 sous Wine — évite de le faire à la main).
3. Noter l'IP du VPS et configurer l'accès SSH par clé (pas par mot de passe).

## 2. Sécuriser le VPS (avant d'y mettre un seul compte de trading)

```bash
# pare-feu : seul SSH ouvert par défaut, le dashboard restera en local
ufw allow OpenSSH
ufw enable

# utilisateur dédié, pas root, pour faire tourner l'engine et Wine/MetaTrader
adduser edgeflow
usermod -aG sudo edgeflow
```

Ne jamais exposer le dashboard (`dashboard/app.py`) directement sur internet : il n'a pas d'authentification. Le laisser en écoute sur `127.0.0.1` et y accéder via un tunnel SSH (`ssh -L 8000:localhost:8000 edgeflow@<ip>`), ou mettre un reverse proxy (Caddy/Nginx) avec auth devant si un accès distant est nécessaire.

## 3. Installer les terminaux MetaTrader (si le template ne les fournit pas déjà)

```bash
sudo apt update && sudo apt install -y wine
winecfg   # première initialisation du préfixe Wine
# télécharger et installer les .exe MT4/MT5 de chaque broker dans ce préfixe
```

Chaque terminal (un par broker/compte) tourne dans son propre préfixe Wine ou son propre dossier d'installation, pour ne jamais mélanger les fichiers `edgeflow/` de comptes différents.

## 4. Installer le moteur Python

```bash
sudo apt install -y python3.12-venv
cd /home/edgeflow/ia-edgeflow
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
# éditer config/config.yaml : chemins réels des dossiers Files/edgeflow de chaque terminal
```

## 5. Copier les EA dans chaque terminal

Voir `mql/README.md`. En résumé : compiler `SignalPublisher` sur le compte source, `CommandExecutor` sur chaque compte cible, activer le trading algorithmique.

## 6. Lancer en service (systemd)

```bash
sudo cp deploy/systemd/edgeflow-engine.service /etc/systemd/system/
sudo cp deploy/systemd/edgeflow-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now edgeflow-engine edgeflow-dashboard
sudo journalctl -u edgeflow-engine -f   # suivre les logs
```

## 7. Vérifier avant de connecter un compte réel

- Ouvrir/fermer une position de test (micro-lot) sur un **compte démo** source et vérifier dans le dashboard (`/api/status`) qu'elle est bien répliquée avec le bon volume sur le compte cible.
- Vérifier les CGU du/des broker(s) concernant l'automatisation et la copie de trades — certains l'interdisent explicitement.
- Tester le bouton d'arrêt d'urgence du dashboard : une position ouverte pendant que le kill-switch est actif ne doit générer aucune nouvelle copie, seulement autoriser les fermetures.

## Évolution (phase 2 — reconnaissance de patterns)

Le composant `patterns/` est prévu pour tourner en inférence sur ce même VPS (léger, pas de GPU nécessaire pour un scoring de similarité). L'entraînement d'un modèle plus lourd (si besoin) doit se faire ailleurs (poste personnel ou cloud GPU) — Hostinger KVM ne propose pas d'instance GPU.
