"""Archétypes : regroupement des joueurs par style de jeu.

Principe : un archétype décrit *comment* un joueur joue, jamais *à quel
niveau*. Toutes les variables sont donc des parts et des ratios, jamais des
volumes — un titulaire d'un grand club touche plus de ballons qu'un remplaçant
de milieu de tableau au style identique, et un clustering sur volumes
retrouverait le niveau au lieu du style.

Trois validations :
  1. stabilité — le nombre de clusters est choisi par rééchantillonnage
  2. neutralité — un archétype sain contient des joueurs de statuts variés
  3. concordance — comparaison aux étiquettes manuelles de l'ancien projet

Entrées : data/joueurs_saisons.csv.gz, data/statut.csv, data/labels_manuels.csv
Sortie  : data/archetypes.csv

Usage : python clustering.py
"""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import adjusted_rand_score, silhouette_score

BASE = Path("data/joueurs_saisons.csv.gz")
STATUT = Path("data/statut.csv")
LABELS = Path("data/labels_manuels.csv")
SORTIE = Path("data/archetypes.csv")

# Codes de poste de la saison 2024-25 vers les familles
POSTES = {
    "BU": "Attaquants", "AD": "Ailiers", "AG": "Ailiers",
    "MO": "Milieux offensifs", "MD": "Milieux",
    "DD": "Latéraux", "DG": "Latéraux",
    "DC": "Défenseurs centraux", "GK": "Gardiens",
}

SAISON_REFERENCE = 2024    # saison sur laquelle les centroïdes sont estimés
MIN_MINUTES = 900          # ratios trop bruités en dessous
K_CANDIDATS = range(2, 8)
N_TIRAGES = 30             # rééchantillonnages pour la stabilité
SEUIL_STABILITE = 0.65     # au-delà, un k est considéré comme fiable
SEUIL_PETIT_EFFECTIF = 0.55   # seuil assoupli sous PETIT_EFFECTIF joueurs
PETIT_EFFECTIF = 120
GRAINE = 0


# Noms français des familles de postes
FAMILLES = {
    "Forwards": "Attaquants",
    "Wingers": "Ailiers",
    "Att Mid": "Milieux offensifs",
    "Midfielders": "Milieux",
    "Fullbacks": "Latéraux",
    "Center Backs": "Défenseurs centraux",
    "Goalkeepers": "Gardiens",
}

# Signature attendue de chaque profil : variables discriminantes et sens.
# Les numéros de cluster étant arbitraires d'une exécution à l'autre, le nom
# est attribué par appariement optimal entre profil observé et signature.
SIGNATURES = {
    "Attaquants": {
        "Faux neuf": {"part_passes_longues": 1, "passes_par_touche": 1,
                      "distance_passe": 1, "surface_adv": -1},
        "Renard de surface": {"surface_adv": 1, "tirs_par_touche": 1,
                              "passes_par_touche": -1},
        "Attaquant mobile": {"dribbles_par_touche": 1, "sca_dribble": 1,
                             "duels_aeriens_par_touche": -1},
        "Pivot": {"duels_aeriens_par_touche": 1, "zone_mil": 1,
                  "dribbles_par_touche": -1},
    },
    "Ailiers": {
        "Ailier percuteur": {"conduites_surface": 1, "zone_att": 1,
                             "surface_adv": 1},
        "Ailier repiquant": {"part_passes_longues": 1, "sca_coup_de_pied": 1,
                             "distance_frappe": 1},
        "Ailier combattant": {"duels_aeriens_par_touche": 1,
                              "fautes_par_touche": 1, "passes_par_touche": -1},
        "Piston": {"zone_def": 1, "zone_att": -1, "passes_cles": -1},
    },
    "Milieux offensifs": {
        "Meneur avancé": {"zone_att": 1, "surface_adv": 1, "passes_cles": 1},
        "Meneur reculé": {"zone_def": 1, "zone_mil": 1, "surface_adv": -1},
    },
    "Milieux": {
        "Régisseur": {"reussite_passes": 1, "passes_par_touche": 1,
                      "actions_def_par_touche": -1},
        "Passeur long": {"sca_coup_de_pied": 1, "part_passes_longues": 1,
                         "distance_passe": 1},
        "Récupérateur": {"actions_def_par_touche": 1,
                         "recuperations_par_touche": 1,
                         "duels_aeriens_par_touche": 1},
        "Milieu offensif": {"surface_adv": 1, "conduites_surface": 1,
                            "zone_att": 1},
        "Milieu de position": {"zone_def": 1, "zone_att": -1,
                               "passes_cles": -1},
    },
    "Latéraux": {
        "Latéral technique": {"reussite_passes": 1, "passes_par_touche": 1,
                              "part_passes_longues": -1},
        "Latéral direct": {"part_passes_longues": 1, "distance_passe": 1,
                           "reussite_passes": -1},
        "Latéral offensif": {"surface_adv": 1, "dribbles_par_touche": 1,
                             "conduites_surface": 1},
    },
    "Défenseurs centraux": {
        "Défenseur relanceur": {"reussite_passes": 1, "passes_par_touche": 1,
                                "actions_def_par_touche": -1},
        "Défenseur de duel": {"actions_def_par_touche": 1,
                              "duels_aeriens_par_touche": 1, "zone_def": 1},
        "Défenseur latéralisé": {"passes_surface": 1, "zone_att": 1,
                                 "centres_surface": 1},
    },
    "Gardiens": {
        "Gardien libéro": {"sorties_hors_surface": 1, "distance_sorties": 1},
        "Gardien relanceur long": {"part_longues_relances": 1,
                                   "longueur_relance": 1},
        "Gardien de ligne": {"sorties_hors_surface": -1,
                             "longueur_relance": -1, "sorties_aeriennes": -1},
    },
}


