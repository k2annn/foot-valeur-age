"""Modèle de valeur marchande : archétype, statut, âge, joueur.

    ln V = mu[archétype] + tau * statut + f[famille](âge) + alpha[joueur] + e

La courbe d'âge f n'est pas réestimée ici : elle est reprise de figures/courbes.csv,
ajustée sur vingt ans de valorisations. Les quatre saisons dont on dispose pour
le statut et l'archétype ne suffiraient pas à identifier une trajectoire de
carrière, et la réestimer reviendrait à confondre effet d'âge et composition de
l'échantillon.

Trois sorties :
  1. décomposition de la variance entre les composantes
  2. cote estimée de chaque archétype, à statut et âge égaux
  3. validation hors échantillon contre deux modèles de référence

Entrées : data/modelisation.csv, figures/courbes.csv
Sortie  : figures/modele_archetypes.csv

Usage : python modele.py
"""

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from numpy.linalg import LinAlgError

MODELISATION = Path("data/modelisation.csv")
# Courbe d'âge utilisée. "figures/courbes_intra.csv" est estimée en
# intra-joueur et corrige le biais de survie de la courbe transversale ;
# "figures/courbes.csv" garde l'ancienne, pour comparaison.
COURBES = Path("figures/courbes_intra.csv")
SORTIE = Path("figures/modele_archetypes.csv")

SAISON_TEST = 2025          # dernière saison, réservée à la validation

# Familles de postes vers les quatre postes du panel Transfermarkt
POSTE = {
    "Gardiens": "Goalkeeper",
    "Défenseurs centraux": "Defender",
    "Latéraux": "Defender",
    "Milieux": "Midfield",
    "Milieux offensifs": "Midfield",
    "Attaquants": "Attack",
    "Ailiers": "Attack",
}


def courbe_age() -> pd.Series:
    """Effet d'âge en log, centré *par poste*, indexé par (poste, âge).

    Le centrage doit se faire poste par poste. Centrer globalement laisserait
    dans effet_age l'écart de niveau entre postes — un gardien vaut moins cher
    qu'un milieu à tout âge — qui n'a rien à voir avec l'âge et fausserait la
    constante du modèle.
    """
    courbes = pd.read_csv(COURBES)
    effet = np.log(courbes["valeur"])
    courbes["effet_age"] = effet - effet.groupby(courbes["position"]).transform("mean")
    return courbes.set_index(["position", "age"])["effet_age"]


def preparer() -> pd.DataFrame:
    d = pd.read_csv(MODELISATION)
    d["poste"] = d["famille"].map(POSTE)

    effet = courbe_age()
    d["effet_age"] = list(zip(d["poste"], d["age"]))
    d["effet_age"] = d["effet_age"].map(effet)

    manquants = d["effet_age"].isna().mean()
    if manquants:
        print(f"âge hors plage de la courbe : {manquants:.1%} des lignes, "
              f"effet fixé à 0")
    d["effet_age"] = d["effet_age"].fillna(0)

    # La variable expliquée est la valeur nette de l'effet d'âge : le modèle
    # n'a plus qu'à estimer la cote de l'archétype et l'effet du statut.
    d["valeur_ajustee"] = d["log_valeur"] - d["effet_age"]
    d["statut"] = d["score_statut"] / 100
    return d.dropna(subset=["valeur_ajustee", "statut", "archetype"])


# La famille de postes n'a pas besoin d'être déclarée : chaque archétype
# appartient à une seule famille, les indicatrices d'archétype en portent donc
# déjà le niveau.
FORMULE = "valeur_ajustee ~ C(archetype) + statut"


class Ajustement:
    """Enveloppe commune au modèle mixte et à son repli en moindres carrés."""

    def __init__(self, resultat, mixte: bool):
        self.resultat = resultat
        self.mixte = mixte
        self.fe_params = resultat.fe_params if mixte else resultat.params
        self.bse_fe = resultat.bse_fe if mixte else resultat.bse

    def predict(self, data):
        return self.resultat.predict(data)

    def effets_joueurs(self) -> pd.Series:
        """Intercept estimé pour chaque joueur vu à l'apprentissage.

        C'est la composante la plus lourde du modèle : l'ignorer en prédiction
        revient à jeter les deux tiers de l'information et rend toute
        comparaison avec un modèle de persistance dénuée de sens.
        """
        if not self.mixte:
            return pd.Series(dtype=float)
        return pd.Series({
            joueur: float(np.asarray(effet).ravel()[0])
            for joueur, effet in self.resultat.random_effects.items()
        })


