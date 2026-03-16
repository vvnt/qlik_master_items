#!/usr/bin/env python3
"""
qlik_master_items.py
--------------------
Outil en ligne de commande pour gérer les master items de type "measure"
d'une application Qlik Cloud via le SDK officiel qlik-sdk.

Dépendances :
    pip install qlik-sdk

Usage :
    python qlik_master_items.py --help
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Import du SDK Qlik
# ---------------------------------------------------------------------------
try:
    from qlik_sdk import Apps, AuthType, Config
    _QLIK_SDK_AVAILABLE = True
except ImportError:
    _QLIK_SDK_AVAILABLE = False

# ---------------------------------------------------------------------------
# Chemins des fichiers de référence (relatifs au script)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()
LOG_FILE = SCRIPT_DIR / "qlik_master_items.log"
DEFAULT_CONFIG = SCRIPT_DIR / "config.json"
NUMBER_FORMATS_FILE = SCRIPT_DIR / "number_formats.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    """
    - Fichier : DEBUG + horodatage, mode append
    - Console : INFO, message brut uniquement
    """
    logger = logging.getLogger("qlik_mi")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
        logger.addHandler(ch)
    return logger


log = setup_logging()

# ---------------------------------------------------------------------------
# Helpers fichiers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict | list:
    if not path.exists():
        log.error("Fichier introuvable : %s", path)
        sys.exit(1)
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        log.error("JSON invalide dans %s : %s", path, exc)
        sys.exit(1)


def load_config(config_path: Path) -> dict:
    cfg = load_json(config_path)
    for key in ("tenant_url", "api_key"):
        if key not in cfg or not cfg[key]:
            log.error("Clé manquante ou vide dans la config : '%s'", key)
            sys.exit(1)
    return cfg


def load_number_formats() -> dict:
    return load_json(NUMBER_FORMATS_FILE).get("formats", {})


# ---------------------------------------------------------------------------
# Connexion Qlik
# ---------------------------------------------------------------------------

def get_qlik_app(tenant_url: str, api_key: str, app_id: str):
    """
    Retourne l'objet app du SDK. La session WebSocket s'ouvre avec
    `with app.open():` dans chaque commande.
    """
    if not _QLIK_SDK_AVAILABLE:
        log.error("Le SDK Qlik n'est pas installé. Exécutez : pip install qlik-sdk")
        sys.exit(1)
    try:
        cfg = Config(host=tenant_url, auth_type=AuthType.APIKey, api_key=api_key)
        app = Apps(config=cfg).get(app_id)
        log.debug("Application %s récupérée.", app_id)
        return app
    except Exception as exc:
        log.error("Impossible de récupérer l'application : %s", exc)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Engine API — helpers
# ---------------------------------------------------------------------------

_MEASURES_LIST_PROPS = {
    "qInfo": {"qType": "MeasureList"},
    "qMeasureListDef": {
        "qType": "measure",
        "qData": {"title": "/qMetaDef/title"},
    },
}


def _attr(obj, key, default=""):
    """Lecture uniforme d'un champ sur un objet SDK ou un dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default) or default


def get_all_measures(app) -> list[dict]:
    """
    Retourne la liste des master items de type measure avec leurs propriétés
    complètes. Doit être appelée dans un bloc `with app.open():`.
    """
    session_obj = app.create_session_object(_MEASURES_LIST_PROPS)
    layout = session_obj.get_layout()
    measure_list = getattr(layout, "qMeasureList", None)
    items = getattr(measure_list, "qItems", []) if measure_list else []

    measures = []
    for item in items:
        obj_id = _attr(getattr(item, "qInfo", None), "qId")
        if not obj_id:
            continue
        try:
            props = app.get_measure(obj_id).get_properties()
        except Exception as exc:
            log.warning("Propriétés inaccessibles pour %s : %s", obj_id, exc)
            continue

        q_measure = getattr(props, "qMeasure", None) or {}
        q_meta    = getattr(props, "qMetaDef",  None) or {}

        # Couleur
        coloring   = _attr(q_measure, "coloring", {})
        base_color = (_attr(coloring, "baseColor", {}) if not isinstance(coloring, dict)
                      else coloring.get("baseColor", {}))
        color = (_attr(base_color, "color") if not isinstance(base_color, dict)
                 else base_color.get("color", ""))

        measures.append({
            "id":               obj_id,
            "title":            _attr(q_meta,    "title"),
            "description":      _attr(q_meta,    "description"),
            "expression":       _attr(q_measure, "qDef"),
            "label_expression": _attr(q_measure, "qLabelExpression"),
            "num_format":       _attr(q_measure, "qNumFormat", {}),
            "color":            color,
        })
    return measures


