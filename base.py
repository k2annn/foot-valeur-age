"""Construction de la base unifiée à partir des fichiers Excel bruts.

À NE LANCER QU'UNE FOIS, ou lors de l'ajout d'une nouvelle saison. Les CSV
produits dans data/ sont versionnés et se suffisent à eux-mêmes : les fichiers
Excel source peuvent être archivés hors du dépôt une fois ce script exécuté.

Les quatre saisons FBref n'ont pas les mêmes en-têtes : 2021-24 sont en
anglais, 2024-25 est traduit en français. Les colonnes s'alignent en revanche
exactement par position sur les onze feuilles, ce qui permet de ramener tout le
monde au schéma anglais, retenu comme canonique.

Produit trois fichiers dans data/ :

    joueurs_saisons.csv  une ligne par joueur et par saison, toutes les
                         statistiques FBref préfixées par feuille
    clubs.csv            force des clubs
    labels_manuels.csv   étiquettes de l'ancien projet (contrôle uniquement)

Usage : python base.py
"""

from pathlib import Path

import pandas as pd

SOURCE = Path("data/brut_xlsx")
SORTIE = Path("data")

SAISONS = {
    2022: "FBREF_2021_2022.xlsx",
    2023: "FBREF_2022_2023.xlsx",
    2024: "FBREF_2023_2024.xlsx",
    2025: "DBFOOT_2024_2025.xlsx",
}

REFERENCE = "FBREF_2023_2024.xlsx"   # fournit le schéma canonique (anglais)
CLASSEMENT = "Données_FBREF.xlsx"
ETIQUETTES = "DBFOOT_rangs_top_otw1_otw2.xlsx"

# Feuille -> préfixe des colonnes dans la base finale
FEUILLES = {
    "standard": "std",
    "shooting": "sh",
    "passing": "pas",
    "passing_types": "pty",
    "gca": "gca",
    "defense": "def",
    "possession": "pos",
    "playing_time": "tps",
    "misc": "msc",
    "keepers": "gk",
    "keepers_adv": "gka",
}

CLES = ["Player", "Squad"]
IDENTITE = ["Player", "Squad", "Comp", "Nation", "Pos", "Age", "Born"]


def schema_canonique() -> dict[str, list[str]]:
    """En-têtes de référence, feuille par feuille."""
    classeur = pd.ExcelFile(SOURCE / REFERENCE)
    return {f: list(classeur.parse(f, nrows=0).columns) for f in FEUILLES
            if f in classeur.sheet_names}


def lire_feuille(classeur: pd.ExcelFile, feuille: str,
                 canonique: list[str]) -> pd.DataFrame | None:
    """Lit une feuille et la ramène au schéma canonique."""
    if feuille not in classeur.sheet_names:
        return None

    df = classeur.parse(feuille)
    if len(df.columns) != len(canonique):
        print(f"    ! {feuille} ignorée : {len(df.columns)} colonnes "
              f"au lieu de {len(canonique)}")
        return None

    df.columns = canonique
    return df.loc[:, ~df.columns.duplicated()]


def lire_saison(annee: int, fichier: str,
                canonique: dict[str, list[str]]) -> pd.DataFrame:
    """Assemble les onze feuilles d'une saison en une table large."""
    classeur = pd.ExcelFile(SOURCE / fichier)
    base = None

    for feuille, prefixe in FEUILLES.items():
        df = lire_feuille(classeur, feuille, canonique[feuille])
        if df is None:
            continue

        df = df.drop_duplicates(CLES)

        if base is None:
            # La feuille standard porte l'identité du joueur.
            mesures = [c for c in df.columns if c not in IDENTITE]
            base = df[IDENTITE + mesures].rename(
                columns={c: f"{prefixe}_{c}" for c in mesures}
            )
        else:
            mesures = [c for c in df.columns if c not in IDENTITE]
            df = df[CLES + mesures].rename(
                columns={c: f"{prefixe}_{c}" for c in mesures}
            )
            base = base.merge(df, on=CLES, how="left")

    base["saison"] = annee
    print(f"  {annee} : {len(base):,} lignes, {len(base.columns)} colonnes")
    return base


