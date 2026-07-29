# ha-freebox-ultra — architecture et plan d'implémentation

Intégration custom Home Assistant pour Freebox Ultra (v9), domaine
`freebox_ultra`, volontairement séparée de l'intégration `freebox` du core.

- Mapping endpoint → entité : [`docs/api-mapping.md`](docs/api-mapping.md)
- État actuel du dépôt : phase 1 (MVP réseau) livrée, voir [Roadmap](#7-roadmap).

---

## 1. Décisions d'architecture

Les huit décisions ci-dessous conditionnent tout le reste. Chacune a un coût
assumé.

### ADR-1 — Client HTTP maison, pas `freebox-api`

`freebox-api` (1.3.1, ex-`freepybox`, utilisé par l'intégration core) est propre
et couvre 27 modules, mais quatre points le disqualifient comme dépendance ici :

1. **Son flux d'appairage est bloquant et orienté console** : `Freepybox.open()`
   boucle sur `while status != "granted"` avec un `print()`. Impossible de
   brancher ça sur `async_show_progress`, donc impossible d'avoir une UI
   d'appairage correcte.
2. **Il crée sa propre `ClientSession`** au lieu d'utiliser celle de Home
   Assistant (`async_get_clientsession`), ce qui double les connecteurs.
3. **Il stocke l'`app_token` dans un fichier annexe** qu'il gère lui-même — voir
   ADR-2.
4. **Pas de WebSocket**, et pas de module pour `vpn_client`, `profile` /
   `network_control`, c'est-à-dire précisément les parties « exhaustives » visées.

Le client de `api.py` fait ~400 lignes, s'appuie sur la session partagée de HA,
et expose `async_request(method, path)` comme échappatoire universelle : tout
endpoint non documenté reste accessible sans modifier le client.

**Ce qu'on perd** : la maintenance mutualisée, et le bundle de certificats. Ce
dernier est donc **vendoré** dans `freebox_certificates.pem` (Freebox Root CA,
Freebox ECC Root CA, Iliadbox ECC Root CA — valides jusqu'en 2035/2040).

Ce qu'il faut relire dans `freebox-api` quand un endpoint résiste :
`src/freebox_api/api/*.py`, un fichier par module.

### ADR-2 — L'`app_token` vit dans `entry.data`, pas dans un fichier

L'intégration core écrit le token dans `.storage/freebox/<host>.conf`. Ici il est
dans `entry.data[CONF_APP_TOKEN]`, donc dans `.storage/core.config_entries`.

Pourquoi : le token part avec les sauvegardes HA de façon atomique avec l'entrée
qui le décrit, le flux de réauthentification peut le remplacer proprement via
`async_update_reload_and_abort(data_updates=…)`, et supprimer l'intégration ne
laisse pas de fichier orphelin.

**Limite à connaître** : `.storage` n'est pas chiffré. C'est le même niveau
d'exposition que n'importe quel mot de passe d'intégration HA. Le token n'est
utilisable que depuis le LAN sauf si l'accès distant à l'API est explicitement
activé sur le boîtier, et il est révocable dans Freebox OS.

### ADR-3 — Un `DataUpdateCoordinator` par catégorie

Rapport de 20× entre les cadences naturelles (débit toutes les 15 s, disques
toutes les 5 min). Un coordinator unique obligerait à choisir entre marteler le
boîtier et afficher un débit mou, et une catégorie en panne (pas de disque, pas
de Freebox Home, permission manquante) ferait tomber tout le reste.

Le compromis : N minuteries au lieu d'une. Coût négligeable, HA en gère des
milliers.

Contrat de fiabilité au démarrage : seul `CONNECTION` est bloquant
(`async_config_entry_first_refresh` → `ConfigEntryNotReady`). Toutes les autres
catégories utilisent `async_refresh()` : leurs entités démarrent indisponibles
plutôt que d'empêcher le chargement.

### ADR-4 — Les permissions sont détectées, pas déclarées

Le SDK ne documente que sept permissions (`settings`, `contacts`, `calls`,
`explorer`, `downloader`, `parental`, `pvr`). Or Freebox Home, les caméras, le
player et les profils en exigent d'autres (`home`, `camera`, `player`,
`profile`), non documentées. Une table statique serait donc fausse.

Le mécanisme retenu, robuste quelle que soit la vérité :

1. `permissions` est lu au moment de l'ouverture de session (exposé dans les
   diagnostics) ;
2. quand un endpoint répond `insufficient_rights`, la catégorie crée un **issue
   de réparation** nommant la permission manquante et **cesse de poller**
   (`update_interval = None`) — inutile de marteler le boîtier pour un droit que
   seul l'utilisateur peut accorder ;
3. au rechargement de l'entrée, les issues sont purgées et les catégories
   retentent.

C'est important : **l'appairage n'accorde aucune permission optionnelle**. Il
faut aller les cocher dans Freebox OS → Paramètres → Gestion des accès →
Applications. C'est la première cause de « ça ne marche pas » sur ce type
d'intégration.

### ADR-5 — Un device distinct de celui de l'intégration core

`device_info` déclare `identifiers={(DOMAIN, serial)}` **sans**
`connections={(CONNECTION_NETWORK_MAC, mac)}`. Avec la MAC, HA fusionnerait ce
device avec celui créé par l'intégration `freebox`, mélangeant les entités des
deux intégrations sur une seule carte.

Corollaires pour la cohabitation :

- `app_id` distinct (`ha_freebox_ultra` vs `hass` du core) : chaque `app_id` ne
  possède qu'un `app_token`, les partager ferait se battre les deux intégrations
  sur la même autorisation. Cela implique **deux validations sur l'écran** du
  boîtier et deux entrées dans la liste des applications.
- `unique_id` des entités préfixés par le numéro de série.
- Les `device_tracker` restent **désactivés par défaut** dans le registre
  (phase 2) : le core les fournit déjà, et dupliquer 40 entités de présence est
  hostile.
- Les deux intégrations pollent le même boîtier. Si les deux sont installées,
  désactiver les catégories redondantes ici, ou retirer l'intégration core.

### ADR-6 — Polling d'abord, WebSocket en phase 4

Le WebSocket est la bonne réponse pour la sonnerie du fixe et les événements
Freebox Home (30 s de latence sur une détection de mouvement est inacceptable).
Mais **les noms d'événements ne sont pas documentés** : c'est un travail
d'exploration, pas une fonctionnalité à planifier au jour 1. Le polling marche,
livre de la valeur tout de suite, et restera le filet de sécurité.

### ADR-7 — Toujours joindre le boîtier par son `api_domain`

Le certificat de passerelle est émis pour `<uid>.fbxos.fr`. Utiliser une IP fait
échouer la vérification du nom d'hôte. La découverte mDNS fournit `api_domain` :
c'est lui qu'on garde, et on **ignore l'IP découverte**. Le formulaire manuel
l'explique dans son `data_description`.

### ADR-8 — Désactiver `VERIFY_X509_STRICT`

Python 3.13 (donc HA 2025+) active des contrôles X.509 stricts que le certificat
de la passerelle ne satisfait pas. Sans
`context.verify_flags &= ~ssl.VERIFY_X509_STRICT`, la poignée de main échoue.
Le contexte SSL est construit **dans un executor** (`load_verify_locations` lit
le disque) : le faire dans la boucle d'événements déclenche l'avertissement
« blocking call » de HA.

---

## 2. Arborescence

```
ha-freebox-ultra/
├── custom_components/freebox_ultra/
│   ├── __init__.py            # async_setup_entry / unload, device_info
│   ├── manifest.json          # zeroconf, version, iot_class
│   ├── const.py               # domaine, app_desc, Category + CATEGORY_META
│   ├── exceptions.py          # hiérarchie d'erreurs mappée sur les error_code
│   ├── api.py                 # BoxDescriptor, FreeboxClient, TLS, appairage
│   ├── coordinator.py         # FreeboxCoordinator + un fetcher par catégorie
│   ├── config_flow.py         # user / zeroconf / link / authorize / reauth
│   │                          # / reconfigure + OptionsFlow
│   ├── entity.py              # FreeboxUltraEntity (CoordinatorEntity)
│   ├── sensor.py              # ✅ phase 1
│   ├── binary_sensor.py       # ✅ phase 1
│   ├── diagnostics.py         # ✅ avec redaction
│   ├── freebox_certificates.pem
│   ├── strings.json           # source des traductions
│   └── translations/{en,fr}.json
├── tests/
│   ├── __init__.py            # requis pour `from .conftest import …`
│   ├── conftest.py            # payloads réalistes + mock du client
│   ├── test_config_flow.py
│   └── test_init.py
├── docs/api-mapping.md
├── ARCHITECTURE.md
├── hacs.json
├── pyproject.toml             # config ruff + pytest
├── requirements-test.txt
└── .github/workflows/validate.yml   # hassfest + HACS + ruff + pytest
```

Fichiers à créer aux phases suivantes : `device_tracker.py`, `switch.py`,
`button.py`, `alarm_control_panel.py`, `cover.py`, `camera.py`, `event.py`,
`media_player.py`, `update.py`, `services.yaml`, `websocket.py`, `icons.json`.

---

## 3. Authentification

### Séquence d'appairage

```
config_flow                        client                     Freebox
    │                                │                           │
    │ async_step_user/zeroconf       │                           │
    │──── async_probe ───────────────┼── GET /api_version ──────>│  (TLS, sans session)
    │                                │                           │
    │ async_step_link (formulaire « préparez-vous »)              │
    │                                │                           │
    │ async_step_authorize           │                           │
    │  └─ tâche de fond ────────────>│                           │
    │                                │ POST /login/authorize/    │
    │                                │──────────────────────────>│  affiche la demande
    │                                │<─ app_token + track_id ───│  sur l'écran
    │   async_show_progress          │                           │
    │   (spinner)                    │ GET /login/authorize/{id} │  ┐ toutes les 2 s
    │                                │<──── status: pending ─────│  │ pendant 180 s max
    │                                │<──── status: granted ─────│  ┘
    │                                │ GET /login/  ────────────>│
    │                                │<──── challenge ───────────│
    │                                │ POST /login/session/      │  password =
    │                                │   {app_id, password} ────>│  hmac_sha1(app_token,
    │                                │<─ session_token + perms ──│           challenge)
    │ async_show_progress_done        │                           │
    │ async_step_finish → entrée créée avec l'app_token           │
```

Points d'implémentation :

- La tâche de fond **ne lève jamais** : elle place un code d'erreur dans
  `_auth_error` et `async_step_finish` renvoie l'utilisateur sur `link` avec le
  message adéquat. Une exception dans une `progress_task` produirait un
  « Unexpected exception » illisible.
- Le token est **vérifié avant d'être persisté** (on ouvre une session avec lui),
  donc une entrée créée est une entrée fonctionnelle.
- Budget de 180 s côté intégration (`AUTH_TIMEOUT`), via `asyncio.timeout`, pour
  ne pas dépendre du timeout du boîtier.

### Cycle de vie de la session

Le `session_token` expire silencieusement. `FreeboxClient.async_request` traite
ça de façon transparente :

| `error_code` (toujours HTTP 403) | Traitement |
|---|---|
| `auth_required`, `invalid_session` | réouverture de session + **un seul** réessai (garde `_retried`, pas de récursion infinie) |
| `invalid_token`, `apps_denied`, `new_apps_denied`, `denied_from_external_ip` | `FreeboxInvalidToken` → `ConfigEntryAuthFailed` → **flux de réauthentification** HA |
| `insufficient_rights` | `FreeboxInsufficientRights` → issue de réparation, catégorie mise en sommeil (ADR-4) |
| `pending_token` | `FreeboxPendingAuth` (appairage non validé) |
| autre | `FreeboxError` → `UpdateFailed`, l'entité devient indisponible |

`async_open_session` est protégée par un `asyncio.Lock` : au réveil de plusieurs
coordinators en même temps, une seule renégociation a lieu.

---

## 4. Coordinators et cadences

| Catégorie | Intervalle | Défaut | Permission | Appels par cycle |
|---|---|---|---|---|
| `connection` | 15 s | ✅ | — | 2 (`/connection/`, `/connection/ftth/`) |
| `system` | 60 s | ✅ | — | 1 |
| `lan` | 60 s | ✅ | — | 1 + 1 par interface |
| `wifi` | 120 s | ✗ | `settings` | 2 + 1 par radio |
| `storage` | 5 min | ✗ | `settings` | 1 (partitions imbriquées) |
| `home` | 30 s | ✗ | `home` | 1 |
| `phone` | 30 s | ✗ | `settings` | 1 |
| `calls` | 60 s | ✗ | `calls` | 1 |
| `vpn` | 2 min | ✗ | `settings` | 2 |
| `player` | 30 s | ✗ | `player` | 1 + 1 par player |
| `downloads` | 30 s | ✗ | `downloader` | 1 |
| `profiles` | 2 min | ✗ | `profile` | 1 |

Tout est surchargeable par l'utilisateur dans les options (5 s à 3600 s). Les
valeurs par défaut visent ~5 requêtes/minute au repos avec les trois catégories
par défaut.

Ajouter une catégorie = ajouter une valeur à `Category`, une entrée dans
`CATEGORY_META`, un fetcher dans `CATEGORY_FETCHERS`, et les libellés dans
`strings.json` + `translations/`. Rien d'autre.

---

## 5. Config flow

| Étape | Rôle |
|---|---|
| `user` | Saisie hôte/port, **pré-remplie** par `GET http://mafreebox.freebox.fr/api_version`. Fallback manuel si la découverte échoue (mode bridge, VLAN, DNS). |
| `zeroconf` | `_fbx-api._tcp.local.` — le TXT contient tout (`api_domain`, `https_port`, `api_base_url`, `api_version`, `uid`). Abandon si `https_available` est faux. |
| `link` | Formulaire vide servant d'écran d'explication : « préparez-vous à appuyer sur ✓ ». Sans lui, l'utilisateur découvre la demande sur l'écran du boîtier après l'avoir déjà expirée. |
| `authorize` | `async_show_progress` + `progress_task`. |
| `finish` | Création de l'entrée, ou retour sur `link` avec l'erreur. |
| `reauth` / `reauth_confirm` | Rejoue l'appairage et remplace le seul `app_token`. |
| `reconfigure` | Change hôte/port sans réappairer. `_abort_if_unique_id_mismatch` empêche de pointer sur un autre boîtier. |

`unique_id` = `uid` du boîtier (issu de `/api_version`), stable et disponible
avant toute authentification — contrairement au numéro de série qui exige une
session.

**Options** (`OptionsFlowWithReload`, rechargement automatique de l'entrée) :

1. `init` — catégories activées (multi-select traduit), suivi des nouveaux
   appareils, `consider_home`.
2. `intervals` — un champ par catégorie activée, pré-rempli avec le défaut.

---

## 6. Tests et conformité

### Tests

`pytest-homeassistant-custom-component` fournit les fixtures HA (`hass`,
`enable_custom_integrations`, `MockConfigEntry`). Ce qui est en place :

```
tests/conftest.py         payloads réalistes (api_version, system, connection, ftth)
                          + fixture `mock_client` qui ne mocke QUE le transport
tests/test_config_flow.py flux user, découverte mDNS, doublon, refus sur le boîtier
tests/test_init.py        création du device et des entités, valeurs, réauth, retry
```

Le principe qui rend ces tests utiles : **seul le client HTTP est mocké**. Les
coordinators, les `EntityDescription` et les `value_fn` tournent pour de vrai
contre les payloads. Un mauvais nom de champ ou une conversion oubliée (les
centièmes de dBm) est attrapé.

Statut actuel : `8 passed`, `ruff check` propre, tous les modules importent
contre Home Assistant 2026.2.3.

À ajouter au fil des phases : snapshots d'entités (`syrupy`), test de l'options
flow, test de l'issue de réparation sur `insufficient_rights`, test du parsing
des nodes Freebox Home sur un `/home/nodes/` capturé.

### Checklist de conformité

Fait :

- [x] `manifest.json` avec `version` (obligatoire hors core), `config_flow`,
      `iot_class`, `zeroconf`, `integration_type: hub`, `codeowners`
- [x] `strings.json` **et** `translations/en.json` — pour une intégration custom,
      c'est `translations/<lang>.json` qui est chargé à l'exécution, `strings.json`
      seul ne suffit pas
- [x] `unique_id` sur l'entrée et sur chaque entité, préfixé par le n° de série
- [x] `_attr_has_entity_name = True` + `translation_key` partout
- [x] `entity_category: diagnostic` sur ce qui n'est pas de la donnée « métier »
- [x] `state_class` pour l'historique long terme, `TOTAL_INCREASING` sur les
      compteurs qui se réinitialisent
- [x] `device_class` + `native_unit_of_measurement` cohérents
- [x] Disponibilité gérée par le coordinator (`UpdateFailed`), pas à la main
- [x] `ConfigEntryNotReady` vs `ConfigEntryAuthFailed` distingués
- [x] Réauthentification et reconfiguration
- [x] `diagnostics.py` avec redaction (token, série, MAC, IP, SSID, noms d'hôtes)
- [x] Aucune I/O bloquante dans la boucle (contexte SSL en executor)
- [x] `entry.runtime_data` typé, pas de `hass.data[DOMAIN]`
- [x] CI : hassfest + action HACS + ruff + pytest

À faire aux phases suivantes :

- [ ] `icons.json` (icônes par `translation_key` plutôt que `_attr_icon`)
- [ ] `services.yaml` + `strings.json` pour les services (WoL, reboot, appairage
      Freebox Home)
- [ ] Snapshots d'entités
- [ ] Couverture ≥ 95 % du `config_flow` (le seuil du core)
- [ ] `quality_scale` : la mécanique (`quality_scale.yaml`) est réservée au core.
      La checklist ci-dessus vise le niveau *silver* ; ne pas mettre la clé dans
      le manifeste d'une intégration custom.

---

## 7. Roadmap

### Phase 0 — Socle ✅

Client, TLS, appairage, config flow complet, coordinators, options, diagnostics,
CI, tests.

### Phase 1 — MVP réseau ✅

`sensor` : débits montant/descendant, bande passante, octets cumulés, IPv4/IPv6,
puissances optiques, températures et ventilateurs (dynamiques), dernier
démarrage. `binary_sensor` : connexion Internet, module SFP, signal optique, lien
fibre.

**Prochaine étape concrète** : installer sur l'instance HA, appairer, et vérifier
sur le boîtier réel les trois points incertains — présence du tableau `sensors[]`
/ `fans[]` sur le firmware de l'Ultra, `api_version` réellement annoncée (le plan
suppose ≥ 8 et plafonne à 12), et unité de `sfp_pwr_rx`/`sfp_pwr_tx`. Les
diagnostics de l'entrée donnent les trois d'un coup.

### Phase 2 — Présence et stockage

`device_tracker` (désactivés par défaut, cf. ADR-5), `sensor` d'occupation des
partitions et températures de disques avec un device par disque
(`via_device`), `sensor` d'appels manqués, clients Wi-Fi par radio.

### Phase 3 — Actions

`switch` Wi-Fi global / accès distant / adblock, `button` reboot et
Wake-on-LAN, `button` « marquer les appels comme lus », service
`freebox_ultra.wake_on_lan`.

### Phase 4 — Freebox Home

Le gros morceau, et le seul dont l'API est totalement non documentée. Ordre
recommandé : capturer un `/home/nodes/` complet dans les diagnostics, écrire le
parseur de nodes/endpoints avec ses tests sur ce capture, puis
`alarm_control_panel` → `binary_sensor` (PIR, DWS, batteries) → `cover` (RTS en
`assumed_state`) → `camera` → `event` pour les télécommandes.

### Phase 5 — Temps réel

WebSocket : `iot_class` → `local_push`, événements Freebox Home et sonnerie du
fixe poussés, intervalles de polling relâchés à 5 min en filet de sécurité.
Prérequis : avoir identifié les noms d'événements (cf. `docs/api-mapping.md`).

### Phase 6 — Le reste

Téléphonie DECT, VPN, contrôle parental par profil, téléchargements, player /
AirMedia, entité `update` sur le firmware.
