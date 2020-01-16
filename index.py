#!/usr/bin/python3
__host__='localhost'
__port__=8080
__root__="."
__form__="index.html"

from enum import Enum
from bottle import run, static_file, template, request, response, route, default_app
import trueskill
import json
import logging
import time

class Mode(Enum):
    Player = 1
    Team = 2

class GameInfo():
    "Trueskill settings for a particular game"
    def __init__(self, source):
        if not source is None:
            self.iMu = source.get("iMu", str(trueskill.MU))
            self.iSigma = source.get("iSigma", str(trueskill.SIGMA))
            self.Beta = source.get("Beta", str(trueskill.BETA))
            self.Tau = source.get("Tau", str(trueskill.TAU))
            self.pDraw = source.get("pDraw", str(trueskill.DRAW_PROBABILITY))
            self.Delta = source.get("Delta", str(trueskill.DELTA))

class MatchInfo():
    "Description of a particular game/match - who played and how they ranked"
    def __init__(self, source):
        if not source is None:
            self.Teams = source.getall("Teams")
            self.Players = source.getall("Players")
            self.PlayerTeams = source.getall("PlayerTeams")
            self.Mus = source.getall("Mus")
            self.Sigmas = source.getall("Sigmas")
            self.Etas = []
            self.Ranking = source.getall("Ranking")
            self.Weights = source.getall("Weights")
            self.Mode = Mode.Player if len(self.Teams) == 0 else Mode.Team

class ResultInfo():
    "Results of a Trueskill update. Takes a results group as produced by trueskill.rate. Wants to have a GameInfo structure too, for the RankingFactor, and a MatchInfo structure so it can order the Players in same way as the MatchInfo"
    def __init__(self, GI, MI, source):
        if GI is None:
            RankingFactor = 3.0
        else:
            RankingFactor = float(GI.iMu) / float(GI.iSigma)

        if MI is None:
            self.Players = None
            self.Mus = None
            self.Sigmas = None
            self.Etas = None
        else:
            self.Players = [None]*len(MI.Players)
            self.Mus = [None]*len(MI.Players)
            self.Sigmas = [None]*len(MI.Players)
            self.Etas = [None]*len(MI.Players)

        if not source is None:
            for Team in source:
                for Player in Team:
                    assert Player in MI.Players, "Internal Error: Trueskill returned a player that it was not provided"
                    p = MI.Players.index(Player)
                    self.Players[p] = Player
                    self.Mus[p] = str(Team[Player].mu)
                    self.Sigmas[p] = str(Team[Player].sigma)
                    self.Etas[p] = str(max(float(self.Mus[p]) - RankingFactor * float(self.Sigmas[p]),0.0))
            assert not None in self.Players, "Internal Error: Trueskill returned fewer players than provided."

def CleanList(l):
    "Tidies up a list that might contain items with leading and trailing spaces and or comma separated lists, into a simple list of values"
    return list(filter(lambda x:x != '', [s.strip() for s in ','.join(l).split(',')]))

def CheckMatchInfo(GI, MI):
    "Basic data cleaning and quality check on incoming data"
    GI.iMu = float(GI.iMu)
    GI.iSigma = float(GI.iSigma)
    GI.Beta = float(GI.Beta)
    GI.Tau = float(GI.Tau)
    GI.pDraw = float(GI.pDraw)
    GI.Delta = float(GI.Delta)

    MI.Players = CleanList(MI.Players)
    MI.Mus = CleanList(MI.Mus)
    MI.Sigmas = CleanList(MI.Sigmas)
    MI.Weights = CleanList(MI.Weights)
    MI.Ranking = CleanList(MI.Ranking)
    MI.Teams = CleanList(MI.Teams)
    MI.PlayerTeams = CleanList(MI.PlayerTeams)

    assert len(MI.Players) == 0 or len(MI.Players) > 1, "You must specify at least two players."
    assert len(MI.Mus) == 0 or len(MI.Mus) == len(MI.Players), "You must specify one mean per player."
    assert len(MI.Sigmas) == 0 or len(MI.Sigmas) == len(MI.Players), "You must specify one standard deviation per player."
    assert len(MI.Weights) == 0 or len(MI.Weights) == len(MI.Players), "You must specify one weight per player."

    if MI.Mode == Mode.Player:
        assert len(MI.Ranking) == 0 or len(MI.Ranking) == len(MI.Players), "You must specify one ranking per player."
    else:
        assert len(MI.Ranking) == 0 or len(MI.Ranking) == len(MI.Teams), "You must specify one ranking per team."
        assert len(MI.Teams) > 1, "You must specify at least two teams."
        assert len(MI.PlayerTeams) == len(MI.Players), "You must specify at least one team per player."
        TeamsWithPlayers = set()
        for team in MI.PlayerTeams:
            assert team in MI.Teams, "Every team a player is in must be listed as a team as well."
            TeamsWithPlayers.add(team)
        for team in MI.Teams:
            assert team in TeamsWithPlayers, "Each team must have at least one player."

    assert len(MI.Players)==len(set(MI.Players)), "Players must all have unique names."
    assert len(MI.Teams)==len(set(MI.Teams)), "Teams must all have unique names."

    if len(MI.Ranking) == 0: MI.Ranking = None
    if len(MI.Mus) == 0: MI.Mus = [str(GI.iMu)] * len(MI.Players)
    if len(MI.Sigmas) == 0: MI.Sigmas = [str(GI.iSigma)] * len(MI.Players)
    if len(MI.Weights) == 0: MI.Weights = None

    RankingFactor = float(GI.iMu) / float(GI.iSigma)
    MI.Etas = []
    for i in range(0, len(MI.Players)):
        MI.Etas.append(str(max(float(MI.Mus[i]) - RankingFactor * float(MI.Sigmas[i]),0.0)))