def force_des_clubs() -> pd.DataFrame:
    classement = pd.read_excel(SOURCE / CLASSEMENT, sheet_name="Ranking")
    return classement.rename(columns={
        "Clubs": "club", "Stars": "etoiles", "Score": "score_club",
        "Rank": "rang_club", "Nations": "pays_club",
    })[["club", "pays_club", "rang_club", "score_club", "etoiles"]]


def etiquettes_manuelles() -> pd.DataFrame | None:
    """Étiquettes de l'ancien projet, conservées pour contrôle a posteriori."""
    if not (SOURCE / ETIQUETTES).exists():
        return None

    classeur = pd.ExcelFile(SOURCE / ETIQUETTES)
    familles = pd.concat(
        [classeur.parse(f).assign(famille=f) for f in classeur.sheet_names],
        ignore_index=True,
    )
    colonnes = {"AAA": "joueur", "Club": "club", "Rang": "palier",
                "Tier": "cote", "Attribut 1": "attribut_1",
                "Attribut 2": "attribut_2", "Poste": "poste", "famille": "famille"}
    familles = familles[list(colonnes)].rename(columns=colonnes)
    return familles.assign(saison=2024).dropna(subset=["joueur"])


def main():
    SORTIE.mkdir(parents=True, exist_ok=True)
    canonique = schema_canonique()

    print("Lecture des saisons")
    saisons = [lire_saison(a, f, canonique) for a, f in SAISONS.items()
               if (SOURCE / f).exists()]
    if not saisons:
        raise FileNotFoundError(f"aucun fichier FBref trouvé dans {SOURCE}/")

    panel = pd.concat(saisons, ignore_index=True)

    # Un joueur transféré en cours de saison apparaît deux fois : on retient
    # le club où il a le plus joué, et on garde la trace du transfert.
    minutes = "std_Min_Playing"
    panel = panel.sort_values(minutes, ascending=False)
    panel["transfert_en_cours"] = panel.duplicated(["Player", "saison"], keep=False)
    panel = panel.drop_duplicates(["Player", "saison"])

    clubs = force_des_clubs()
    panel = panel.merge(clubs, left_on="Squad", right_on="club", how="left")
    panel["etoiles"] = panel["etoiles"].fillna(1.0)

    panel = panel.rename(columns={"Player": "joueur", "Squad": "club_saison",
                                  "Comp": "championnat", "Nation": "nationalite",
                                  "Pos": "poste_fbref", "Born": "naissance"})
    panel["age"] = panel["saison"] - panel["naissance"]

    tete = ["joueur", "saison", "club_saison", "championnat", "nationalite",
            "poste_fbref", "naissance", "age", "etoiles", "score_club",
            "transfert_en_cours"]
    autres = [c for c in panel.columns if c not in tete + ["club", "pays_club",
                                                           "rang_club", "Age"]]
    panel = panel[tete + autres].sort_values(["saison", "joueur"])

    # Arrondi : la précision au dix-millième suffit et divise le poids par deux.
    for colonne in panel.select_dtypes("float").columns:
        panel[colonne] = panel[colonne].round(4)

    panel.to_csv(SORTIE / "joueurs_saisons.csv.gz", index=False,
                 compression="gzip")
    clubs.to_csv(SORTIE / "clubs.csv", index=False)

    print(f"\njoueurs_saisons.csv : {len(panel):,} lignes x {len(panel.columns)} "
          f"colonnes, {panel['joueur'].nunique():,} joueurs")
    print(f"  saisons : {sorted(panel['saison'].unique())}")
    print(f"  transferts en cours de saison : {panel['transfert_en_cours'].sum():,}")
    print(f"clubs.csv : {len(clubs)} clubs classés")

    labels = etiquettes_manuelles()
    if labels is not None:
        labels.to_csv(SORTIE / "labels_manuels.csv", index=False)
        print(f"labels_manuels.csv : {len(labels):,} étiquettes (saison 2024)")


if __name__ == "__main__":
    main()