def estimer(d: pd.DataFrame) -> Ajustement:
    """Modèle mixte à intercept par joueur.

    Repli en moindres carrés avec erreurs types groupées par joueur si le
    modèle mixte ne converge pas — ce qui arrive quand la variance
    individuelle est proche de zéro et rend la matrice singulière.
    """
    try:
        resultat = smf.mixedlm(FORMULE, data=d, groups=d["joueur"]).fit(
            method="lbfgs", maxiter=500
        )
        return Ajustement(resultat, mixte=True)
    except (LinAlgError, np.linalg.LinAlgError, ValueError) as erreur:
        print(f"  modèle mixte non convergent ({type(erreur).__name__}), "
              f"repli sur moindres carrés groupés par joueur")
        resultat = smf.ols(FORMULE, data=d).fit(
            cov_type="cluster", cov_kwds={"groups": d["joueur"]}
        )
        return Ajustement(resultat, mixte=False)


def decomposer(d: pd.DataFrame, ajustement) -> pd.DataFrame:
    """Part de la variance de ln(V) portée par chaque composante."""
    parametres = ajustement.fe_params
    archetype = pd.Series(0.0, index=d.index)
    for nom, valeur in parametres.items():
        if nom.startswith("C(archetype)"):
            cible = nom.split("[T.")[1].rstrip("]")
            archetype += (d["archetype"] == cible) * valeur

    composantes = pd.DataFrame({
        "archetype": archetype,
        "statut": d["statut"] * parametres.get("statut", 0.0),
        "age": d["effet_age"],
    })
    composantes["joueur"] = (
        d["log_valeur"] - composantes.sum(axis=1) - parametres["Intercept"]
    )

    total = d["log_valeur"].var()
    return pd.DataFrame({
        "composante": composantes.columns,
        "variance": [composantes[c].var() for c in composantes],
        "part": [composantes[c].var() / total for c in composantes],
    }).sort_values("part", ascending=False)


def cotes(d: pd.DataFrame, ajustement) -> pd.DataFrame:
    """Cote de chaque archétype : effet sur la valeur, à statut et âge égaux."""
    parametres, erreurs = ajustement.fe_params, ajustement.bse_fe
    lignes = []
    for archetype in sorted(d["archetype"].unique()):
        nom = f"C(archetype)[T.{archetype}]"
        coefficient = parametres.get(nom, 0.0)
        lignes.append({
            "archetype": archetype,
            "famille": d.loc[d["archetype"] == archetype, "famille"].iloc[0],
            "n": int((d["archetype"] == archetype).sum()),
            "cote_log": round(coefficient, 3),
            "effet_multiplicatif": round(float(np.exp(coefficient)), 2),
            "erreur_type": round(float(erreurs.get(nom, np.nan)), 3),
        })
    return pd.DataFrame(lignes)


