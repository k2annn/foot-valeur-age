import unittest
from unittest.mock import patch
from pathlib import Path

import pandas as pd

from iteration_v2 import INJURY_OK, load_and_check, features, STYLES


def sample():
    rows = []
    for year in range(2021, 2026):
        rows.append({
            "player_id": "TM1", "annee": year, "saison": f"{year-1}-{year}",
            "joueur": "Exemple", "naissance_annee": 1999, "poste": "MF",
            "club_1": "A", "club_2": "B" if year == 2023 else None,
            "club_3": None, "nombre_clubs": 2 if year == 2023 else 1,
            "statut_ordre_clubs": ("ordre_non_resolu" if year == 2023
                                   else "un_club_dans_les_statistiques"),
            "clubs_observes": "A; B" if year == 2023 else "A",
            "minutes": 900, "games": 10, "valeur_marche_eur": 1_000_000,
            "date_valeur": f"{year}-06-01",
            "date_reference": f"{year}-06-30",
            "matchs_manques_blessure": 4 if year == 2022 else None,
            "statut_blessures": (INJURY_OK if year == 2022 else
                                "aucun_episode_documente_ne_signifie_pas_zero"),
            "fin_contrat_au_30_juin": None, "statut_contrat": "historique_asof_indisponible",
            "salaire_annuel_brut_estime": None, "devise_salaire": None,
            "statut_salaire": "indisponible",
            **{c: 1 for c in STYLES},
        })
    return pd.DataFrame(rows)


class AnnualContractTest(unittest.TestCase):
    @patch("iteration_v2.pd.read_excel")
    def test_unknown_order_and_injury_not_zero(self, read):
        read.return_value = sample()
        d, report = load_and_check(Path("unused.xlsx"))
        self.assertEqual(report["ordre_clubs_non_resolu"], 1)
        row = d[d.annee == 2023].iloc[0]
        self.assertTrue(pd.isna(row.club_1))
        self.assertEqual(row.clubs_observes, "A; B")
        f = features(d)
        self.assertTrue(pd.isna(f.loc[f.annee == 2021, "matchs_blessure_valides"].iloc[0]))
        self.assertEqual(f.loc[f.annee == 2023, "matchs_blessure_n_1"].iloc[0], 4)

    @patch("iteration_v2.pd.read_excel")
    def test_duplicate_key_fails(self, read):
        d = sample()
        read.return_value = pd.concat([d, d.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "doublon"):
            load_and_check(Path("unused.xlsx"))

    @patch("iteration_v2.pd.read_excel")
    def test_future_valuation_fails(self, read):
        d = sample()
        d.loc[0, "date_valeur"] = "2021-07-01"
        read.return_value = d
        with self.assertRaisesRegex(ValueError, "postérieure"):
            load_and_check(Path("unused.xlsx"))


if __name__ == "__main__":
    unittest.main()
