"""Courbe d'âge estimée en intra-joueur, corrigée du biais de survie.

La courbe transversale de `analyse.py` compare, à chaque âge, des joueurs
différents. Or après 30 ans, seuls restent dans les cinq grands championnats
ceux qui ont tenu, c'est-à-dire les meilleurs. La médiane à 36 ans ne décrit
donc pas un joueur de 36 ans mais un survivant, et la courbe décroît trop peu.

On estime ici la trajectoire *à l'intérieur* de chaque joueur : comment la
valeur d'un même individu évolue quand il vieillit. Le biais de composition
disparaît, puisqu'on ne compare plus des joueurs entre eux.

Une hypothèse est nécessaire. Âge, année et année de naissance sont liés par
une identité — connaître deux termes donne le troisième — de sorte qu'on ne
peut pas estimer simultanément un effet d'âge, un effet de période et un effet
individuel. On impose donc l'effet de période : il est fixé à l'évolution
observée du marché sur un panier de joueurs au plateau de la courbe, où l'effet
d'âge est négligeable. Toute erreur sur cette hypothèse se reporte en pente sur
la courbe d'âge.

Entrée : data/panel.csv.gz
Sortie : figures/courbes_intra.csv (même schéma que figures/courbes.csv)

Usage : python courbe_intra.py
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PANEL = Path("data/panel.csv.gz")
TRANSVERSALE = Path("figures/courbes.csv")
SORTIE = Path("figures/courbes_intra.csv")

AGE_MIN, AGE_MAX = 17, 39
ANNEE_MIN = 2005
AGES_PLATEAU = (24, 29)     # effet d'âge négligeable, sert à l'indice de période
MIN_OBSERVATIONS = 3        # un joueur doit être vu plusieurs fois
N_MIN_PAR_AGE = 50

POSTES = ["Goalkeeper", "Defender", "Midfield", "Attack"]
LIBELLES = {"Goalkeeper": "Gardiens", "Defender": "Défenseurs",
            "Midfield": "Milieux", "Attack": "Attaquants"}
COULEURS = {"Goalkeeper": "#8c8c8c", "Defender": "#1f77b4",
            "Midfield": "#2ca02c", "Attack": "#d62728"}


def charger() -> pd.DataFrame:
    d = pd.read_csv(PANEL)
    d["date"] = pd.to_datetime(d["date"])
    d = d[d["age"].between(AGE_MIN, AGE_MAX)
          & (d["annee"] >= ANNEE_MIN)
          & (d["market_value_in_eur"] > 0)
          & d["position"].isin(POSTES)]

    # Une observation par joueur et par an : la dernière de l'année.
    d = d.sort_values("date").drop_duplicates(["player_id", "annee"], keep="last")
    d["log_valeur"] = np.log(d["market_value_in_eur"])
    return d


def indice_periode(d: pd.DataFrame) -> pd.Series:
    """Évolution du marché, mesurée sur les joueurs au plateau de la courbe."""
    panier = d[d["age"].between(*AGES_PLATEAU)]
    mediane = panier.groupby("annee")["log_valeur"].median()
    return (mediane - mediane.iloc[0]).rename("effet_periode")


def estimer_intra(d: pd.DataFrame) -> pd.DataFrame:
    """Profil d'âge intra-joueur, poste par poste.

    On retire à chaque observation la moyenne de son joueur : ne subsiste que
    la variation individuelle. Le profil d'âge est ensuite la moyenne, à chaque
    âge, de ces écarts.
    """
    periode = indice_periode(d)
    d = d.copy()
    d["net"] = d["log_valeur"] - d["annee"].map(periode)

    # Seuls les joueurs vus plusieurs fois portent de l'information intra.
    compte = d.groupby("player_id")["annee"].transform("size")
    d = d[compte >= MIN_OBSERVATIONS]
    d["ecart"] = d["net"] - d.groupby("player_id")["net"].transform("mean")

    lignes = []
    for poste, groupe in d.groupby("position"):
        profil = groupe.groupby("age").agg(
            effet=("ecart", "mean"),
            n=("ecart", "size"),
            n_joueurs=("player_id", "nunique"),
        )
        profil = profil[profil["n"] >= N_MIN_PAR_AGE]
        if profil.empty:
            continue

        # Le démoyennage par joueur décale le profil d'une constante propre à
        # la composition de l'échantillon ; on le recale sur son maximum.
        profil["effet"] -= profil["effet"].max()
        profil["position"] = poste
        lignes.append(profil.reset_index())

    return pd.concat(lignes, ignore_index=True)


def comparer(intra: pd.DataFrame) -> pd.DataFrame | None:
    """Met la courbe intra en regard de la courbe transversale existante."""
    if not TRANSVERSALE.exists():
        return None
    trans = pd.read_csv(TRANSVERSALE)
    trans["effet_transversal"] = (
        np.log(trans["valeur"])
        - np.log(trans.groupby("position")["valeur"].transform("max"))
    )
    return intra.merge(trans[["position", "age", "effet_transversal"]],
                       on=["position", "age"], how="left")


def main():
    d = charger()
    print(f"panel : {len(d):,} observations, {d['player_id'].nunique():,} joueurs")

    intra = estimer_intra(d)
    print(f"profil intra estimé sur {intra['n'].sum():,} observations\n")

    # Réexprimé en euros, au même format que figures/courbes.csv pour que les
    # scripts en aval fonctionnent sans modification.
    reference = (pd.read_csv(TRANSVERSALE).groupby("position")["valeur"].max()
                 if TRANSVERSALE.exists() else None)
    intra["valeur"] = [
        np.exp(effet) * (reference[poste] if reference is not None else 1e7)
        for effet, poste in zip(intra["effet"], intra["position"])
    ]
    intra[["position", "age", "valeur", "n", "n_joueurs", "effet"]].to_csv(
        SORTIE, index=False
    )

    comparaison = comparer(intra)
    if comparaison is not None:
        print("Décote depuis le pic, en pourcentage de la valeur maximale\n")
        for poste in POSTES:
            sub = comparaison[comparaison["position"] == poste]
            if sub.empty:
                continue
            print(f"{LIBELLES[poste]}")
            for age in (30, 33, 36, 38):
                ligne = sub[sub["age"] == age]
                if ligne.empty:
                    continue
                i = float(np.exp(ligne["effet"].iloc[0]) - 1)
                t = ligne["effet_transversal"].iloc[0]
                t = float(np.exp(t) - 1) if pd.notna(t) else np.nan
                print(f"  {age} ans : intra {i:+.0%}   transversal {t:+.0%}")
            print()

    fig, ax = plt.subplots(figsize=(10, 6))
    for poste in POSTES:
        sub = intra[intra["position"] == poste].sort_values("age")
        if sub.empty:
            continue
        ax.plot(sub["age"], np.exp(sub["effet"]), linewidth=2,
                color=COULEURS[poste], label=f"{LIBELLES[poste]} (intra)")
        if comparaison is not None:
            t = comparaison[comparaison["position"] == poste].sort_values("age")
            ax.plot(t["age"], np.exp(t["effet_transversal"]), linewidth=1,
                    linestyle="--", alpha=0.6, color=COULEURS[poste])
    ax.set(xlabel="Âge", ylabel="Valeur rapportée au pic",
           title="Courbe d'âge : intra-joueur (trait plein) contre "
                 "transversale (pointillés)")
    ax.grid(alpha=0.3)
    ax.legend(frameon=False, fontsize=9)
    fig.savefig("figures/courbe_intra.png", dpi=150, bbox_inches="tight")

    print(f"{SORTIE} et figures/courbe_intra.png écrits")


if __name__ == "__main__":
    main()