def BuildRatingGroups(MI):
    "Takes a MatchInfo structure (built from form or URL input) and builds a RatingGroups structure (for trueskill.rate)"
    RGs = []
    if MI.Mode == Mode.Player:
        for p in range(0, len(MI.Players)):
            RGs.append({MI.Players[p]: trueskill.Rating(mu=float(MI.Mus[p]),sigma=float(MI.Sigmas[p]))})
    else:
        for t in range(0, len(MI.Teams)):
            RGs.append({})
            assert len(RGs)-1 == t, "Internal Error"
            for p in  range(0, len(MI.Players)):
                if MI.PlayerTeams[p] == MI.Teams[t]:
                    RGs[t][MI.Players[p]] = trueskill.Rating(mu=float(MI.Mus[p]),sigma=float(MI.Sigmas[p]))
    assert len(RGs) > 1, "To calculate new ratings, you need to specify at least two players or teams."
    return RGs

def BuildWeightGroups(MI):
    "Takes a MatchInfo structure (built from form or URL input) and builds a dictionary of weights (for trueskill.rate)"
    if MI.Weights is None:
        return None
    else:
        WGs = {}  # A dictionary keyed on a tuple of (team index, player key) which weights as values
        if MI.Mode == Mode.Player:
            for p in range(0, len(MI.Players)):
                WGs[(0, MI.Players[p])] = float(MI.Weights[p])
        else:
            for t in range(0, len(MI.Teams)):
                for p in  range(0, len(MI.Players)):
                    if MI.PlayerTeams[p] == MI.Teams[t]:
                        WGs[(t, MI.Players[p])] = float(MI.Weights[p])
        return WGs

def FormatCSV(MI, results):
    "Produces CSV formated results, returned as a multiline string of comma separated values with a header line"
    csv = "Player, Old Mu, Old Sigma, Old Eta, New Mu, New Sigma, New Eta, Delta Mu, Delta Sigma, Delta Eta\n"
    for p in range(0,len(MI.Players)):
        assert MI.Players[p] == results.Players[p], "Internal Error: TrueSkill returned inconsistent results."
        csv = csv + "{},{},{},{},{},{},{},{},{},{}\n".format(
            MI.Players[p],
            MI.Mus[p],MI.Sigmas[p],MI.Etas[p],
            results.Mus[p],results.Sigmas[p],results.Etas[p],
            float(results.Mus[p])-float(MI.Mus[p]),float(results.Sigmas[p])-float(MI.Sigmas[p]),float(results.Etas[p])-float(MI.Etas[p])
        )
    return csv

def FormatHTML(MI, results):
    "Produces HTML formated results, returned as a multiline string with an unadorned HTML table in it"
    html = "<table>\n<tr><td>Player</td><td>Old Mu</td><td>Old Sigma</td><td>Old Eta</td><td>New Mu</td><td>New Sigma</td><td>New Eta</td><td>Delta Mu</td><td>Delta Sigma</td><td>Delta Eta</td></tr>\n"
    for p in range(0,len(MI.Players)):
        assert MI.Players[p] == results.Players[p], "Internal Error: TrueSkill returned inconsistent results."
        html = html + "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>\n".format(
            MI.Players[p],
            MI.Mus[p],MI.Sigmas[p],MI.Etas[p],
            results.Mus[p],results.Sigmas[p],results.Etas[p],
            float(results.Mus[p])-float(MI.Mus[p]),float(results.Sigmas[p])-float(MI.Sigmas[p]),float(results.Etas[p])-float(MI.Etas[p])
        )
    html = html + "</table>"
    return html

