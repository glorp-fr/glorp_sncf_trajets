# Glorp SNCF Trajets — intégration Home Assistant (HACS) — Design

Date : 2026-10-01
Dépôt : `glorp-fr/glorp_sncf_trajets` (public)
Domaine HA : `sncf_trajets`

## 1. Objectif

Remplacer l'intégration « Trains SNCF » actuellement utilisée par une intégration maison, installable via HACS, qui :

1. remonte, pour des trajets définis par l'utilisateur, les N prochains trains directs dans une plage horaire et des jours donnés ;
2. remonte les perturbations (retards, suppressions, messages) en temps réel ;
3. fournit une carte Lovelace « liste compacte » ;
4. envoie des notifications push sur l'app mobile Home Assistant à chaque changement d'état d'un train suivi (retard apparu / modifié / disparu, suppression, rétablissement).

**Critère de succès** : sur le trajet La Verpillière → Lyon Part-Dieu, 3 trains, 7h30–9h30, lun–ven, le dashboard affiche les bons trains avec leur état temps réel, et le téléphone reçoit un push lorsqu'un de ces trains prend du retard, redevient à l'heure ou est supprimé — sans doublon ni spam.

### Hors périmètre (YAGNI)

- Trajets avec correspondance (uniquement trains directs).
- Voie / quai.
- Autres réseaux que la couverture `sncf` de Navitia.
- Configuration YAML (UI uniquement).

## 2. Source de données

API SNCF (Navitia), `https://api.sncf.com/v1/coverage/sncf/`, authentification HTTP Basic (clé en login, mot de passe vide). Quota : ~5 000 requêtes/jour par clé.

Endpoints utilisés :

- `GET /places?q=<texte>&type[]=stop_area` — recherche de gares (config flow).
- `GET /journeys?from=<stop_area>&to=<stop_area>&datetime=<YYYYMMDDTHHMMSS>&datetime_represents=departure&data_freshness=realtime&max_nb_transfers=0&count=<n>&min_nb_journeys=<n>` — trains directs avec temps réel. Les perturbations liées sont dans `disruptions[]` de la réponse.

Pour un train : horaires théoriques = `base_departure_date_time` / `base_arrival_date_time` ; horaires temps réel = `departure_date_time` / `arrival_date_time` ; numéro = `display_informations.headsign` ; mode commercial = `display_informations.commercial_mode` ; suppression = statut `NO_SERVICE` dans la disruption impactant le vehicle_journey (ou `status` de la section/journey). La cause = `messages[0].text` de la disruption.

**Détection des suppressions** : en `data_freshness=realtime`, Navitia omet les trains supprimés. Chaque rafraîchissement fait donc **deux appels** `/journeys` (`base_schedule` puis `realtime`) et les fusionne par clé `numéro + départ théorique` :
- présent dans les deux → version temps réel ;
- présent seulement en théorique → supprimé si une disruption `NO_SERVICE` le cible, **ou** si le temps réel contient un train de départ théorique postérieur (preuve que la période est couverte) ; sinon conservé tel quel ;
- présent seulement en temps réel (train ajouté) → conservé.

Budget : ~2 × 150 = ~300 requêtes/jour/trajet, compatible avec le quota.

Si plus de trains que nécessaire sont retournés, on filtre côté client ceux dont le départ théorique est dans la plage, puis on garde les N premiers. Si la réponse ne couvre pas toute la plage, on pagine avec le lien `next` (max 3 pages).

## 3. Architecture

```
custom_components/sncf_trajets/
  __init__.py        setup/unload entry, enregistrement de la carte (static path + ressource Lovelace)
  manifest.json      domain, version, iot_class=cloud_polling, config_flow=true
  const.py           constantes, valeurs par défaut
  api.py             SncfApiClient (aiohttp) : search_stations(), get_journeys(); exceptions AuthError, QuotaError, ApiError
  models.py          dataclasses Train, TrajetData (normalisation de la réponse Navitia)
  schedule.py        fonctions pures : prochaine occurrence de plage, intervalle de polling
  coordinator.py     SncfTrajetCoordinator (DataUpdateCoordinator) par config entry
  alerts.py          AlertEngine : diff d'états → liste d'Alert ; persistance Store ; envoi push + événement
  config_flow.py     ConfigFlow + OptionsFlow + reauth
  sensor.py          capteurs
  binary_sensor.py   perturbation en cours
  strings.json, translations/fr.json, translations/en.json
  www/sncf-trajets-card.js   carte Lovelace (JS vanilla, LitElement de HA)
hacs.json
README.md
tests/
.github/workflows/  tests.yml (pytest), validate.yml (hassfest + HACS action)
```