def ratio(numerateur: pd.Series, denominateur: pd.Series) -> pd.Series:
    """Division protégée : un dénominateur nul donne 0, pas un infini."""
    return (numerateur / denominateur.replace(0, np.nan)).fillna(0)


def variables_joueur(d: pd.DataFrame) -> pd.DataFrame:
    """Ratios de style pour les joueurs de champ."""
    touches = d["pos_Touches_Touches"]
    passes = d["pas_Att_Total"]
    sca = d["gca_SCA_SCA"]
    actions_def = d["def_Tkl_Tackles"] + d["def_Int"] + d["def_Clr"]
    duels = d["msc_Won_Aerial"] + d["msc_Lost_Aerial"]

    v = pd.DataFrame(index=d.index)

    # Où le joueur touche le ballon
    v["zone_def"] = ratio(d["pos_Def 3rd_Touches"], touches)
    v["zone_mil"] = ratio(d["pos_Mid 3rd_Touches"], touches)
    v["zone_att"] = ratio(d["pos_Att 3rd_Touches"], touches)
    v["surface_adv"] = ratio(d["pos_Att Pen_Touches"], touches)

    # Ce qu'il en fait
    v["passes_par_touche"] = ratio(passes, touches)
    v["conduites_par_touche"] = ratio(d["pos_Carries_Carries"], touches)
    v["dribbles_par_touche"] = ratio(d["pos_Att_Take"], touches)
    v["tirs_par_touche"] = ratio(d["sh_Sh_Standard"], touches)

    # Les pertes de balle par touche sont écartées : c'est un marqueur de
    # qualité technique plus que de style, et il séparait surtout les joueurs
    # de clubs modestes des autres.

    # Orientation du jeu de passe
    v["part_passes_courtes"] = ratio(d["pas_Att_Short"], passes)
    v["part_passes_longues"] = ratio(d["pas_Att_Long"], passes)
    v["reussite_passes"] = d["pas_Cmp_percent_Total"] / 100
    v["progressivite_passes"] = ratio(d["pas_PrgP"], passes)
    v["passes_cles"] = ratio(d["pas_KP"], passes)
    v["passes_surface"] = ratio(d["pas_PPA"], passes)
    v["centres_surface"] = ratio(d["pas_CrsPA"], passes)
    v["distance_passe"] = ratio(d["pas_TotDist_Total"], d["pas_Cmp_Total"])
    v["part_progression_passe"] = ratio(d["pas_PrgDist_Total"],
                                        d["pas_TotDist_Total"])

    # Composition des actions amenant un tir : le coeur du style offensif
    v["sca_passe"] = ratio(d["gca_PassLive_SCA"], sca)
    v["sca_coup_de_pied"] = ratio(d["gca_PassDead_SCA"], sca)
    v["sca_dribble"] = ratio(d["gca_TO_SCA"], sca)
    v["sca_tir"] = ratio(d["gca_Sh_SCA"], sca)
    v["sca_faute"] = ratio(d["gca_Fld_SCA"], sca)
    v["sca_defensif"] = ratio(d["gca_Def_SCA"], sca)

    # Conduite de balle
    v["progression_conduite"] = ratio(d["pos_PrgDist_Carries"],
                                      d["pos_TotDist_Carries"])
    v["conduites_surface"] = ratio(d["pos_CPA_Carries"],
                                   d["pos_Carries_Carries"])
    v["reussite_dribble"] = d["pos_Succ_percent_Take"] / 100

    # Profil de frappe
    v["distance_frappe"] = d["sh_Dist_Standard"].fillna(
        d["sh_Dist_Standard"].median()
    )
    v["cadrage"] = d["sh_SoT_percent_Standard"].fillna(0) / 100
    v["qualite_occasion"] = d["sh_npxG_per_Sh_Expected"].fillna(0)

    # Registre défensif
    v["def_zone_basse"] = ratio(d["def_Def 3rd_Tackles"], d["def_Tkl_Tackles"])
    v["def_zone_haute"] = ratio(d["def_Att 3rd_Tackles"], d["def_Tkl_Tackles"])
    v["part_interceptions"] = ratio(d["def_Int"], actions_def)
    v["part_degagements"] = ratio(d["def_Clr"], actions_def)
    v["part_tacles"] = ratio(d["def_Tkl_Tackles"], actions_def)
    v["actions_def_par_touche"] = ratio(actions_def, touches)

    # Jeu aérien et duels
    v["duels_aeriens_par_touche"] = ratio(duels, touches)
    v["reussite_aerienne"] = d["msc_Won_percent_Aerial"].fillna(0) / 100
    v["fautes_par_touche"] = ratio(d["msc_Fls"], touches)
    v["fautes_subies_par_touche"] = ratio(d["msc_Fld"], touches)
    v["recuperations_par_touche"] = ratio(d["msc_Recov"], touches)

    return v