def FormatXML(MI, results):
    "Produces XML formated results, returned as a multiline string"
    xml = ""
    for p in range(0,len(MI.Players)):
        assert MI.Players[p] == results.Players[p], "Internal Error: TrueSkill returned inconsistent results."
        xml = xml + "<Player>\n\t<Name>{}</Name>\n\t<OldMu>{}</OldMu>\n\t<OldSigma>{}</OldSigma>\n\t<OldEta>{}</OldEta>\n\t<NewMu>{}</NewMu>\n\t<NewSigma>{}</NewSigma>\n\t<NewEta>{}</NewEta>\n\t<DeltaMu>{}</DeltaMu>\n\t<DeltaSigma>{}</DeltaSigma>\n\t<DeltaEta>{}</DeltaEta>\n</Player>\n".format(
            MI.Players[p],
            MI.Mus[p],MI.Sigmas[p],MI.Etas[p],
            results.Mus[p],results.Sigmas[p],results.Etas[p],
            float(results.Mus[p])-float(MI.Mus[p]),float(results.Sigmas[p])-float(MI.Sigmas[p]),float(results.Etas[p])-float(MI.Etas[p])
        )
    return xml

@route('/')
def form():
    try:
        logging.basicConfig(filename=time.strftime("%Y-%m") + '.log',level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        logging.info(request.remote_addr + " " + request.query_string)

        Error = None
        # Fetch data from the request and check validity
        GI = GameInfo(request.query)
        MI = MatchInfo(request.query)

        CheckMatchInfo(GI, MI)

        Go = request.query.get("Go","").lower() in ("yes", "true", "1")

        if Go:
            TS = trueskill.TrueSkill(mu=GI.iMu, sigma=GI.iSigma, beta=GI.Beta, tau=GI.Tau, draw_probability=GI.pDraw)

            Ranking = None if MI.Ranking == None else list(map(int, MI.Ranking))
            OldRatingGroups = BuildRatingGroups(MI)
            WeightGroups = BuildWeightGroups(MI)
            NewRatingGroups = TS.rate(OldRatingGroups, Ranking, WeightGroups, GI.Delta)
            Results = ResultInfo(GI, MI, NewRatingGroups)

        Format = request.query.get("Format","").lower()

    except Exception as e:
        Error = str(e)

    finally:
        if not 'Results' in locals():
            Results = ResultInfo(None, None, None)

        output = "Internal Error: missing output."

        if (not 'Format' in locals()) or (Format is None) or (len(Format) == 0):
            output = template(__form__,
                        GameInfo=GI,
                        Ranking=json.dumps(MI.Ranking),
                        Players=json.dumps(MI.Players),
                        Mus=json.dumps(MI.Mus),
                        Sigmas=json.dumps(MI.Sigmas),
                        Etas=json.dumps(MI.Etas),
                        Weights=json.dumps(MI.Weights),
                        Teams=json.dumps(MI.Teams),
                        PlayerTeams=json.dumps(MI.PlayerTeams),
                        rMus=json.dumps(Results.Mus),
                        rSigmas=json.dumps(Results.Sigmas),
                        rEtas=json.dumps(Results.Etas),
                        Error=json.dumps(Error),
                        HideForm=json.dumps(False))
        else:
            if not Error is None:
                output = Error
            else:
                if Format == "csv":
                    response.headers['Content-Type'] = 'text/csv'
                    response.headers['Content-Disposition'] = 'attachment; filename="trueskill.csv"'
                    output = FormatCSV(MI, Results)
                else:
                    if Format == "html":
                        response.headers['Content-Type'] = 'text/html'
                        response.headers['Content-Disposition'] = 'inline'
                        output = FormatHTML(MI, Results)
                    else:
                        if Format == "xml":
                            response.headers['Content-Type'] = 'xml/application'
                            response.headers['Content-Disposition'] = 'attachment; filename="trueskill.xml"'
                            output = FormatXML(MI, Results)

        return output

@route('/default.css')
def serve_css():
    return static_file("default.css", __root__)

@route('/help.html')
def serve_help():
    return static_file("help.html", __root__)

@route('/credits.html')
def serve_credits():
    return static_file("credits.html", __root__)

if __name__ == "__main__":
    # Run a simple webserver
    run(host='localhost', port=8085)
else:
    # Inform uWSGI of the app to run
    app = application = default_app()
