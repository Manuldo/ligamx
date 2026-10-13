"""
Equipos oficiales por liga — para detectar intrusos que el scraper metió
en la liga equivocada (ej. Brighton, de la Premier, colado en LaLiga).

No pretende ser exhaustivo al 100%, pero cubre los clubes de primera división
de cada liga en temporada regular. Se usa para diagnóstico y limpieza en el
panel del motor. Comparación laxa (por subcadena, sin acentos ni mayúsculas).
"""
import unicodedata

EQUIPOS_POR_LIGA = {
    "ligamx": [
        "america", "guadalajara", "chivas", "cruz azul", "pumas", "unam",
        "tigres", "monterrey", "toluca", "leon", "santos", "santos laguna",
        "pachuca", "atlas", "puebla", "necaxa", "queretaro", "tijuana", "xolos",
        "mazatlan", "juarez", "fc juarez", "san luis", "atletico san luis",
        "atlante",
    ],
    "premier": [
        "arsenal", "aston villa", "bournemouth", "brentford", "brighton",
        "chelsea", "crystal palace", "everton", "fulham", "ipswich",
        "leicester", "liverpool", "manchester city", "manchester united",
        "man city", "man utd", "newcastle", "nottingham", "forest",
        "southampton", "tottenham", "spurs", "west ham", "wolves",
        "wolverhampton", "leeds", "hull", "burnley", "sheffield", "sunderland",
    ],
    "laliga": [
        "barcelona", "real madrid", "atletico madrid", "atletico de madrid",
        "sevilla", "real betis", "betis", "real sociedad", "villarreal",
        "valencia", "athletic", "athletic club", "bilbao", "osasuna", "celta",
        "celta vigo", "rayo vallecano", "mallorca", "girona", "getafe",
        "las palmas", "alaves", "deportivo alaves", "espanyol", "leganes",
        "valladolid", "elche", "levante", "oviedo", "real oviedo",
    ],
    "seriea": [
        "inter", "milan", "ac milan", "juventus", "napoli", "roma", "lazio",
        "atalanta", "fiorentina", "bologna", "torino", "udinese", "genoa",
        "cagliari", "lecce", "verona", "hellas verona", "empoli", "parma",
        "como", "venezia", "monza", "cremonese", "pisa", "sassuolo",
    ],
    "bundesliga": [
        "bayern", "bayern munich", "dortmund", "borussia dortmund", "leipzig",
        "rb leipzig", "leverkusen", "bayer leverkusen", "frankfurt",
        "eintracht", "wolfsburg", "freiburg", "hoffenheim", "mainz",
        "monchengladbach", "gladbach", "werder", "bremen", "augsburg",
        "stuttgart", "union berlin", "bochum", "heidenheim", "st pauli",
        "holstein kiel", "hamburg", "koln", "colonia",
    ],
    "ligue1": [
        "psg", "paris", "paris saint-germain", "marsella", "marseille",
        "monaco", "lyon", "lille", "nice", "niza", "rennes", "lens",
        "strasbourg", "estrasburgo", "nantes", "montpellier", "toulouse",
        "brest", "reims", "auxerre", "angers", "le havre", "saint-etienne",
        "metz", "lorient", "paris fc",
    ],
}


def _norm(s: str) -> str:
    """minúsculas sin acentos."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return s.lower().strip()


def equipo_pertenece(nombre: str, liga: str) -> bool:
    """True si el equipo pertenece (o parece pertenecer) a la liga dada."""
    lista = EQUIPOS_POR_LIGA.get(liga)
    if not lista:
        return True  # liga desconocida: no marcamos intrusos
    n = _norm(nombre)
    if not n:
        return True
    for eq in lista:
        e = _norm(eq)
        if e in n or n in e:
            return True
    return False
