# Mapping API Freebox OS → entités Home Assistant

Base de toutes les requêtes : `https://{api_domain}:{https_port}{api_base_url}v{major}/`
soit typiquement `https://abcdefgh.fbxos.fr:43210/api/v12/`.

**Légende des colonnes**

- **Permission** : droit à activer *à la main* dans Freebox OS → Paramètres →
  Gestion des accès → Applications. L'appairage n'en accorde aucun.
- **Fréq.** : intervalle de rafraîchissement recommandé (`CATEGORY_META` dans
  `const.py`).
- **Statut** : `documenté` = présent dans le SDK officiel ; `UNSTABLE` = marqué
  comme tel par Free ; `non documenté` = absent du SDK, reverse-engineeré.

---

## 1. Connexion / WAN — `Category.CONNECTION`, 15 s

| Endpoint | Champs | Entité HA | Notes |
|---|---|---|---|
| `GET /connection/` | `rate_down`, `rate_up` | `sensor` · `device_class: data_rate` · `MEASUREMENT` | ⚠️ En **octets/s** (`byte/s` dans la doc). Unité native conservée, affichage suggéré en MB/s. |
| | `bandwidth_down`, `bandwidth_up` | `sensor` diagnostic | ⚠️ En **bits/s**, contrairement à `rate_*` : la doc dit bien « available download bandwidth in bit/s ». Une Ultra renvoie `8000000000`, soit 8 Gbit/s — lu en octets/s cela annoncerait 64 Gbit/s. Débit de synchronisation négocié, quasi statique. |
| | `bytes_down`, `bytes_up` | `sensor` · `data_size` · `TOTAL_INCREASING` | Remis à zéro à chaque reconnexion → `TOTAL_INCREASING` absorbe le reset. |
| | `state` (`up`/`down`) | `binary_sensor` · `connectivity` | |
| | `type`, `media` | attribut / `sensor` diagnostic | `media: ftth` sur l'Ultra. |
| | `ipv4`, `ipv6` | `sensor` diagnostic | IPv6 désactivé par défaut (verbeux). |
| | `ipv4_port_range` | attribut | Plage de ports en IP partagée. |
| `GET /connection/ftth/` | `sfp_pwr_rx`, `sfp_pwr_tx` | `sensor` · `signal_strength` · dBm | **Valeur en centièmes de dBm** → diviser par 100. |
| | `sfp_present`, `sfp_alim_ok`, `sfp_has_signal`, `link` | `binary_sensor` diagnostic | |
| | `sfp_serial`, `sfp_model`, `sfp_vendor` | attributs | À redacter dans les diagnostics. |
| `GET /connection/config/` | `remote_access`, `wol`, `adblock`, `api_remote_access` | `switch` (phase 3) | `PUT` sur le même endpoint. Permission `settings`. |
| `GET /connection/ipv6/config/` | `ipv6_enabled`, `delegations[]` | `binary_sensor` + attributs | |
| `GET /connection/ddns/{provider}/status/` | `status`, `last_refresh`, `next_refresh` | `sensor` diagnostic (phase 4) | |
| `GET /connection/xdsl/` | — | **ignoré** | Boîtier fibre : endpoint sans objet. |

## 2. Système — `Category.SYSTEM`, 60 s

