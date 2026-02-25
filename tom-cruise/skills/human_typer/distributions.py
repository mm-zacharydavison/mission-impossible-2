import math
import random


def standard_normal() -> float:
    """Generate a standard normal random variate using the Box-Muller transform."""
    u1 = 0.0
    u2 = 0.0
    # Avoid log(0)
    while u1 == 0.0:
        u1 = random.random()
    while u2 == 0.0:
        u2 = random.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def log_normal(mu: float, sigma: float) -> float:
    """Generate a log-normal random variate."""
    return math.exp(mu + sigma * standard_normal())


def wpm_to_log_normal_params(wpm: float, wpm_std_dev: float) -> tuple[float, float]:
    """
    Convert a target WPM and standard deviation to log-normal parameters (mu, sigma)
    for inter-keystroke interval (IKI) in milliseconds.

    Average word length is assumed to be 5 characters.
    """
    chars_per_minute = wpm * 5
    mean_iki_ms = 60000 / chars_per_minute

    # Coefficient of variation derived from WPM std dev
    cv = wpm_std_dev / wpm

    # sigma^2 = ln(1 + cv^2)
    sigma2 = math.log(1 + cv * cv)
    sigma = math.sqrt(sigma2)
    mu = math.log(mean_iki_ms) - sigma2 / 2

    return mu, sigma


def sample_iki(wpm: float, wpm_std_dev: float) -> float:
    """Sample an inter-keystroke interval in milliseconds from a log-normal distribution."""
    mu, sigma = wpm_to_log_normal_params(wpm, wpm_std_dev)
    return log_normal(mu, sigma)


QWERTY_NEIGHBORS: dict[str, list[str]] = {
    # Number row
    "1": ["2", "q"],
    "2": ["1", "3", "q", "w"],
    "3": ["2", "4", "w", "e"],
    "4": ["3", "5", "e", "r"],
    "5": ["4", "6", "r", "t"],
    "6": ["5", "7", "t", "y"],
    "7": ["6", "8", "y", "u"],
    "8": ["7", "9", "u", "i"],
    "9": ["8", "0", "i", "o"],
    "0": ["9", "-", "o", "p"],
    # Top row
    "q": ["1", "2", "w", "a"],
    "w": ["2", "3", "q", "e", "a", "s"],
    "e": ["3", "4", "w", "r", "s", "d"],
    "r": ["4", "5", "e", "t", "d", "f"],
    "t": ["5", "6", "r", "y", "f", "g"],
    "y": ["6", "7", "t", "u", "g", "h"],
    "u": ["7", "8", "y", "i", "h", "j"],
    "i": ["8", "9", "u", "o", "j", "k"],
    "o": ["9", "0", "i", "p", "k", "l"],
    "p": ["0", "-", "o", "[", "l", ";"],
    # Home row
    "a": ["q", "w", "s", "z"],
    "s": ["w", "e", "a", "d", "z", "x"],
    "d": ["e", "r", "s", "f", "x", "c"],
    "f": ["r", "t", "d", "g", "c", "v"],
    "g": ["t", "y", "f", "h", "v", "b"],
    "h": ["y", "u", "g", "j", "b", "n"],
    "j": ["u", "i", "h", "k", "n", "m"],
    "k": ["i", "o", "j", "l", "m", ","],
    "l": ["o", "p", "k", ";", ",", "."],
    ";": ["p", "[", "l", "'", ".", "/"],
    # Bottom row
    "z": ["a", "s", "x"],
    "x": ["s", "d", "z", "c"],
    "c": ["d", "f", "x", "v"],
    "v": ["f", "g", "c", "b"],
    "b": ["g", "h", "v", "n"],
    "n": ["h", "j", "b", "m"],
    "m": ["j", "k", "n", ","],
    ",": ["k", "l", "m", "."],
    ".": ["l", ";", ",", "/"],
    "/": [";", "'", "."],
    # Space has no neighbors
    " ": [],
}


def nearby_key(key: str) -> str:
    """Pick a random neighboring key on a QWERTY keyboard for a typo."""
    lower = key.lower()
    neighbors = QWERTY_NEIGHBORS.get(lower)
    if neighbors and len(neighbors) > 0:
        picked = random.choice(neighbors)
        if key != key.lower():
            return picked.upper()
        return picked
    # Fallback: random letter
    letters = "abcdefghijklmnopqrstuvwxyz"
    picked = random.choice(letters)
    return picked.upper() if key != key.lower() else picked