def valider(d: pd.DataFrame) -> pd.DataFrame:
    """Estimation sur le passé, prédiction sur la dernière saison.

    Deux références à battre : la valeur de la saison précédente, qui est
    redoutable car les valorisations sont très persistantes, et la médiane par
    âge et famille, qui ne connaît que la position et l'âge.
    """
    apprentissage = d[d["saison"] < SAISON_TEST]
    test = d[d["saison"] == SAISON_TEST].copy()
    if test.empty or apprentissage.empty:
        print("validation impossible : une des deux périodes est vide")
        return pd.DataFrame()

    ajustement = estimer(apprentissage)

    # Contrôle de calibrage : le résidu médian doit être nul à l'apprentissage.
    # S'il ne l'est pas non plus en test, le modèle dérive dans le temps.
    residu_app = (apprentissage["log_valeur"]
                  - (ajustement.predict(apprentissage)
                     + apprentissage["effet_age"]
                     + apprentissage["joueur"].map(
                         ajustement.effets_joueurs()).fillna(0.0)))
    print(f"  biais médian à l'apprentissage : "
          f"{np.exp(-residu_app.median()) - 1:+.0%}")

    fixe = ajustement.predict(test) + test["effet_age"]

    # Effet joueur appris sur le passé, réinjecté pour les joueurs déjà vus.
    effets = ajustement.effets_joueurs()
    test["effet_joueur"] = test["joueur"].map(effets).fillna(0.0)
    test["prediction"] = fixe + test["effet_joueur"]
    test["prediction_sans_joueur"] = fixe

    # Référence 1 : la valeur de la saison précédente du même joueur
    precedent = (d[d["saison"] == SAISON_TEST - 1]
                 .set_index("joueur")["log_valeur"])
    test["ref_persistance"] = test["joueur"].map(precedent)

    # Référence 2 : médiane apprise par âge et famille
    medianes = apprentissage.groupby(["famille", "age"])["log_valeur"].median()
    test["ref_mediane"] = list(zip(test["famille"], test["age"]))
    test["ref_mediane"] = test["ref_mediane"].map(medianes)

    # Comparaison à périmètre égal : seuls les joueurs pour lesquels toutes les
    # méthodes produisent une prédiction. Sinon la persistance serait jugée sur
    # les seuls joueurs déjà présents l'an passé, donc les mieux établis.
    colonnes = ["prediction", "prediction_sans_joueur",
                "ref_persistance", "ref_mediane"]
    commun = test.dropna(subset=colonnes)
    print(f"  comparaison sur {len(commun):,} joueurs communs "
          f"(sur {len(test):,} dans la saison test)")

    METHODES = [("modèle complet", "prediction"),
                ("modèle sans effet joueur", "prediction_sans_joueur"),
                ("valeur n-1", "ref_persistance"),
                ("médiane âge x poste", "ref_mediane")]

    def erreurs(echantillon, methodes=METHODES):
        lignes = []
        for nom, colonne in methodes:
            v = echantillon.dropna(subset=[colonne])
            if v.empty:
                continue
            erreur = v["log_valeur"] - v[colonne]
            absolue = float(erreur.abs().median())
            lignes.append({
                "modèle": nom,
                "n": len(v),
                "erreur_médiane_log": round(absolue, 3),
                "erreur_relative": f"{np.exp(absolue) - 1:.0%}",
                "RMSE": round(float(np.sqrt((erreur ** 2).mean())), 3),
            })
        return pd.DataFrame(lignes)

    # Biais global en test : c'est ce chiffre qui départage les méthodes de
    # correction de l'inflation, pas le biais d'apprentissage qui est nul par
    # construction. Un biais résiduel important signale que la correction
    # appliquée aux saisons passées ne tient pas sur la saison prédite.
    biais = float((commun["log_valeur"] - commun["prediction"]).median())
    print(f"  biais médian en test : {np.exp(-biais) - 1:+.0%}")

    resultat = erreurs(commun)

    # Le cas d'usage réel : un joueur sans valorisation l'année précédente, pour
    # lequel la persistance ne peut rien dire.
    nouveaux = test[test["ref_persistance"].isna()].dropna(
        subset=["prediction", "ref_mediane"]
    )
    if len(nouveaux) > 30:
        print(f"\n--- Joueurs sans valeur l'an passé ({len(nouveaux):,}) ---")
        print(erreurs(nouveaux, [m for m in METHODES
                                 if m[1] != "ref_persistance"]).to_string(index=False))

    # Par tranche d'âge : la persistance est imbattable sur les joueurs
    # installés, beaucoup moins là où la valeur bouge vite.
    print("\n--- Erreur médiane par tranche d'âge (modèle / valeur n-1) ---")
    tranches = pd.cut(commun["age"], [16, 21, 24, 28, 32, 45],
                      labels=["17-21", "22-24", "25-28", "29-32", "33+"])
    comparaison = []
    for tranche, groupe in commun.groupby(tranches, observed=True):
        ecarts = {
            nom: float((groupe["log_valeur"] - groupe[col]).abs().median())
            for nom, col in [("modèle", "prediction"),
                             ("persistance", "ref_persistance")]
        }
        comparaison.append({
            "âge": tranche, "n": len(groupe),
            "modèle": round(ecarts["modèle"], 3),
            "valeur_n-1": round(ecarts["persistance"], 3),
            "écart": round(ecarts["persistance"] - ecarts["modèle"], 3),
        })
    print(pd.DataFrame(comparaison).to_string(index=False))

    return resultat


def main():
    d = preparer()
    print(f"{len(d):,} observations, {d['joueur'].nunique():,} joueurs, "
          f"{d['archetype'].nunique()} archétypes\n")

    ajustement = estimer(d)

    print("=== Décomposition de la variance de ln(valeur) ===")
    decomposition = decomposer(d, ajustement)
    decomposition["part"] = (decomposition["part"] * 100).round(1)
    print(decomposition.to_string(index=False))

    print("\n=== Effet du statut ===")
    pente = ajustement.fe_params.get("statut", np.nan)
    print(f"  coefficient : {pente:.2f} par unité de statut (0 à 1)")
    print(f"  soit un rapport de {np.exp(pente):.1f} entre statut 0 et statut 100")

    table = cotes(d, ajustement)
    table.to_csv(SORTIE, index=False)
    print("\n=== Cote des archétypes (référence = premier par ordre alphabétique) ===")
    for famille, groupe in table.groupby("famille"):
        print(f"\n{famille}")
        print(groupe.sort_values("cote_log", ascending=False)
              [["archetype", "n", "cote_log", "effet_multiplicatif", "erreur_type"]]
              .to_string(index=False))

    print(f"\n\n=== Validation hors échantillon (saison {SAISON_TEST}) ===")
    print(valider(d).to_string(index=False))
    print(f"\n{SORTIE} écrit")


if __name__ == "__main__":
    main()
