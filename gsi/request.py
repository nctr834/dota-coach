from dotenv import load_dotenv
import cloudscraper
import os

load_dotenv()
token = os.getenv("STRATZ_API_KEY")
scraper = cloudscraper.create_scraper()


def gather_match_data(match_id: int):
    query = f"""
    {{
    live{{
        match (id:{match_id}){{
        matchId
        radiantScore
        direScore
        leagueId
        delay
        spectators
        averageRank
        buildingState
        radiantLead
        lobbyId
        lobbyType
        serverSteamId
        gameTime
        completed
        isUpdating
        isParsing
        radiantTeamId
        direTeamId
        parseBeginGameTime
        numHumanPlayers
        gameMode
        gameState
        gameMinute
        createdDateTime
        modifiedDateTime
        }}
    }}
    }}
    """
    match_data_response = scraper.post(
        "https://api.stratz.com/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query},
    )
    if match_data_response.status_code == 200:
        match_data = match_data_response.json()
        return match_data
    return None


match_id = "8706507400"
print(gather_match_data(match_id))