def variables_gardien(d: pd.DataFrame) -> pd.DataFrame:
    """Ratios de style pour les gardiens : relance, sorties, arrêts."""
    v = pd.DataFrame(index=d.index)
    v["part_longues_relances"] = d["gka_Launch_percent_Passes"].fillna(0) / 100
    v["longueur_relance"] = d["gka_AvgLen_Passes"].fillna(0)
    v["part_longues_degagements"] = d["gka_Launch_percent_Goal"].fillna(0) / 100
    v["longueur_degagement"] = d["gka_AvgLen_Goal"].fillna(0)
    v["reussite_longues"] = d["gka_Cmp_percent_Launched"].fillna(0) / 100
    v["sorties_aeriennes"] = d["gka_Stp_percent_Crosses"].fillna(0) / 100
    v["sorties_hors_surface"] = d["gka_#OPA_per_90_Sweeper"].fillna(0)
    v["distance_sorties"] = d["gka_AvgDist_Sweeper"].fillna(0)
    v["arrets_vs_attendu"] = d["gka__per_90_Expected"].fillna(0)
    v["difficulte_tirs"] = d["gka_PSxG_per_SoT_Expected"].fillna(0)
    v["relances_main"] = ratio(d["gka_Thr_Passes"], d["gka_Att (GK)_Passes"])
    return v


def winsoriser(v: pd.DataFrame) -> pd.DataFrame:
    """Borne les valeurs extrêmes aux centiles 2,5 et 97,5.

    Sans cela, un joueur atypique à six écarts-types se retrouve seul dans son
    propre cluster et consomme une partition entière.
    """
    return v.clip(v.quantile(0.025), v.quantile(0.975), axis=1)


def standardiser(v: pd.DataFrame) -> np.ndarray:
    """Centrage-réduction *à l'intérieur de la famille*.

    Standardiser sur l'échantillon entier ferait ressortir ce qui distingue les
    postes entre eux — déjà connu — au lieu de ce qui distingue les joueurs d'un
    même poste.
    """
    return ((v - v.mean()) / v.std().replace(0, 1)).fillna(0).to_numpy()


def stabilite(Z: np.ndarray, k: int, tirages: int = N_TIRAGES) -> float:
    """Concordance moyenne entre partitions obtenues sur des sous-échantillons.

    Un k stable produit les mêmes groupes quand on retire 20 % des joueurs au
    hasard. Un k instable découpe du continu.
    """
    alea = np.random.default_rng(GRAINE)
    reference = KMeans(k, n_init=10, random_state=GRAINE).fit_predict(Z)
    scores = []
    for _ in range(tirages):
        idx = alea.choice(len(Z), int(0.8 * len(Z)), replace=False)
        partition = KMeans(k, n_init=10, random_state=GRAINE).fit_predict(Z[idx])
        scores.append(adjusted_rand_score(reference[idx], partition))
    return float(np.mean(scores))


