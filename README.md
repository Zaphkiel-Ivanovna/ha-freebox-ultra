# Freebox Ultra pour Home Assistant

Intégration custom exposant les données de la **Freebox Ultra (v9)** dans Home
Assistant : réseau et fibre, système, appareils connectés, Wi-Fi, stockage,
Freebox Home, téléphonie, VPN, player.

Séparée de l'intégration `freebox` du core, avec laquelle elle peut cohabiter
(voir [ADR-5](ARCHITECTURE.md#adr-5--un-device-distinct-de-celui-de-lintégration-core)).

> **État : phase 1.** Réseau, fibre et système sont fonctionnels. Les autres
> catégories sont câblées dans les coordinators mais n'ont pas encore
> d'entités — voir la [roadmap](ARCHITECTURE.md#7-roadmap).

## Installation

### HACS

Ajouter ce dépôt en dépôt personnalisé (catégorie *Intégration*), installer,
redémarrer Home Assistant.

### Manuelle

Copier `custom_components/freebox_ultra/` dans le dossier `custom_components/`
de votre configuration, puis redémarrer.

## Configuration

La Freebox est détectée automatiquement par mDNS : une notification de
découverte devrait apparaître dans **Paramètres → Appareils et services**. Sinon,
**Ajouter une intégration → Freebox Ultra**.

L'appairage demande une validation physique :

1. Validez le formulaire « Autoriser Home Assistant » ;
2. sur l'écran de la Freebox, appuyez sur la **flèche droite** puis sur **✓**.

### ⚠️ Étape indispensable : accorder les permissions

L'appairage n'accorde **aucune** permission optionnelle. Pour Freebox Home, le
Wi-Fi, le stockage, la téléphonie ou le player, il faut les activer à la main :

> Freebox OS → Paramètres → Gestion des accès → Applications →
> **Home Assistant (Freebox Ultra)** → cocher les droits → rechargez
> l'intégration.

Sans cela, les catégories concernées remontent une alerte de réparation nommant
la permission manquante et cessent d'interroger le boîtier.

### Options

**Paramètres → Appareils et services → Freebox Ultra → Configurer** permet de
choisir les catégories de données interrogées et l'intervalle de chacune.

## Entités fournies (phase 1)

| Entité | Type | Notes |
|---|---|---|
| Débit descendant / montant | `sensor` | octets/s, convertible en Mbit/s dans l'UI |
| Bande passante descendante / montante | `sensor` | diagnostic |
| Données reçues / envoyées | `sensor` | compteurs cumulés, statistiques long terme |
| Adresse IPv4 / IPv6 | `sensor` | IPv6 désactivée par défaut |
| Puissance optique reçue / émise | `sensor` | dBm, diagnostic |
| Températures et ventilateurs | `sensor` | une entité par sonde annoncée par le boîtier |
| Dernier démarrage | `sensor` | horodatage |
| Connexion Internet | `binary_sensor` | |
| Module SFP, signal optique, lien fibre | `binary_sensor` | diagnostic |

## Développement

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-test.txt
pytest
ruff check .
```

- Architecture, décisions et roadmap : [`ARCHITECTURE.md`](ARCHITECTURE.md)
- Mapping API → entités : [`docs/api-mapping.md`](docs/api-mapping.md)

## Dépannage

Le premier réflexe : **télécharger les diagnostics** de l'entrée (menu ⋮ sur la
carte de l'intégration). Ils contiennent les permissions accordées, la version
d'API du boîtier, l'état de chaque coordinator et les payloads bruts (secrets
expurgés).

| Symptôme | Cause probable |
|---|---|
| « Impossible de joindre la Freebox » | Adresse IP au lieu du nom `*.fbxos.fr` : le certificat ne correspond pas |
| Une catégorie reste indisponible | Permission non accordée dans Freebox OS |
| Demande de réauthentification | Autorisation révoquée sur le boîtier |
| Aucun appareil connecté | Freebox en mode bridge (l'API répond `nodev`) |

## Références

- [SDK Freebox OS](https://dev.freebox.fr/sdk/os/)
- Intégration core [`freebox`](https://github.com/home-assistant/core/tree/dev/homeassistant/components/freebox)
- [`gvigroux/freebox_home`](https://github.com/gvigroux/freebox_home) — référence pour l'API Home non documentée
- [`hacf-fr/freebox-api`](https://github.com/hacf-fr/freebox-api) — référence d'endpoints
