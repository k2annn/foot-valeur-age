"""Ajustement de fonctions paramétriques sur les courbes valeur/âge.

Les modèles sont estimés sur le logarithme de la valeur médiane, pondéré par
l'effectif de chaque cellule (poste, âge).

Usage : python modelisation.py
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

POSTES = {"Goalkeeper": "Gardiens", "Defender": "Défenseurs",
          "Midfield": "Milieux", "Attack": "Attaquants"}


# --- Les quatre familles de courbes ----------------------------------------
# Toutes renvoient log(valeur). Le passage au log est essentiel : la valeur
# marchande varie sur deux ordres de grandeur, un ajustement en euros bruts
# serait entièrement dominé par les âges les plus chers.

def quadratique(x, a, b, c):
    """Parabole en log. Symétrique par construction : sert de référence."""
    return a + b * x + c * x ** 2


def cubique(x, a, b, c, d):
    """Un degré de plus, qui autorise une asymétrie."""
    return a + b * x + c * x ** 2 + d * x ** 3


def gaussienne_asymetrique(x, sommet, mu, sigma_g, sigma_d):
    """Cloche à deux largeurs : montée en sigma_g, descente en sigma_d.

    Paramètres directement interprétables : mu est l'âge du pic, et le rapport
    sigma_d / sigma_g mesure l'asymétrie de la carrière.
    """
    sigma = np.where(x < mu, sigma_g, sigma_d)
    return sommet - (x - mu) ** 2 / (2 * sigma ** 2)


def gamma_log(x, constante, alpha, beta, origine):
    """Forme gamma : croissance en puissance, décroissance exponentielle.

    Asymétrique par nature, avec un pic en origine + alpha / beta.
    """
    u = np.clip(x - origine, 1e-6, None)
    return constante + alpha * np.log(u) - beta * u


MODELES = {
    "quadratique": (quadratique, lambda x, y: [y.mean(), 0.0, -0.01]),
    "cubique": (cubique, lambda x, y: [y.mean(), 0.0, -0.01, 0.0]),
    "gaussienne asym.": (
        gaussienne_asymetrique,
        lambda x, y: [y.max(), x[np.argmax(y)], 5.0, 7.0],
    ),
    "gamma": (
        gamma_log,
        lambda x, y: [y.max(), 2.0, 2.0 / max(x[np.argmax(y)] - 14.0, 1.0), 14.0],
    ),
}


# --- Estimation ------------------------------------------------------------

def ajuster(modele, depart, x, y, poids):
    """Estime les paramètres et renvoie les diagnostics d'ajustement."""
    parametres, _ = curve_fit(
        modele, x, y, p0=depart(x, y), sigma=1 / np.sqrt(poids),
        absolute_sigma=False, maxfev=20000,
    )
    residus = y - modele(x, *parametres)
    scr = float(np.sum(residus ** 2))
    n, k = len(x), len(parametres)

    grille = np.linspace(x.min(), x.max(), 1000)
    pic = float(grille[np.argmax(modele(grille, *parametres))])

    return {
        "parametres": parametres,
        "rmse": float(np.sqrt(scr / n)),
        "r2": float(1 - scr / np.sum((y - y.mean()) ** 2)),
        "aic": float(n * np.log(scr / n) + 2 * k),
        "age_du_pic": pic,
    }


def main():
    courbes = pd.read_csv("figures/courbes.csv")
    resultats = []
    ajustements = {}

    for poste in POSTES:
        sub = courbes[courbes["position"] == poste].sort_values("age")
        x = sub["age"].to_numpy(float)
        y = np.log(sub["valeur"].to_numpy(float))
        poids = sub["n"].to_numpy(float)

        for nom, (modele, depart) in MODELES.items():
            try:
                resultat = ajuster(modele, depart, x, y, poids)
            except (RuntimeError, ValueError):
                print(f"  échec de convergence : {POSTES[poste]} / {nom}")
                continue

            ajustements[(poste, nom)] = (modele, resultat["parametres"])
            resultats.append({
                "poste": POSTES[poste],
                "modele": nom,
                "k": len(resultat["parametres"]),
                "R2": round(resultat["r2"], 4),
                "RMSE_log": round(resultat["rmse"], 4),
                "AIC": round(resultat["aic"], 1),
                "age_du_pic": round(resultat["age_du_pic"], 1),
            })

    table = pd.DataFrame(resultats)
    table.to_csv("figures/modeles.csv", index=False)

    print("\nQualité d'ajustement par poste et par modèle\n")
    for poste in POSTES.values():
        bloc = table[table["poste"] == poste].sort_values("AIC")
        print(f"{poste}")
        print(bloc[["modele", "k", "R2", "RMSE_log", "AIC",
                    "age_du_pic"]].to_string(index=False))
        print()

    meilleurs = table.loc[table.groupby("poste")["AIC"].idxmin()]
    print("Modèle retenu (AIC minimal) par poste\n")
    print(meilleurs[["poste", "modele", "R2", "age_du_pic"]].to_string(index=False))

    # --- Graphique : données observées et courbes ajustées ------------------
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)

    for ax, (poste, libelle) in zip(axes.ravel(), POSTES.items()):
        sub = courbes[courbes["position"] == poste].sort_values("age")
        x = sub["age"].to_numpy(float)
        ax.scatter(x, sub["valeur"] / 1e6, s=18, color="black",
                   zorder=3, label="Médiane observée")

        grille = np.linspace(x.min(), x.max(), 300)
        for nom in MODELES:
            if (poste, nom) not in ajustements:
                continue
            modele, parametres = ajustements[(poste, nom)]
            ax.plot(grille, np.exp(modele(grille, *parametres)) / 1e6,
                    linewidth=1.6, alpha=0.85, label=nom)

        ax.set_title(libelle)
        ax.grid(alpha=0.3)

    axes[0, 0].legend(frameon=False, fontsize=8)
    for ax in axes[1, :]:
        ax.set_xlabel("Âge")
    for ax in axes[:, 0]:
        ax.set_ylabel("Valeur médiane (M€)")

    fig.suptitle("Ajustement de fonctions paramétriques sur les courbes observées")
    fig.tight_layout()
    fig.savefig("figures/modeles.png", dpi=150, bbox_inches="tight")

    print("\nfigures/modeles.png et figures/modeles.csv écrits")


if __name__ == "__main__":
    main()