def choisir_k(Z: np.ndarray, k_max: int | None = None) -> tuple[int, pd.DataFrame]:
    """Le découpage le plus fin parmi ceux qui restent stables.

    Le seuil est assoupli sur les petites familles : retirer 20 % de 85 joueurs
    ampute proportionnellement plus d'information que sur 290, et la stabilité
    y est donc mécaniquement plus basse à qualité de partition égale.
    """
    seuil = SEUIL_STABILITE if len(Z) >= PETIT_EFFECTIF else SEUIL_PETIT_EFFECTIF
    lignes = []
    plafond = k_max or max(K_CANDIDATS)
    for k in K_CANDIDATS:
        # Le plafond est le nombre de profils nommés : au-delà, les clusters
        # supplémentaires resteraient anonymes et donc inexploitables.
        if k > plafond or len(Z) < k * 15:
            continue
        partition = KMeans(k, n_init=10, random_state=GRAINE).fit_predict(Z)
        lignes.append({
            "k": k,
            "stabilite": round(stabilite(Z, k), 3),
            "silhouette": round(silhouette_score(Z, partition), 3),
        })
    diag = pd.DataFrame(lignes)

    # Le maximum de stabilité favorise mécaniquement les petits k : à deux
    # groupes, il est facile de retrouver la même partition.
    fiables = diag[diag["stabilite"] >= seuil]
    if fiables.empty:
        fiables = diag.nlargest(1, "stabilite")
    return int(fiables["k"].max()), diag


def nommer(v: pd.DataFrame, partition: np.ndarray, famille: str) -> dict:
    """Associe un nom de profil à chaque cluster.

    Chaque cluster est décrit par ses moyennes en écarts-types ; on calcule sa
    ressemblance à chaque signature, puis on résout l'affectation globale pour
    qu'un nom ne serve qu'une fois.
    """
    signatures = SIGNATURES.get(famille, {})
    clusters = sorted(set(partition))
    if not signatures:
        return {c: f"{famille} {c}" for c in clusters}

    z = (v - v.mean()) / v.std().replace(0, 1)
    profils = np.array([
        z[partition == c].mean().reindex(v.columns).fillna(0).to_numpy()
        for c in clusters
    ])

    noms = list(signatures)
    poids = np.array([
        [signatures[n].get(col, 0) for col in v.columns] for n in noms
    ], dtype=float)

    ressemblance = profils @ poids.T
    lignes, colonnes = linear_sum_assignment(-ressemblance)

    attribution = {clusters[i]: noms[j] for i, j in zip(lignes, colonnes)}
    for c in clusters:                      # sécurité si k > nombre de noms
        attribution.setdefault(c, f"{famille} {c}")
    return attribution


def decrire(v: pd.DataFrame, partition: np.ndarray, n: int = 4) -> dict:
    """Variables les plus caractéristiques de chaque cluster, en écarts-types."""
    z = (v - v.mean()) / v.std().replace(0, 1)
    z["cluster"] = partition
    profils = {}
    for cluster, groupe in z.groupby("cluster"):
        moyennes = groupe.drop(columns="cluster").mean()
        fortes = moyennes.reindex(moyennes.abs().sort_values(ascending=False).index)
        profils[cluster] = [
            f"{nom} {'+' if val > 0 else ''}{val:.1f}"
            for nom, val in fortes.head(n).items()
        ]
    return profils


