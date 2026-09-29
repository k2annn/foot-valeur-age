"""Itération annuelle 2021–2025 à partir de l'export DBFOOT enrichi.

Usage: python iteration_v2.py /chemin/DBFOOT_joueurs_2021_2025.xlsx
Le classeur source reste hors du dépôt ; les sorties reproductibles vont dans data/v2.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

OUT = Path("data/v2")
YEARS = [2021, 2022, 2023, 2024, 2025]
STYLES = ["shots", "passes", "passes_completed", "touches", "tackles",
          "interceptions", "aerials_won", "carries", "progressive_passes",
          "progressive_carries", "gk_saves", "gk_passes"]
INJURY_OK = "episodes_documentes_sans_chevauchement_toutes_competitions"
MAX_AGE_VALUE_DAYS = 180


def load_and_check(path: Path) -> tuple[pd.DataFrame, dict]:
    d = pd.read_excel(path, sheet_name="Joueurs")
    required = {"player_id", "annee", "saison", "joueur", "naissance_annee",
                "poste", "club_1", "club_2", "club_3", "nombre_clubs",
                "statut_ordre_clubs", "clubs_observes", "minutes", "games",
                "valeur_marche_eur", "date_valeur", "matchs_manques_blessure",
                "statut_blessures", "fin_contrat_au_30_juin",
                "statut_contrat", "salaire_annuel_brut_estime",
                "devise_salaire", "statut_salaire", "date_reference",
                *STYLES}
    missing = required - set(d)
    if missing:
        raise ValueError(f"Colonnes requises absentes : {sorted(missing)}")
    years = sorted(d.annee.dropna().astype(int).unique())
    if years != YEARS or d.annee.isna().any():
        raise ValueError(f"Années attendues {YEARS}, trouvées {years}")
    if d.player_id.isna().any() or d.duplicated(["player_id", "annee"]).any():
        raise ValueError("Identifiant absent ou doublon joueur-année")
    expected_seasons = d.annee.map(lambda y: f"{y-1}-{y}")
    if not d.saison.eq(expected_seasons).all():
        raise ValueError("Incohérence entre saison et année de fin")
    if d.minutes.isna().any() or (d.minutes <= 0).any():
        raise ValueError("La population doit avoir des minutes positives documentées")
    if ((d.club_2.notna().astype(int) + d.club_3.notna().astype(int) + 1)
            > d.nombre_clubs).any():
        raise ValueError("Nombre de clubs incohérent")
    d["date_reference"] = pd.to_datetime(d.date_reference)
    d["date_valeur"] = pd.to_datetime(d.date_valeur)
    if (d.date_reference.dt.year != d.annee).any():
        raise ValueError("Date de référence hors année de fin")
    if (d.date_valeur > d.date_reference).any():
        raise ValueError("Valorisation postérieure à la date de référence")
    # La présence d'une archive de contrat n'établit pas sa validité au 30 juin.
    if d.fin_contrat_au_30_juin.notna().any():
        raise ValueError("Échéance non nulle au 30 juin : revoir sa certification")
    # Les colonnes club_1/2/3 ne doivent pas suggérer un ordre inconnu.
    unresolved = d.statut_ordre_clubs.eq("ordre_non_resolu")
    d.loc[unresolved, ["club_1", "club_2", "club_3"]] = pd.NA
    coverage = {
        "grain": "player_id × annee (année de fin de saison)",
        "source": path.name,
        "exhaustivite_fbref_certifiee": False,
        "par_annee": {},
        "ordre_clubs_non_resolu": int(unresolved.sum()),
        "doublons": 0,
    }
    for year, group in d.groupby("annee"):
        coverage["par_annee"][str(year)] = {
            "joueurs": len(group),
            "multi_clubs": int((group.nombre_clubs > 1).sum()),
            "valeurs": int(group.valeur_marche_eur.notna().sum()),
            "valorisations_plus_de_180_jours": int(
                ((group.date_reference - group.date_valeur).dt.days > MAX_AGE_VALUE_DAYS).sum()),
            "blessures_documentees": int(group.statut_blessures.eq(INJURY_OK).sum()),
            "salaires": int(group.salaire_annuel_brut_estime.notna().sum()),
            "contrats_certifies_au_30_juin": int(group.fin_contrat_au_30_juin.notna().sum()),
        }
    return d, coverage


def features(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["age"] = d.annee - pd.to_numeric(d.naissance_annee, errors="coerce")
    d["log_minutes"] = np.log1p(d.minutes)
    d["rang_minutes_saison"] = d.groupby("annee").minutes.rank(pct=True)
    d["blessure_documentee"] = d.statut_blessures.eq(INJURY_OK).astype(int)
    d["matchs_blessure_valides"] = pd.to_numeric(
        d.matchs_manques_blessure, errors="coerce"
    ).where(d.statut_blessures.eq(INJURY_OK))
    # L'absence d'épisode dans l'archive demeure inconnue, y compris dans
    # l'historique : on ne transforme jamais un manque en saison saine.
    history = d.sort_values(["player_id", "annee"]).groupby("player_id")
    d["matchs_blessure_n_1"] = history.matchs_blessure_valides.shift(1)
    d["matchs_blessure_cumul_documente"] = history.matchs_blessure_valides.cumsum()
    d["log_salaire"] = np.log(pd.to_numeric(
        d.salaire_annuel_brut_estime, errors="coerce").where(
            pd.to_numeric(d.salaire_annuel_brut_estime, errors="coerce") > 0))
    d["salaire_observe"] = d.log_salaire.notna().astype(int)
    d["style_poste"] = d.poste.fillna("inconnu").astype(str)
    for col in STYLES:
        d[f"style_{col}"] = pd.to_numeric(d[col], errors="coerce") / d.minutes * 900
    return d


def archetypes(train: pd.DataFrame, test: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Centres appris uniquement avant l'année test, par famille de poste."""
    a = pd.Series(index=train.index, dtype="object")
    b = pd.Series(index=test.index, dtype="object")
    cols = [f"style_{c}" for c in STYLES]
    for position in sorted(set(train.style_poste) | set(test.style_poste)):
        tr = train[train.style_poste == position]
        te = test[test.style_poste == position]
        if len(tr) < 30:
            a.loc[tr.index] = position + "_0"
            b.loc[te.index] = position + "_0"
            continue
        med = tr[cols].median().fillna(0)
        scale = tr[cols].std().replace(0, 1).fillna(1)
        x = ((tr[cols].fillna(med) - med) / scale).clip(-5, 5)
        y = ((te[cols].fillna(med) - med) / scale).clip(-5, 5)
        k = min(4, max(2, len(tr) // 40))
        km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(x)
        a.loc[tr.index] = [f"{position}_{i}" for i in km.labels_]
        if len(te):
            b.loc[te.index] = [f"{position}_{i}" for i in km.predict(y)]
    return a, b


def fit_predict(train: pd.DataFrame, test: pd.DataFrame,
                extra: list[str]) -> np.ndarray:
    numeric = ["age", "rang_minutes_saison", "log_minutes", *extra]
    categories = ["archetype"]
    preprocess = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median",
                     add_indicator=True)), ("scale", StandardScaler())]), numeric),
        ("cat", OneHotEncoder(handle_unknown="ignore"), categories),
    ])
    model = Pipeline([("prep", preprocess), ("ridge", Ridge(alpha=20))])
    model.fit(train[numeric + categories], np.log(train.valeur_marche_eur))
    base_train = model.predict(train[numeric + categories])
    base_test = model.predict(test[numeric + categories])
    # Mémoire individuelle apprise uniquement sur les saisons passées,
    # avec réduction vers zéro quand l'historique est court.
    residual = train.assign(residu=np.log(train.valeur_marche_eur) - base_train)
    effects = residual.groupby("player_id").residu.agg(["sum", "count"])
    effect = effects["sum"] / (effects["count"] + 2)
    return base_test + test.player_id.map(effect).fillna(0).to_numpy()


