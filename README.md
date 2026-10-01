# SNCF Trajets

Intégration Home Assistant (HACS) pour suivre vos trajets SNCF du quotidien : les prochains trains **directs** d'un trajet dans une plage horaire, leur état en temps réel (retard, suppression), une carte Lovelace compacte et des notifications push sur l'application mobile.

```
┌──────────────────────────────────────────┐
│ La Verpillière → Lyon Part-Dieu          │
│ 🚆 07:42  TER 17716   ✅ À l'heure       │
│ 🚆 08:12  TER 17718   ⚠️ +8 min          │
│ 🚆 08:42  TER 17720   ❌ Supprimé        │
└──────────────────────────────────────────┘
```

Données : API SNCF (Navitia, couverture `sncf`), en temps réel.

## Installation (HACS)

1. HACS, menu ⋮, **Dépôts personnalisés**.
2. Dépôt : `https://github.com/glorp-fr/ha-sncf-trajets`, catégorie **Intégration**.
3. Installer **SNCF Trajets**, puis redémarrer Home Assistant.
4. Paramètres, Appareils et services, **Ajouter une intégration**, **SNCF Trajets**.

## Clé API

Une clé gratuite est nécessaire. Elle s'obtient ici : <https://numerique.sncf.com/startup/api/token-developpeur/>

## Configuration

L'assistant demande successivement :

1. la clé API SNCF ;
2. la gare de départ (recherche par nom, puis choix dans la liste) ;
3. la gare d'arrivée (idem) ;
4. la plage horaire (de / à), les jours de la semaine (lundi à vendredi par défaut) et le nombre de trains suivis (3 par défaut) ;
5. les notifications : appareils mobiles à prévenir (services `notify.mobile_app_*`), seuil de retard (5 min par défaut) et heures de silence (22:00 à 06:00 par défaut).

Chaque trajet est une entrée distincte ; vous pouvez en ajouter autant que nécessaire. Les réglages (plage, jours, nombre de trains, notifications) sont modifiables ensuite via **Configurer**. Si la clé devient invalide, Home Assistant propose de la renouveler.

## Entités créées

Pour chaque trajet :

| Entité | Type | État | Attributs |
|--------|------|------|-----------|
| Prochain train | `sensor` (horodatage) | heure de départ réelle du prochain train non supprimé | `trains` (liste : `number`, `mode`, `base_departure`, `departure`, `base_arrival`, `arrival`, `delay_minutes`, `cancelled`, `cause`), `disruptions`, `window_start`, `window_end`, `is_future_window`, `stale`, `last_update`, `from_name`, `to_name`, `threshold` |
| Train 1 … Train N | `sensor` (énumération) | `à l'heure`, `en retard` (retard supérieur ou égal au seuil) ou `supprimé` | détail du train (mêmes champs que dans `trains`) |
| Perturbation | `binary_sensor` (problème) | actif si un train est en retard au-delà du seuil ou supprimé, ou si l'API signale une perturbation | |

Hors plage horaire, les capteurs affichent les trains de la prochaine plage (`is_future_window`).

## Carte Lovelace

```yaml
type: custom:sncf-trajets-card
entity: sensor.la_verpilliere_lyon_part_dieu_prochain_train
title: Mon trajet   # optionnel
```

`entity` est le capteur « Prochain train » du trajet. En mode stockage (par défaut), la ressource de la carte est ajoutée automatiquement. En mode YAML, ajoutez-la manuellement :

```yaml
lovelace:
  resources:
    - url: /sncf_trajets/sncf-trajets-card.js
      type: module
```

## Notifications

Les notifications sont envoyées aux services `notify.mobile_app_*` choisis dans la configuration, à chaque changement d'état d'un train suivi, sans doublon :

| Type (`type`) | Exemple de message |
|---------------|--------------------|
| `delay` | `⚠️ TER 17716 08:12 → +8 min (départ 08:20). Panne de signalisation` |
| `delay_changed` (variation de 5 min ou plus) | `⚠️ TER 17716 08:12 → maintenant +15 min (départ 08:27)` |
| `on_time` | `✅ TER 17716 08:12 de nouveau à l'heure` |
| `cancelled` | `❌ TER 17716 08:12 SUPPRIMÉ. Panne de signalisation. Prochain train : 08:42` |
| `restored` | `✅ TER 17716 08:12 rétabli` |

Le titre est `🚆 <Départ> → <Arrivée>`. Les suppressions sont envoyées en priorité haute (niveau d'interruption « time-sensitive » sur iOS).

**Heures de silence** (22:00 à 06:00 par défaut) : aucune notification n'est envoyée ; les alertes sont mises en file, puis, à la fin du silence, un résumé unique (une ligne par train concerné) est envoyé. Si tout est revenu à la normale, rien n'est envoyé.

### Événement et automatisations

Chaque alerte déclenche aussi, y compris pendant les heures de silence, l'événement `sncf_trajets_alert` avec les champs `entry_id`, `trajet`, `type`, `train_number`, `base_departure`, `departure`, `delay_minutes`, `cause`.

```yaml
automation:
  - alias: Lumière rouge si train supprimé
    trigger:
      - platform: event
        event_type: sncf_trajets_alert
        event_data:
          type: cancelled
    action:
      - service: light.turn_on
        target: {entity_id: light.entree}
        data: {color_name: red}
```

## Quota API et limites

- La fréquence d'interrogation s'adapte (2 min pendant la plage horaire, moins souvent en dehors), soit environ 300 requêtes par jour et par trajet, pour un quota SNCF d'environ 5 000 requêtes par jour et par clé.
- Trains directs uniquement (pas de correspondance), pas d'information de voie.
- Couverture `sncf` de Navitia uniquement ; configuration par l'interface uniquement.

## Développement

```bash
pip install -r requirements_test.txt
pytest -q
node --test tests/card/*.test.mjs
```

Licence MIT.