def find_measure_by_name(measures: list[dict], name: str) -> dict | None:
    for m in measures:
        if m.get("title", "").lower() == name.lower():
            return m
    return None


def build_measure_properties(item_def: dict, formats: dict) -> dict:
    """
    Construit le payload Engine API pour une measure.

    Règles clés :
    - qLabel = name : Qlik résout les références entre master items sur
      qLabel (ex: "ca_n / commande_nb_n"). Le label d'affichage est porté
      exclusivement par qLabelExpression.
    - qLabelExpression = "'label'" (sans =) pour un label statique.
    - qNumFormat : omis si le format est "none".
    """
    name        = item_def.get("name",             "")
    expression  = item_def.get("expression",        "")
    label       = item_def.get("label",             "").strip()
    label_expr  = item_def.get("label_expression",  "").strip()
    description = item_def.get("description",       "")
    color       = item_def.get("color",             "")
    fmt_key     = item_def.get("number_format",     "none")

    # qLabelExpression : expression explicite ou "'label'" pour un label statique
    if not label_expr and label:
        label_expr = f"'{label}'"

    # Format numérique
    fmt_def = formats.get(fmt_key)
    if fmt_def is None:
        log.warning("Format inconnu '%s' pour '%s' — 'none' utilisé.", fmt_key, name)
        fmt_def = formats.get("none", {})

    include_fmt = bool(fmt_def.get("qFmt", "").strip())
    if include_fmt:
        num_format: dict = {"qType": fmt_def.get("qType", "U")}
        if fmt_def.get("qFmt"):  num_format["qFmt"]  = fmt_def["qFmt"]
        if fmt_def.get("qDec"):  num_format["qDec"]  = fmt_def["qDec"]
        if fmt_def.get("qThou"):
            num_format["qThou"]    = fmt_def["qThou"]
            num_format["qUseThou"] = 1
        else:
            num_format["qUseThou"] = 0
        if "qnDec" in fmt_def:   num_format["qnDec"] = fmt_def["qnDec"]

    q_measure: dict = {"qDef": expression, "qLabel": name}
    if label_expr:               q_measure["qLabelExpression"] = label_expr
    if include_fmt:              q_measure["qNumFormat"]       = num_format
    if color:                    q_measure["coloring"]         = {"baseColor": {"color": color, "index": -1}}

    return {
        "qInfo":    {"qType": "measure"},
        "qMetaDef": {"title": name, "description": description},
        "qMeasure": q_measure,
    }


# ---------------------------------------------------------------------------
# Format numérique — reverse lookup
# ---------------------------------------------------------------------------

def _num_fmt_to_dict(num_fmt) -> dict:
    """Normalise un qNumFormat (objet SDK ou dict) en dict Python."""
    if not num_fmt:
        return {}
    if isinstance(num_fmt, dict):
        return num_fmt
    return {k: getattr(num_fmt, k) for k in
            ("qType", "qFmt", "qDec", "qThou", "qnDec", "qUseThou")
            if getattr(num_fmt, k, None) not in (None, "")}


