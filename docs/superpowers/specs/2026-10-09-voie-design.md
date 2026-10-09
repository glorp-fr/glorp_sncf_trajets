# Glorp SNCF Trajets — Affichage et push de la voie — Design

Date : 2026-10-09
Version cible : 0.2.0
Spec parente : `2026-10-01-sncf-trajets-design.md` (la voie y était hors périmètre)

## 1. Objectif

Afficher la voie de départ des trains suivis dès qu'elle est connue, dans la carte Lovelace `sncf-trajets-card`, et envoyer un push mobile quand elle est annoncée ou quand elle change.

**Critère de succès** : sur La Verpillière → Lyon Part-Dieu, à partir de 10 min avant le départ réel d'un train, la carte affiche « Voie B » sur sa ligne ; le téléphone reçoit « 🛤️ TER 17616 08:25 → voie B » pour le prochain train (réglage par défaut), et « ⚠️ … changement de voie B → A » si la voie change avant le départ, quel que soit le train suivi.

### Hors périmètre (YAGNI)

- Voie à l'arrivée.
- Widget Android (la carte Lovelace suffit).
- Envoi direct vers Awtrix depuis `sncf_trajets` (voir §8).
- Interrogation du tableau en dehors des 10 min précédant un départ.

## 2. Source de données : tableaux d'affichage Gares & Connexions

L'API Navitia (`api.sncf.com`) ne renseigne pas la voie (`stop_point.platform_code` absent, vérifié le 2026-10-09 sur Lyon Part-Dieu).

Source retenue : l'API des tableaux d'affichage en gare, publique et non authentifiée, mais non officielle :

```
GET https://www.garesetconnexions.sncf/schedule-table/Departures/00<UIC>
```

- `<UIC>` est le code à 8 chiffres extrait de `from_id` : `stop_area:SNCF:87723395` → `0087723395`. Si `from_id` ne se termine pas par 8 chiffres, la voie est simplement désactivée pour ce trajet (log debug).
- La réponse est une liste JSON d'environ 20 départs. Champs utiles : `trainNumber` (str), `scheduledTime` (ISO 8601 avec offset, UTC), `platform.track` (str, ex. `"B"`, ou `null`/`""`).
- L'API est protégée par DataDome : une requête jugée suspecte reçoit un 403 avec une URL de captcha. Les mesures prévues sont un en-tête `User-Agent` de navigateur, `Accept: application/json`, un timeout de 10 s et un volume d'appels minimal (§4).

Rapprochement avec un `Train` : `trainNumber == train.number` **et** `scheduledTime` (datetime avec fuseau) `== train.base_departure`. On compare des datetimes avec fuseau, pas des chaînes.

## 3. Client tableau (`boards.py`, nouveau)

- `uic_from_stop_area(stop_area_id: str) -> str | None` : fonction pure.
- `parse_board(payload: Any) -> dict[tuple[str, datetime], str]` : fonction pure. Elle renvoie `{(numéro, départ théorique): voie}` et ignore les entrées sans voie ou mal formées. Si le payload n'est pas une liste, elle lève `BoardError`.
- `class BoardClient(session)` : `async get_platforms(uic) -> dict[...]`. Elle lève `BoardError` pour tout HTTP ≠ 200, timeout, erreur réseau ou JSON invalide.
- `BoardError` est indépendante de `SncfApiError` : un échec du tableau ne touche jamais `stale`, `_failures` ni `update_interval`.

## 4. Modèle et coordinator

- `Train` reçoit le champ `platform: str | None = None`, ajouté dans `as_dict()` (`"platform"`). `Train.id` est inchangé.
- Le coordinator possède `self._platforms: dict[str, str]` (clé `train.id`), en mémoire seulement. Le dict est purgé des ids absents de la liste courante.
- À chaque rafraîchissement réussi de Navitia, après la sélection des trains :
  1. `due = [t for t in selected if not t.cancelled and t.departure - PLATFORM_LEAD <= now < t.departure]`, avec `PLATFORM_LEAD = timedelta(minutes=10)` (const), calculé sur le départ **réel**.
  2. Si `due` n'est pas vide et que l'UIC est connu, on fait **un seul** appel `get_platforms`. Pour chaque train de `due` trouvé dans le résultat, on écrit `self._platforms[t.id] = voie`. Si la voie disparaît du tableau, l'ancienne valeur est conservée. En cas de `BoardError`, on journalise un warning et on ne modifie rien.
  3. `selected = [replace(t, platform=self._platforms.get(t.id)) for t in selected]`.
- Dans la branche « API SNCF indisponible » (données conservées), les trains conservés gardent déjà leur `platform`. Aucun appel au tableau n'est fait.
- La cadence ne change pas : un départ est toujours dans la plage active, donc le rafraîchissement a lieu toutes les 2 min, soit environ 5 appels au tableau par train suivi. On continue jusqu'au départ même quand la voie est connue, pour détecter les changements de dernière minute.

## 5. Push (`alerts.py`)