def affecter_familles(base: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Famille de postes pour chaque joueur, sur toutes les saisons.

    Deux sources exactes : les étiquettes manuelles de 2023-24, et les codes de
    poste détaillés présents dans le fichier 2024-25. La famille étant une
    propriété stable du joueur, elle est propagée à toutes ses saisons. Le
    reliquat est classé par forêt aléatoire à partir des ratios de style.
    """
    base = base.copy()

    connu = labels.dropna(subset=["famille"]).set_index("joueur")["famille"]
    depuis_codes = (base[base["poste_fbref"].isin(POSTES)]
                    .drop_duplicates("joueur")
                    .set_index("joueur")["poste_fbref"].map(POSTES))
    reference = pd.concat([depuis_codes, connu[~connu.index.isin(depuis_codes.index)]])

    base["famille"] = base["joueur"].map(reference)
    manquants = base["famille"].isna()
    print(f"famille connue : {(~manquants).mean():.1%} des lignes")

    if manquants.any():
        appris = base[~manquants & (base["std_Min_Playing"] >= MIN_MINUTES)]
        modele = RandomForestClassifier(300, random_state=GRAINE, n_jobs=-1)
        modele.fit(standardiser(winsoriser(variables_joueur(appris))),
                   appris["famille"])
        base.loc[manquants, "famille"] = modele.predict(
            standardiser(winsoriser(variables_joueur(base[manquants])))
        )
        print(f"  {manquants.sum():,} lignes classées par le modèle")

    return base


def main():
    base = pd.read_csv(BASE, low_memory=False)
    statut = pd.read_csv(STATUT)[["joueur", "saison", "score_statut"]]
    labels = pd.read_csv(LABELS).drop_duplicates("joueur")
    labels["famille"] = labels["famille"].map(FAMILLES)

    base = affecter_familles(base, labels)
    d = base.merge(statut, on=["joueur", "saison"], how="left")
    d = d.merge(labels[["joueur", "attribut_2"]], on="joueur", how="left")
    print(f"{len(d):,} lignes joueur-saison à classer\n")

    resultats, diagnostics = [], []

    for famille, groupe in d.groupby("famille"):
        groupe = groupe.reset_index(drop=True)
        apprentissage = (
            (groupe["saison"] == SAISON_REFERENCE)
            & (groupe["std_Min_Playing"] >= MIN_MINUTES)
        ).to_numpy()
        if apprentissage.sum() < 40:
            continue
        gardiens = famille == "Gardiens"
        mesures = (variables_gardien if gardiens else variables_joueur)(groupe)

        # Les centroïdes sont estimés sur la seule saison de référence, puis
        # appliqués à toutes les saisons : un joueur peut ainsi changer
        # d'archétype d'une année sur l'autre si son jeu évolue.
        reference = winsoriser(mesures[apprentissage])
        centre, echelle = reference.mean(), reference.std().replace(0, 1)
        Z_ref = ((reference - centre) / echelle).fillna(0).to_numpy()

        k, diag = choisir_k(Z_ref, k_max=len(SIGNATURES.get(famille, {})) or None)
        modele = KMeans(k, n_init=20, random_state=GRAINE).fit(Z_ref)

        bornes = winsoriser(mesures)
        Z_tout = ((bornes - centre) / echelle).fillna(0).to_numpy()
        partition = modele.predict(Z_tout)

        v, Z = reference, Z_ref
        partition_ref = modele.labels_

        ref = groupe[apprentissage]

        # 1. concordance avec les étiquettes manuelles
        connus = ref["attribut_2"].notna()
        concordance = (
            adjusted_rand_score(ref.loc[connus, "attribut_2"],
                                partition_ref[connus.to_numpy()])
            if connus.sum() > 10 else np.nan
        )

        # 2. neutralité : le statut doit se répartir également entre clusters
        statuts = ref.groupby(partition_ref)["score_statut"].median()
        ecart_statut = float(statuts.max() - statuts.min())

        noms = nommer(v, partition_ref, famille)
        groupe = groupe.assign(
            archetype=[noms[c] for c in partition],
            archetype_fiable=groupe["std_Min_Playing"] >= MIN_MINUTES,
        )
        resultats.append(groupe[["joueur", "saison", "famille", "archetype",
                                 "archetype_fiable", "score_statut"]])

        diagnostics.append({
            "famille": famille, "n_ref": int(apprentissage.sum()),
            "n_total": len(groupe), "k": k,
            "stabilite": diag.loc[diag["k"] == k, "stabilite"].iloc[0],
            "silhouette": diag.loc[diag["k"] == k, "silhouette"].iloc[0],
            "concordance_labels": round(concordance, 3),
            "ecart_statut": round(ecart_statut, 1),
        })

        print(f"--- {famille} (référence {apprentissage.sum()}, "
              f"total {len(groupe)}, k={k}) ---")
        for cluster, traits in decrire(v, partition_ref).items():
            effectif = (partition_ref == cluster).sum()
            print(f"  {noms[cluster]:<24} n={effectif:3}  "
                  f"statut méd. {statuts.loc[cluster]:4.0f}  |  "
                  f"{', '.join(traits)}")
        print()

    archetypes = pd.concat(resultats, ignore_index=True)
    archetypes.to_csv(SORTIE, index=False)

    print("\n=== Diagnostics ===")
    print(pd.DataFrame(diagnostics).to_string(index=False))
    print(f"\n{SORTIE} écrit ({len(archetypes):,} joueurs, "
          f"{archetypes['archetype'].nunique()} archétypes)")


if __name__ == "__main__":
    main()
