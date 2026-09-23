# TaM Montpellier – tramway pour Home Assistant

<img src="custom_components/tam_montpellier/brand/icon@2x.png" alt="Icône" width="128" align="right">

Intégration Home Assistant qui affiche les **prochains passages du tramway de
Montpellier** aux arrêts de votre choix, à partir de l'open data temps réel de la
TaM ([jeu de données](https://data.montpellier3m.fr/dataset/offre-de-transport-tam-en-temps-reel),
licence ODbL).

## Installation

**HACS** : *Intégrations → ⋮ → Dépôts personnalisés*, ajouter l'URL de ce dépôt
(catégorie *Intégration*), installer « TaM Montpellier » puis redémarrer.

**Manuelle** : copier `custom_components/tam_montpellier` dans le dossier
`custom_components` de votre configuration, puis redémarrer.

Home Assistant 2026.2 ou plus récent est requis (version testée).

## Configuration

1. *Paramètres → Appareils et services → Ajouter une intégration → TaM Montpellier*.
   Les adresses des flux proposées par défaut conviennent.
2. Sur la page de l'intégration, **Ajouter un arrêt** : choisir la ligne, la
   direction, puis l'arrêt. Recommencer pour chaque arrêt à suivre.

Chaque arrêt suivi devient un appareil (ex. « Comédie → Mosson (Tram 1) ») avec :

| Entité | Description |
|---|---|
| `sensor.…_minutes_avant_le_prochain_passage` | **Minutes avant le prochain tram, arrondies à l'inférieur** : l'affichage à privilégier |
| `sensor.…_prochain_passage` | Heure exacte du prochain passage |
| `sensor.…_passage_suivant` | Heure exacte du passage d'après |
| `sensor.…_destination_du_prochain_passage` | Destination du prochain tram, telle qu'affichée à l'avant de la rame. Utile sur les lignes à branches (T3 : Lattes ou Pérols) et en cas de service modifié (terminus partiel) |

**Arrondi « pour ne pas rater le tram ».** Les minutes sont toujours arrondies à
l'inférieur et mises à jour à la seconde exacte où elles changent. Quand le
capteur annonce « 2 min », il reste donc au moins 2 minutes : en partant à ce
moment-là, on arrive en avance, jamais en retard. À l'inverse, l'affichage
« dans X minutes » que Home Assistant génère pour les capteurs d'heure arrondit
au plus proche et ne se rafraîchit qu'une fois par minute. Il peut surestimer
le temps restant jusqu'à 1 min 30 : préférez le capteur minutes pour décider
quand partir.

Attributs : `line`, `line_color`, `destination`, `delay` (minutes) et `source`.
Le capteur minutes porte aussi `departures`, les 6 prochains passages avec
leurs minutes restantes, calculées selon le même arrondi.

`source` indique la fiabilité de l'horaire :

- `realtime` : heure annoncée par le temps réel pour cet arrêt ;
- `estimated` : tram pas encore parti de son terminus, horaire théorique décalé
  du retard annoncé au départ ;
- `scheduled` : horaire théorique, sans information temps réel.

Si le flux temps réel est indisponible, les capteurs basculent sur les horaires
théoriques plutôt que de devenir indisponibles.

## Exemples

### Tableau de départs (carte Markdown)

```yaml
type: markdown
content: >
  {% set s = 'sensor.comedie_mosson_tram_1_minutes_avant_le_prochain_passage' %}
  ### Tram {{ state_attr(s, 'line') }} · Comédie
  Prochain : **{{ states('sensor.comedie_mosson_tram_1_destination_du_prochain_passage') }}**
  {% for d in state_attr(s, 'departures') or [] %}
  - **{{ d.minutes }} min** → {{ d.destination }}
    {%- if d.source == 'scheduled' %} _(théorique)_{% endif %}
    {%- if d.delay %} · retard {{ d.delay }} min{% endif %}
  {% endfor %}
```

### « Il est temps de partir »

Notification quand le prochain tram passe dans 8 minutes (temps de marche
jusqu'à l'arrêt) :

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.comedie_mosson_tram_1_minutes_avant_le_prochain_passage
    below: 9
actions:
  - action: notify.mobile_app_mon_telephone
    data:
      message: >
        Tram {{ state_attr(trigger.entity_id, 'line') }}
        dans {{ states(trigger.entity_id) }} min, c'est le moment de partir.
```

## Fonctionnement

- Le flux temps réel (GTFS-RT `TripUpdate`) est interrogé toutes les 30 secondes.
- Les horaires théoriques (GTFS, environ 5 Mo) sont téléchargés au démarrage puis
  chaque nuit à 3 h 30, et mis en cache dans `.storage/tam_montpellier/`. Seul le
  tram est conservé en mémoire.
- Le flux TaM ne détaille les trajets qu'une fois la rame partie de son terminus.
  Pour un arrêt en milieu de ligne, les passages suivants sont donc estimés à
  partir de l'horaire théorique et du retard annoncé au terminus.

## Développement

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python pytest-homeassistant-custom-component "protobuf==6.32.0" "gtfs-realtime-bindings==2.2.0"
.venv/bin/python -m pytest
```

Tableau de départs en direct depuis les vrais flux, sans Home Assistant :

```bash
.venv/bin/python scripts/live_board.py "Comédie" --line 1
```

L'icône rend hommage aux livrées des rames montpelliéraines, les hirondelles
de la ligne 1 et les fleurs de la ligne 2 dessinées par Garouste & Bonetti. Source :
[`assets/icon.svg`](assets/icon.svg). Elle s'affiche dans Home Assistant 2026.3+.

Données : © TaM / Montpellier Méditerranée Métropole, licence ODbL.
