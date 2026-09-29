# Itération annuelle enrichie (2021–2025)

## Entrée et reproduction

Exécuter depuis la racine du dépôt :

```bash
pip install -r requirements.txt
python iteration_v2.py /chemin/DBFOOT_joueurs_2021_2025.xlsx
python -m unittest discover -s tests
```

Le classeur source, 13 637 lignes et 237 colonnes sur la feuille `Joueurs`, n'est pas
versionné. Les sorties sont `data/v2/joueurs_annees.csv.gz` (données annuelles
enrichies), `data/v2/couverture.json` (contrôles de couverture) et
`data/v2/validation.csv` (résultats hors période). Les sorties du pipeline
historique, dans `data/` et `figures/`, restent la référence v1.
Le fichier annuel détaillé reste local et est ignoré par Git ; seuls les
contrôles et résultats agrégés sont publiés dans ce dépôt public.

## Contrat des données

Clé : `player_id, annee`. L'année est celle de fin de saison : 2023
correspond à 2022–23. Population : joueurs avec minutes documentées dans les
cinq grands championnats. Les cinq années 2021 à 2025 sont obligatoires.
L'export provient d'archives FBref détaillées ; son exhaustivité ne peut pas
être certifiée. Il a déjà regroupé les passages entre clubs : les volumes
disponibles ont été additionnés ; les ratios dépendant de données absentes
sont signalés dans `ratios_non_agreges`. `statistiques_multi_clubs` indique
les 698 lignes concernées. Le script **ne réagrège pas** les 237 colonnes
à partir de passages par club, car les lignes source club × joueur ne sont
pas présentes dans cet export. Une réconciliation des sommes par club exigera
ces lignes source.

`club_1`, `club_2`, `club_3` sont ordonnés lorsqu'une chronologie est
établie. Pour les 93 saisons marquées `ordre_non_resolu`, ces trois
colonnes sont vidées dans la sortie ; `clubs_observes` conserve la liste,
`nombre_clubs` le nombre. Aucun club de fin de saison n'est inféré.

| Champs | Définition et statut |
| --- | --- |
| `minutes`, statistiques FBref | Championnat seulement ; les volumes regroupent les clubs si les mesures source le permettent. |
| `valeur_marche_eur`, `date_valeur` | Estimation Transfermarkt datée ; elle n'est ni une transaction ni automatiquement une observation au 30 juin. La validation utilise seulement les valeurs antérieures à la date de référence de 180 jours au plus. |
| `matchs_manques_blessure`, `statut_blessures` | Archive toutes compétitions ; seuls les épisodes documentés et sans chevauchement entrent comme valeur chiffrée dans le modèle. L'absence d'épisode documenté reste manquante. |
| `fin_contrat_archive_saison`, `statut_contrat` | Une archive existe pour une partie de 2020–21 mais n'est pas certifiée au 30 juin. `fin_contrat_au_30_juin` est vide dans les cinq années ; aucune durée restante n'entre dans le modèle. |
| `salaire_annuel_brut_estime`, `devise_salaire`, `statut_salaire` | Estimation Capology dans la devise originale : GBP en 2021, EUR pour le sous-ensemble disponible en 2023–25, aucun salaire en 2022. Le taux annuel du dernier club observé n'est pas un revenu cumulé de saison. Pas de conversion ni d'imputation. |
| `tm_id`, `fbref_id`, `statut_matching` | Identifiants et traçabilité des appariements ; `player_id` conserve les joueurs non appariés. |

## Modèle expérimental

`iteration_v2.py` estime séparément 2024 sur 2021–23, puis 2025 sur
2021–24. Il combine âge, rang des minutes dans la saison (indicateur
de statut), groupes de statistiques par poste (style), et résidu individuel
réduit vers zéro. Les groupes sont réappris exclusivement sur les années
d'entraînement à chaque fenêtre ; ils portent des codes, **pas** les noms
des 24 archétypes v1. La seconde variante ajoute les matchs de blessure
documentés, leur historique et un indicateur de disponibilité. Les valeurs
manquantes reçoivent une imputation apprise sur l'entraînement avec indicateur
de manque ; elles ne sont pas interprétées comme zéro.

Les salaires demeurent dans la table et le rapport de couverture. Leur
couverture et leur mélange de devises ne permettent pas d'estimer un effet
général comparable entre saisons ; les contrats n'ont aucune date historique
certifiée. Une estimation de leur contribution attend des sources meilleures.
Les données de la saison test sont utilisées pour ses variables de fin de
saison, jamais pour ajuster l'estimateur ni les centroïdes.

`validation.csv` compare les variantes sur toute la population évaluable,
puis sur **exactement les mêmes joueurs** que la valeur de l'année précédente.
L'erreur relative est `exp(médiane(|log(valeur/prédiction)|)) - 1`.
Le modèle expérimental sous-performe actuellement la persistance sur le
sous-ensemble comparable : il ne remplace pas le modèle historique. Les
34 % annoncés par la v1 concernent un autre échantillon et une autre
procédure, donc ne constituent pas un écart de performance mesuré ici.

## Contrôles et limites

Le pipeline échoue si une année, une clé, une colonne obligatoire, une minute
positive, une saison ou une date de référence est incohérente. Il refuse une
valorisation postérieure à la date de référence et une échéance de contrat
présentée comme certifiée alors que ce statut n'existe pas dans l'export.
Le rapport donne les effectifs, valeurs, blessures documentées, salaires,
valorisations anciennes et contrats certifiés par année.

Le statut v2 utilise le temps de jeu observé : la force des clubs v1 n'est
pas historique et ne peut être attribuée correctement à chacun des passages.
Le modèle v2 est une expérience distincte, sans prétention à reproduire
les estimations, les archétypes nommés ou la précision de la v1. La base
locale est une transformation de l'export de travail, et non une preuve
d'exhaustivité ou une archive des épisodes de blessure individuels.
