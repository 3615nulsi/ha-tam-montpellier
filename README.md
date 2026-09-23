# TaM Montpellier – tramway pour Home Assistant

<img src="custom_components/tam_montpellier/brand/icon@2x.png" alt="Icône" width="128" align="right">

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Validate](https://github.com/3615nulsi/ha-tam-montpellier/actions/workflows/validate.yml/badge.svg)](https://github.com/3615nulsi/ha-tam-montpellier/actions/workflows/validate.yml)
[![Tests](https://github.com/3615nulsi/ha-tam-montpellier/actions/workflows/tests.yml/badge.svg)](https://github.com/3615nulsi/ha-tam-montpellier/actions/workflows/tests.yml)

Intégration Home Assistant qui affiche les **prochains passages du tramway de
Montpellier** aux arrêts de votre choix, à partir de l'open data temps réel de la
TaM ([jeu de données](https://data.montpellier3m.fr/dataset/offre-de-transport-tam-en-temps-reel),
licence ODbL).

## Installation

### Avec HACS (recommandé)

[![Ouvrir dans HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=3615nulsi&repository=ha-tam-montpellier&category=integration)

Ou manuellement : *HACS → ⋮ → Dépôts personnalisés*, ajouter
`https://github.com/3615nulsi/ha-tam-montpellier` (type *Intégration*),
installer **TaM Montpellier**, puis redémarrer Home Assistant.

### Manuelle

Copier `custom_components/tam_montpellier` dans le dossier `custom_components`
de votre configuration, puis redémarrer Home Assistant.

Home Assistant 2026.2 ou plus récent est requis ; l'icône de l'intégration
s'affiche à partir de 2026.3.

## Configuration

1. [![Ajouter l'intégration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=tam_montpellier)
   ou *Paramètres → Appareils et services → Ajouter une intégration → TaM Montpellier*.
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

| `binary_sensor.…_perturbation` | Allumé quand une alerte trafic TaM en cours concerne la ligne, l'arrêt ou tout le réseau |
| `sensor.…_message_de_perturbation` | Texte des alertes en cours (séparées par « • »), ou « Aucune perturbation ». Tronqué à 255 caractères, limite de HA ; le texte complet est dans l'attribut `full_message` |

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

Le capteur **Perturbation** expose `message` (le texte de la première alerte) et
`alerts`, la liste des alertes en cours avec leur message, leur fin prévue et
leur code interne TaM (`title`). Si le flux d'alertes est momentanément
injoignable, les dernières alertes connues sont conservées.

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

### Carte « afficheur TaM » (button-card)

Le dossier [`lovelace/`](lovelace/) contient une carte façon afficheur de quai,
aux couleurs officielles de chaque ligne : bandeau de ligne (avec les hirondelles
de la T1 ou les fleurs de la T2 en filigrane), compte à rebours en grand, voyant
temps réel, trams suivants en pastilles et bandeau d'alerte en cas de
perturbation. Elle nécessite [button-card](https://github.com/custom-cards/button-card).

```bash
python3 lovelace/build_dashboard.py sensor.comedie_mosson_tram_1_minutes_avant_le_prochain_passage
```

génère `lovelace/dashboard.json` : le modèle `tam_stop` (dans
`button_card_templates`) et une vue avec une carte par arrêt. Une fois le modèle
en place, une carte se déclare ainsi :

```yaml
type: custom:button-card
template: tam_stop
entity: sensor.comedie_mosson_tram_1_minutes_avant_le_prochain_passage
triggers_update:
  - sensor.comedie_mosson_tram_1_message_de_perturbation
```

### Bandeau de perturbation (affiché seulement en cas d'alerte)

```yaml
type: conditional
conditions:
  - condition: state
    entity: binary_sensor.comedie_mosson_tram_1_perturbation
    state: "on"
card:
  type: markdown
  content: >
    ⚠️ {{ state_attr('sensor.comedie_mosson_tram_1_message_de_perturbation', 'full_message') }}
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

- Les flux temps réel (GTFS-RT `TripUpdate` et `Alert`) sont interrogés toutes les 30 secondes.
- Les horaires théoriques (GTFS, environ 5 Mo) sont téléchargés au démarrage puis
  chaque nuit à 3 h 30, et mis en cache dans `.storage/tam_montpellier/`. Seul le
  tram est conservé en mémoire.
- Le flux TaM ne détaille les trajets qu'une fois la rame partie de son terminus.
  Pour un arrêt en milieu de ligne, les passages suivants sont donc estimés à
  partir de l'horaire théorique et du retard annoncé au terminus.

## Développement

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
```

Tableau de départs en direct depuis les vrais flux, sans Home Assistant :

```bash
.venv/bin/python scripts/live_board.py "Comédie" --line 1
```

L'icône rend hommage aux livrées des rames montpelliéraines, les hirondelles
de la ligne 1 et les fleurs de la ligne 2 dessinées par Garouste & Bonetti. Source :
[`assets/icon.svg`](assets/icon.svg). Elle s'affiche dans Home Assistant 2026.3+.

## Licence

Code sous licence [MIT](LICENSE). Projet indépendant, non affilié à TaM ni à
Montpellier Méditerranée Métropole.

Données : © TaM / Montpellier Méditerranée Métropole, licence
[ODbL](https://opendatacommons.org/licenses/odbl/).
