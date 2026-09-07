"""Courbes de valeur marchande par poste et par âge, à partir de data/panel.csv.gz."""

import matplotlib.pyplot as plt
import pandas as pd

POSTES = {"Goalkeeper": "Gardiens", "Defender": "Défenseurs",
          "Midfield": "Milieux", "Attack": "Attaquants"}


def main():
    df = pd.read_csv("data/panel.csv.gz")

    # Le marché a fortement inflaté depuis 2005, et les jeunes joueurs sont
    # surtout observés dans les années récentes. Sans cette correction, la
    # courbe mesurerait autant l'année d'observation que l'âge.
    mediane = df.groupby("annee")["market_value_in_eur"].transform("median")
    echelle = df[df["annee"] == df["annee"].max()]["market_value_in_eur"].median()
    df["valeur"] = df["market_value_in_eur"] / mediane * echelle

    courbes = (
        df.groupby(["position", "age"])
        .agg(valeur=("valeur", "median"), n=("valeur", "size"))
        .reset_index()
        .query("n >= 30")
    )

    print(f"\n{len(df):,} observations, {df['player_id'].nunique():,} joueurs\n")
    for poste, libelle in POSTES.items():
        sub = courbes[courbes["position"] == poste]
        pic = sub.loc[sub["valeur"].idxmax()]
        print(f"{libelle:<12} pic à {pic['age']} ans ({pic['valeur'] / 1e6:.1f} M€)")

    fig, ax = plt.subplots(figsize=(10, 6))
    for poste, libelle in POSTES.items():
        sub = courbes[courbes["position"] == poste].sort_values("age")
        ax.plot(sub["age"], sub["valeur"] / 1e6, marker="o",
                markersize=4, linewidth=2, label=libelle)

    ax.set(xlabel="Âge", ylabel="Valeur marchande médiane (M€)",
           title="Valeur marchande par âge et par poste — Big 5, euros constants")
    ax.grid(alpha=0.3)
    ax.legend(frameon=False)
    fig.savefig("figures/courbes.png", dpi=150, bbox_inches="tight")
    courbes.to_csv("figures/courbes.csv", index=False)

    print("\nfigures/courbes.png et figures/courbes.csv écrits")


if __name__ == "__main__":
    main()
