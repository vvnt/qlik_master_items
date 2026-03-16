"""
generate_derivative_measures.py
--------------------
Génère des mesures comparatives (N vs N-1) à partir d'un fichier JSON de mesures Qlik Sense.

Règles de transformation :
  - Seules les mesures dont le 'name' se termine strictement par '_n' sont étendues.
  - Cas général (_n) : génère _n1, _n_n1, _n_n1_pc.
  - Cas particulier (_tx_n) : génère _n1, _tx_n_n1_pts (au lieu de _n_n1 + _n_n1_pc).
  - Pour _n1, les références à des mesures '_n' dans l'expression sont renommées en '_n1'.
"""

import argparse
import json
import logging
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expr_to_n1(expression: str) -> str:
    """
    Transforme une expression pour sa version _n1 :
      1. Remplace le filtre set-analysis  CAL = {'N'}  par  CAL = {'$(vCAL)'}.
      2. Renomme toutes les références à des mesures '_n' en '_n1'
         (word-boundary stricte : '_n' non suivi d'un chiffre ou d'une lettre).
    """
    # Remplacement du filtre calendrier
    expr = re.sub(
        r"CAL\s*=\s*\{['\"]N['\"]\}",
        "CAL = {'$(vCAL)'}",
        expression,
    )
    # Renommage des mesures _n → _n1  (ex: ca_n → ca_n1, marge_n → marge_n1)
    # On cible '_n' suivi d'une frontière de mot (\b) pour ne pas toucher _n1, _n2…
    expr = re.sub(r"(_n)\b", r"\g<1>1", expr)
    return expr


# ---------------------------------------------------------------------------
# Expansion des mesures
# ---------------------------------------------------------------------------

def expand_measures(measures: list[dict]) -> list[dict]:
    """
    Parcourt la liste des mesures et insère les dérivées juste après
    chaque mesure dont le 'name' se termine strictement par '_n'.
    """
    result = []

    for measure in measures:
        result.append(measure)

        name = measure.get("name", "")

        # Filtre strict : se termine par _n (pas _n1, _n2, _tx_n1, etc.)
        if not re.search(r"_n$", name):
            logger.debug("Mesure ignorée (pas de suffixe _n) : %s", name)
            continue

        label       = measure.get("label", "")
        description = measure.get("description", "")
        fmt         = measure.get("number_format", "integer")
        color       = measure.get("color", "#02b1fd")
        expr_n      = measure.get("expression", "")

        # ── Cas particulier : _tx_n ──────────────────────────────────────────
        is_tx = bool(re.search(r"_tx_n$", name))

        # base_full : tout le name sans le suffixe "_n"  →  ex: "marge_tx"
        base_full = name[:-2]          # retire "_n"
        name_n1   = f"{base_full}_n1"  # ex: "ca_n1" ou "marge_tx_n1"

        # ── _n1 ──────────────────────────────────────────────────────────────
        expr_n1 = _expr_to_n1(expr_n)

        measure_n1 = {
            "name":          name_n1,
            "expression":    expr_n1,
            "label":         f"{label} $(vCAL_display)",
            "number_format": fmt,
            "description":   description,
            "color":         "#999999",
        }
        logger.info("  + %s", name_n1)

        if is_tx:
            # ── _tx_n_n1_pts (écart en points de %) ─────────────────────────
            # ex: name = "marge_tx_n"  →  derived = "marge_tx_n_n1_pts"
            measure_pts = {
                "name":          f"{name}_n1_pts",
                "expression":    f"({name} - {name_n1}) * 100",
                "label":         f"{label} vs $(vCAL_display) %",
                "number_format": "var_pts_1",
                "description":   description,
                "color":         "#02b1fd",
            }
            logger.info("  + %s  [cas _tx_n]", measure_pts["name"])
            result.extend([measure_n1, measure_pts])

        else:
            # ── _n_n1 (écart absolu) ─────────────────────────────────────────
            var_format = "var_pc_1" if "_pc" in fmt else "var_0"

            measure_n_n1 = {
                "name":          f"{base_full}_n_n1",
                "expression":    f"{name} - {name_n1}",
                "label":         f"{label} vs $(vCAL_display)",
                "number_format": var_format,
                "description":   description,
                "color":         color,
            }

            # ── _n_n1_pc (écart en %) ────────────────────────────────────────
            measure_n_n1_pc = {
                "name":          f"{base_full}_n_n1_pc",
                "expression":    f"{name} / {name_n1} - 1",
                "label":         f"{label} vs $(vCAL_display) %",
                "number_format": "var_pc_1",
                "description":   description,
                "color":         color,
            }

            logger.info("  + %s, %s", measure_n_n1["name"], measure_n_n1_pc["name"])
            result.extend([measure_n1, measure_n_n1, measure_n_n1_pc])

    return result


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def load_measures(path: Path) -> list[dict]:
    logger.info("Lecture du fichier : %s", path)
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_measures(measures: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(measures, f, ensure_ascii=False, indent=2)
    logger.info("Fichier écrit : %s", path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Génère des mesures comparatives N vs N-1 pour QlikSense.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Fichier JSON d'entrée (mesures sources).",
    )
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=None,
        help=(
            "Fichier JSON de sortie. "
            "Par défaut : même répertoire que l'entrée, "
            "avec 'base' remplacé par 'extra' dans le nom."
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Affiche les messages de debug.",
    )
    return parser.parse_args()


def default_output_path(input_path: Path) -> Path:
    stem = input_path.stem.replace("base", "extra")
    if stem == input_path.stem:
        stem = f"{input_path.stem}_extra"
    return input_path.with_name(stem + input_path.suffix)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    input_path  = args.input
    output_path = args.output or default_output_path(input_path)

    if not input_path.exists():
        logger.error("Fichier introuvable : %s", input_path)
        raise SystemExit(1)

    measures = load_measures(input_path)
    logger.info("%d mesure(s) chargée(s)", len(measures))

    expanded = expand_measures(measures)

    save_measures(expanded, output_path)
    logger.info(
        "Terminé : %d mesure(s) en entrée → %d en sortie",
        len(measures),
        len(expanded),
    )


if __name__ == "__main__":
    main()
