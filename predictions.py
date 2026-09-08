"""Comparaison des valeurs prédites pour 2024-25 aux valeurs Transfermarkt.

Le modèle est estimé sur les saisons antérieures uniquement : aucune
information de 2024-25 n'entre dans l'estimation. Les écarts constatés sont
donc de vraies erreurs de prédiction, pas des résidus d'ajustement.

Deux lectures :
  - les joueurs que le modèle valorise nettement au-dessus du marché
  - ceux qu'il valorise en dessous

Un écart n'est pas une opportunité : c'est d'abord un candidat au diagnostic.
Blessure longue, fin de contrat, transfert vers un championnat exotique,
suspension — autant de causes que le modèle ignore et qui expliquent la
majorité des grands écarts. L'inspection qualitative de cette liste fait
partie de la méthode, pas de son commentaire.

Entrées : data/modelisation.csv, figures/courbes.csv
Sortie  : figures/predictions_2025.csv

Usage : python predictions.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

from modele import SAISON_TEST, estimer, preparer

SORTIE = Path("figures/predictions_2025.csv")
N_EXTREMES = 15


def main():
    d = preparer()
    apprentissage = d[d["saison"] < SAISON_TEST]
    test = d[d["saison"] == SAISON_TEST].copy()

    print(f"estimation sur {len(apprentissage):,} observations "
          f"({sorted(apprentissage['saison'].unique())})")
    print(f"prédiction sur {len(test):,} joueurs (saison {SAISON_TEST})\n")

    ajustement = estimer(apprentissage)
    effets = ajustement.effets_joueurs()

    test["effet_joueur"] = test["joueur"].map(effets)
    test["deja_vu"] = test["effet_joueur"].notna()
    test["effet_joueur"] = test["effet_joueur"].fillna(0.0)

    log_predit = (ajustement.predict(test) + test["effet_age"]
                  + test["effet_joueur"])
    test["valeur_predite"] = np.exp(log_predit)
    test["valeur_reelle"] = test["valeur"]
    test["rapport"] = test["valeur_predite"] / test["valeur_reelle"]
    test["ecart_log"] = log_predit - test["log_valeur"]

    colonnes = ["joueur", "age", "famille", "archetype", "score_statut",
                "deja_vu", "valeur_reelle", "valeur_predite", "rapport",
                "ecart_log"]
    resultat = test[colonnes].sort_values("rapport", ascending=False)
    resultat.to_csv(SORTIE, index=False)

    def afficher(titre, sous_ensemble):
        vue = sous_ensemble.copy()
        vue["réelle"] = (vue["valeur_reelle"] / 1e6).round(1)
        vue["prédite"] = (vue["valeur_predite"] / 1e6).round(1)
        vue["rapport"] = vue["rapport"].round(2)
        print(f"\n{titre}")
        print(vue[["joueur", "age", "archetype", "réelle", "prédite",
                   "rapport"]].to_string(index=False))

    # On se limite aux joueurs déjà connus : pour les autres, l'écart mesure
    # surtout l'absence d'effet individuel, pas un désaccord avec le marché.
    connus = resultat[resultat["deja_vu"]]

    afficher(f"=== Sous-évalués par le marché selon le modèle "
             f"(top {N_EXTREMES}) ===", connus.head(N_EXTREMES))
    afficher(f"=== Surévalués par le marché selon le modèle "
             f"(top {N_EXTREMES}) ===", connus.tail(N_EXTREMES).iloc[::-1])

    print("\n=== Qualité globale ===")
    for nom, sel in [("joueurs déjà vus", connus),
                     ("joueurs nouveaux", resultat[~resultat["deja_vu"]])]:
        if sel.empty:
            continue
        absolue = sel["ecart_log"].abs()
        dans_30 = (absolue < np.log(1.3)).mean()
        print(f"  {nom:18} n={len(sel):5}  "
              f"erreur médiane {np.exp(absolue.median()) - 1:5.0%}  "
              f"à moins de 30 % : {dans_30:.0%}")

    print(f"\n=== Écart médian par tranche de valeur réelle ===")
    tranches = pd.cut(connus["valeur_reelle"] / 1e6,
                      [0, 1, 5, 20, 50, 1000],
                      labels=["<1 M€", "1-5", "5-20", "20-50", ">50"])
    table = connus.groupby(tranches, observed=True).apply(
        lambda g: pd.Series({
            "n": len(g),
            "erreur_médiane": f"{np.exp(g['ecart_log'].abs().median()) - 1:.0%}",
            "biais_médian": f"{np.exp(g['ecart_log'].median()) - 1:+.0%}",
        }), include_groups=False
    )
    print(table.to_string())

    print("\n=== Biais médian par tranche d'âge ===")
    # La courbe d'âge vient du panel Transfermarkt complet, où les joueurs
    # encore présents après 33 ans sont les survivants, donc les meilleurs.
    # Si le biais explose aux âges élevés, c'est ce biais de survie qui parle.
    tranches_age = pd.cut(connus["age"], [16, 21, 24, 28, 32, 36, 45],
                          labels=["17-21", "22-24", "25-28", "29-32",
                                  "33-36", "37+"])
    par_age = connus.groupby(tranches_age, observed=True).apply(
        lambda g: pd.Series({
            "n": len(g),
            "biais_médian": f"{np.exp(g['ecart_log'].median()) - 1:+.0%}",
            "erreur_médiane": f"{np.exp(g['ecart_log'].abs().median()) - 1:.0%}",
        }), include_groups=False
    )
    print(par_age.to_string())

    print(f"\n{SORTIE} écrit")


if __name__ == "__main__":
    main()