def evaluate(d: pd.DataFrame) -> pd.DataFrame:
    rows = []
    eligible = ((d.date_reference - d.date_valeur).dt.days.between(0, MAX_AGE_VALUE_DAYS))
    for year in (2024, 2025):
        train = d[(d.annee < year) & eligible & (d.valeur_marche_eur > 0)].copy()
        test = d[(d.annee == year) & eligible & (d.valeur_marche_eur > 0)].copy()
        train["archetype"], test["archetype"] = archetypes(train, test)
        if train.empty or test.empty:
            raise ValueError(f"Validation impossible pour {year}")
        previous = (train[train.annee == year - 1]
                    .drop_duplicates("player_id", keep="last")
                    .set_index("player_id"))
        test["persistance"] = test.player_id.map(previous.valeur_marche_eur)
        variants = {
            "age_statut_style_joueur": [],
            "plus_blessures": ["matchs_blessure_valides", "blessure_documentee",
                               "matchs_blessure_n_1",
                               "matchs_blessure_cumul_documente"],
        }
        # La comparaison salariale n'a de sens que sur les lignes dotées d'un
        # salaire, dans une seule devise et avec une couverture suffisante.
        for name, extra in variants.items():
            pred = fit_predict(train, test, extra)
            error = np.log(test.valeur_marche_eur.to_numpy()) - pred
            rows.append({"annee_test": year, "modele": name, "n": len(test),
                         "erreur_mediane_log": float(np.median(abs(error))),
                         "erreur_relative_mediane": float(np.expm1(np.median(abs(error)))),
                         "rmse_log": float(np.sqrt(np.mean(error ** 2)))})
            prior = test.persistance.gt(0).to_numpy()
            same = error[prior]
            rows.append({"annee_test": year, "modele": name + "_comparaison_persistance",
                         "n": int(prior.sum()),
                         "erreur_mediane_log": float(np.median(abs(same))),
                         "erreur_relative_mediane": float(np.expm1(np.median(abs(same)))),
                         "rmse_log": float(np.sqrt(np.mean(same ** 2)))})
        known = test.persistance.gt(0)
        error = np.log(test.loc[known, "valeur_marche_eur"]) - np.log(
            test.loc[known, "persistance"])
        rows.append({"annee_test": year, "modele": "persistance", "n": int(known.sum()),
                     "erreur_mediane_log": float(error.abs().median()),
                     "erreur_relative_mediane": float(np.expm1(error.abs().median())),
                     "rmse_log": float(np.sqrt(np.mean(error ** 2)))})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("classeur", type=Path)
    args = parser.parse_args()
    d, coverage = load_and_check(args.classeur)
    OUT.mkdir(parents=True, exist_ok=True)
    d.to_csv(OUT / "joueurs_annees.csv.gz", index=False, compression="gzip")
    with (OUT / "couverture.json").open("w", encoding="utf-8") as f:
        json.dump(coverage, f, ensure_ascii=False, indent=2)
    result = evaluate(features(d))
    result.to_csv(OUT / "validation.csv", index=False)
    print(json.dumps(coverage, ensure_ascii=False, indent=2))
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
