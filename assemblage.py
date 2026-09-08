"""Assemblage du jeu de modélisation : valeur marchande, âge, statut, archétype.

Joint quatre sources par l'identifiant Transfermarkt :

    panel.csv.gz                  historique des valorisations (preparation.py)
    identifiants_transfermarkt    pont FBref <-> Transfermarkt
    statut.csv                    niveau du joueur, par saison
    archetypes.csv                style de jeu

Règle de rattachement : une valorisation datée est rattachée à la saison en
cours à cette date (une saison européenne court de juillet à juin). Le statut
et l'archétype utilisés sont donc contemporains de la valeur, jamais postérieurs.

Sortie : data/modelisation.csv

Usage : python assemblage.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

PANEL = Path("data/panel.csv.gz")
IDENTIFIANTS = Path("data/identifiants_transfermarkt.csv")
STATUT = Path("data/statut.csv")
ARCHETYPES = Path("data/archetypes.csv")
SORTIE = Path("data/modelisation.csv")

MOIS_BASCULE = 7          # une saison commence en juillet
AGES_PANIER = (24, 29)    # plateau de la courbe d'âge, pour l'indice

# Méthode de correction de l'inflation du marché :
#   "panier"  indice à composition constante, sur les joueurs revus d'une
#             saison à l'autre — conceptuellement le plus juste
#   "mediane" rapport des médianes annuelles — simple, mais mélange inflation
#             et renouvellement de l'échantillon
#   "aucune"  valeurs brutes
# Le bon choix se juge au biais de prédiction hors échantillon, pas a priori :
# lancer modele.py avec chacune et retenir celle qui le minimise.
METHODE_INFLATION = "panier"


def saison_de(dates: pd.Series) -> pd.Series:
    """Saison au sens FBref : 2024 désigne la saison 2023-24."""
    return dates.dt.year + (dates.dt.month >= MOIS_BASCULE).astype(int)


def charger_valorisations() -> pd.DataFrame:
    if not PANEL.exists():
        raise FileNotFoundError(f"{PANEL} absent — lancer d'abord preparation.py")

    panel = pd.read_csv(PANEL)
    if "date" not in panel.columns:
        raise ValueError(
            "panel.csv.gz ne contient pas de colonne 'date' : relancer "
            "preparation.py avec la version à jour du script"
        )

    panel["date"] = pd.to_datetime(panel["date"])
    panel["saison"] = saison_de(panel["date"])

    # L'âge et le poste viennent de FBref, plus fiables et déjà harmonisés.
    panel = panel.drop(columns=["age", "position", "sub_position"],
                       errors="ignore")
    return panel.rename(columns={"player_id": "tm_id"})


def indice_panier_constant(d: pd.DataFrame) -> pd.Series:
    """Indice d'évolution du marché, mesuré à composition constante.

    La médiane brute par saison mélange deux choses : l'inflation du marché et
    le renouvellement de l'échantillon. Corriger l'une avec l'autre gonfle le
    passé et fait surestimer la saison prédite.

    On mesure donc l'évolution sur les seuls joueurs présents deux saisons de
    suite, en chaînant les variations — le principe des indices de prix à
    ventes répétées.

    Le panier est restreint au plateau de la courbe d'âge (24-29 ans). Un
    joueur vieillit d'un an entre deux observations, et sa variation de valeur
    mélange donc inflation et effet d'âge ; sur le plateau, ce second terme est
    négligeable.
    """
    panier = d[d["age"].between(*AGES_PANIER)]
    saisons = sorted(d["saison"].unique())
    valeurs = panier.set_index(["joueur", "saison"])["market_value_in_eur"]

    indice = {saisons[0]: 1.0}
    for precedente, courante in zip(saisons, saisons[1:]):
        avant = valeurs.xs(precedente, level="saison")
        apres = valeurs.xs(courante, level="saison")
        communs = avant.index.intersection(apres.index)
        if len(communs) < 30:
            variation = 1.0
        else:
            variation = float(np.exp(
                np.log(apres[communs] / avant[communs]).median()
            ))
        indice[courante] = indice[precedente] * variation

    return pd.Series(indice, name="indice")


def indice_median(d: pd.DataFrame) -> pd.Series:
    """Rapport des médianes de chaque saison à celle de la première."""
    medianes = d.groupby("saison")["market_value_in_eur"].median()
    return (medianes / medianes.iloc[0]).rename("indice")


def indexer_inflation(d: pd.DataFrame) -> pd.DataFrame:
    """Exprime toutes les valeurs en euros de la dernière saison observée."""
    saisons = sorted(d["saison"].unique())
    if METHODE_INFLATION == "panier":
        indice = indice_panier_constant(d)
    elif METHODE_INFLATION == "mediane":
        indice = indice_median(d)
    elif METHODE_INFLATION == "aucune":
        indice = pd.Series(1.0, index=saisons, name="indice")
    else:
        raise ValueError(f"méthode inconnue : {METHODE_INFLATION}")
    derniere = max(indice.index)

    d = d.copy()
    d["indice"] = d["saison"].map(indice)
    d["valeur"] = d["market_value_in_eur"] * (indice[derniere] / d["indice"])
    d["log_valeur"] = np.log(d["valeur"])

    comparaison = pd.DataFrame({
        "médiane_brute_M€": (d.groupby("saison")["market_value_in_eur"].median()
                             / 1e6).round(2),
        "indice_panier": indice_panier_constant(d).round(3),
        "indice_médiane": indice_median(d).round(3),
        "retenu": indice.round(3),
    })
    print(f"\nÉvolution du marché (méthode retenue : {METHODE_INFLATION})")
    print(comparaison.to_string())

    return d


def main():
    valeurs = charger_valorisations()
    identifiants = pd.read_csv(IDENTIFIANTS)
    statut = pd.read_csv(STATUT)
    archetypes = pd.read_csv(ARCHETYPES)

    print(f"valorisations : {len(valeurs):,} lignes, "
          f"{valeurs['tm_id'].nunique():,} joueurs Transfermarkt")

    # Une seule valorisation par joueur et par saison : la plus tardive, donc
    # celle qui incorpore le plus d'information sur la saison écoulée.
    valeurs = (valeurs.sort_values("date")
               .drop_duplicates(["tm_id", "saison"], keep="last"))

    d = valeurs.merge(identifiants[["joueur", "tm_id"]], on="tm_id", how="inner")
    print(f"après appariement FBref : {len(d):,} lignes, "
          f"{d['joueur'].nunique():,} joueurs")

    d = d.merge(statut[["joueur", "saison", "age", "score_statut", "minutes"]],
                on=["joueur", "saison"], how="inner")
    print(f"avec statut contemporain : {len(d):,} lignes")

    # L'archétype n'est estimé que sur la saison de référence. On le rattache
    # au joueur pour toutes ses saisons : c'est une approximation assumée, un
    # style de jeu pouvant évoluer avec l'âge.
    d = d.merge(archetypes[["joueur", "famille", "archetype"]].drop_duplicates("joueur"),
                on="joueur", how="left")
    couverture = d["archetype"].notna().mean()
    print(f"avec archétype : {couverture:.1%} des lignes")

    d = d.dropna(subset=["archetype", "score_statut", "market_value_in_eur"])
    d = d[d["market_value_in_eur"] > 0]

    d = indexer_inflation(d)
    d["log_valeur"] = np.log(d["valeur"])

    colonnes = ["joueur", "tm_id", "saison", "annee", "age", "famille",
                "archetype", "score_statut", "minutes", "market_value_in_eur",
                "indice", "valeur", "log_valeur"]
    d[colonnes].sort_values(["joueur", "saison"]).to_csv(SORTIE, index=False)

    print(f"\n{SORTIE} : {len(d):,} observations, {d['joueur'].nunique():,} joueurs")
    print(f"saisons : {sorted(d['saison'].unique())}")
    print(f"âges    : {d['age'].min():.0f} à {d['age'].max():.0f}")
    print(f"\nvaleur médiane par saison (euros constants)")
    print((d.groupby("saison")["valeur"].median() / 1e6).round(2).to_string())


if __name__ == "__main__":
    main()
