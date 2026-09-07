"""Télécharge les données Transfermarkt et produit l'extrait data/panel.csv.gz.

À lancer une fois. L'extrait produit est versionné, les sources ne le sont pas.
"""

from pathlib import Path

import pandas as pd
import requests

URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"
BRUT = Path("data/brut")
SORTIE = Path("data/panel.csv.gz")

LIGUES = ["GB1", "ES1", "IT1", "L1", "FR1"]
POSTES = ["Goalkeeper", "Defender", "Midfield", "Attack"]


def telecharger(nom):
    BRUT.mkdir(parents=True, exist_ok=True)
    fichier = BRUT / nom
    if not fichier.exists():
        print(f"téléchargement de {nom}...")
        reponse = requests.get(f"{URL}/{nom}", timeout=300)
        reponse.raise_for_status()
        fichier.write_bytes(reponse.content)
    return fichier


def main():
    joueurs = pd.read_csv(
        telecharger("players.csv.gz"),
        usecols=["player_id", "date_of_birth", "position", "sub_position"],
        parse_dates=["date_of_birth"],
    )
    valeurs = pd.read_csv(telecharger("player_valuations.csv.gz"), parse_dates=["date"])

    df = valeurs.merge(joueurs, on="player_id")
    df = df.dropna(subset=["date_of_birth", "market_value_in_eur", "position"])

    ligue = "player_club_domestic_competition_id"
    if ligue in df.columns:
        df = df[df[ligue].isin(LIGUES)]

    df["age"] = ((df["date"] - df["date_of_birth"]).dt.days / 365.25).astype(int)
    df["annee"] = df["date"].dt.year

    df = df[
        df["age"].between(16, 39)
        & (df["annee"] >= 2005)
        & (df["market_value_in_eur"] > 0)
        & df["position"].isin(POSTES)
    ]

    colonnes = ["player_id", "annee", "age", "position", "sub_position",
                "market_value_in_eur"]
    df[colonnes].to_csv(SORTIE, index=False)

    print(f"{SORTIE} : {len(df):,} lignes, "
          f"{SORTIE.stat().st_size / 1e6:.1f} Mo")


if __name__ == "__main__":
    main()