Une **config entry = un trajet**. La clé API est stockée dans chaque entry (saisie pré-remplie à partir d'une entry existante pour éviter de la retaper).

### 3.1 Unités et interfaces

- `schedule.next_window(now, start: time, end: time, weekdays: set[int]) -> (datetime_start, datetime_end)` : renvoie la plage en cours si `now` est dedans, sinon la prochaine occurrence valide. Gère `end < start` (plage traversant minuit) et fuseau `Europe/Paris` (via fuseau HA).
- `schedule.poll_interval(now, window) -> timedelta` :
  - de `window.start - 60 min` à `window.end` : 2 min ;
  - entre 00:00 et 05:00 (hors cas précédent) : 60 min ;
  - sinon : 15 min.
- `SncfApiClient.get_journeys(from_id, to_id, start: datetime, count) -> list[Train]`.
- `Train` : `id` (vehicle_journey id + date), `number`, `mode`, `base_departure`, `departure`, `base_arrival`, `arrival`, `delay_minutes`, `cancelled: bool`, `cause: str | None`.
- `TrajetData` : `window_start`, `window_end`, `trains: list[Train]`, `disruptions: list[str]` (messages uniques), `last_update`, `stale: bool`.
- `AlertEngine.process(trains) -> list[Alert]` puis `AlertEngine.dispatch(alerts)`.

### 3.2 Flux de données

1. Le coordinateur calcule `next_window(now)`.
2. Il appelle `get_journeys` à partir de `window_start` et filtre/limite à N trains.
3. Il produit `TrajetData`, met à jour les entités.
4. Il passe les trains à `AlertEngine` qui compare avec l'état mémorisé et émet les alertes.
5. Il recalcule `update_interval = poll_interval(now, window)`.

## 4. Configuration (config flow)

Étapes :

1. **Clé API** — validée par un appel `/places?q=paris`.
2. **Gare de départ** — champ texte → appel `/places` → liste déroulante des résultats.
3. **Gare d'arrivée** — idem.
4. **Plage et trains** — heure début, heure fin, jours (multi-sélection, défaut lun–ven), nombre de trains (1–10, défaut 3).
5. **Notifications** — appareils `notify.mobile_app_*` (multi-sélection, peut être vide), seuil de retard (min, défaut 5), heures de silence (début/fin, défaut 22:00–06:00).

Titre de l'entry : `<Départ> → <Arrivée>` ; unique_id : `<from_id>_<to_id>_<start>_<end>`.

**OptionsFlow** : modifie les étapes 4 et 5 (pas les gares — pour changer de gares, recréer le trajet).

**Reauth** : déclenché sur 401, demande une nouvelle clé.

## 5. Entités

Par trajet (device = le trajet) :

- `sensor.<trajet>_prochain_train` — état : heure de départ réelle du prochain train non supprimé (timestamp, `device_class: timestamp`). Attributs : `trains` (liste sérialisée de tous les trains : number, mode, base_departure, departure, arrival, delay_minutes, cancelled, cause), `disruptions`, `window_start`, `window_end`, `is_future_window` (bool), `stale`, `from_name`, `to_name`. C'est l'entité utilisée par la carte.
- `sensor.<trajet>_train_1` … `_train_N` — état : `à l'heure` / `retard` / `supprimé` (enum) ; attributs du train. Pratique pour des automatisations.
- `binary_sensor.<trajet>_perturbation` — `on` si au moins un train en retard ≥ seuil ou supprimé, ou si `disruptions` non vide.

## 6. Moteur d'alertes

État mémorisé par train (clé `Train.id`) : `{"notified_delay": int, "cancelled": bool}`, persisté via `homeassistant.helpers.storage.Store` (clé `sncf_trajets.<entry_id>`). Les entrées des trains dont le départ est passé depuis plus de 12 h sont purgées.

Transitions (S = seuil, défaut 5 min) — pour chaque train de la fenêtre :

| Condition | Alerte | Nouvel état |
|---|---|---|
| non supprimé avant, supprimé maintenant | `cancelled` | cancelled=true |
| supprimé avant, non supprimé maintenant | `restored` | cancelled=false, notified_delay=delay si ≥S sinon 0 |
| notified_delay=0 et delay ≥ S | `delay` | notified_delay=delay |
| notified_delay>0 et delay ≥ S et \|delay − notified_delay\| ≥ 5 | `delay_changed` | notified_delay=delay |
| notified_delay>0 et delay < S | `on_time` | notified_delay=0 |

Premier passage sur un train jamais vu : on n'émet d'alerte que s'il est déjà en retard ≥ S ou supprimé (pas d'alerte « à l'heure »).

Données « stale » (erreur API) : aucune alerte n'est calculée.

### 6.1 Envoi

Pour chaque alerte :

- Événement HA `sncf_trajets_alert` : `{entry_id, trajet, type, train_number, base_departure, departure, delay_minutes, cause}`.
- Si hors heures de silence : appel `notify.<service>` pour chaque appareil configuré :
  - `title` : `🚆 <Départ> → <Arrivée>`
  - `message` selon type :
    - delay : `⚠️ <mode> <num> <HH:MM> → +<d> min (départ <HH:MM>). <cause>`
    - delay_changed : `⚠️ <mode> <num> <HH:MM> → maintenant +<d> min (départ <HH:MM>)`
    - on_time : `✅ <mode> <num> <HH:MM> de nouveau à l'heure`
    - cancelled : `❌ <mode> <num> <HH:MM> SUPPRIMÉ. <cause>. Prochain train : <HH:MM>` (prochain train non supprimé de la liste, omis si aucun)
    - restored : `✅ <mode> <num> <HH:MM> rétabli`
  - `data` : `tag: sncf_<num>_<YYYYMMDD>` ; pour `cancelled` : `priority: high`, `ttl: 0`, `push: {interruption-level: time-sensitive}`.
- Si pendant les heures de silence : les alertes sont mises en file (persistée) ; à la fin de la période de silence, un push unique résume l'état actuel des trains concernés (un message par ligne). Si l'état final est revenu à la normale pour tous, pas de push.

## 7. Carte Lovelace `custom:sncf-trajets-card`

Config :

```yaml
type: custom:sncf-trajets-card
entity: sensor.la_verpilliere_lyon_part_dieu_prochain_train
title: (optionnel, défaut "<Départ> → <Arrivée>")
```

Rendu (liste compacte) :

- En-tête : titre ; si `is_future_window`, sous-titre « <jour> <HH:MM>–<HH:MM> ».
- Une ligne par train : `🚆 HH:MM  <mode> <num>` + statut :
  - à l'heure : ✅ « À l'heure » (vert)
  - retard : heure théorique barrée + heure réelle + badge orange `+X`
  - supprimé : ligne grisée, ❌ « Supprimé » (rouge)
- Bandeau perturbation (ℹ️) si `disruptions` non vide (messages, dédupliqués).
- Pied : `MAJ HH:MM` ; si `stale`, mention « données non à jour ».
- Aucun train : « Aucun train direct dans la plage ».
- Couleurs via variables de thème HA (`--success-color`, `--warning-color`, `--error-color`, `--secondary-text-color`) : compatible clair/sombre.
- Éditeur visuel (`getConfigElement`) avec sélecteur d'entité filtré sur le domaine `sensor` de l'intégration.

Distribution : le fichier JS est servi par l'intégration (`hass.http.async_register_static_paths`) sous `/sncf_trajets/sncf-trajets-card.js` et ajouté automatiquement aux ressources Lovelace en mode storage ; README documente l'ajout manuel en mode YAML.

## 8. Gestion des erreurs

| Cas | Comportement |
|---|---|
| 401/403 | `ConfigEntryAuthFailed` → flux reauth |
| 429 | données précédentes conservées, `stale=true`, intervalle = min(2 × précédent, 60 min) jusqu'à succès |
| 5xx / timeout / réseau | idem 429 |
| Réponse sans train | `trains=[]`, capteur `unknown`, carte « Aucun train… » |
| Gare introuvable (config flow) | erreur `no_station_found` sur le formulaire |

Aucun appel API n'est fait plus d'une fois par 60 s par trajet (garde-fou).

## 9. Tests

- `pytest` + `pytest-homeassistant-custom-component`, fixtures JSON de réponses Navitia réelles anonymisées (train à l'heure, en retard, supprimé, avec disruption).
- `test_schedule.py` : plage en cours, avant, après, week-end → lundi, plage traversant minuit, changement d'heure.
- `test_alerts.py` : chaque transition du tableau §6, premier passage, seuil, écart < 5 min sans alerte, pas de doublon après rechargement du Store, heures de silence + résumé, stale → pas d'alerte.
- `test_api.py` : parsing des réponses, mapping des erreurs HTTP.
- `test_config_flow.py` : parcours complet, clé invalide, gare introuvable, options, reauth.
- `test_sensor.py` : états/attributs.
- CI : `tests.yml` (pytest), `validate.yml` (hassfest + `hacs/action`).
- Validation finale manuelle sur le HA de l'utilisateur avec sa clé API (non commitée).

## 10. Publication

- Dépôt public `glorp-fr/glorp_sncf_trajets`, `hacs.json` (`name: "Glorp SNCF Trajets"`, `render_readme: true`, `homeassistant` version minimale).
- README en français : installation HACS (dépôt personnalisé), obtention de la clé API, configuration, carte, exemples d'automatisation sur `sncf_trajets_alert`.
- Release GitHub `v0.1.0` pour que HACS propose la version.