- L'état persisté par train reçoit deux clés, toutes deux lues avec `.get()` pour rester compatibles avec le stockage existant :
  - `platform: str | None` : dernière voie vue, qui sert à détecter les changements.
  - `platform_notified: bool` : indique si le push « voie connue » a déjà été envoyé.
- `compute_alerts(state, trains, threshold, platform_notify)` :
  - Pour un train supprimé : aucune alerte de voie.
  - Si `t.platform` est renseigné, que `prev["platform"]` l'est aussi et qu'ils diffèrent → alerte `platform_changed` (toujours émise), et `platform_notified = True`.
  - Sinon, si `t.platform` est renseigné, que `platform_notified` est faux et que le train est éligible selon le réglage → alerte `platform`, et `platform_notified = True`.
  - Ensuite, on écrit `prev["platform"] = t.platform` si celle-ci est renseignée.
  - Conséquence voulue : en mode `next`, un train dont la voie était déjà connue alors qu'il n'était pas encore le prochain (trains espacés de moins de 10 min) reçoit son push « voie » dès qu'il devient le prochain.
  - Les alertes de voie s'ajoutent aux alertes de retard ou de suppression sans les remplacer.
- Réglage `platform_notify` : `off` | `next` (défaut) | `all`.
  - `next` : seul le premier train non supprimé de `trains` donne lieu à une alerte `platform`.
  - `off` : aucune alerte `platform`.
  - `platform_changed` n'est jamais filtrée.
- Messages (`format_message`) :
  - `platform` : `🛤️ {label} → voie {voie}`
  - `platform_changed` : `⚠️ {label} : changement de voie {ancienne} → {nouvelle}`, avec `priority: high`, `ttl: 0` et `interruption-level: time-sensitive`, comme `cancelled`.
- Tag de notification : le même que les alertes du train (`sncf_{numéro}_{date}`), pour que le dernier message remplace le précédent.
- Heures silencieuses : les alertes de voie ne sont **pas** mises en `pending`. Pendant ces heures, elles sont abandonnées, puisqu'elles seraient obsolètes après le départ. Elles ne figurent pas dans `format_summary`.
- L'événement `sncf_trajets_alert` est émis pour ces deux types avec le champ supplémentaire `platform`. Le champ `previous_platform` est ajouté pour `platform_changed`.

## 6. Configuration

- Nouvelles constantes : `CONF_PLATFORM_NOTIFY = "platform_notify"`, `PLATFORM_NOTIFY_MODES = ["off", "next", "all"]`, `DEFAULT_PLATFORM_NOTIFY = "next"`.
- Le champ est ajouté au schéma de l'étape notifications (`NOTIFY_KEYS`), donc à la création et dans les options. Il utilise un `SelectSelector` en mode liste avec `translation_key="platform_notify"`.
- Traductions dans `strings.json`, `fr.json` et `en.json` (libellés : Désactivé / Prochain train uniquement / Tous les trains).
- Les entrées existantes n'ont pas la clé : on lit avec `opts.get(..., DEFAULT_PLATFORM_NOTIFY)`, donc aucune migration.

## 7. Carte Lovelace

- Si `t.platform` est renseigné, un badge `Voie {voie}` apparaît dans la colonne du numéro (classe `.platform` : fond `--primary-color`, texte contrasté, coins arrondis), sur les lignes à l'heure ou en retard. Rien n'apparaît sur les lignes supprimées.
- La valeur est échappée avec `esc()`.
- `VERSION` passe à `0.2.0`.

## 8. Awtrix (étude, hors implémentation)

L'afficheur Awtrix est déjà piloté par `glorp_awtrix_notifier`, dont la règle « SNCF » a été migrée depuis les anciennes automations. Recommandation : ne pas dupliquer de logique MQTT dans `sncf_trajets`. Une fois la v0.2.0 installée, on reconfigurera cette règle pour lire les attributs du capteur `prochain train` (`trains[0].departure`, `delay_minutes`, `platform`) dans ses templates Jinja. Ce sera traité dans une étape séparée.

## 9. Tests

- `tests/test_boards.py` :
  - `uic_from_stop_area` (cas valide et invalide).
  - `parse_board` à partir d'un extrait réel du tableau de La Verpillière : voie lue, entrées sans voie ignorées, payload non-liste → `BoardError`.
  - Client : 200, 403 DataDome → `BoardError`, timeout → `BoardError`.
- `tests/test_coordinator.py` :
  - Train hors fenêtre des 10 min → aucun appel au tableau.
  - Train dans la fenêtre → appel et voie posée.
  - Échec du tableau → voie précédente conservée et `stale` reste `False`.
  - Voie changée → mise à jour.
  - Train retardé : la fenêtre suit le départ réel.
- `tests/test_alerts.py` : voie apparue (`next`/`all`/`off`), filtrage « prochain train », changement de voie toujours émis, train supprimé sans alerte de voie, état ancien sans les clés de voie, push « voie » différé quand un train devient le prochain.
- `tests/test_notifier.py` : alertes de voie abandonnées en heures silencieuses, priorité haute pour `platform_changed`.
- `tests/card/render.test.mjs` : badge présent ou absent, échappement.
- Version : `const.VERSION`, `manifest.json` et la carte en `0.2.0`.