| Endpoint | Champs | Entité HA | Notes |
|---|---|---|---|
| `GET /system/` | `sensors[]` = `{id, name, value}` | un `sensor` température par entrée, diagnostic | **Le SDK ne documente que `temp_cpum`/`temp_cpub`/`temp_sw`, mais les firmwares récents renvoient un tableau `sensors[]`.** Le nombre de sondes dépend du modèle → entités construites dynamiquement depuis le payload. |
| | `fans[]` = `{id, name, value}` | un `sensor` RPM par entrée, diagnostic | Idem, tableau non documenté. |
| | `uptime_val` | `sensor` · `device_class: timestamp` | Publier l'instant de boot, pas la durée, avec une tolérance de 60 s pour éviter le battement d'état. |
| | `firmware_version` | `sw_version` du device (pas d'entité) | Éventuellement une entité `update` en phase 5. |
| | `serial`, `mac`, `board_name` | identité du device | `serial` = identifiant stable des `unique_id`. |
| | `disk_status` | `sensor` enum diagnostic | `not_detected` / `disabled` / `initializing` / `error` / `active`. |
| | `box_flavor` | attribut | `full` / `light`. |
| `POST /system/reboot/` | — | `button` (phase 3) | Permission `settings`. Prévoir une confirmation. |

## 3. Appareils connectés — `Category.LAN`, 60 s

| Endpoint | Champs | Entité HA | Notes |
|---|---|---|---|
| `GET /lan/browser/interfaces/` | `name` | — | Itérer : `pub` en routeur, plusieurs en cas de VLAN. Renvoie l'erreur `nodev` en mode bridge → catégorie à neutraliser proprement. |
| `GET /lan/browser/{interface}/` | `active`, `reachable` | `device_tracker` (`SourceType.ROUTER`) | Appliquer `consider_home` sur `last_activity` plutôt que de basculer sur `active` brut, sinon les téléphones en veille clignotent. |
| | `primary_name`, `host_type`, `vendor_name` | nom + icône du device | `host_type` → icône (`smartphone`, `laptop`, `printer`…). |
| | `l2ident[].id` (MAC) | clé du device | |
| | `l3connectivities[]` | attribut (IPv4/IPv6) | |
| | `last_activity`, `last_time_reachable` | attributs `timestamp` | |
| `GET /lan/config/` | `name`, `mode` (`router`/`bridge`) | `sensor` diagnostic | Permet de détecter le mode bridge en amont. |
| `POST /lan/wol/{interface}/` | `mac` | `button` par appareil (phase 3) | Ou un `service` `freebox_ultra.wake_on_lan`. |
| `GET /dhcp/static_lease/` | `mac`, `ip`, `hostname` | attributs (phase 4) | |

> **Cohabitation.** L'intégration core `freebox` crée déjà ces `device_tracker`.
> `Category.LAN` est activée par défaut mais **les `device_tracker` ne le seront
> qu'en phase 2 et désactivés par défaut** dans le registre d'entités, pour ne
> pas dupliquer 40 entités chez quelqu'un qui a les deux intégrations.

## 4. Wi-Fi — `Category.WIFI`, 120 s, permission `settings`

| Endpoint | Champs | Entité HA | Notes |
|---|---|---|---|
| `GET /wifi/config/` | `enabled` | `switch` | `PUT /wifi/config/` pour couper le Wi-Fi global. |
| | `mac_filter_state` | `sensor` enum diagnostic | `disabled` / `whitelist` / `blacklist`. |
| `GET /wifi/ap/` | `status.state` | `binary_sensor` par radio | 12 valeurs possibles (`active`, `dfs`, `ht_scan`, `failed`…) → plutôt un `sensor` enum qu'un booléen. |
| | `config.band` | nom de l'entité | Le SDK documente `2d4g`/`5g`/`60g`. **L'Ultra étant Wi-Fi 7, une bande 6 GHz est attendue : valeur à relever sur le boîtier.** |
| | `config.primary_channel`, `channel_width` | `sensor` diagnostic | |
| `GET /wifi/ap/{id}/stations/` | nombre d'entrées | `sensor` « clients Wi-Fi » par radio | |
| | `signal`, `tx_rate`, `rx_rate`, `host` | attributs, ou `sensor` RSSI par appareil | Une entité RSSI par client = beaucoup d'entités : à réserver à une option. |
| `GET /wifi/ap/{id}/neighbors/` | SSID, canal, signal | — | Diagnostic ponctuel, pas d'entité. |
| `GET /wifi/planning/` | `use_planning`, `mapping[]` | `switch` (phase 4) | |
| `GET /wifi/bss/` | SSID par bande | attributs | Redacter le SSID dans les diagnostics. |

## 5. Stockage — `Category.STORAGE`, 5 min, permission `settings`

| Endpoint | Champs | Entité HA | Notes |
|---|---|---|---|
| `GET /storage/disk/` | `temp` | `sensor` température par disque | |
| | `state`, `idle`, `spinning` | `binary_sensor` / `sensor` enum | |
| | `model`, `serial`, `firmware` | identité d'un device enfant | Créer un device par disque, rattaché au boîtier via `via_device`. |
| | `total_bytes`, `active_duration`, `idle_duration` | `sensor` diagnostic | |
| | `partitions[]` | → voir ligne suivante | Les partitions sont imbriquées : un seul appel suffit. |
| `GET /storage/partition/` | `free_bytes`, `used_bytes`, `total_bytes` | `sensor` · `data_size` + un `sensor` % d'occupation | `label` pour nommer l'entité. |
| | `state`, `fstype`, `fsck_result` | `sensor` enum diagnostic | |
| `GET /storage/config/` | `external_pm_enabled` | `switch` (phase 4) | |
| RAID | — | — | **Aucun endpoint RAID n'est documenté.** L'intégration core tente un appel et désactive la fonctionnalité sur erreur ; à traiter de la même façon (essai + neutralisation) si le besoin se présente. |

## 6. Freebox Home — `Category.HOME`, 30 s, permission `home`

> **API entièrement non documentée** : absente de l'index du SDK. Le mapping
> ci-dessous vient de l'intégration core `freebox` et de `gvigroux/freebox_home`.
> Tout ici doit être confirmé sur le boîtier avant d'être considéré comme acquis.

Modèle de données : `GET /home/nodes/` renvoie une liste de **nodes**, chacun
portant un tableau `show_endpoints[]`. Un endpoint a un `name`, un `ep_type`
(`signal` = lecture, `slot` = commande), un `id` et une `value`.

- Lire une valeur : la `value` est déjà dans le payload de `/home/nodes/`, sinon
  `GET /home/endpoints/{node_id}/{endpoint_id}`.
- Agir : `PUT /home/endpoints/{node_id}/{endpoint_id}` avec `{"value": …}`.

| `category` du node | Entité HA | Endpoints clés |
|---|---|---|
| `alarm` | `alarm_control_panel` | signal `state` ; slots `alarm1` (armé total), `alarm2` (armé partiel), `off`, `trigger`, `skip`, `pin`, `sound`, `volume`, `timeout1..3` |
| `pir` | `binary_sensor` · `motion` | signal de détection + `battery`, `cover_open` |
| `dws` | `binary_sensor` · `door`/`window` | idem |
| `kfb` | `event` / `device_trigger` | télécommande porte-clés : plutôt une entité `event` qu'un `binary_sensor` |
| `camera` | `camera` | `stream_url` (RTSP/HLS), plus détection de mouvement |
| `rts`, `basic_shutter`, `shutter`, `opener` | `cover` | position, `up`/`down`/`stop` ; RTS est **sans retour d'état** → `assumed_state = True` |
| `iohome` | `cover` / `switch` | protocole Somfy io |
| tous | `sensor` · `battery` | endpoint `battery` quand il existe |

États de l'alarme → `AlarmControlPanelState` :

| Valeur Freebox | État HA |
|---|---|
| `idle` | `disarmed` |
| `alarm1_arming`, `alarm2_arming` | `arming` |
| `alarm1_armed` | `armed_away` |
| `alarm2_armed` | `armed_home` |
| `alarm1_alert_timer`, `alarm2_alert_timer` | `pending` |
| `alert` | `triggered` |

## 7. Téléphonie — `Category.PHONE` 30 s / `Category.CALLS` 60 s

| Endpoint | Champs | Entité HA | Permission | Notes |
|---|---|---|---|---|
| `GET /phone/` | `is_ringing` | `binary_sensor` | `settings` | Non documenté dans l'index du SDK, mais couvert par la lib `freebox-api`. 30 s est trop lent pour déclencher sur une sonnerie → **cas d'usage principal du WebSocket** (phase 4). |
| | `on_hook`, `hardware_defect`, `type` (`fxs`/`dect`) | `binary_sensor` diagnostic | | |
| `GET /call/log/` | entrées `{type, number, name, datetime, duration, new}` | `sensor` « appels manqués » (valeur = nombre, attributs = derniers appels) | `calls` | `type` ∈ `missed`/`accepted`/`outgoing`. |
| `POST /call/log/mark_all_as_read/` | — | `button` | `calls` | |
| `GET /contacts/` | — | — | `contacts` | Sert à résoudre les noms ; pas d'entité. |

## 8. VPN — `Category.VPN`, 2 min, permission `settings`

| Endpoint | Entité HA | Statut |
|---|---|---|
| `GET /vpn/` | `binary_sensor` par serveur (activé/non) | **UNSTABLE** |
| `GET /vpn/connection/` | `sensor` « clients VPN connectés » + attributs | **UNSTABLE** |
| `GET /vpn_client/…` | `binary_sensor` état du client VPN sortant | **UNSTABLE**, chemin exact à relever |

## 9. Player / Devialet — `Category.PLAYER`, 30 s, permission `player`

| Endpoint | Entité HA | Notes |
|---|---|---|
| `GET /player/` | un device enfant par player | Donne l'id et l'`api_version` propre au player. |
| `GET /player/{id}/api/v6/status/` | `media_player` | **API imbriquée** : le player expose sa propre API versionnée derrière celle du serveur. Chemin et version à confirmer. |
| `GET /airmedia/config/` | `switch` AirPlay | Permission `settings`. |
| `GET /airmedia/receivers/` + `POST /airmedia/receivers/{name}` | `media_player` (envoi d'URL) | Voie la plus fiable pour « jouer un flux sur l'enceinte » sans dépendre de l'API player. |

> Le haut-parleur Devialet n'a pas d'endpoint dédié : il est piloté via le
> player (volume, source) et/ou AirMedia. À cadrer par expérimentation.

## 10. Divers (phase 4+)

| Catégorie | Endpoint | Entité HA | Permission |
|---|---|---|---|
| Téléchargements | `GET /downloads/stats/` | `sensor` débit + nb de tâches | `downloader` |
| | `GET /downloads/` | `sensor` par tâche (option) | `downloader` |
| Contrôle parental | `GET /profile/`, `GET /network_control/{profile_id}` | `switch` « couper Internet » par profil | `profile` — **non documenté**, remplace l'ancien `/parental/` |
| Redirections de ports | `GET /nat/redir/` | `switch` par règle | `settings` |
| UPnP IGD | `GET /igd/config/` | `switch` | `settings` |
| Freeplug | `GET /freeplug/` | `sensor` débit CPL | — |
| RRD | `POST /rrd/` | — | **UNSTABLE**, sert à l'historique : inutile, HA a le sien |
| LCD | `GET /lcd/config/` | `number` luminosité | `settings` |

---

## WebSocket (phase 4)

`wss://{host}:{port}/api/v{major}/ws/event/`, mêmes en-têtes que le HTTP
(`X-Fbx-App-Auth`). Après connexion, envoyer un message d'enregistrement :

```json
{ "action": "register", "events": ["home_node_update", "phone_state_update"] }
```

**Les noms d'événements ci-dessous sont observés, pas documentés** — la liste
dans `const.WS_EVENTS` est un point de départ à valider :

`home_node_update`, `phone_state_update`, `call_state_update`,
`dhcp_lease_update`, `download_task_update`, `fs_tasks_update`.

Procédure de découverte recommandée : s'enregistrer sur la liste ci-dessus, logger
tout message reçu dont le `event_id`/`source` est inconnu, et provoquer des
événements à la main (sonner sur le fixe, ouvrir une porte, brancher un appareil).

Une fois le WebSocket en place, l'`iot_class` du manifeste passe à
`local_push` et les intervalles de `HOME`, `PHONE`, `CALLS` et `LAN` deviennent
des filets de sécurité (5 min) plutôt que le mécanisme principal.