def reverse_lookup_format(num_fmt, formats: dict, measure_name: str = "") -> str:
    """
    Retrouve la clé de format à partir d'un qNumFormat retourné par l'Engine.
    Correspondance exacte sur qType, qFmt, qDec, qThou, qnDec.
    """
    d = _num_fmt_to_dict(num_fmt)
    if not d or not d.get("qFmt", "").strip():
        return "none"
    for key, fmt_def in formats.items():
        if key == "none":
            continue
        if all([
            d.get("qType", "") == fmt_def.get("qType", ""),
            d.get("qFmt",  "") == fmt_def.get("qFmt",  ""),
            d.get("qDec",  "") == fmt_def.get("qDec",  ""),
            d.get("qThou", "") == fmt_def.get("qThou", ""),
            int(d.get("qnDec", 0)) == int(fmt_def.get("qnDec", 0)),
        ]):
            return key
    log.warning(
        "Format non reconnu pour '%s' : %s\n"
        "  → Ajoutez cette entrée dans number_formats.json si nécessaire.",
        measure_name, json.dumps(d, ensure_ascii=False),
    )
    return "none"


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> None:
    """
    Liste les master items de type measure.
    Avec -o : exporte un fichier JSON ré-injectable dans upsert.
    """
    cfg     = load_config(Path(args.config))
    formats = load_number_formats()
    app     = get_qlik_app(cfg["tenant_url"], cfg["api_key"], args.app_id)

    with app.open():
        measures = get_all_measures(app)

    if not measures:
        log.info("Aucun master item de type measure trouvé.")
        return

    items_out = []
    for m in measures:
        fmt_key   = reverse_lookup_format(m.get("num_format"), formats, m["title"])
        label_exp = m.get("label_expression", "")

        # Extraire le label depuis "'label'" ou "='label'"
        label = ""
        for prefix, suffix in (("='", "'"), ("'", "'")):
            if (label_exp.startswith(prefix) and label_exp.endswith(suffix)
                    and len(label_exp) > len(prefix) + len(suffix)):
                label = label_exp[len(prefix):-len(suffix)]
                break

        item: dict = {"name": m["title"], "expression": m["expression"]}
        if label:
            item["label"] = label
        elif label_exp:
            item["label_expression"] = label_exp
        if fmt_key != "none":
            item["number_format"] = fmt_key
        if m.get("description"):
            item["description"] = m["description"]
        if m.get("color"):
            item["color"] = m["color"]
        items_out.append(item)

    log.info("=== %d master item(s) de type measure ===", len(items_out))
    for item in items_out:
        log.info(
            "  [%-20s]  label=%-15s  fmt=%-12s  expr=%s",
            item["name"],
            item.get("label", "—"),
            item.get("number_format", "none"),
            item["expression"][:50],
        )

    if args.output:
        out = Path(args.output)
        out.write_text(json.dumps(items_out, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Export JSON : %s", out.resolve())


def cmd_upsert(args: argparse.Namespace) -> None:
    """
    Crée ou met à jour des master items depuis un fichier JSON.
    Correspondance sur le champ `name`.

    qLabel = name : Qlik résout les références entre master items sur qLabel.
    """
    cfg       = load_config(Path(args.config))
    formats   = load_number_formats()
    items_def = load_json(Path(args.file))
    if isinstance(items_def, dict):
        items_def = [items_def]

    app = get_qlik_app(cfg["tenant_url"], cfg["api_key"], args.app_id)
    created = updated = errors = 0

    with app.open():
        existing = get_all_measures(app)

        for item_def in items_def:
            name       = item_def.get("name",       "").strip()
            expression = item_def.get("expression", "").strip()
            if not name:
                log.warning("Item sans 'name' ignoré.")
                errors += 1
                continue
            if not expression:
                log.warning("Item '%s' ignoré : 'expression' obligatoire.", name)
                errors += 1
                continue

            props         = build_measure_properties(item_def, formats)
            existing_item = find_measure_by_name(existing, name)

            try:
                if existing_item is None:
                    measure_obj = app.create_measure(props)
                    log.info("CRÉÉ       : %s", name)
                    created += 1
                else:
                    measure_obj = app.get_measure(existing_item["id"])
                    props["qInfo"]["qId"] = existing_item["id"]
                    measure_obj.set_properties(props)
                    log.info("MIS À JOUR : %s", name)
                    updated += 1
                measure_obj.get_layout()
            except Exception as exc:
                log.error("Erreur sur '%s' : %s", name, exc)
                errors += 1

        if created + updated > 0:
            try:
                app.do_save()
                log.info("Application sauvegardée.")
            except Exception as exc:
                log.error("Erreur lors de la sauvegarde : %s", exc)

    log.info("Résultat : %d créé(s), %d mis à jour, %d erreur(s).", created, updated, errors)


def cmd_delete(args: argparse.Namespace) -> None:
    """Supprime des master items par nom ou depuis un fichier JSON."""
    cfg = load_config(Path(args.config))
    app = get_qlik_app(cfg["tenant_url"], cfg["api_key"], args.app_id)

    names_to_delete: list[str] = []
    if args.names:
        names_to_delete.extend(args.names)
    if args.file:
        items_def = load_json(Path(args.file))
        if isinstance(items_def, dict):
            items_def = [items_def]
        names_to_delete.extend(i.get("name", "").strip() for i in items_def if i.get("name"))

    if not names_to_delete:
        log.warning("Aucun nom fourni pour la suppression.")
        return

    deleted = not_found = errors = 0

    with app.open():
        existing = get_all_measures(app)
        for name in names_to_delete:
            item = find_measure_by_name(existing, name)
            if item is None:
                log.warning("INTROUVABLE : '%s'", name)
                not_found += 1
                continue
            try:
                app.destroy_measure(item["id"])
                log.info("SUPPRIMÉ : %s", name)
                deleted += 1
            except Exception as exc:
                log.error("Erreur suppression '%s' : %s", name, exc)
                errors += 1

        if deleted > 0:
            try:
                app.do_save()
                log.info("Application sauvegardée.")
            except Exception as exc:
                log.error("Erreur lors de la sauvegarde : %s", exc)

    log.info("Résultat : %d supprimé(s), %d introuvable(s), %d erreur(s).", deleted, not_found, errors)


def cmd_dump(args: argparse.Namespace) -> None:
    """
    Dump brut complet (get_properties + get_layout) pour une ou plusieurs
    measures. Utile pour diagnostiquer ou comparer avant/après une action.
    """
    cfg          = load_config(Path(args.config))
    app          = get_qlik_app(cfg["tenant_url"], cfg["api_key"], args.app_id)
    filter_names = {n.lower() for n in args.names} if args.names else set()

    def _to_dict(obj):
        if isinstance(obj, dict):    return {k: _to_dict(v) for k, v in obj.items()}
        if isinstance(obj, list):    return [_to_dict(v) for v in obj]
        if hasattr(obj, "__dict__"): return {k: _to_dict(v) for k, v in vars(obj).items() if not k.startswith("_")}
        return obj

    results = []
    with app.open():
        session_obj  = app.create_session_object(_MEASURES_LIST_PROPS)
        layout       = session_obj.get_layout()
        measure_list = getattr(layout, "qMeasureList", None)
        items        = getattr(measure_list, "qItems", []) if measure_list else []

        for item in items:
            obj_id = _attr(getattr(item, "qInfo", None), "qId")
            if not obj_id:
                continue
            measure_obj = app.get_measure(obj_id)
            try:    props_dict  = _to_dict(measure_obj.get_properties())
            except Exception as e: props_dict  = {"error": str(e)}
            try:    layout_dict = _to_dict(measure_obj.get_layout())
            except Exception as e: layout_dict = {"error": str(e)}

            title = (props_dict.get("qMetaDef") or {}).get("title", obj_id)
            if filter_names and title.lower() not in filter_names:
                continue

            results.append({"id": obj_id, "title": title,
                            "get_properties": props_dict, "get_layout": layout_dict})
            log.info("=== %s ===", title)
            log.info("  properties : %s", json.dumps(props_dict,  ensure_ascii=False, default=str))
            log.info("  layout     : %s", json.dumps(layout_dict, ensure_ascii=False, default=str))

    if not results:
        log.info("Aucune measure trouvée%s.", f" pour : {args.names}" if filter_names else "")
        return
    if args.output:
        out = Path(args.output)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log.info("Dump : %s (%d measure(s))", out.resolve(), len(results))


def cmd_inspect(args: argparse.Namespace) -> None:
    """Affiche le qNumFormat brut pour chaque measure — pour calibrer number_formats.json."""
    cfg = load_config(Path(args.config))
    app = get_qlik_app(cfg["tenant_url"], cfg["api_key"], args.app_id)

    with app.open():
        measures = get_all_measures(app)

    if not measures:
        log.info("Aucun master item de type measure trouvé.")
        return

    results = []
    log.info("=== qNumFormat pour %d measure(s) ===", len(measures))
    for m in measures:
        d = _num_fmt_to_dict(m.get("num_format"))
        log.info("  [%s]", m["title"])
        for field, val in d.items():
            log.info("    %-10s = %s", field, repr(val))
        if not d:
            log.info("    (aucun format défini)")
        results.append({"name": m["title"], "qNumFormat": d})

    if args.output:
        out = Path(args.output)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Export : %s", out.resolve())


def cmd_list_formats(_args: argparse.Namespace) -> None:
    """Affiche les formats numériques disponibles dans number_formats.json."""
    formats = load_number_formats()
    log.info("=== %d formats disponibles ===", len(formats))
    for key, fmt in formats.items():
        log.info("  %-14s  %-28s  qFmt=%s", key, fmt.get("label", ""), repr(fmt.get("qFmt", "")))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qlik_master_items",
        description="Gestion des master items de type measure sur Qlik Cloud.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python qlik_master_items.py --app-id <ID> list
  python qlik_master_items.py --app-id <ID> list -o export.json
  python qlik_master_items.py --app-id <ID> upsert -f measures.json
  python qlik_master_items.py --app-id <ID> delete -n ca_n marge_n
  python qlik_master_items.py --app-id <ID> delete -f measures.json
  python qlik_master_items.py --app-id <ID> dump -n panier_moy_n -o avant.json
  python qlik_master_items.py --app-id <ID> inspect -o formats_bruts.json
  python qlik_master_items.py formats
        """,
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), metavar="PATH",
                        help=f"Fichier de configuration (défaut : {DEFAULT_CONFIG})")
    parser.add_argument("--app-id", metavar="APP_ID",
                        help="Identifiant de l'application Qlik Cloud")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="Lister les master items")
    p.add_argument("-o", "--output", metavar="PATH", default=None,
                   help="Export JSON ré-injectable dans upsert")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("upsert", help="Créer ou mettre à jour des master items")
    p.add_argument("-f", "--file", required=True, metavar="PATH")
    p.set_defaults(func=cmd_upsert)

    p = sub.add_parser("delete", help="Supprimer des master items")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("-n", "--names", nargs="+", metavar="NAME")
    g.add_argument("-f", "--file",  metavar="PATH")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("dump", help="Dump brut complet pour diagnostic")
    p.add_argument("-n", "--names", nargs="+", metavar="NAME", default=None)
    p.add_argument("-o", "--output", metavar="PATH", default=None)
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("inspect", help="Inspecter les qNumFormat bruts")
    p.add_argument("-o", "--output", metavar="PATH", default=None)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("formats", help="Lister les formats numériques disponibles")
    p.set_defaults(func=cmd_list_formats)

    return parser


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()
    if args.command != "formats" and not args.app_id:
        parser.error(f"--app-id est obligatoire pour la commande '{args.command}'.")
    log.debug("Commande : %s | app_id : %s", args.command, getattr(args, "app_id", "—"))
    args.func(args)


if __name__ == "__main__":
    main()
