# Valeur marchande des footballeurs par poste et par âge

Étude descriptive : à quel âge un joueur vaut-il le plus cher, et comment cela
varie selon son poste. Périmètre : les cinq grands championnats européens
depuis 2005.

## Installation

```bash
pip install -r requirements.txt
```

## Utilisation

```bash
python preparation.py   # une seule fois : télécharge et prépare data/panel.csv.gz
python analyse.py       # produit figures/courbes.png
```

## Structure

```
data/brut/        fichiers source téléchargés (~300 Mo, non versionnés)
data/panel.csv.gz extrait de travail (versionné)
preparation.py    téléchargement et préparation
analyse.py        agrégation et graphique
figures/          sorties
```

## Données

Source : [transfermarkt-datasets](https://github.com/dcaribou/transfermarkt-datasets).
Les valeurs Transfermarkt sont des estimations, pas des montants de transaction.

## Méthode

Chaque valeur est rapportée à la médiane de son année avant agrégation, pour
neutraliser l'inflation du marché : sans cela on mesurerait autant l'année
d'observation que l'âge.

À noter dans l'interprétation : après 30 ans, seuls les joueurs restés
compétitifs figurent dans l'échantillon. La médiane à 34 ans décrit donc un
survivant, pas un joueur moyen de 34 ans.
