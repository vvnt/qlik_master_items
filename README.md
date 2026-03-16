# qlik_master_items — Gestion des master items Qlik Cloud

Outil en ligne de commande Python pour gérer les **master items de type measure**
d'une application Qlik Cloud via le SDK officiel `qlik-sdk`.

---

## Structure des fichiers

```
qlik_master_items/
├── qlik_master_items.py   ← script principal
├── config.json            ← configuration tenant / clé API
├── number_formats.json    ← bibliothèque de formats numériques
├── measures_example.json  ← exemple de fichier de master items
├── qlik_master_items.log  ← fichier de log (créé automatiquement)
└── README.md
```

---

## Prérequis

```bash
pip install qlik-sdk
```

---

## Configuration (`config.json`)

```json
{
  "tenant_url": "https://your-tenant.eu.qlikcloud.com",
  "api_key": "your_api_key_here"
}
```

Créer une clé API dans Qlik Cloud :
**Profil → Paramètres → Clés API → Générer une nouvelle clé**.

---

## Commandes disponibles

### Lister les master items

```bash
python qlik_master_items.py --app-id <APP_ID> list
```

### Exporter la liste (format ré-injectable dans `upsert`)

```bash
python qlik_master_items.py --app-id <APP_ID> list -o export.json
```

### Créer / Mettre à jour (`upsert`)

```bash
python qlik_master_items.py --app-id <APP_ID> upsert -f measures.json
```

- Si un master item avec ce `name` **n'existe pas** → il est **créé**.
- S'il **existe déjà** → il est **mis à jour**.
- La correspondance se fait sur le champ `name`.

### Supprimer

```bash
# Par nom(s) explicite(s)
python qlik_master_items.py --app-id <APP_ID> delete -n ca_n marge_n

# Via un fichier JSON (utilise le champ "name")
python qlik_master_items.py --app-id <APP_ID> delete -f measures.json
```

### Lister les formats numériques disponibles

```bash
python qlik_master_items.py formats
```

### Inspecter les formats bruts retournés par l'Engine

```bash
python qlik_master_items.py --app-id <APP_ID> inspect
python qlik_master_items.py --app-id <APP_ID> inspect -o formats_bruts.json
```

Utile pour calibrer `number_formats.json` sur les valeurs exactes de Qlik
(notamment pour les measures créées manuellement dans l'UI).

### Dump brut pour diagnostic

```bash
python qlik_master_items.py --app-id <APP_ID> dump
python qlik_master_items.py --app-id <APP_ID> dump -n panier_moy_n ca_n -o diagnostic.json
```

Exporte `get_properties` + `get_layout` bruts pour une ou plusieurs measures.
Utile pour comparer l'état d'une measure avant/après une modification.

### Fichier de configuration personnalisé

```bash
python qlik_master_items.py --config /chemin/config.json --app-id <APP_ID> list
```

---

## Format du fichier de master items (JSON)

Le fichier contient un tableau d'objets. Chaque objet décrit une measure :

| Champ              | Type   | Obligatoire | Description                                   |
|--------------------|--------|-------------|-----------------------------------------------|
| `name`             | string | ✅           | Identifiant unique, utilisé pour l'upsert     |
| `expression`       | string | ✅           | Expression Qlik                               |
| `label`            | string | ✗           | Label affiché dans les visualisations         |
| `label_expression` | string | ✗           | Label dynamique (expression Qlik)             |
| `number_format`    | string | ✗           | Clé du format dans `number_formats.json`      |
| `description`      | string | ✗           | Description longue                            |
| `color`            | string | ✗           | Couleur hexadécimale (`#RRGGBB`)              |

### Exemple

```json
[
  {
    "name": "ca_n",
    "expression": "Sum(sales)",
    "label": "CA",
    "number_format": "currency_eur_0",
    "description": "Chiffre d'affaires",
    "color": "#2CA02C"
  },
  {
    "name": "commande_nb_n",
    "expression": "Count(distinct order_id)",
    "label": "Nb commandes",
    "number_format": "integer"
  },
  {
    "name": "panier_moy_n",
    "expression": "ca_n / commande_nb_n",
    "label": "Panier moyen",
    "number_format": "currency_eur_2"
  },
  {
    "name": "marge_tx_n",
    "expression": "marge_n / ca_n",
    "label": "Taux de marge",
    "number_format": "percent_1"
  }
]
```

---

## Formats numériques disponibles

| Clé           | Label                   | Exemple affiché |
|---------------|-------------------------|-----------------|
| `integer`     | Entier                  | `1 234`         |
| `float_1`   | Décimal (1 chiffre)     | `1 234,5`       |
| `float_2`   | Décimal (2 chiffres)    | `1 234,56`      |
| `currency_eur_0`    | Monétaire (€)           | `1 234 €`    |
| ...           |                         |                 |
| `none`        | Aucun format            | valeur brute    |

Le séparateur de milliers utilise l'**espace insécable** (U+00A0).

Pour ajouter un format, éditer `number_formats.json`. En cas de doute sur
les valeurs exactes attendues par Qlik, utiliser la commande `inspect` après avoir créé une measure manuellement dans l'UI.

---

## Logging

- **Console** : niveau INFO, sans horodatage.
- **Fichier** `qlik_master_items.log` : tous les niveaux (DEBUG inclus),
  avec horodatage — mode **append**.
