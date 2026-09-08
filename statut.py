"""Score de statut : position d'un joueur dans la hiérarchie du football.

Remplace les trois paliers manuels (émergent / confirmé / top) par un score
continu, construit uniquement à partir de variables observables :

  - force du club de la saison
  - part des minutes de l'équipe jouées dans la saison
  - plancher : la pire part de minutes sur les saisons passées
  - saisons "élite" (>=60% des minutes dans un club coté), normalisées par
    le nombre de saisons possibles depuis les 18 ans du joueur

Aucun jugement manuel n'entre dans le calcul. Les étiquettes de l'ancien
projet servent uniquement de contrôle a posteriori.

Entrée : data/joueurs_saisons.csv (produit par base.py)
Sortie : data/statut.csv

Usage : python statut.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path("data/joueurs_saisons.csv.gz")
LABELS = Path("data/labels_manuels.csv")
SORTIE = Path("data/statut.csv")

SEUIL_MINUTES_ELITE = 60.0   # part des minutes de l'équipe
SEUIL_ETOILES_ELITE = 2.5    # force du club
MIN_MINUTES = 450            # en dessous, le joueur n'est pas évaluable

VARIABLES = ["etoiles", "part_minutes", "plancher_minutes", "saisons_elite"]


def charger() -> pd.DataFrame:
    if not BASE.exists():
        raise FileNotFoundError(f"{BASE} absent — lancer d'abord base.py")

    colonnes = ["joueur", "saison", "club_saison", "championnat", "age",
                "etoiles", "std_Min_Playing", "std_Starts_Playing",
                "tps_Min_percent_Playing.Time"]
    df = pd.read_csv(BASE, usecols=colonnes, low_memory=False)

    df = df.rename(columns={
        "std_Min_Playing": "minutes",
        "std_Starts_Playing": "titularisations",
        "tps_Min_percent_Playing.Time": "part_minutes",
    })
    df["part_minutes"] = df["part_minutes"].fillna(0)

    print(f"base : {len(df):,} lignes, {df['joueur'].nunique():,} joueurs, "
          f"saisons {sorted(df['saison'].unique())}")
    return df


def construire_variables(panel: pd.DataFrame) -> pd.DataFrame:
    """Ajoute le plancher et les saisons élite, calculés sur le passé."""
    panel = panel.copy()

    panel["elite"] = (
        (panel["part_minutes"] >= SEUIL_MINUTES_ELITE)
        & (panel["etoiles"] >= SEUIL_ETOILES_ELITE)
    ).astype(int)

    # Seules les saisons antérieures ou égales comptent : pas de fuite du futur.
    morceaux = []
    for saison in sorted(panel["saison"].unique()):
        cumul = (
            panel[panel["saison"] <= saison]
            .groupby("joueur")
            .agg(plancher_minutes=("part_minutes", "min"),
                 saisons_elite_brut=("elite", "sum"),
                 saisons_vues=("saison", "nunique"))
        )
        morceaux.append(
            panel[panel["saison"] == saison].merge(cumul, on="joueur", how="left")
        )
    panel = pd.concat(morceaux, ignore_index=True)

    # Normalisation par l'ancienneté : sans elle, le compteur de saisons élite
    # encoderait l'âge, déjà présent par ailleurs dans le modèle de valeur.
    possibles = np.clip(
        np.minimum(panel["age"] - 17, panel["saisons_vues"]), 1, None
    )
    panel["saisons_elite"] = panel["saisons_elite_brut"] / possibles

    return panel


def calculer_score(panel: pd.DataFrame) -> pd.DataFrame:
    """Premier axe principal des variables de statut, en centiles par saison.

    L'analyse en composantes principales est préférée à une pondération
    choisie : les quatre variables étant toutes positivement corrélées, le
    premier axe est l'axe de statut, et ses poids sont estimés, pas décrétés.

    Les centiles sont calculés saison par saison. Sur l'ensemble du panel, le
    plancher se durcit à mesure que l'historique s'allonge, ce qui ferait
    baisser artificiellement le score des saisons récentes — une tendance que
    le modèle de valeur confondrait avec l'inflation du marché.
    """
    panel = panel.copy()
    evaluable = (
        (panel["minutes"] >= MIN_MINUTES)
        & panel[VARIABLES].notna().all(axis=1)
    )
    print(f"évaluables (>= {MIN_MINUTES} min) : {evaluable.sum():,} "
          f"sur {len(panel):,}")

    X = panel.loc[evaluable, VARIABLES].astype(float)
    Z = ((X - X.mean()) / X.std().replace(0, 1)).to_numpy()

    _, valeurs, vecteurs = np.linalg.svd(Z - Z.mean(0), full_matrices=False)
    axe = vecteurs[0]
    if axe.sum() < 0:                       # orienter le score vers le haut
        axe = -axe

    panel.loc[evaluable, "axe_statut"] = Z @ axe
    panel["score_statut"] = (
        panel.groupby("saison")["axe_statut"].rank(pct=True) * 100
    )

    variance = valeurs[0] ** 2 / (valeurs ** 2).sum()
    print(f"\npoids du premier axe (variance expliquée {variance:.0%}) :")
    print(pd.Series(axe, index=VARIABLES).round(3).to_string())

    return panel


def controle(panel: pd.DataFrame):
    """Comparaison avec les étiquettes manuelles, jamais utilisées au calcul."""
    if not LABELS.exists():
        return

    labels = pd.read_csv(LABELS).drop_duplicates("joueur")[["joueur", "palier"]]
    ref = (
        panel[panel["saison"] == 2024]
        .merge(labels, on="joueur", how="inner")
        .dropna(subset=["score_statut"])
    )
    if ref.empty:
        return

    print("\nContrôle sur les étiquettes 2023-24 (hors calcul)")
    print(ref.groupby("palier")["score_statut"]
          .describe()[["count", "25%", "50%", "75%"]]
          .round(1).reindex(["otw2", "otw1", "top"]).to_string())


def main():
    panel = calculer_score(construire_variables(charger()))

    colonnes = ["joueur", "saison", "club_saison", "championnat", "age",
                "minutes", *VARIABLES, "score_statut"]
    panel[colonnes].sort_values(["saison", "joueur"]).to_csv(SORTIE, index=False)

    print("\nMédiane du score par saison (doit rester proche de 50)")
    print(panel.groupby("saison")["score_statut"].median().round(1).to_string())

    print("\nMédiane du score par tranche d'âge")
    tranches = pd.cut(panel["age"], [17, 20, 23, 26, 29, 32, 40])
    print(panel.groupby(tranches, observed=True)["score_statut"]
          .median().round(1).to_string())

    controle(panel)
    print(f"\n{SORTIE} écrit")


if __name__ == "__main__":
    main()
